import numpy as np
import pandas as pd
import pathlib
from typing import overload

from glassesTools import annotation, timestamps, video_utils

from . import episode, naming

def get_cols(do_time_stretch: bool):
    cols    = ['t_ref','t_this','offset']
    if do_time_stretch:
        cols += ['t_ref_elapsed','diff_offset','stretch_fac']
    else:
        cols += ['mean_off']

def get_sync_for_recs(working_dir: str|pathlib.Path, recs: str|list[str], ref_rec: str, do_time_stretch: bool, sync_average_recordings: list[str]):
    working_dir  = pathlib.Path(working_dir)
    if isinstance(recs,str):
        recs = [recs]
    recs = [r for r in recs if r!=ref_rec]
    ref_episodes = get_coding_file(working_dir / ref_rec)
    video_ts_ref = timestamps.VideoTimestamps(working_dir / ref_rec / 'frameTimestamps.tsv')

    index = pd.MultiIndex.from_product([recs, range(len(ref_episodes))], names=['recording','interval'])
    sync  = pd.DataFrame(columns=get_cols(do_time_stretch), dtype=float, index=index)

    # collect timestamps for the recordings
    for r in recs:
        # get interval coding for this recording
        episodes = get_coding_file(working_dir / r)

        # check intervals
        assert len(episodes)==len(ref_episodes), f"The number of sync points for this recording ({len(episodes)}, {r}) is not equal to that for the reference recording ({len(ref_episodes)}, {ref_rec}). Cannot continue, fix your coding"

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
        if sync_average_recordings:
            recs_gr = [r for r in recs if r not in sync_average_recordings]
            recs_gr.append(sync_average_recordings)
        else:
            recs_gr = recs
        for r in recs_gr:
            for ival in range(len(ref_episodes)-1):
                t_ref = sync.loc[(r,ival+1),'t_ref' ]
                offset= sync.loc[(r,ival+1),'offset']
                if not isinstance(t_ref,float) and 'interval' in t_ref.index.names:
                    t_ref = t_ref .droplevel('interval')
                    offset= offset.droplevel('interval')
                sync.loc[(r,ival),'t_ref_elapsed'] = t_ref-sync.loc[(r,ival),'t_ref' ]
                sync.loc[(r,ival),'diff_offset']   = offset-sync.loc[(r,ival),'offset']
                sync.loc[(r,ival),'stretch_fac']   = sync.loc[(r,ival),'diff_offset'].mean()/sync.loc[(r,ival),'t_ref_elapsed'].mean()
    return sync

def apply_sync(rec: str,
               sync: pd.DataFrame,
               data_timestamps: np.ndarray,
               reference_video_timestamps: np.ndarray,
               do_time_stretch,
               stretch_which: str):
    reference_video_timestamps  = np.array(reference_video_timestamps).copy()
    data_timestamps             = np.array(data_timestamps).copy()
    t_start, t_end              = data_timestamps.min(), data_timestamps.max()
    num_reference_episodes      = sync.loc[rec].shape[0]
    if do_time_stretch:
        for ival in range(num_reference_episodes-1):
            # set up the problem - piecewise linear scale
            # 1. get known good location for this interval, and the stretch factor
            pivot       = sync.loc[(rec,ival),'t_ref']
            stretch_fac = sync.loc[(rec,ival),'stretch_fac']
            # 2. determine data range to apply stretch for this interval to
            if ival==0:
                # first interval, apply all the way from start of data
                start = t_start
            else:
                start = sync.loc[(rec,ival),'t_ref']
            if ival==num_reference_episodes-2:  # NB: zero-based indexing
                # last interval, apply all the way to end of data
                end = t_end
            else:
                end = sync.loc[(rec,ival+1),'t_ref']
            data_sel = (data_timestamps >= start) & (data_timestamps <= end)
            # calculate new timestamps
            # 1. first translate gaze ts to reference timestamps
            data_timestamps[data_sel] += sync.loc[(rec,ival),'offset']*1000.   # s -> ms
            # 2. apply scaling
            if stretch_which=='ref':
                data_sel = (reference_video_timestamps >= start) & (reference_video_timestamps <= end)
                reference_video_timestamps[data_sel] = (reference_video_timestamps[data_sel]-pivot)*(1-stretch_fac)+pivot
            elif stretch_which=='other':
                raise NotImplementedError()
    else:
        data_timestamps += sync.loc[(rec,0),'mean_off']*1000.   # s -> ms
    fr_ref = video_utils.timestamps_to_frame_number(data_timestamps,reference_video_timestamps,trim=True)['frame_idx'].to_numpy()
    return data_timestamps, reference_video_timestamps, fr_ref

def get_coding_file(working_dir: str|pathlib.Path):
    working_dir  = pathlib.Path(working_dir)
    coding_file = working_dir / naming.coding_file
    assert coding_file.is_file(), f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} episode. Not found: {coding_file}'
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[annotation.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    assert episodes, f'No {annotation.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} point.'
    return episodes

def get_episode_frame_indices_from_ref(working_dir: str|pathlib.Path, event: annotation.Event, rec: str, ref_rec:str, do_time_stretch: bool, sync_average_recordings: list[str], stretch_which: str, extra_fr=0):
    working_dir  = pathlib.Path(working_dir)
    ref_episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir.parent / ref_rec / naming.coding_file))
    assert event in ref_episodes, f'Trying to get {event.value} episodes from the reference recording ({ref_rec}), but the coding file for this reference recording doesn\'t contain any ({event.value}) episodes'
    # get sync and timestamp info we need to transform reference frames indices to frame indices of this recording
    sync = get_sync_for_recs(working_dir.parent, rec, ref_rec, do_time_stretch, sync_average_recordings)
    video_ts_ref = timestamps.VideoTimestamps(working_dir.parent / ref_rec / 'frameTimestamps.tsv')
    video_ts     = timestamps.VideoTimestamps(working_dir / 'frameTimestamps.tsv')
    # get frame indices in this recording's video corresponding to each of the reference frames
    frame_idx = get_frame_idxs_from_reference(rec, sync, ref_episodes[event], video_ts.timestamps, video_ts_ref.timestamps, do_time_stretch, stretch_which)
    return [[i+e for i,e in zip(ifs, [-extra_fr, extra_fr])] for ifs in frame_idx]   # expand by extra_fr frames on each edge

@overload
def get_frame_idxs_from_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[int], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]: ...
@overload
def get_frame_idxs_from_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[list[int]], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[list[int]]: ...
def get_frame_idxs_from_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[int]|list[list[int]], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]|list[list[int]]:
    if not fr_idxs:
        return []

    # get where (which frame) each of the video's timestamps occur in the reference video, given the sync info
    # (fr_idx_ref contains the reference frame_idxs corresponding to this video's frames, video_ts)
    _, _, fr_idx_ref = apply_sync(rec, sync, video_ts, video_ts_ref, do_time_stretch, stretch_which)

    # find frame idxs in this video for each reference frame specified in fr_idxs
    if isinstance(fr_idxs[0],list):
        return [[idx[i] if (idx:=np.nonzero(fr_idx_ref==x)[0]).size else -1 for x,i in zip(y,[0,-1])] for y in fr_idxs]
    else:
        return [ idx[0] if (idx:=np.nonzero(fr_idx_ref==x)[0]).size else -1 for x in fr_idxs]