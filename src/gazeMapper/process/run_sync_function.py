import pathlib
import pandas as pd
import numpy as np
import typing
import functools

from glassesTools import annotation, drawing, naming as gt_naming, pose, process_pool, propagating_thread, ocv, timestamps, transforms
from glassesTools.gui.video_player import GUI

from .. import config, episode, naming, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None=None, show_visualization=False, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected markers and assemblies
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
        gui.set_detachable(True, on_detach=lambda: process_pool.release_current_job_resource(('gui',)))
        gui.set_screenshot_filename_prefix(f'{working_dir.parent.name}_{working_dir.name}')
        gui.set_show_controls(True)
        gui.set_show_play_percentage(True)
        gui.set_show_action_tooltip(True)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, progress_indicator), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, progress_indicator, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI|None, progress_indicator: process_pool.JobProgress|None, **study_settings):
    from .. import process
    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('frames')
    progress_indicator.set_start_time_to_now()

    # get settings for the study (NB: reloaded including recording overrides below)
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent}, **study_settings)

    # get info about recording
    rec_name = working_dir.name
    rec_def = study_config.session_def.get_recording_def(rec_name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run run_sync_function on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')
    # check if this recording is replaced by a head-attached camera recording, and if so, use that recording instead
    replacement_name = process.get_replacement_recording_name(study_config, rec_name)
    source_working_dir = working_dir.parent / replacement_name if replacement_name else working_dir
    # get new study config, potentially for the replacement recording, with the applicable recording overrides
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: source_working_dir}, **study_settings)
    source_rec_def = study_config.session_def.get_recording_def(source_working_dir.name)
    in_video = session.read_recording_info(source_working_dir, source_rec_def.type)[1]

    # get interval(s) coded to be analyzed, if any
    # We don't need them if they would be ignored because the whole video would be processed. The whole video is processed when study_config.auto_code_sync_points or study_config.auto_code_episodes are set
    function_events = [
        cs for cs in study_config.coding_setup
        if cs.get('sync_setup') is not None
        and cs['sync_setup']['get_cam_movement_method']=='function'
        and (cs['which_recordings'] is None or source_working_dir.name in cs['which_recordings'])
    ]
    wanted_episodes = {cs['name'] for cs in function_events}
    if not wanted_episodes:
        raise RuntimeError(f'No episodes for configured for running a sync function found for recording "{source_working_dir.name}". Cannot run Sync by Function.')
    episodes = episode.load_episodes_from_all_recordings(study_config, source_working_dir, wanted_episodes)[0]

    # remove empty episode lists (no coding)
    episodes = {cs:episodes[cs] for cs in episodes if episodes[cs][1]}
    # check there is anything to do
    if not episodes:
        raise RuntimeError(f'No episodes to analyze for recording "{source_working_dir.name}". Cannot run Sync by Function.')

    camera_params = ocv.CameraParams.read_from_file(source_working_dir / gt_naming.scene_camera_calibration_fname)
    sync_target_functions, function_frames  = _get_sync_function(study_config, source_rec_def, episodes, camera_params)
    if not sync_target_functions:
        raise RuntimeError(f'No sync target functions are configured for the "{source_working_dir.name}" recording, cannot run Sync by Function.')

    # set up video processor
    estimator = pose.Estimator(in_video, source_working_dir / gt_naming.frame_timestamps_fname, source_working_dir / gt_naming.scene_camera_calibration_fname)
    # setup sync functions
    for sfe in sync_target_functions:
        estimator.register_extra_processing_fun(f'sync_{sfe}', function_frames[sfe], *sync_target_functions[sfe])
    estimator.attach_gui(gui)
    if gui is not None:
        gui.set_show_timeline(True, timestamps.VideoTimestamps(source_working_dir / gt_naming.frame_timestamps_fname), annotation.flatten_annotation_dict(episodes), window_id=gui.main_window_id)

    # prep progress indicator
    progress_indicator.set_total(total:=estimator.video_ts.get_last()[0])
    progress_indicator.set_intervals(step:=min(20,int(total/200)), step)
    estimator.set_progress_updater(progress_indicator.update)

    _, _, sync_target_signals = estimator.process_video()
    target_dimensions = 3 if replacement_name else 2

    for s in sync_target_signals:
        signals = sync_target_signals[s]
        if any(len(target_pos) != target_dimensions for _, target_pos in signals):
            expected = '(x, y, z) in replacement-camera coordinates' if replacement_name else '(x, y) in camera-image pixels'
            raise ValueError(f'The sync-target function for "{s}" must return {expected}.')
        columns = ['frame_idx', 'target_x', 'target_y'] + (['target_z'] if target_dimensions==3 else [])
        df = pd.DataFrame([[frame_idx, *target_pos] for frame_idx, target_pos in signals], columns=columns)
        nm = s.removeprefix('sync_')
        df.to_csv(source_working_dir/f'{naming.target_sync_prefix}{nm}.tsv', sep='\t', index=False, na_rep='nan', float_format="%.8f")

    # update state
    session.update_action_states(working_dir, process.Action.RUN_SYNC_FUNCTION, process_pool.State.Completed, study_config)


