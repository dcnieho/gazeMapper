import pathlib
import pandas as pd
import numpy as np
from typing import Any, Callable

from glassesTools import annotation, aruco, drawing, marker as gt_marker, naming as gt_naming, pose, process_pool, propagating_thread, ocv, timestamps
from glassesTools.gui.video_player import GUI

from .. import config, episode, marker, naming, plane, process, session, synchronization


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, visualization_show_rejected_markers=False, progress_indicator: process_pool.JobProgress=None, **study_settings):
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


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, visualization_show_rejected_markers: bool, progress_indicator: process_pool.JobProgress, **study_settings):
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
    has_auto_code = not not study_config.auto_code_sync_points or not not study_config.auto_code_episodes
    episode_file = working_dir / naming.coding_file
    if episode_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(episode_file), study_config.episodes_to_code)
    else:
        if not has_auto_code:   # missing coding is ok when auto coding is set up, as then we process all frames anyway
            raise RuntimeError(f'Coding is missing, cannot run Detect Markers\n{episode_file}')
        episodes = episode.get_empty_marker_dict(list(study_config.episodes_to_code))

    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        if annotation.Event.Trial in episodes and episodes[annotation.Event.Trial]:
            raise ValueError(f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}) and should not be coded for this recording ({rec_def.name})')
        if annotation.Event.Trial in study_config.episodes_to_code:
            all_recs = [r.name for r in study_config.session_def.recordings]
            # NB: don't error if we don't need trial episodes for coding.
            episodes[annotation.Event.Trial] = synchronization.get_episode_frame_indices_from_ref(working_dir, annotation.Event.Trial, rec_def.name, study_config.sync_ref_recording, all_recs, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, study_config.sync_ref_stretch_which, missing_ref_coding_ok=has_auto_code)

    sync_target_function         = _get_sync_function(study_config, rec_def, None if annotation.Event.Sync_ET_Data not in episodes else episodes[annotation.Event.Sync_ET_Data])
    planes_setup, analyze_frames = _get_plane_setup(study_config, config_dir, episodes)

    # set up pose estimator
    estimator = pose.Estimator(in_video, working_dir / gt_naming.frame_timestamps_fname, working_dir / gt_naming.scene_camera_calibration_fname)
    # first, register all ArUco planes and individual markers with ArUco manager, which
    # will then wrap their detection and register them with the pose estimator
    aruco_manager = aruco.Manager()
    for p in planes_setup:
        aruco_manager.add_plane(p, planes_setup[p], analyze_frames[p])
        if hasattr(planes_setup[p]['plane'],'is_dynamic') and planes_setup[p]['plane'].is_dynamic():
            markers = planes_setup[p]['plane'].get_marker_IDs()
            marker_setup = planes_setup[p]['plane'].get_dynamic_marker_setup()
            for c in markers:
                if c=='plane':
                    continue
                for m in markers[c]:
                    aruco_manager.add_individual_marker(m, marker_setup, analyze_frames[p])
    for m in (markers:=marker.get_setup_for_markers(study_config.individual_markers)):
        aruco_manager.add_individual_marker(m, markers[m])
    aruco_manager.consolidate_setup()
    aruco_manager.register_with_estimator(estimator)
    # other setup of estimator
    if sync_target_function is not None:
        estimator.register_extra_processing_fun('sync', *sync_target_function)
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
    total = estimator.video_ts.get_last()[0]
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(int(total/200), int(total/200))
    estimator.set_progress_updater(progress_indicator.update)

    poses, individual_markers, sync_target_signal = estimator.process_video()

    for p in poses:
        pose.write_list_to_file(poses[p], working_dir/f'{naming.plane_pose_prefix}{p}.tsv', skip_failed=True)
    for m in individual_markers:
        gt_marker.write_list_to_file(individual_markers[m], gt_marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir), skip_failed=False)
    if sync_target_signal:
        df = pd.DataFrame(sync_target_signal['sync'],columns=['frame_idx','target_x','target_y'])
        df.to_csv(working_dir/naming.target_sync_file, sep='\t', index=False, na_rep='nan', float_format="%.8f")

    # update state
    session.update_action_states(working_dir, process.Action.DETECT_MARKERS, process_pool.State.Completed, study_config)


