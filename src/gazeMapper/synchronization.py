import numpy as np
import pandas as pd
import pathlib
import warnings
from typing import overload

from glassesTools import annotation, naming as gt_naming, process_pool, timestamps, video_utils

from . import config, episode, naming, process

def get_cols(do_time_stretch: bool):
    cols    = ['t_ref','t_this','offset']
    if do_time_stretch:
        cols += ['t_ref_elapsed','diff_offset','stretch_fac']
    else:
        cols += ['mean_off']

def _half_frame_corrected_timestamp(video_ts: timestamps.VideoTimestamps, frame_idx: int) -> float:
    timestamp = video_ts.get_timestamp(frame_idx)
    if timestamp < 0:
        raise ValueError(f'No video timestamp is available for sync frame {frame_idx}')
    if frame_idx == video_ts.indices[0]:
        return timestamp
    previous_timestamp = video_ts.get_timestamp(frame_idx - 1)
    if previous_timestamp < 0:
        raise ValueError(f'No video timestamp is available for the frame before sync frame {frame_idx}')
    return (previous_timestamp + timestamp) / 2

def _cluster_sync_points(timestamps_ms: list[float], threshold_ms: float) -> tuple[list[float], list[int]]:
    if not timestamps_ms:
        return [], []
    if threshold_ms <= 0:
        return timestamps_ms, [1] * len(timestamps_ms)

    clusters: list[list[float]] = [[timestamps_ms[0]]]
    for timestamp in timestamps_ms[1:]:
        if timestamp - clusters[-1][-1] < threshold_ms:
            clusters[-1].append(timestamp)
        else:
            clusters.append([timestamp])
    return [float(np.mean(cluster)) for cluster in clusters], [len(cluster) for cluster in clusters]

def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix}'

def _normalize_sync_points(sync_points: list[tuple[int, annotation.EventType]],
                           video_ts: timestamps.VideoTimestamps,
                           recording: str,
                           average_onset_offset: bool,
                           max_onset_offset_ms: float,
                           cluster_threshold_ms: float) -> tuple[list[float], list[int]]:
    typed_timestamps: dict[annotation.EventType, list[float]] = {
        event_type: [] for event_type in (annotation.EventType.Sync_Camera, annotation.EventType.Sync_Camera_Offset)
    }
    for frame_idx, event_type in sync_points:
        typed_timestamps[event_type].append(_half_frame_corrected_timestamp(video_ts, frame_idx))

    if average_onset_offset:
        onsets = typed_timestamps[annotation.EventType.Sync_Camera]
        offsets = typed_timestamps[annotation.EventType.Sync_Camera_Offset]
        if len(onsets) != len(offsets):
            raise ValueError(f'Recording {recording} has {len(onsets)} sync onsets and {len(offsets)} sync offsets; they must be paired one-to-one')
        sync_timestamps = []
        for index, (onset, offset) in enumerate(zip(onsets, offsets), start=1):
            duration_ms = offset - onset
            if duration_ms <= 0:
                raise ValueError(f'Recording {recording} sync pair {index} has an offset that does not follow its onset')
            if duration_ms > max_onset_offset_ms:
                raise ValueError(f'Recording {recording} sync pair {index} lasts {duration_ms:.3f} ms, exceeding the configured maximum of {max_onset_offset_ms:.3f} ms')
            sync_timestamps.append((onset + offset) / 2)
    else:
        sync_timestamps = sorted(typed_timestamps[annotation.EventType.Sync_Camera] + typed_timestamps[annotation.EventType.Sync_Camera_Offset])

    return _cluster_sync_points(sync_timestamps, cluster_threshold_ms)

