import pathlib
import numpy as np
import pandas as pd
import polars as pl

from glassesTools import gaze_headref, timestamps, video_utils


from . import naming, _utils
from .. import config, episode, session


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path, do_time_stretch=True, stretch_which='ref'):
    # if do_time_stretch is True, time is stretched by a linear scale factor
    # based on differences in elapsed time for the reference and the used camera
    # if False, one single offset is applied. If there are multiple sync points, the average is used
    # stretch_which='ref' means that the time of the reference is judged to be unreliable and will be stretched
    # you may wish to do this if you has as ref and overview camera and are syncing one or two eye trackers to it,
    # so that the timing of your eye tracker events does not change (and the timing of some webcam recording may
    # indeed be unreliable).
    # TODO: option to average the stretch_fac of multiple cameras (e.g. two identical eye trackers)
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    config_dir  = pathlib.Path(config_dir)
    print('processing: {}'.format(working_dir.name))

    # get info about the study it is a part of
    study_config = config.Study.load_from_json(config_dir)

    # get session info
    session_info = session.Session.load_from_json(working_dir)

    # get info from reference recording
    ref_episodes = _get_coding_file(working_dir / study_config.sync_ref_recording)
    ref_vid_ts_file = working_dir / study_config.sync_ref_recording / 'frameTimestamps.tsv'
    video_ts_ref = timestamps.VideoTimestamps(ref_vid_ts_file)

    # check input
    if do_time_stretch:
        assert len(ref_episodes)>1, f"You requested to do time stretching when syncing the recordings, but there is only one camera sync point. At least two sync points are required for time stretching"
        if study_config.sync_average_recordings:
            for r in study_config.sync_average_recordings:
                assert r in session_info.recordings, f'Recording {r} not found for session {session_info.name}'
                assert r!=study_config.sync_ref_recording, f'Recording {r} is the reference recording for sync, should not be specified in study_config.sync_average_recordings'

    # prep for sync info
    cols    = ['t_ref','t_this','offset']
    if do_time_stretch:
        cols += ['t_ref_elapsed','diff_offset','stretch_fac']
    else:
        cols += ['mean_off']
    recs    = [r for r in session_info.recordings if r!=study_config.sync_ref_recording]
    index   = pd.MultiIndex.from_product([recs, range(len(ref_episodes))], names=['recording','interval'])
    sync    = pd.DataFrame(columns=cols, dtype=float, index=index)

    # collect timestamps for the recordings
    for r in recs:
        # get interval coding for this recording
        episodes = _get_coding_file(working_dir / r)

        # check intervals
        assert len(episodes)==len(ref_episodes), f"The number of sync points for this recording ({len(episodes)}, {r}) is not equal to that for the reference recording ({len(episodes)}, {study_config.sync_ref_recording}). Cannot continue, fix your coding"

        # get time information
        video_ts = timestamps.VideoTimestamps(working_dir / r / 'frameTimestamps.tsv')

        # get timestamps corresponding to sync frames
        for ival in range(len(episodes)):
            sync.loc[(r,ival),'t_ref']  = video_ts_ref.get_timestamp(ref_episodes[ival])/1000.  # ms -> s
            sync.loc[(r,ival),'t_this'] =   video_ts  .get_timestamp(    episodes[ival])/1000.  # ms -> s
            sync.loc[(r,ival),'offset'] = sync.loc[(r,ival),'t_ref']-sync.loc[(r,ival),'t_this']
        if not do_time_stretch:
            # no time stretching, get average offset. Applies to whole file, store only for first interval
            sync.loc[(r,0),'mean_off'] = sync.loc[(r,slice(None)),'offset'].mean()

    if do_time_stretch:
        # get stretch factor for each interval between two sync points
        if study_config.sync_average_recordings:
            recs_gr = [r for r in recs if r not in study_config.sync_average_recordings]
            recs_gr.append(study_config.sync_average_recordings)
        for r in recs_gr:
            for ival in range(len(episodes)-1):
                sync.loc[(r,ival),'t_ref_elapsed'] = sync.loc[(r,ival+1),'t_ref' ].droplevel('interval')-sync.loc[(r,ival),'t_ref' ]
                sync.loc[(r,ival),'diff_offset']   = sync.loc[(r,ival+1),'offset'].droplevel('interval')-sync.loc[(r,ival),'offset']
                sync.loc[(r,ival),'stretch_fac']   = sync.loc[(r,ival),'diff_offset'].mean()/sync.loc[(r,ival),'t_ref_elapsed'].mean()

    # store sync info
    sync.to_csv(working_dir / 'ref_sync.tsv', sep='\t', na_rep='nan', float_format="%.16f")

    # now that we have determined how to sync, apply
    for r in recs:
        # just read whole gaze dataframe so we can apply things vectorized
        df = pd.read_csv(working_dir / r / 'gazeData.tsv', delimiter='\t', index_col=False)
        ts_col = 'timestamp_VOR' if 'timestamp_VOR' in df else 'timestamp'
        # get gaze timestamps and camera frame numbers _in reference video timeline_
        if do_time_stretch:
            ref_vid_ts = np.array(video_ts_ref.timestamps)
            ts_ref     = df[ts_col].to_numpy()
            for ival in range(len(episodes)-1):
                # set up the problem - piecewise linear scale
                # 1. get known good location for this interval, and the stretch factor
                pivot       = sync.loc[(r,ival),'t_ref']
                stretch_fac = sync.loc[(r,ival),'stretch_fac']
                # 2. determine data range to apply stretch for this interval to
                if ival==0:
                    # first interval, apply all the way from start of data
                    start = df[ts_col].min()
                else:
                    start = sync.loc[(r,ival),'t_ref']
                if ival==len(episodes)-2:
                    # last interval, apply all the way to end of data
                    end = df[ts_col].max()
                else:
                    end = sync.loc[(r,ival+1),'t_ref']
                data_sel = (ts_ref >= start) & (ts_ref <= end)
                # calculate new timestamps
                # 1. first translate gaze ts to reference timestamps
                ts_ref[data_sel] += sync.loc[(r,ival),'offset']*1000.   # s -> ms
                # 2. apply scaling
                if stretch_which=='ref':
                    data_sel = (ref_vid_ts >= start) & (ref_vid_ts <= end)
                    ref_vid_ts[data_sel] = (ref_vid_ts[data_sel]-pivot)*(1-stretch_fac)+pivot
                elif stretch_which=='other':
                    raise NotImplementedError()
            # store new time signal if one was made
            if stretch_which=='ref':
                vid_ts_df = pd.read_csv(ref_vid_ts_file, delimiter='\t', index_col='frame_idx')
                should_store = False
                if 'timestamp_stretched' not in vid_ts_df.columns:
                    # doesn't exist, insert
                    vid_ts_df.insert(1,'timestamp_stretched', ref_vid_ts)
                    should_store = True
                elif max(vid_ts_df['timestamp_stretched'].to_numpy()-ref_vid_ts)<10e-5:
                    # exists but what we just computed is different, update
                    vid_ts_df['timestamp_stretched'] = ref_vid_ts
                    should_store = True
                if should_store:
                    vid_ts_df.to_csv(ref_vid_ts_file, sep='\t')
        else:
            ts_ref = df[ts_col].to_numpy() + sync.loc[(r,0),'mean_off']*1000.   # s -> ms
            ref_vid_ts = video_ts_ref.timestamps
        fr_ref = video_utils.tssToFrameNumber(ts_ref,ref_vid_ts,trim=True)['frame_idx'].to_numpy()
        # write into df (use polars as that library saves to file waaay faster)
        df = _utils.insert_ts_fridx_in_df(df, gaze_headref.Gaze, 'ref', ts_ref, fr_ref)
        df = pl.from_pandas(df)
        df.write_csv(working_dir / r / 'gazeData.tsv', separator='\t', null_value='nan', float_precision=8)

def _get_coding_file(working_dir: str|pathlib.Path):
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {episode.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[episode.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {episode.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {episode.Event.Sync_Camera.value} point.'
    return episodes