def _get_sync_function(study_config: config.Study,
                       rec_def: session.RecordingDefinition,
                       episodes: list[list[int]]) -> None | list[Callable[[str,int,np.ndarray,ocv.CameraParams,Any], tuple[float,float]], list[list[int]], dict[str], Callable[[str,int,np.ndarray,int,float,float], None]]:
    sync_target_function: list[Callable[[str,int,np.ndarray,ocv.CameraParams,Any], tuple[float,float]], list[int]|list[list[int]], dict[str], Callable[[str,int,np.ndarray,int,float,float], None]] = None
    if rec_def.type==session.RecordingType.Eye_Tracker:
        # NB: only for eye tracker recordings, others don't have eye tracking data and thus nothing to sync
        match study_config.get_cam_movement_for_et_sync_method:
            case '':
                pass # nothing to do
            case 'plane':
                if annotation.Event.Sync_ET_Data not in study_config.planes_per_episode:
                    raise ValueError(f'The method for synchronizing eye tracker data to the scene camera (get_cam_movement_for_et_sync_method) is set to "plane" but no plane is configured for {annotation.Event.Sync_ET_Data.name} in the planes_per_episode config')
                # NB: no extra_funcs to run
            case 'function':
                import importlib
                to_load = study_config.get_cam_movement_for_et_sync_function['module_or_file']
                if (to_load_path:=pathlib.Path(to_load)).is_file():
                    import sys
                    module_name = to_load_path.stem
                    spec = importlib.util.spec_from_file_location(module_name, to_load_path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                else:
                    module = importlib.import_module(study_config.get_cam_movement_for_et_sync_function['module_or_file'])
                func = getattr(module,study_config.get_cam_movement_for_et_sync_function['function'])
                sync_target_function = [func, episodes, study_config.get_cam_movement_for_et_sync_function['parameters'], _sync_function_output_drawer]
            case _:
                raise ValueError(f'study config get_cam_movement_for_et_sync_method={study_config.get_cam_movement_for_et_sync_method} not understood')

    return sync_target_function

def _sync_function_output_drawer(proc_name: str, frame: np.ndarray, frame_idx: int, tx: float, ty: float, sub_pixel_fac=8):
    # input is tx, ty pixel positions on the camera image
    ll = 20
    drawing.openCVLine(frame, (tx,ty-ll), (tx,ty+ll), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVLine(frame, (tx-ll,ty), (tx+ll,ty), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVCircle(frame, (tx,ty), 3, (0,0,255), -1, sub_pixel_fac)

def _get_plane_setup(study_config: config.Study,
                     config_dir: pathlib.Path,
                     episodes: dict[annotation.Event,list[list[int]]] = None) -> tuple[dict[str, aruco.PlaneSetup], dict[str, list[list[int]]|None]]:
    # process the above into a dict of plane definitions and a dict with frame number intervals for which to use each
    planes = {v for k in study_config.planes_per_episode for v in study_config.planes_per_episode[k]}
    planes_setup: dict[str, aruco.PlaneSetup] = {}
    analyze_frames: dict[str, list[list[int]]] = {}
    for p in planes:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        pl = plane.get_plane_from_definition(p_def, config_dir/p)
        planes_setup[p] = pl.get_plane_setup()
        if episodes:
            # determine for which frames this plane should be used
            anal_episodes = [k for k in study_config.planes_per_episode if p in study_config.planes_per_episode[k]]
            all_episodes = [ep for k in anal_episodes for ep in episodes[k] if ep]  # filter out empty
            analyze_frames[p] = sorted(all_episodes, key = lambda x: x[1])
        else:
            analyze_frames[p] = None

    return planes_setup, analyze_frames