def get_sync_for_recs(working_dir: str|pathlib.Path, recs: str|list[str], ref_rec: str, do_time_stretch: bool, average_recordings: list[str], missing_ref_coding_ok=False) -> pd.DataFrame|None:
    working_dir  = pathlib.Path(working_dir)
    if isinstance(recs,str):
        recs = [recs]
    config_dir = config.guess_config_dir(working_dir)
    study_config = config.Study.load_from_json(config_dir)
    if study_config is None:
        raise ValueError(f'Could not load study configuration from {config_dir}')
    sync_types = [annotation.EventType.Sync_Camera, annotation.EventType.Sync_Camera_Offset]
    sync_events = [cs for cs in config.get_coding_setup_with_auto_sync_offsets(study_config.coding_setup) if cs['event_type'] in sync_types]
    sync_event_defs = [(cs['name'], cs['event_type']) for cs in sync_events]
    ref_sync_points = get_coding_file(working_dir / ref_rec, sync_event_defs, missing_ref_coding_ok)
    if ref_sync_points is None:
        return None
    video_ts_ref = timestamps.VideoTimestamps(working_dir / ref_rec / gt_naming.frame_timestamps_fname)
    ref_episodes, ref_cluster_sizes = _normalize_sync_points(
        ref_sync_points, video_ts_ref, ref_rec,
        study_config.sync_ref_average_onset_offset,
        study_config.sync_ref_max_onset_offset_ms,
        study_config.sync_ref_cluster_threshold_ms,
    )

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
        sync_points = get_coding_file(working_dir / r, sync_event_defs, missing_ref_coding_ok)
        if sync_points is None:
            if missing_ref_coding_ok:
                return None
            else:
                raise FileNotFoundError(f'A coding file must be available for the recording ({r}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {annotation.tooltip_map[annotation.EventType.Sync_Camera]}. Not found: {working_dir / r / naming.coding_file}')

        # Normalize points the same way in every recording, then validate matching cluster structure.
        video_ts = timestamps.VideoTimestamps(working_dir / r / gt_naming.frame_timestamps_fname)
        episodes, cluster_sizes = _normalize_sync_points(
            sync_points, video_ts, r,
            study_config.sync_ref_average_onset_offset,
            study_config.sync_ref_max_onset_offset_ms,
            study_config.sync_ref_cluster_threshold_ms,
        )
        if cluster_sizes != ref_cluster_sizes:
            has_merged_clusters = any(size > 1 for size in cluster_sizes + ref_cluster_sizes)
            if not has_merged_clusters:
                mismatch = (
                    f'The number of sync events for recording {r} ({len(episodes)}) is not equal to '
                    f'that for the reference recording {ref_rec} ({len(ref_episodes)}).'
                )
            elif len(cluster_sizes) != len(ref_cluster_sizes):
                mismatch = (
                    f'Recording {r} has {len(cluster_sizes)} sync clusters, while reference recording '
                    f'{ref_rec} has {len(ref_cluster_sizes)}.'
                )
            else:
                different_clusters = [
                    f'{_ordinal(index)} (recording {r}: {recording_count}, reference {ref_rec}: {reference_count})'
                    for index, (recording_count, reference_count) in enumerate(zip(cluster_sizes, ref_cluster_sizes), start=1)
                    if recording_count != reference_count
                ]
                mismatch = 'Different sync cluster member counts: ' + '; '.join(different_clusters) + '.'
            raise ValueError(f'{mismatch} Cannot continue, fix the event coding for your recordings.')

        # Store normalized sync timestamps in seconds.
        for ival in range(len(episodes)):
            sync.loc[(r,ival),'t_ref']  = ref_episodes[ival]/1000.  # ms -> s
            sync.loc[(r,ival),'t_this'] = episodes[ival]/1000.      # ms -> s
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
        rec_sync = sync.loc[rec]
        for ival in range(num_reference_episodes-1):
            # set up the problem - piecewise linear scale
            # 1. get known good location for this interval, and the stretch factor
            t_ref_start  = rec_sync.loc[ival,'t_ref' ]*1000.   # s -> ms
            t_ref_end    = rec_sync.loc[ival+1,'t_ref' ]*1000. # s -> ms
            t_this_start = rec_sync.loc[ival,'t_this']*1000.   # s -> ms
            t_this_end   = rec_sync.loc[ival+1,'t_this']*1000. # s -> ms
            stretch_fac  = rec_sync.loc[ival,'stretch_fac']
            # 2. determine the timeline ranges for this interval
            if ival==0:
                # first interval, apply all the way from start of data/reference
                d_start = dt_start
                r_start = rt_start
            else:
                d_start = t_this_start
                r_start = t_ref_start
            if ival==num_reference_episodes-2:  # NB: zero-based indexing
                # last interval, apply all the way to end of data/reference
                d_end = dt_end
                r_end = rt_end
            else:
                d_end = t_this_end
                r_end = t_ref_end
            # calculate new timestamps
            if stretch_which=='ref':
                # 1. first translate gaze ts to reference timestamps
                if has_data_ts:
                    data_sel = (data_timestamps >= d_start) & (data_timestamps <= d_end)
                    new_data_timestamps[data_sel] += rec_sync.loc[ival,'offset']*1000.   # s -> ms
                # 2. apply scaling
                ref_sel = (reference_video_timestamps >= r_start) & (reference_video_timestamps <= r_end)
                new_reference_video_timestamps[ref_sel] = (reference_video_timestamps[ref_sel]-t_ref_start)*(1-stretch_fac)+t_ref_start
            elif stretch_which=='other':
                if has_data_ts:
                    data_sel = (data_timestamps >= d_start) & (data_timestamps <= d_end)
                    new_data_timestamps[data_sel] = t_ref_start+(data_timestamps[data_sel]-t_this_start)/(1-stretch_fac)
                # else nothing to do...
    else:
        if has_data_ts:
            new_data_timestamps += sync.loc[(rec,0),'mean_off']*1000.   # s -> ms
    if has_data_ts:
        fr_ref = video_utils.timestamps_to_frame_number(new_data_timestamps,new_reference_video_timestamps,trim=True)['frame_idx'].to_numpy(copy=True)
    else:
        fr_ref = None
    return new_data_timestamps, new_reference_video_timestamps, fr_ref

