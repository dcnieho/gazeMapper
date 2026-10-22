import pathlib
import numpy as np
import cv2
import pandas as pd
import polars as pl
from collections import defaultdict
import sys
import time
import typing

isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, gaze_headref, naming as gt_naming, ocv, pose, process_pool, propagating_thread, timestamps, transforms, video_utils
from glassesTools.gui.signal_sync import GUI, TargetPos

from . import _utils
from .. import config, episode, naming, process, session, synchronization


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
    # apply_average: if True: the average offset for all VOR sync episodes will be applied to the timestamps
    # if False, the VOR offset for the first episode will be applied, the rest are taken as checks
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    gui = GUI(use_thread = False)

    proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui), kwargs=study_settings, cleanup_fun=gui.stop)
    proc_thread.start()
    gui.start()
    proc_thread.join()

def _project_replacement_target_to_eye_tracker(target_point: np.ndarray, replacement_camera: ocv.CameraParams, eye_tracker_camera: ocv.CameraParams, roi_offset: np.ndarray) -> np.ndarray:
    if not replacement_camera.has_extrinsics():
        raise ValueError('The replacement camera calibration does not include extrinsics.')
    if replacement_camera.rotation_vec is None or replacement_camera.position is None:
        raise ValueError('The replacement camera calibration does not include extrinsics.')
    if not eye_tracker_camera.has_intrinsics():
        raise ValueError('The eye-tracker camera calibration does not include intrinsics.')
    replacement_to_scene = cv2.Rodrigues(replacement_camera.rotation_vec)[0]
    point_in_scene = replacement_to_scene @ target_point + replacement_camera.position
    points_in_scene = np.asarray(point_in_scene, dtype=np.float64).reshape((1, 3))
    return transforms.project_points(points_in_scene, eye_tracker_camera, ROI_offset=roi_offset)[0]


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # check there is a sync setup
    sync_events = process.get_specific_event_types(study_config, annotation.EventType.Sync_ET_Data)
    # remove events that are not configured for this recording
    recs_to_check = {working_dir.name}
    if study_config.sync_ref_recording:
        recs_to_check.add(study_config.sync_ref_recording)
    replacement_name = process.get_replacement_recording_name(study_config, working_dir.name)
    if replacement_name:
        recs_to_check.add(replacement_name)
    sync_events = [cs for cs in sync_events if cs['which_recordings'] is None or recs_to_check.intersection(cs['which_recordings'])]
    if not sync_events:
        raise ValueError('No ET sync events are configured for the study, nothing to process')

    # check this is an eye tracker recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run sync_et_to_cam on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get interval coding
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {cs['name'] for cs in sync_events})[0]
    replacement_dir = working_dir.parent / replacement_name if replacement_name else None
    if replacement_dir is not None and study_config.sync_ref_recording is not None:
        source_coding_file = replacement_dir / naming.coding_file
        if source_coding_file.is_file():
            source_episodes = episode.list_to_marker_dict(episode.read_list_from_file(source_coding_file))
            for cs in sync_events:
                event_name = cs['name']
                if (cs['which_recordings'] is not None and replacement_dir.name not in cs['which_recordings']) or event_name not in source_episodes or not source_episodes[event_name][1]:
                    continue
                mapped_intervals = synchronization.get_episode_frame_indices_from_other_video(
                    working_dir,
                    event_name,
                    working_dir.name,
                    replacement_dir.name,
                    study_config.sync_ref_recording,
                    [recording.name for recording in study_config.session_def.recordings if recording.name != study_config.sync_ref_recording],
                    bool(study_config.sync_ref_do_time_stretch),
                    list(study_config.sync_ref_average_recordings or []),
                    study_config.sync_ref_stretch_which or 'other',
                    missing_other_coding_ok=True,
                )
                if mapped_intervals:
                    episodes[event_name] = (cs['event_type'], mapped_intervals)
    if not episodes or not any(episodes[e][1] for e in episodes):
        raise RuntimeError(f'No {annotation.tooltip_map[annotation.EventType.Sync_ET_Data]}s found for this recording. Run code_episodes and code at least one {annotation.tooltip_map[annotation.EventType.Sync_ET_Data]}.')

    # Read gaze data
    gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, [v for e in episodes for v in episodes[e][1]])[0]
    # time info
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    # get frame info for the scene video, if available
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    frame_info_file = in_video.parent / (in_video.stem+gt_naming.frame_info_suffix)
    frame_info = ocv.FrameInfoHandler(frame_info_file)
    if frame_info.data is not None and not frame_info.single_entry and len(frame_info.data)!=len(video_ts.indices):
        raise ValueError(f'The number of frames in the frame info file ({len(frame_info.data)}) does not match the number of frames in the video ({len(video_ts.indices)}). Please check your files.')

    # Read target positions
    target_positions: dict[str, dict[int, TargetPos]] = {}
    for cs in sync_events:
        nm = cs['name']
        match cs['sync_setup']['get_cam_movement_method']:
            case 'assembly':
                if len(cs['marker_assemblies'])!=1:
                    raise ValueError(f'ET Sync event "{nm}" should be coded for exactly one marker assembly, found {len(cs["marker_assemblies"])}')
                assembly_name = next(iter(cs['marker_assemblies']))

                # get camera calibration info
                camera_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

                # read pose file
                if (fname := working_dir/f'{naming.pose_prefix}{assembly_name}.tsv').is_file():
                    poses = pose.read_dict_from_file(fname, episodes[nm][1])
                elif (fname := working_dir/f'{naming.plane_pose_prefix}{assembly_name}.tsv').is_file():
                    poses = pose.read_plane_dict_from_file(fname, episodes[nm][1])
                else:
                    raise FileNotFoundError(f'No pose file for marker assembly "{assembly_name}" is found. Run detect_markers to create it.')

                # project the marker assembly origin into the scene camera image
                target_positions[nm] = {}
                for frame_idx in poses:
                    info = frame_info.get_frame_info(frame_idx)
                    ROI_offset = [info['offset_x'], info['offset_y']] if 'offset_x' in info and 'offset_y' in info else [0., 0.]
                    t_pos = poses[frame_idx].get_origin_on_image(camera_params, ROI_offset)
                    if not np.isnan(t_pos[0]):
                        target_positions[nm][frame_idx] = TargetPos(video_ts.get_timestamp(frame_idx), frame_idx, t_pos)
            case 'function':
                target_dir = working_dir
                if replacement_dir is not None and (cs['which_recordings'] is None or replacement_dir.name in cs['which_recordings']) and (replacement_dir / f'{naming.target_sync_prefix}{nm}.tsv').is_file():
                    target_dir = replacement_dir
                target_path = target_dir / f'{naming.target_sync_prefix}{nm}.tsv'
                df = pd.read_csv(target_path, delimiter='\t', index_col=False, dtype=defaultdict(lambda: float, frame_idx=int))
                df['cam_pos'] = [x for x in df[['target_x','target_y']].values]
                if target_dir == working_dir:
                    target_positions[nm] = {idx: TargetPos(video_ts.get_timestamp(idx), **kwargs) for idx, kwargs in zip(df['frame_idx'].values, df[['frame_idx', 'cam_pos']].to_dict(orient='records'))}
                else:
                    # get (3D) target positions in replacement-camera coordinates, and map them to eye-tracker scene camera coordinates
                    source_ts = timestamps.VideoTimestamps(target_dir / gt_naming.frame_timestamps_fname)
                    if not {'target_x', 'target_y', 'target_z'}.issubset(df.columns):
                        raise ValueError(f'The sync-target function for "{nm}" must return a 3D point in replacement-camera coordinates when the eye-tracker scene camera is replaced.')
                    replacement_camera = ocv.CameraParams.read_from_file(target_dir / gt_naming.scene_camera_calibration_fname)
                    eye_tracker_camera = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)
                    sync = synchronization.get_sync_for_recs(working_dir.parent, [working_dir.name], target_dir.name, bool(study_config.sync_ref_do_time_stretch), list(study_config.sync_ref_average_recordings or []))
                    if sync is None:
                        raise RuntimeError(f'Cannot map sync-function results from replacement recording "{target_dir.name}" to "{working_dir.name}".')
                    source_frames = df['frame_idx'].to_list()
                    eye_tracker_frames = synchronization.reference_frames_to_video(working_dir.name, sync, source_frames, video_ts.timestamps, source_ts.timestamps, bool(study_config.sync_ref_do_time_stretch), study_config.sync_ref_stretch_which or 'other')
                    mapped_positions = []
                    for source_frame, et_frame, target_point in zip(source_frames, eye_tracker_frames, df[['target_x', 'target_y', 'target_z']].to_numpy()):
                        if et_frame < 0:
                            continue
                        info = frame_info.get_frame_info(et_frame)
                        roi_offset = np.array([info['offset_x'], info['offset_y']]) if 'offset_x' in info and 'offset_y' in info else np.zeros(2)
                        eye_tracker_pos = _project_replacement_target_to_eye_tracker(target_point, replacement_camera, eye_tracker_camera, roi_offset)
                        if np.isfinite(eye_tracker_pos).all():
                            mapped_positions.append((source_frame, et_frame, eye_tracker_pos))
                    target_positions[nm] = {et_frame: TargetPos(video_ts.get_timestamp(et_frame), et_frame, cam_pos) for _, et_frame, cam_pos in mapped_positions}

    # flatten into list of tuples for easier processing
    episodes = [(e, v) for e in episodes for v in episodes[e][1]]

    # get previous sync settings, if any
    VOR_sync_file = working_dir / naming.VOR_sync_file
    if VOR_sync_file.is_file():
        VOR_sync = pd.read_csv(VOR_sync_file, index_col=0, delimiter='\t')
        # make sure we have the expected number of intervals
        VOR_sync.drop([v for v in VOR_sync.index if v not in range(len(episodes))])
        for i in [v for v in range(len(episodes)) if v not in VOR_sync.index]:
            VOR_sync.loc[i] = np.nan
    else:
        VOR_sync = pd.DataFrame(columns=['offset_t'], dtype=float, index=pd.Index(list(range(len(episodes))),name='interval'))
    VOR_sync_original = VOR_sync.copy() if VOR_sync_file.is_file() else None

    # show
    has_requested_focus = not isMacOS # False only if on Mac OS, else True since its a no-op
    ival = 0
    need_to_load = True
    while True:
        if gui.is_running() and not has_requested_focus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            has_requested_focus = True

        if gui.is_running() and need_to_load:
            # select data
            e,(start, end) = episodes[ival]
            plot_gaze  = {fr:gazes              [fr] for fr in      gazes          if fr>=start and fr<=end}
            plot_t_pos = {fr:target_positions[e][fr] for fr in target_positions[e] if fr>=start and fr<=end}
            if not plot_gaze:
                raise RuntimeError(f'No gaze data found between frames {start} and {end}')
            if not plot_t_pos:
                raise RuntimeError(f'No target/scene camera data found between frames {start} and {end} for episode "{e}"')
            # determine initial offset
            toff = VOR_sync.loc[ival, 'offset_t']
            if np.isnan(toff):
                toff = VOR_sync.loc[ival-1, 'offset_t'] if ival>0 else 0.
            # submit to GUI
            gui.set_data(f'{working_dir.parent.name}, {working_dir.name}', ival, plot_gaze, plot_t_pos, offset_t=toff)
            need_to_load = False
        if not gui.is_running():
            # suspend thread so GUI can start running
            time.sleep(0.001)


        closed,is_done = gui.get_state()
        if closed:
            break
        if is_done:
            # store offset
            VOR_sync.loc[ival, 'offset_t'] = gui.offset_t
            # move to next interval, if any
            ival += 1
            if ival>len(episodes)-1:
                # no more episodes, we're done
                break
            else:
                need_to_load = True

    gui.stop()

    # early exit if nothing has changed
    if VOR_sync_original is not None and VOR_sync.equals(VOR_sync_original):
        if session.get_action_states(working_dir, True)[process.Action.SYNC_ET_TO_CAM]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.SYNC_ET_TO_CAM, process_pool.State.Completed, study_config, unchanged=True)
        return

    # store to file
    VOR_sync.to_csv(VOR_sync_file, sep='\t', float_format="%.4f") # .1 ms resolution

    # apply offset to gaze
    if len(set(cs['sync_setup']['use_average'] for cs in sync_events))!=1:
        raise ValueError('The setting "use_average" for ET sync events is not consistent across configured events. Please set all to True or all to False.')
    if sync_events[0]['sync_setup']['use_average']:
        toff = VOR_sync['offset_t'].mean()
    else:
        toff = VOR_sync.iloc[0,'offset_t']
    # just read whole gaze dataframe so we can apply things vectorized
    df = pd.read_csv(working_dir / gt_naming.gaze_data_fname, delimiter='\t', index_col=False)
    # resync gaze timestamps using VOR, and get correct scene camera frame numbers
    ts_VOR = df['timestamp'].to_numpy() + toff*1000.   # s -> ms
    fr_VOR = video_utils.timestamps_to_frame_number(ts_VOR,video_ts.timestamps,trim=True)['frame_idx'].to_numpy()
    # write into df (use polars as that library saves to file waaay faster)
    df = _utils.insert_ts_fridx_in_df(df, gaze_headref.Gaze, 'VOR', ts_VOR, fr_VOR)
    df = pl.from_pandas(df)
    df.write_csv(working_dir / gt_naming.gaze_data_fname, separator='\t', null_value='nan', float_precision=8)

    # update state
    session.update_action_states(working_dir, process.Action.SYNC_ET_TO_CAM, process_pool.State.Completed, study_config)