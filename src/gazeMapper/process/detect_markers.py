import pathlib
import pandas as pd
import numpy as np
import typing

from glassesTools import annotation, aruco, drawing, marker as gt_marker, naming as gt_naming, pose, process_pool, propagating_thread, ocv, timestamps
from glassesTools.gui.video_player import GUI

from .. import config, episode, marker, naming, plane, process, session, synchronization


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None=None, show_visualization=False, visualization_show_rejected_markers=False, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected markers and planes
    # if show_rejected_markers, rejected ArUco marker candidates are also shown in the viewer. Possibly useful for debug
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # if we need gui, we run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    if show_visualization:
        gui = GUI(use_thread = False)
        gui.add_window(working_dir.name)
        gui.set_interruptible(False)
        gui.set_detachable(True)
        gui.set_show_controls(True)
        gui.set_show_play_percentage(True)
        gui.set_show_action_tooltip(True)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, visualization_show_rejected_markers, progress_indicator), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, False, progress_indicator, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI|None, visualization_show_rejected_markers: bool, progress_indicator: process_pool.JobProgress|None, **study_settings):
    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('frames')
    progress_indicator.set_start_time_to_now()

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]

    # get interval(s) coded to be analyzed, if any
    # We don't need them if they would be ignored because the whole video would be processed. The whole video is processed when study_config.auto_code_sync_points or study_config.auto_code_episodes are set
    has_auto_code = process.config_has_auto_coding(study_config)
    episode_file = working_dir / naming.coding_file
    if episode_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(episode_file), [cs['name'] for cs in study_config.coding_setup])
    else:
        if not has_auto_code:   # missing coding is ok when auto coding is set up, as then we process all frames anyway
            raise RuntimeError(f'Coding is missing, cannot run Detect Markers\n{episode_file}')
        episodes = episode.get_empty_marker_dict(annotation.get_all_event_names())

    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        trial_events = process.get_specific_event_types(study_config, annotation.EventType.Trial)
        if trial_events and any(episodes[cs['name']] for cs in trial_events):
            raise ValueError(f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}) and should not be coded for this recording ({rec_def.name})')
        all_recs = [r.name for r in study_config.session_def.recordings]
        for cs in trial_events:
            # NB: don't error if we don't need trial episodes for coding.
            episodes[cs['name']] = synchronization.get_episode_frame_indices_from_ref(working_dir, cs['name'], rec_def.name, study_config.sync_ref_recording, all_recs, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, study_config.sync_ref_stretch_which, missing_ref_coding_ok=has_auto_code)

    sync_target_functions, function_frames  = _get_sync_function(study_config, rec_def, episodes)
    planes_setup, plane_frames              = _get_plane_setup(study_config, config_dir, episodes)

    # set up pose estimator
    estimator = pose.Estimator(in_video, working_dir / gt_naming.frame_timestamps_fname, working_dir / gt_naming.scene_camera_calibration_fname)
    # first, register all ArUco planes and individual markers with ArUco manager, which
    # will then wrap their detection and register them with the pose estimator
    aruco_manager = aruco.Manager()
    for p in planes_setup:
        aruco_manager.add_plane(p, planes_setup[p], plane_frames[p])
        if hasattr(planes_setup[p]['plane'],'is_dynamic') and planes_setup[p]['plane'].is_dynamic():
            markers = planes_setup[p]['plane'].get_marker_IDs()
            marker_setup = planes_setup[p]['plane'].get_dynamic_marker_setup()
            for c in markers:
                if c=='plane':
                    continue
                for m in markers[c]:
                    aruco_manager.add_individual_marker(m, marker_setup, plane_frames[p])
    for m in (markers:=marker.get_setup_for_markers(study_config.individual_markers)):
        aruco_manager.add_individual_marker(m, markers[m])
    aruco_manager.consolidate_setup()
    aruco_manager.register_with_estimator(estimator)
    # other setup of estimator
    for sfe in sync_target_functions:
        estimator.register_extra_processing_fun(f'sync_{sfe}', function_frames[sfe], *sync_target_functions[sfe])
    estimator.attach_gui(gui)
    if gui is not None:
        gui.set_show_timeline(True, timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname), annotation.flatten_annotation_dict(episodes), window_id=gui.main_window_id)
        # override colors with settings, but do not disable drawing ones that settings disable
        colors = {}
        for c in ('mapped_video_plane_marker_color','mapped_video_recovered_plane_marker_color','mapped_video_individual_marker_color','mapped_video_unexpected_marker_color'):
            clr = getattr(study_config,c) if getattr(study_config,c) is not None else config.study_defaults[c]
            colors[c.removeprefix('mapped_video_')] = clr
        if visualization_show_rejected_markers:
            colors['rejected_marker_color'] = study_config.mapped_video_rejected_marker_color or (255,0,0)
        aruco_manager.set_visualization_colors(**colors)

    # prep progress indicator
    progress_indicator.set_total(total:=estimator.video_ts.get_last()[0])
    progress_indicator.set_intervals(step:=min(20,int(total/200)), step)
    estimator.set_progress_updater(progress_indicator.update)

    poses, individual_markers, sync_target_signals = estimator.process_video()

    for p in poses:
        pose.write_list_to_file(poses[p], working_dir/f'{naming.plane_pose_prefix}{p}.tsv', skip_failed=True)
    for m in individual_markers:
        gt_marker.write_list_to_file(individual_markers[m], gt_marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir), skip_failed=False)
    for s in sync_target_signals:
        df = pd.DataFrame(sync_target_signals[s],columns=['frame_idx','target_x','target_y'])
        nm = s.removeprefix('sync_')
        df.to_csv(working_dir/f'{naming.target_sync_prefix}{nm}.tsv', sep='\t', index=False, na_rep='nan', float_format="%.8f")

    # update state
    session.update_action_states(working_dir, process.Action.DETECT_MARKERS, process_pool.State.Completed, study_config)