def get_coding_file(working_dir: str|pathlib.Path, all_sync_events: list[tuple[str, annotation.EventType]], missing_ref_coding_ok=False):
    working_dir  = pathlib.Path(working_dir)
    coding_file = working_dir / naming.coding_file
    if not coding_file.is_file():
        if missing_ref_coding_ok:
            return None
        raise FileNotFoundError(f'A coding file must be available for the recording ({working_dir.name}) to run sync_to_ref, but it is not. Run code_episodes and code at least one {annotation.tooltip_map[annotation.EventType.Sync_Camera]}. Not found: {coding_file}')
    if not all_sync_events:
        raise ValueError('No camera sync events have been set up to be coded. Cannot continue.')
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), all_sync_events)
    sync_points = sorted(
        ((int(point[0]), event_type) for name, event_type in all_sync_events for point in episodes[name][1]),
        key=lambda point: point[0],
    )
    if not sync_points:
        if missing_ref_coding_ok:
            return None
        raise ValueError(f'No {annotation.tooltip_map[annotation.EventType.Sync_Camera]} codes found for this recording ({working_dir.name}). Run code_episodes and code at least one of the configured {annotation.tooltip_map[annotation.EventType.Sync_Camera]} events.')
    return sync_points

class EpisodeFrameMappingCache:
    def __init__(self, working_dir: str|pathlib.Path, all_recs: list[str], ref_rec: str, do_time_stretch: bool, average_recordings: list[str], stretch_which: str):
        self.working_dir = pathlib.Path(working_dir)
        self.all_recs = all_recs
        self.ref_rec = ref_rec
        self.do_time_stretch = do_time_stretch
        self.average_recordings = average_recordings
        self.stretch_which = stretch_which
        self._sync_loaded = False
        self._sync: pd.DataFrame|None = None
        self._source_episodes: dict[str, episode.EpisodeMap|None] = {}
        self._video_timestamps: dict[str, timestamps.VideoTimestamps] = {}
        self._warned_sync_errors: set[str] = set()

    def warn_sync_error(self, error: ValueError) -> None:
        message = str(error)
        if message not in self._warned_sync_errors:
            self._warned_sync_errors.add(message)
            warnings.warn(message, process_pool.ProcessingWarning, stacklevel=2)

    def get_sync(self, missing_ref_coding_ok: bool) -> pd.DataFrame|None:
        if not self._sync_loaded:
            self._sync = get_sync_for_recs(self.working_dir, self.all_recs, self.ref_rec, self.do_time_stretch, self.average_recordings, missing_ref_coding_ok)
            self._sync_loaded = True
        return self._sync

    def get_source_episodes(self, recording: str, missing_coding_ok: bool) -> episode.EpisodeMap|None:
        if recording not in self._source_episodes:
            coding_file = self.working_dir / recording / naming.coding_file
            if not coding_file.is_file():
                self._source_episodes[recording] = None
            else:
                self._source_episodes[recording] = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))
        source_episodes = self._source_episodes[recording]
        if source_episodes is None and not missing_coding_ok:
            coding_file = self.working_dir / recording / naming.coding_file
            raise FileNotFoundError(f'The coding file for the other recording is not found, cannot continue ("{coding_file}").')
        return source_episodes

    def get_video_timestamps(self, recording: str) -> timestamps.VideoTimestamps:
        if recording not in self._video_timestamps:
            self._video_timestamps[recording] = timestamps.VideoTimestamps(self.working_dir / recording / gt_naming.frame_timestamps_fname)
        return self._video_timestamps[recording]

