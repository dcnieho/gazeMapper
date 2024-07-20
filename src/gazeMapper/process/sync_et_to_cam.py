import pathlib
import threading
import numpy as np
import pandas as pd
import polars as pl
from collections import defaultdict

import sys
isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, gaze_headref, ocv, plane, timestamps, video_utils
from glassesTools.signal_gui import GUI, TargetPos


from . import _utils
from .. import config, episode, naming, session



stopAllProcessing = False
def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, apply_average=True):
    # apply_average: if True: the average offset for all VOR sync episodes will be applied to the timestamps
    # if False, the VOR offset for the first episode will be applied, the rest are taken as checks
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    gui = GUI(use_thread = False)

    proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, apply_average))
    proc_thread.start()
    gui.start()
    proc_thread.join()
    return stopAllProcessing


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, apply_average: bool):
    # get info about the study it is a part of
    study_config = config.Study.load_from_json(config_dir)

    # check this is an eye tracker recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    assert rec_def.type==session.RecordingType.EyeTracker, f'You can only run sync_et_to_cam on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording'

    # get interval coding
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available to run sync_et_to_cam, but it is not. Run code_episodes and code at least one {annotation.Event.Sync_ET_Data.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[annotation.Event.Sync_ET_Data]
    assert episodes, f'No {annotation.Event.Sync_ET_Data.value} episodes found for this recording. Run code_episodes and code at least one {annotation.Event.Sync_ET_Data.value} episode.'

    # Read gaze data
    gazes = gaze_headref.read_dict_from_file(working_dir / 'gazeData.tsv', episodes)[0]
    # time info
    video_ts = timestamps.VideoTimestamps(working_dir / 'frameTimestamps.tsv')

    match study_config.get_cam_movement_for_et_sync_method:
        case '':
            raise ValueError('There is no eye tracker data to scene camera synchronization defined, should not run this function')
        case 'plane':
            assert annotation.Event.Sync_ET_Data in study_config.planes_per_episode, f'No plane specified for syncing eye tracker data to the scene cam, cannot continue'
            planes = study_config.planes_per_episode[annotation.Event.Sync_ET_Data]
            assert len(planes)==1, "sync_et_to_cam only supports a single plane being used for synchronizing eye tracking data to the scene camera, contact developer if this is an issue"
            pln = planes[0]

            # Read pose w.r.t plane
            pln_file = working_dir/f'{naming.plane_pose_prefix}{pln}.tsv'
            assert pln_file.is_file(), f'A planePose file for the {pln} plane is not found, but is needed. Run detect_markers to create this file.'
            poses = plane.read_dict_from_file(pln_file, episodes)

            # get camera calibration info
            cameraParams= ocv.CameraParams.readFromFile(working_dir / "calibration.xml")
            cameraParams.has_intrinsics()

            # compute target positions
            target_positions: dict[int, TargetPos] = {}
            for frame_idx in poses:
                if poses[frame_idx].pose_N_markers>0:
                    t_pos = poses[frame_idx].planeToCamPose(np.zeros((3,)), cameraParams)
                elif poses[frame_idx].homography_N_markers>0:
                    t_pos = poses[frame_idx].planeToCamHomography(np.zeros((3,)), cameraParams)
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
    hasRequestedFocus = not isMacOS # False only if on Mac OS, else True since its a no-op
    ival = 0
    need_to_load = True
    stopAllProcessing = False
    while True:
        if not hasRequestedFocus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            hasRequestedFocus = True

        if need_to_load:
            # select data
            start, end = episodes[ival]
            plot_gaze  = {fr:gazes           [fr] for fr in      gazes       if fr>=start and fr<=end}
            plot_t_pos = {fr:target_positions[fr] for fr in target_positions if fr>=start and fr<=end}
            assert plot_gaze, f'No gaze data found between frames {start} and {end}'
            assert plot_t_pos, f'No target/scene camera data found between frames {start} and {end}'
            # determine initial offset
            toff = VOR_sync.loc[ival, 'offset_t']
            if np.isnan(toff):
                toff = VOR_sync.loc[ival-1, 'offset_t'] if ival>0 else 0.
            # submit to GUI
            gui.set_data(f'{working_dir.parent.name}, {working_dir.name}', ival, plot_gaze, plot_t_pos, offset_t=toff)
            need_to_load = False


        closed,is_done = gui.get_state()
        if closed:
            stopAllProcessing = True
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
    if apply_average:
        toff = VOR_sync['offset_t'].mean()
    else:
        toff = VOR_sync.iloc[0,'offset_t']
    # just read whole gaze dataframe so we can apply things vectorized
    df = pd.read_csv(working_dir / 'gazeData.tsv', delimiter='\t', index_col=False)
    # resync gaze timestamps using VOR, and get correct scene camera frame numbers
    ts_VOR = df['timestamp'].to_numpy() + toff*1000.   # s -> ms
    fr_VOR = video_utils.tssToFrameNumber(ts_VOR,video_ts.timestamps,trim=True)['frame_idx'].to_numpy()
    # write into df (use polars as that library saves to file waaay faster)
    df = _utils.insert_ts_fridx_in_df(df, gaze_headref.Gaze, 'VOR', ts_VOR, fr_VOR)
    df = pl.from_pandas(df)
    df.write_csv(working_dir / 'gazeData.tsv', separator='\t', null_value='nan', float_precision=8)

    return stopAllProcessing