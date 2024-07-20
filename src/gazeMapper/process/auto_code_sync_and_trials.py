import pathlib
import numpy as np
import pandas as pd
import shutil

from glassesTools import annotation

from .. import config, episode, marker, naming


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    study_config = config.Study.load_from_json(config_dir)
    assert study_config.auto_code_sync_points or study_config.auto_code_trials_episodes, f'No automatic sync point detection or trial episode coding is defined for this study, nothing to do'
    rec_def = study_config.session_def.get_recording_def(working_dir.name)

    if not study_config.auto_code_sync_points:
        if not (study_config.auto_code_trials_episodes and (not study_config.sync_ref_recording or rec_def.name==study_config.sync_ref_recording)):
            if not study_config.auto_code_trials_episodes:
                raise RuntimeError('Nothing to do, neither auto_code_sync_points nor auto_code_trials_episodes are defined')
            else:
                raise RuntimeError(f'Nothing to do, auto_code_trials_episodes is defined, but you have a sync_ref_recording ({study_config.sync_ref_recording}) and this ({rec_def.name}) isn\'t it')

    # get already coded interval(s), if any
    coding_file = working_dir / naming.coding_file
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), study_config.episodes_to_code)
        # flatten
        for e in episodes:
            episodes[e] = [i for iv in episodes[e] for i in iv]
    else:
        episodes = episode.get_empty_marker_dict(study_config.episodes_to_code)

    # automatic sync point detection
    if study_config.auto_code_sync_points:
        # get marker files
        markers = [marker.load_file(m, working_dir) for m in study_config.individual_markers if m.id in study_config.auto_code_sync_points['markers']]
        # recode so we have a boolean with when markers are present
        markers = [marker.code_marker_for_presence(m) for m in markers]
        # fill gaps in marker detection
        for i in range(len(markers)):
            markers[i] = marker.fill_gaps_in_marker_detection(markers[i], fill_value=False)
        # see where stretches of True (marker presence) start
        marker_starts = []
        for i in range(len(markers)):
            start_frames,_ = get_marker_starts_ends(markers[i], study_config.auto_code_sync_points['max_gap_duration'], study_config.auto_code_sync_points['min_duration'])
            marker_starts.extend(start_frames)
        # insert in episodes
        [episodes[annotation.Event.Sync_Camera].append(i) for i in marker_starts if i not in episodes[annotation.Event.Sync_Camera]]

    # automatic trial episode coding
    if study_config.auto_code_trials_episodes and (not study_config.sync_ref_recording or rec_def.name==study_config.sync_ref_recording):
        # get marker files
        markers = {m.id: marker.load_file(m, working_dir) for m in study_config.individual_markers if m.id in study_config.auto_code_trials_episodes['start_markers']+study_config.auto_code_trials_episodes['end_markers']}
        # recode so we have a boolean with when markers are present
        markers = {i: marker.code_marker_for_presence(markers[i]) for i in markers}
        # fill gaps in marker detection
        for i in markers:
            markers[i] = marker.fill_gaps_in_marker_detection(markers[i], fill_value=False)
        # see where stretches of marker presence start and end
        marker_starts: dict[int,list[int]] = {}
        marker_ends  : dict[int,list[int]] = {}
        for i in markers:
            marker_starts[i], marker_ends[i] = get_marker_starts_ends(markers[i], study_config.auto_code_trials_episodes['max_gap_duration'], study_config.auto_code_trials_episodes['min_duration'])
        # find potential trial starts and ends
        if len(study_config.auto_code_trials_episodes['start_markers'])>1:
            starts = get_trial_from_markers(marker_starts, marker_ends, study_config.auto_code_trials_episodes['start_markers'], study_config.auto_code_trials_episodes['max_intermarker_gap_duration'], side='end')
        else:
            starts = marker_ends  [study_config.auto_code_trials_episodes['start_markers'][0]]
        if len(study_config.auto_code_trials_episodes[ 'end_markers' ])>1:
            ends   = get_trial_from_markers(marker_starts, marker_ends, study_config.auto_code_trials_episodes[ 'end_markers' ], study_config.auto_code_trials_episodes['max_intermarker_gap_duration'], side='start')
        else:
            ends   = marker_starts[study_config.auto_code_trials_episodes[ 'end_markers' ][0]]
        # now match trial starts and ends
        # strategy: run through starts and find latest start that is before first end (discard ends that are before the start)
        # keep pointer into array keeping track of ends and start already discarded or consumed
        # NB: this assumes starts and ends are sorted, which the above procedures should indeed deliver
        trials: list[tuple[int,int]] = []
        s_idx = 0
        e_idx = 0
        while s_idx<len(starts):
            # remove ends before the current start
            e_skip = np.nonzero(ends[e_idx:]<starts[s_idx])[0]
            if e_skip.size:
                e_idx += e_skip[-1]+1
            if e_idx > len(ends)-1:
                # we're out of ends, done
                break
            # for all starts in contention, find the last one that is before the next end
            gaps = ends[e_idx]-starts[s_idx:]
            # NB: it cannot occur that ther are no starts before this end, since we move e_idx above in that case
            # and bail out if there are no ends left
            gaps[gaps<=0] = np.iinfo(gaps.dtype).max
            mini = np.argmin(gaps)
            trials.append((starts[s_idx+mini], ends[e_idx]))
            # these are consumed
            s_idx+=mini+1
            e_idx+=1
        # now insert into coding file. This just overwrites whatever is there
        episodes[annotation.Event.Trial] = [y for x in trials for y in x]

    # back up coding file if it exists
    if coding_file.is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)