def get_episode_frame_indices_from_other_video(working_dir: str|pathlib.Path, event: str, rec: str, other_rec: str, ref_rec:str, all_recs: list[str], do_time_stretch: bool, average_recordings: list[str], stretch_which: str, extra_fr=0, missing_other_coding_ok=False, mapping_cache: EpisodeFrameMappingCache|None=None, ignore_sync_errors=False) -> list[list[int]]|None:
    working_dir  = pathlib.Path(working_dir)
    if mapping_cache is None:
        mapping_cache = EpisodeFrameMappingCache(working_dir.parent, all_recs, ref_rec, do_time_stretch, average_recordings, stretch_which)
    other_episodes = mapping_cache.get_source_episodes(other_rec, missing_other_coding_ok)
    if other_episodes is None:
        return None
    if event not in other_episodes:
        if missing_other_coding_ok:
            return None
        raise KeyError(f'Requested event "{event}" is not found. Either the requested event does not exist in the event coding for the other recording ({other_rec}) or the event is unknown.')
    # get sync and timestamp info we need to transform reference frames indices to frame indices of this recording
    try:
        sync = mapping_cache.get_sync(missing_other_coding_ok)
    except ValueError as error:
        if ignore_sync_errors:
            mapping_cache.warn_sync_error(error)
            return None
        raise
    if sync is None:
        return None
    video_ts_other = mapping_cache.get_video_timestamps(other_rec)
    video_ts       = mapping_cache.get_video_timestamps(rec)
    def _check_bounds(eps: list[list[int]], max_i: int) -> list[list[int]]:
        return [[max(0,ep[0]), min(ep[1], max_i)] for ep in eps if not all([x==-1 for x in ep])]
    # if one of the recordings is the reference recording, we can directly go from reference to this recording
    if other_rec==ref_rec:
        frame_idx = reference_frames_to_video(rec, sync, other_episodes[event][1], video_ts.timestamps, video_ts_other.timestamps, do_time_stretch, stretch_which)
    elif rec==ref_rec:
        frame_idx = video_frames_to_reference(other_rec, sync, other_episodes[event][1], video_ts_other.timestamps, video_ts.timestamps, do_time_stretch, stretch_which)
    else:
        # else we need to go from other_rec to ref_rec and then from ref_rec to this recording. Do so with a function that does not clip, nor quantize to the frames in the reference video
        # so that we do not lose precision or range
        video_ts_ref = mapping_cache.get_video_timestamps(ref_rec)
        frame_idx = video_frames_to_other_video(other_rec, rec, sync, other_episodes[event][1], video_ts_other.timestamps, video_ts.timestamps, video_ts_ref.timestamps, do_time_stretch, stretch_which)
    # remove out of range
    frame_idx = _check_bounds(frame_idx, video_ts.indices[-1])
    return [[i+e for i,e in zip(ifs, [-extra_fr, extra_fr])] for ifs in frame_idx]   # expand by extra_fr frames on each edge


