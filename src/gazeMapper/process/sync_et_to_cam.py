import pathlib
import numpy as np
import pandas as pd
import polars as pl
from collections import defaultdict
import sys
import time

isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, gaze_headref, naming as gt_naming, ocv, pose, process_pool, propagating_thread, timestamps, video_utils
from glassesTools.gui.signal_sync import GUI, TargetPos

from . import _utils
from .. import config, episode, naming, process, session


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


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # check there is a sync setup
    sync_events = process.get_specific_event_types(study_config, annotation.EventType.Sync_ET_Data)
    if not sync_events:
        raise ValueError('No ET sync events are configured for the study, nothing to process')

    # check this is an eye tracker recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run sync_et_to_cam on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get interval coding
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {cs['name'] for cs in sync_events})[0]
    if not episodes or not any(episodes[e] for e in episodes):
        raise RuntimeError(f'No {annotation.tooltip_map[annotation.EventType.Sync_ET_Data]}s found for this recording. Run code_episodes and code at least one {annotation.tooltip_map[annotation.EventType.Sync_ET_Data]}.')

    # Read gaze data
    gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, [v for e in episodes for v in episodes[e]])[0]
    # time info
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    # Read target positions
    target_positions: dict[str, dict[int, TargetPos]] = {}
    for cs in sync_events:
        nm = cs['name']
        match cs['sync_setup']['get_cam_movement_method']:
            case 'plane':
                if len(cs['planes'])!=1:
                    raise ValueError(f'ET Sync event "{nm}" should be coded for exactly one plane, found {len(cs["planes"])}')
                pln = list(cs['planes'])[0]

                # Read pose w.r.t plane
                pln_file = working_dir/f'{naming.plane_pose_prefix}{pln}.tsv'
                if not pln_file.is_file():
                    raise FileNotFoundError(f'A planePose file for the {pln} plane is not found, but is needed. Run detect_markers to create this file.')
                poses = pose.read_dict_from_file(pln_file, episodes[nm])

                # get camera calibration info
                camera_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

                # compute target positions on camera frame
                target_positions[nm] = {}
                for frame_idx in poses:
                    t_pos = poses[frame_idx].get_origin_on_image(camera_params)
                    if not np.isnan(t_pos[0]):
                        target_positions[nm][frame_idx] = TargetPos(video_ts.get_timestamp(frame_idx), frame_idx, t_pos)
            case 'function':
                df = pd.read_csv(working_dir / f'{naming.target_sync_prefix}{nm}.tsv', delimiter='\t', index_col=False, dtype=defaultdict(lambda: float, frame_idx=int))
                df['cam_pos'] = [x for x in df[['target_x','target_y']].values]
                target_positions[nm] = {idx:TargetPos(video_ts.get_timestamp(idx), **kwargs) for idx,kwargs in zip(df['frame_idx'].values,df[['frame_idx','cam_pos']].to_dict(orient='records'))}

    # flatten into list of tuples for easier processing
    episodes = [(e, v) for e in episodes for v in episodes[e]]

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