import pandas as pd
import pathlib

from glassesTools import annotation, timestamps

from . import episode, naming

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
    working_dir  = pathlib.Path(working_dir)
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[annotation.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {annotation.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} point.'
    return episodes

def get_episode_frame_indices_from_ref(working_dir: str|pathlib.Path, event: annotation.Event, ref_rec: str, rec: str, extra_fr=10):
    working_dir  = pathlib.Path(working_dir)
    ref_episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir.parent / ref_rec / naming.coding_file))
    assert event in ref_episodes, f'Trial episodes are gotten from the reference recording ({ref_rec}), but the coding file for this reference recording doesn\'t contain any ({event.value}) episodes'
    # get sync and timestamp info we need to transform reference frames indices to frame indices of this recording
    sync = get_sync_for_recs(working_dir.parent, ref_rec, rec)
    video_ts_ref = timestamps.VideoTimestamps(working_dir.parent / ref_rec / 'frameTimestamps.tsv')
    video_ts     = timestamps.VideoTimestamps(working_dir / 'frameTimestamps.tsv')
    off          = -sync.loc[(rec,0),'mean_off']*1000.   # s -> ms, negate because value is sync this_rec->ref, we need the opposite
    frame_ts_ref = [[video_ts_ref.get_timestamp(i) for i in ifs] for ifs in ref_episodes[event]]
    frame_idx    = [[video_ts.find_frame(i+off) for i in ts] for ts in frame_ts_ref]
    return [[ifs[0]-extra_fr, ifs[1]+extra_fr] for ifs in frame_idx]   # arbitrarily expand by x frames on each edge, so we've likely got the frame we need