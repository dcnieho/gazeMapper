import pathlib
import pandas as pd

from glassesTools import timestamps


from . import naming
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
    i2t_ref = timestamps.Idx2Timestamp(working_dir / study_config.sync_ref_recording / 'frameTimestamps.tsv')

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
        i2t     = timestamps.Idx2Timestamp(working_dir / r / 'frameTimestamps.tsv')

        # get timestamps corresponding to sync frames
        for ival in range(len(episodes)):
            sync.loc[(r,ival),'t_ref']  = i2t_ref.get(ref_episodes[ival])/1000.    # ms -> s
            sync.loc[(r,ival),'t_this'] =   i2t  .get(    episodes[ival])/1000.    # ms -> s
            sync.loc[(r,ival),'offset'] = sync.loc[(r,ival),'t_ref']-sync.loc[(r,ival),'t_this']
        if not do_time_stretch:
            # no time stretching, get average offset
            sync.loc[(r,slice(None)),'mean_off'] = sync.loc[(r,ival),'offset'].mean()

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

    a=3

def _get_coding_file(working_dir: str|pathlib.Path):
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {episode.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[episode.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {episode.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {episode.Event.Sync_Camera.value} point.'
    return episodes