def get_marker_starts_ends(m: pd.DataFrame, max_gap_duration: int, min_duration: int):
    vals   = np.pad(m['marker_presence'].values.astype(int), (1, 1), 'constant', constant_values=(0, 0))
    d      = np.diff(vals)
    starts = np.nonzero(d == 1)[0]
    ends   = np.nonzero(d == -1)[0]
    gaps   = starts[1:]-ends[:-1]
    # fill gaps in marker detection
    gapi   = np.nonzero(gaps<=max_gap_duration)[0]
    starts = np.delete(starts,gapi+1)
    ends   = np.delete(ends,gapi)
    # remove too short
    lengths= ends-starts
    shorti = np.nonzero(lengths<=min_duration)[0]
    starts = np.delete(starts,shorti)
    ends   = np.delete(ends,shorti)
    # turn first and last frames into frame_idx values
    return m.loc[starts,'frame_idx'].values, m.loc[ends-1,'frame_idx'].values # NB: -1 so that ends point to last frame during which marker was last seen (and to not index out of the array)

def get_trial_from_markers(starts: dict[int,list[int]], ends: dict[int,list[int]], pattern: list[int], max_intermarker_gap_duration: int, side='start') -> np.ndarray:
    # find marker pattern (sequence of markers following in right order with gap no longer than max_intermarker_gap_duration)
    pairs: list[tuple[int,int]] = []
    for i in range(len(ends[pattern[0]])):
        end_idx = i
        for j in range(len(pattern)-1):
            if end_idx is None:
                break
            end     = ends[pattern[j]][end_idx]
            gaps    = starts[pattern[j+1]]-end
            end_idx = get_minimum_gap_marker(gaps,max_intermarker_gap_duration)
        if end_idx is not None:
            pairs.append((starts[pattern[0]][i], ends[pattern[-1]][end_idx]))

    idx = 0 if side=='start' else 1
    return np.array([p[idx] for p in pairs])

def get_minimum_gap_marker(gaps: np.ndarray, max_intermarker_gap_duration: int):
    gapi    = np.nonzero(np.logical_and(gaps>=0, gaps<=max_intermarker_gap_duration))[0]
    if gapi.size:
        # if there are multiple that qualify, take the smallest gap
        mini    = np.argmin(gaps[gapi])
        return gapi[mini]
    return None