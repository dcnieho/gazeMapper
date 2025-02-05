import pathlib
import numpy as np
import pandas as pd
import polars as pl
from collections import defaultdict

import sys
isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, gaze_headref, naming as gt_naming, ocv, plane, propagating_thread, timestamps, video_utils
from glassesTools.gui.signal_sync import GUI, TargetPos


from . import _utils
from .. import config, episode, naming, process, session



def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
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
    if study_config.get_cam_movement_for_et_sync_method not in ['plane', 'function']:
        raise ValueError('There is no eye tracker data to scene camera synchronization defined, should not run this function')
    if annotation.Event.Sync_ET_Data not in study_config.episodes_to_code:
        raise ValueError('ET sync episodes are not set up to be coded, nothing to do here')
    if study_config.get_cam_movement_for_et_sync_method=='plane' and annotation.Event.Sync_ET_Data not in study_config.planes_per_episode:
        raise ValueError(f'No plane specified for syncing eye tracker data to the scene cam, cannot continue')

    # check this is an eye tracker recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run sync_et_to_cam on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get interval coding
    coding_file = working_dir / naming.coding_file
    if not coding_file.is_file():
        raise FileNotFoundError(f'A coding file must be available to run sync_et_to_cam, but it is not. Run code_episodes and code at least one {annotation.Event.Sync_ET_Data.value} episode. Not found: {coding_file}')
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[annotation.Event.Sync_ET_Data]
    if not episodes:
        raise RuntimeError(f'No {annotation.Event.Sync_ET_Data.value} episodes found for this recording. Run code_episodes and code at least one {annotation.Event.Sync_ET_Data.value} episode.')

    # Read gaze data
    gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, episodes)[0]
    # time info
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    match study_config.get_cam_movement_for_et_sync_method:
        case 'plane':
            planes = list(study_config.planes_per_episode[annotation.Event.Sync_ET_Data])
            if len(planes)!=1:
                raise NotImplementedError("sync_et_to_cam only supports a single plane being used for synchronizing eye tracking data to the scene camera, contact developer if this is an issue")
            pln = planes[0]

            # Read pose w.r.t plane
            pln_file = working_dir/f'{naming.plane_pose_prefix}{pln}.tsv'
            if not pln_file.is_file():
                raise FileNotFoundError(f'A planePose file for the {pln} plane is not found, but is needed. Run detect_markers to create this file.')
            poses = plane.read_dict_from_file(pln_file, episodes)

            # get camera calibration info
            camera_params= ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)
            camera_params.has_intrinsics()

            # compute target positions
            target_positions: dict[int, TargetPos] = {}
            for frame_idx in poses:
                t_pos = poses[frame_idx].get_origin_on_image(camera_params)
                if not np.isnan(t_pos[0]):
                    target_positions[frame_idx] = TargetPos(video_ts.get_timestamp(frame_idx), frame_idx, t_pos)
        case 'function':
            df = pd.read_csv(working_dir/naming.target_sync_file, delimiter='\t', index_col=False, dtype=defaultdict(lambda: float, frame_idx=int))
            df['cam_pos'] = [x for x in df[['target_x','target_y']].values]
            target_positions = {idx:TargetPos(video_ts.get_timestamp(idx), **kwargs) for idx,kwargs in zip(df['frame_idx'].values,df[['frame_idx','cam_pos']].to_dict(orient='records'))}

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

    # show
    has_requested_focus = not isMacOS # False only if on Mac OS, else True since its a no-op
    ival = 0
    need_to_load = True
    while True:
        if not has_requested_focus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            has_requested_focus = True

        if need_to_load:
            # select data
            start, end = episodes[ival]
            plot_gaze  = {fr:gazes           [fr] for fr in      gazes       if fr>=start and fr<=end}
            plot_t_pos = {fr:target_positions[fr] for fr in target_positions if fr>=start and fr<=end}
            if not plot_gaze:
                raise RuntimeError(f'No gaze data found between frames {start} and {end}')
            if not plot_t_pos:
                raise RuntimeError(f'No target/scene camera data found between frames {start} and {end}')
            # determine initial offset
            toff = VOR_sync.loc[ival, 'offset_t']
            if np.isnan(toff):
                toff = VOR_sync.loc[ival-1, 'offset_t'] if ival>0 else 0.
            # submit to GUI
            gui.set_data(f'{working_dir.parent.name}, {working_dir.name}', ival, plot_gaze, plot_t_pos, offset_t=toff)
            need_to_load = False


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

    # store to file
    VOR_sync.to_csv(VOR_sync_file, sep='\t', float_format="%.4f") # .1 ms resolution

    # apply offset to gaze
    if study_config.sync_et_to_cam_use_average:
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
    session.update_action_states(working_dir, process.Action.SYNC_ET_TO_CAM, process.State.Completed, study_config)