SyncTarget = tuple[float, float] | tuple[float, float, float]

def _get_sync_function(study_config: config.Study,
                       rec_def: session.RecordingDefinition,
                       episodes: dict[str,tuple[annotation.EventType, list[list[int]]]]|None = None,
                       camera_params: ocv.CameraParams|None = None) -> tuple[dict[str, tuple[typing.Callable[[str,int,np.ndarray,ocv.CameraParams,typing.Any], SyncTarget], dict[str, typing.Any], typing.Callable[[str,np.ndarray,dict[str, typing.Any],int,SyncTarget|None], None]]], dict[str, tuple[annotation.EventType, list[list[int]]]|None]]:
    from .. import process
    sync_target_function: dict[str, tuple[typing.Callable[[str,int,np.ndarray,ocv.CameraParams,typing.Any], SyncTarget], dict[str, typing.Any], typing.Callable[[str,np.ndarray,dict[str, typing.Any],int,SyncTarget|None], None]]] = {}
    analyze_frames: dict[str, tuple[annotation.EventType, list[list[int]]]|None] = {}
    # NB: only for eye tracker recordings, others don't have eye tracking data and thus nothing to sync
    et_sync_events = [
        cs for cs in process.get_specific_event_types(study_config, annotation.EventType.Sync_ET_Data, ['sync_setup'])
        if cs['which_recordings'] is None or rec_def.name in cs['which_recordings']
    ]
    for cs in et_sync_events:
        match cs['sync_setup']['get_cam_movement_method']:
            case 'assembly':
                if not cs['marker_assemblies']:
                    raise ValueError(f'The method for synchronizing eye tracker data to the scene camera (get_cam_movement_method) is set to "assembly" for the "{cs["name"]}" event but no marker assembly (e.g., plane) is configured for this event. Cannot continue.')
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
                visualizer = functools.partial(_sync_function_output_drawer, camera_params=camera_params)
                sync_target_function[cs["name"]] = (func, cs['sync_setup']['get_cam_movement_function']['parameters'], visualizer)
                if episodes and cs['name'] in episodes:
                    analyze_frames[cs['name']] = episodes[cs['name']]
                else:
                    analyze_frames[cs['name']] = None
            case _:
                raise ValueError(f'sync_setup.get_cam_movement_method={cs["sync_setup"]["get_cam_movement_method"]} for the "{cs["name"]}" event not understood')

    return sync_target_function, analyze_frames

def _sync_function_output_drawer(proc_name: str, frame: np.ndarray, frame_info: dict[str, typing.Any], frame_idx: int, t: SyncTarget|None, sub_pixel_fac=8, camera_params: ocv.CameraParams|None=None):
    if t is None:
        return
    if len(t) == 3:
        if camera_params is None or not camera_params.has_intrinsics():
            return
        roi_offset   = typing.cast(np.ndarray[tuple[     typing.Literal[2]], np.dtype[np.float64]], np.array([frame_info.get('offset_x', 0), frame_info.get('offset_y', 0)], dtype=np.float64))
        target_point = typing.cast(np.ndarray[tuple[int, typing.Literal[3]], np.dtype[np.float64]], np.asarray([t], dtype=np.float64))
        tx, ty = transforms.project_points(target_point, camera_params, ROI_offset=roi_offset)[0]
    else:
        tx, ty = t
    # input is tx, ty pixel positions on the camera image
    ll = 20
    drawing.openCVLine(frame, (tx,ty-ll), (tx,ty+ll), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVLine(frame, (tx-ll,ty), (tx+ll,ty), (0,255,0), 1, sub_pixel_fac)
    drawing.openCVCircle(frame, (tx,ty), 3, (0,0,255), -1, sub_pixel_fac)