def _get_sync_function(study_config: config.Study,
                       rec_def: session.RecordingDefinition,
                       episodes: dict[str,list[list[int]]]|None = None) -> tuple[dict[str, tuple[typing.Callable[[str,int,np.ndarray,ocv.CameraParams,typing.Any], tuple[float,float]], dict[str, typing.Any], typing.Callable[[str,np.ndarray,int,tuple[float,float]], None]]], dict[str, list[list[int]]|None]]:
    sync_target_function: dict[str, tuple[typing.Callable[[str,int,np.ndarray,ocv.CameraParams,typing.Any], tuple[float,float]], dict[str, typing.Any], typing.Callable[[str,np.ndarray,int,tuple[float,float]], None]]] = {}
    analyze_frames: dict[str, list[list[int]]|None] = {}
    # NB: only for eye tracker recordings, others don't have eye tracking data and thus nothing to sync
    if rec_def.type==session.RecordingType.Eye_Tracker:
        et_sync_events = process.get_specific_event_types(study_config, annotation.EventType.Sync_ET_Data, ['sync_setup'])
        for cs in et_sync_events:
            match cs['sync_setup']['get_cam_movement_method']:
                case 'plane':
                    if not cs['planes']:
                        raise ValueError(f'The method for synchronizing eye tracker data to the scene camera (get_cam_movement_method) is set to "plane" for the "{cs["name"]}" event but no plane is configured for this event. Cannot continue.')
                    # NB: no extra_funcs to run
                case 'function':
                    import importlib
                    to_load = cs['sync_setup']['get_cam_movement_function']['module_or_file']
                    if (to_load_path:=pathlib.Path(to_load)).is_file():
                        import sys
                        module_name = to_load_path.stem
                        spec = importlib.util.spec_from_file_location(module_name, to_load_path)
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                    else:
                        module = importlib.import_module(cs['sync_setup']['get_cam_movement_function']['module_or_file'])
                    func = getattr(module, cs['sync_setup']['get_cam_movement_function']['function'])
                    sync_target_function[cs["name"]] = (func, cs['sync_setup']['get_cam_movement_function']['parameters'], _sync_function_output_drawer)
                    if episodes and cs['name'] in episodes:
                        analyze_frames[cs['name']] = episodes[cs['name']]
                    else:
                        analyze_frames[cs['name']] = None
                case _:
                    raise ValueError(f'sync_setup.get_cam_movement_method={cs["sync_setup"]["get_cam_movement_method"]} for the "{cs["name"]}" event not understood')

    return sync_target_function, analyze_frames

def _sync_function_output_drawer(proc_name: str, frame: np.ndarray, frame_idx: int, t: tuple[float,float], sub_pixel_fac=8):
    # input is tx, ty pixel positions on the camera image
    ll = 20
    tx,ty = t
    drawing.openCVLine(frame, (tx,ty-ll), (tx,ty+ll), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVLine(frame, (tx-ll,ty), (tx+ll,ty), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVCircle(frame, (tx,ty), 3, (0,0,255), -1, sub_pixel_fac)

def _get_plane_setup(study_config: config.Study,
                     config_dir: pathlib.Path,
                     episodes: dict[str,list[list[int]]]|None = None) -> tuple[dict[str, aruco.PlaneSetup], dict[str, list[list[int]]|None]]:
    # process the above into a dict of plane definitions and a dict with frame number intervals for which to use each
    planes = {v for cs in study_config.coding_setup for v in cs['planes']}
    planes_setup: dict[str, aruco.PlaneSetup] = {}
    analyze_frames: dict[str, list[list[int]]|None] = {}
    for p in planes:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        pl = plane.get_plane_from_definition(p_def, config_dir/p)
        planes_setup[p] = pl.get_plane_setup()
        if episodes:
            # determine for which frames this plane should be used
            anal_events  = [cs['name'] for cs in study_config.coding_setup if p in cs['planes']]
            all_episodes = [ep for k in anal_events for ep in episodes[k] if ep]  # filter out empty
            analyze_frames[p] = sorted(all_episodes, key = lambda x: x[1])
        else:
            analyze_frames[p] = None

    return planes_setup, analyze_frames