import pathlib
import pandas as pd

from glassesTools import data_files, gaze_headref, timestamps, video_utils


from . import naming, _utils
from .. import config, episode, session


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path, do_time_stretch=True):
    # if do_time_stretch is True, time is stretched by a linear scale factor
    # based on differences in elapsed time for the reference and the used camera
    # if False, one single offset is applied. If there are multiple sync points, the average is used
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    config_dir  = pathlib.Path(config_dir)
    print('processing: {}'.format(working_dir.name))

    # get info about the study it is a part of
    study_config = config.Study.load_from_json(config_dir)

    # get session info
    session_info = session.Session.load_from_json(working_dir)

    # get info from reference recording
    ref_episodes = _get_coding_file(working_dir / study_config.sync_ref_recording)
    video_ts_ref = timestamps.VideoTimestamps(working_dir / study_config.sync_ref_recording / 'frameTimestamps.tsv')

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
            recs = [r for r in recs if r not in study_config.sync_average_recordings]
            recs.append(study_config.sync_average_recordings)
        for r in recs:
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
            pass
        else:
            ts_ref = df[ts_col].to_numpy() + sync.loc[(r,0),'mean_off']*1000.   # s -> ms
        fr_ref = video_utils.tssToFrameNumber(ts_ref,video_ts_ref.timestamps,trim=True)['frame_idx'].to_numpy()
        # write into df
        df = _utils.insert_ts_fridx_in_df(df, gaze_headref.Gaze, 'ref', ts_ref, fr_ref)
        df.to_csv(working_dir / r / 'gazeData.tsv', index=False, sep='\t', na_rep='nan', float_format="%.8f")

def _get_coding_file(working_dir: str|pathlib.Path):
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {episode.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[episode.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {episode.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {episode.Event.Sync_Camera.value} point.'
    return episodes