def video_episode_dict_to_reference(rec: str,
                                    sync: pd.DataFrame,
                                    episodes: episode.EpisodeMap,
                                    this_video_ts: list[float] | np.ndarray,
                                    video_ts_ref: list[float] | np.ndarray,
                                    do_time_stretch: bool,
                                    stretch_which: str) -> episode.EpisodeMap:
    return {
        event: (
            episodes[event][0],
            video_frames_to_reference(rec, sync, episodes[event][1], this_video_ts, video_ts_ref, do_time_stretch, stretch_which),
        )
        for event in episodes
    }


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
    fr_idx_ref = video_utils.timestamps_to_frame_number(video_ts_ref, this_video_ts_ref, trim=True)['frame_idx'].to_numpy(copy=True)
    # Reference frames before the synced video starts have no match in this recording.
    fr_idx_ref[video_ts_ref<this_video_ts_ref[0]] = -1
    # Allow a normal first-frame interval to map back to frame 0 instead of dropping it.
    if fr_idx_ref.size>=2 and fr_idx_ref[0]==-1 and fr_idx_ref[1]>0:
        ifi = np.mean(np.diff(video_ts_ref))
        # If the first reference timestamp is only about one frame early, keep it.
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
    fr_idx = video_utils.timestamps_to_frame_number(this_video_ts_ref, video_ts_ref, trim=True)['frame_idx'].to_numpy(copy=True)
    fr_idx[this_video_ts_ref<video_ts_ref[0]] = -1
    # NB: no need to check for timestamps beyond the reference video's end here; trim=True already ensures that these are marked as invalid.

    return fr_idx[fr_idxs].tolist()

@overload
def video_frames_to_other_video(other_rec: str, rec: str, sync: pd.DataFrame, fr_idxs: list[int], other_video_ts: list[float]|np.ndarray, this_video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]: ...
@overload
def video_frames_to_other_video(other_rec: str, rec: str, sync: pd.DataFrame, fr_idxs: list[list[int]], other_video_ts: list[float]|np.ndarray, this_video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[list[int]]: ...
def video_frames_to_other_video(other_rec: str, rec: str, sync: pd.DataFrame, fr_idxs: list[int]|list[list[int]], other_video_ts: list[float]|np.ndarray, this_video_ts: list[float]|np.ndarray, video_ts_ref: list[float]|np.ndarray, do_time_stretch: bool, stretch_which: str) -> list[int]|list[list[int]]:
    if not fr_idxs:
        return []

    # get timestamps of both videos in time of the reference video
    other_video_ts_ref, _, _ = apply_sync(other_rec, sync, other_video_ts, video_ts_ref, do_time_stretch, stretch_which)
    this_video_ts_ref, _, _ = apply_sync(rec, sync, this_video_ts, video_ts_ref, do_time_stretch, stretch_which)

    # Match both videos after expressing them in the same reference-time clock.
    frame_idx = video_utils.timestamps_to_frame_number(other_video_ts_ref, this_video_ts_ref, trim=False)['frame_idx'].to_numpy(copy=True)
    # Timestamps before this recording starts cannot map to any frame here.
    frame_idx[other_video_ts_ref<this_video_ts_ref[0]] = -1
    if this_video_ts_ref.size>=2:
        ifi = np.mean(np.diff(this_video_ts_ref))
        # Likewise, reject timestamps that land more than about one frame past the end.
        frame_idx[other_video_ts_ref>this_video_ts_ref[-1]+ifi] = -1

    return frame_idx[fr_idxs].tolist()

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