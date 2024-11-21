import numpy as np
import pandas as pd
import pathlib
from typing import overload

from glassesTools import annotation, naming as gt_naming, timestamps, video_utils

from . import episode, naming

def get_cols(do_time_stretch: bool):
    cols    = ['t_ref','t_this','offset']
    if do_time_stretch:
        cols += ['t_ref_elapsed','diff_offset','stretch_fac']
    else:
        cols += ['mean_off']

def get_sync_for_recs(working_dir: str|pathlib.Path, recs: str|list[str], ref_rec: str, do_time_stretch: bool, average_recordings: list[str], missing_ref_coding_ok=False):
    working_dir  = pathlib.Path(working_dir)
    if isinstance(recs,str):
        recs = [recs]
    ref_episodes = get_coding_file(working_dir / ref_rec, missing_ref_coding_ok)
    if ref_episodes is None:
        return None
    video_ts_ref = timestamps.VideoTimestamps(working_dir / ref_rec / gt_naming.frame_timestamps_fname)

    if do_time_stretch:
        if len(ref_episodes)<2:
            if missing_ref_coding_ok:
                return None
            raise ValueError(f"You requested to do time stretching when syncing the recordings, but there is only one camera sync point in the reference recording. At least two sync points are required for time stretching")
        for r in average_recordings:
            if r not in recs:
                raise ValueError(f'Recording {r} not found')
            if r==ref_rec:
                raise ValueError(f'Recording {r} is the reference recording for sync, should not be specified in study_config.sync_average_recordings')

    index = pd.MultiIndex.from_product([recs, range(len(ref_episodes))], names=['recording','interval'])
    sync  = pd.DataFrame(columns=get_cols(do_time_stretch), dtype=float, index=index)

    # collect timestamps for the recordings
    for r in recs:
        # get interval coding for this recording
        episodes = get_coding_file(working_dir / r, missing_ref_coding_ok)
        if episodes is None and missing_ref_coding_ok:
            return None

        # check intervals
        if len(episodes)!=len(ref_episodes):
            if missing_ref_coding_ok:
                return None
            raise ValueError(f"The number of sync points for this recording ({len(episodes)}, {r}) is not equal to that for the reference recording ({len(ref_episodes)}, {ref_rec}). Cannot continue, fix your coding")

        # get time information
        video_ts = timestamps.VideoTimestamps(working_dir / r / gt_naming.frame_timestamps_fname)

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
        if average_recordings:
            recs_gr = [r for r in recs if r not in average_recordings]
            recs_gr.append(list(average_recordings))
        else:
            recs_gr = recs
        for r in recs_gr:
            for ival in range(len(ref_episodes)-1):
                t_ref = sync.loc[(r,ival+1),'t_ref' ]
                offset= sync.loc[(r,ival+1),'offset']
                if not isinstance(t_ref,float) and 'interval' in t_ref.index.names:
                    t_ref = t_ref .droplevel('interval')
                    offset= offset.droplevel('interval')
                sync.loc[(r,ival),'t_ref_elapsed'] = t_ref-sync.loc[(r,ival),'t_ref']
                sync.loc[(r,ival),'diff_offset']   = offset-sync.loc[(r,ival),'offset']
                sync.loc[(r,ival),'stretch_fac']   = sync.loc[(r,ival),'diff_offset'].mean()/sync.loc[(r,ival),'t_ref_elapsed'].mean()
    return sync

