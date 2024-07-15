import pandas as pd
import pathlib

from glassesTools import timestamps

from .process import naming
from . import episode

def get_cols(do_time_stretch: bool):
    cols    = ['t_ref','t_this','offset']
    if do_time_stretch:
        cols += ['t_ref_elapsed','diff_offset','stretch_fac']
    else:
        cols += ['mean_off']

def get_sync_for_recs(working_dir: str|pathlib.Path, ref_rec: str, recs: str|list[str], do_time_stretch=False):
    working_dir  = pathlib.Path(working_dir)
    if isinstance(recs,str):
        recs = [recs]
    ref_episodes = get_coding_file(working_dir / ref_rec)
    video_ts_ref = timestamps.VideoTimestamps(working_dir / ref_rec / 'frameTimestamps.tsv')

    index = pd.MultiIndex.from_product([recs, range(len(ref_episodes))], names=['recording','interval'])
    sync  = pd.DataFrame(columns=get_cols(do_time_stretch), dtype=float, index=index)

    # collect timestamps for the recordings
    for r in recs:
        # get interval coding for this recording
        episodes = get_coding_file(working_dir / r)

        # check intervals
        assert len(episodes)==len(ref_episodes), f"The number of sync points for this recording ({len(episodes)}, {r}) is not equal to that for the reference recording ({len(episodes)}, {ref_rec}). Cannot continue, fix your coding"

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

    return sync

def get_coding_file(working_dir: str|pathlib.Path):
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {episode.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[episode.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {episode.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {episode.Event.Sync_Camera.value} point.'
    return episodes