def apply_sync(rec: str,
               sync: pd.DataFrame,
               data_timestamps: np.ndarray|None,
               reference_video_timestamps: np.ndarray,
               do_time_stretch,
               stretch_which: str):
    reference_video_timestamps  = np.array(reference_video_timestamps).copy()
    new_reference_video_timestamps = reference_video_timestamps.copy()
    rt_start, rt_end            = reference_video_timestamps.min(), reference_video_timestamps.max()
    if has_data_ts := data_timestamps is not None:
        data_timestamps     = np.array(data_timestamps).copy()
        dt_start, dt_end    = data_timestamps.min(), data_timestamps.max()
        new_data_timestamps = data_timestamps.copy()
    else:
        dt_start, dt_end    = None, None
    num_reference_episodes      = sync.loc[rec].shape[0]
    if do_time_stretch:
        for ival in range(num_reference_episodes-1):
            # set up the problem - piecewise linear scale
            # 1. get known good location for this interval, and the stretch factor
            pivot       = (sync.loc[(rec,ival),'t_ref'] if stretch_which=='ref' else sync.loc[(rec,ival),'t_this'])*1000.   # s -> ms
            stretch_fac = sync.loc[(rec,ival),'stretch_fac']
            # 2. determine data range to apply stretch for this interval to
            if ival==0:
                # first interval, apply all the way from start of data
                d_start = dt_start
                r_start = rt_start
            else:
                d_start = r_start = sync.loc[(rec,ival),'t_ref']
            if ival==num_reference_episodes-2:  # NB: zero-based indexing
                # last interval, apply all the way to end of data
                d_end = dt_end
                r_end = rt_end
            else:
                d_end = r_end = sync.loc[(rec,ival+1),'t_ref']
            # calculate new timestamps
            if stretch_which=='ref':
                # 1. first translate gaze ts to reference timestamps
                if has_data_ts:
                    data_sel = (data_timestamps >= d_start) & (data_timestamps <= d_end)
                    new_data_timestamps[data_sel] += sync.loc[(rec,ival),'offset']*1000.   # s -> ms
                # 2. apply scaling
                data_sel = (reference_video_timestamps >= r_start) & (reference_video_timestamps <= r_end)
                new_reference_video_timestamps[data_sel] = (reference_video_timestamps[data_sel]-pivot)*(1-stretch_fac)+pivot
            elif stretch_which=='other':
                if has_data_ts:
                    data_sel = (data_timestamps >= d_start) & (data_timestamps <= d_end)
                    new_data_timestamps[data_sel] = (data_timestamps[data_sel]-pivot)*(1+stretch_fac)+pivot
                    new_data_timestamps[data_sel] += sync.loc[(rec,ival),'offset']*1000.   # s -> ms
                # else nothing to do...
    else:
        if has_data_ts:
            new_data_timestamps += sync.loc[(rec,0),'mean_off']*1000.   # s -> ms
    if has_data_ts:
        fr_ref = video_utils.timestamps_to_frame_number(new_data_timestamps,new_reference_video_timestamps,trim=True)['frame_idx'].to_numpy()
    else:
        fr_ref = None
    return new_data_timestamps, new_reference_video_timestamps, fr_ref

def get_coding_file(working_dir: str|pathlib.Path, missing_ref_coding_ok=False):
    working_dir  = pathlib.Path(working_dir)
    coding_file = working_dir / naming.coding_file
    if not coding_file.is_file():
        if missing_ref_coding_ok:
            return None
        raise FileNotFoundError(f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} episode. Not found: {coding_file}')
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))[annotation.Event.Sync_Camera]
    episodes = [x[0] for x in episodes] # remove inner wrapping list, there are only single values in it anyway
    if not episodes:
        if missing_ref_coding_ok:
            return None
        raise ValueError(f'No {annotation.Event.Sync_Camera.value} points found for this recording ({working_dir.name}). Run code_episodes and code at least one {annotation.Event.Sync_Camera.value} point.')
    return episodes

def get_episode_frame_indices_from_ref(working_dir: str|pathlib.Path, event: annotation.Event, rec: str, ref_rec:str, all_recs: list[str], do_time_stretch: bool, average_recordings: list[str], stretch_which: str, extra_fr=0, missing_ref_coding_ok=False) -> list[list[int]]:
    working_dir  = pathlib.Path(working_dir)
    ref_coding_file = working_dir.parent / ref_rec / naming.coding_file
    if not ref_coding_file.is_file():
        if missing_ref_coding_ok:
            return [[]]
        raise FileNotFoundError(f'The coding file for the reference recording is not found, cannot continue ("{ref_coding_file}").')
    ref_episodes = episode.list_to_marker_dict(episode.read_list_from_file(ref_coding_file))
    if event not in ref_episodes:
        if missing_ref_coding_ok:
            return [[]]
        raise KeyError(f'Trying to get {event.value} episodes from the reference recording ({ref_rec}), but the coding file for this reference recording doesn\'t contain any ({event.value}) episodes')
    # get sync and timestamp info we need to transform reference frames indices to frame indices of this recording
    sync = get_sync_for_recs(working_dir.parent, all_recs, ref_rec, do_time_stretch, average_recordings, missing_ref_coding_ok)
    if sync is None:
        return [[]]
    video_ts_ref = timestamps.VideoTimestamps(working_dir.parent / ref_rec / gt_naming.frame_timestamps_fname)
    video_ts     = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)
    # get frame indices in this recording's video corresponding to each of the reference frames
    frame_idx = reference_frames_to_video(rec, sync, ref_episodes[event], video_ts.timestamps, video_ts_ref.timestamps, do_time_stretch, stretch_which)
    return [[i+e for i,e in zip(ifs, [-extra_fr, extra_fr])] for ifs in frame_idx]   # expand by extra_fr frames on each edge

@overload
def reference_frames_to_video(rec: str, sync: pd.DataFrame, fr_idxs: list[int], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]: ...
@overload
def reference_frames_to_video(rec: str, sync: pd.DataFrame, fr_idxs: list[list[int]], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[list[int]]: ...
def reference_frames_to_video(rec: str, sync: pd.DataFrame, fr_idxs: list[int]|list[list[int]], this_video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]|list[list[int]]:
    if not fr_idxs:
        return []

    # get the video's timestamps in time of the reference video
    this_video_ts_ref, video_ts_ref, _ = apply_sync(rec, sync, this_video_ts, video_ts_ref, do_time_stretch, stretch_which)

    # get where (which frame) each of this video's timestamps occur in the reference video, given the sync info
    # (fr_idx_ref contains the reference frame_idxs corresponding to this video's frames, video_ts)
    fr_idx_ref = video_utils.timestamps_to_frame_number(video_ts_ref, this_video_ts_ref, trim=True)['frame_idx'].to_numpy()
    fr_idx_ref[video_ts_ref<this_video_ts_ref[0]] = -1
    # in case only the first fr_idx is trimmed, a little bit of leeway is ok
    if fr_idx_ref.size>=2 and fr_idx_ref[0]==-1 and fr_idx_ref[1]>0:
        ifi = np.mean(np.diff(video_ts_ref))
        # that means, do assign a frame to the first frame if its a usual frame (judged by ifi)
        if video_ts_ref[1]-video_ts_ref[0] < ifi*1.2:
            fr_idx_ref[0] = fr_idx_ref[1]-1

    return fr_idx_ref[fr_idxs].tolist()

@overload
def video_frames_to_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[int], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]: ...
@overload
def video_frames_to_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[list[int]], video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[list[int]]: ...
def video_frames_to_reference(rec: str, sync: pd.DataFrame, fr_idxs: list[int]|list[list[int]], this_video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]|list[list[int]]:
    if not fr_idxs:
        return []

    # get the video's timestamps in time of the reference video
    this_video_ts_ref, video_ts_ref, _ = apply_sync(rec, sync, this_video_ts, video_ts_ref, do_time_stretch, stretch_which)

    # get where (which frame) each of the reference video frames occur in this video, given the sync info
    # (fr_idx contains this video's frame_idxs corresponding to this reference's frames, video_ts)
    fr_idx = video_utils.timestamps_to_frame_number(this_video_ts_ref, video_ts_ref, trim=True)['frame_idx'].to_numpy()
    fr_idx[this_video_ts_ref<video_ts_ref[0]] = -1

    return fr_idx[fr_idxs].tolist()

def smooth_video_frames_indices(fr_idxs: list[int]):
    # detect plateaus of N samples followed by a step of N samples
    # that may occur if sampling rates of two videos are unmatched when syncing them.
    # while the plateaus are correct for "nearest" frame logic, for display purposes
    # a smoothed version of the higher framerate video may be wanted
    fr_idxs = np.array(fr_idxs)

    # get steps in frame_signal
    d       = np.diff(fr_idxs)
    if np.any(d<0):
        # below logic assumes a increasing frame index array
        return fr_idxs.tolist()

    # find where the plateaus are (consecutive steps of 0)
    vals    = np.pad((d==0).astype(int), (1, 1), 'constant', constant_values=(0, 0))
    d2      = np.diff(vals)
    starts  = np.nonzero(d2 == 1)[0]
    ends    = np.nonzero(d2 == -1)[0]
    # ensure we're not out of bounds
    if ends.size>0 and ends[-1]==len(d):
        starts = starts[:-1]
        ends   = ends[:-1]

    # for each plateau, see how long it is, and what the step to the next value after it is
    plateau_len = ends-starts+1
    step_at_end = d[ends]

    # select those plateaus whose step after is the same size as the plateau length (e.g. frame indices [235 236 236 238], a plateau of length 2, and a step of two frames thereafter)
    # those we can fix up
    to_fix = np.nonzero(np.logical_and(plateau_len==step_at_end, starts!=-1))[0]

    # fix em
    for i in to_fix:
        fr_start = fr_idxs[starts[i]]
        fr_end   = fr_idxs[ends[i]+1]
        new_vals = list(range(fr_start,fr_end))
        fr_idxs[starts[i]:ends[i]+1] = new_vals

    return fr_idxs.tolist()