import pathlib
import numpy as np
import pandas as pd
import shutil

from glassesTools import annotation

from .. import config, episode, marker, naming, process, session
from . import _utils


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    if not study_config.auto_code_trial_episodes:
        raise ValueError(f'Nothing to do, no automatic trial episode coding is defined for this study')
    rec_def = study_config.session_def.get_recording_def(working_dir.name)

    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        raise RuntimeError(f'Nothing to do, auto_code_trials_episodes is defined and you have a sync_ref_recording ({study_config.sync_ref_recording}), but this recording ({rec_def.name}) is another one')

    # get already coded interval(s), if any
    coding_file = working_dir / naming.coding_file
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), study_config.episodes_to_code)
        # flatten
        for e in episodes:
            episodes[e] = [i for iv in episodes[e] for i in iv]
    else:
        episodes = episode.get_empty_marker_dict(study_config.episodes_to_code)

    # get marker files
    markers = {m.id: marker.load_file(m.id, working_dir) for m in study_config.individual_markers if m.id in study_config.auto_code_trial_episodes['start_markers']+study_config.auto_code_trial_episodes['end_markers']}
    # recode so we have a boolean with when markers are present
    markers = {i: marker.code_marker_for_presence(markers[i], allow_failed=True) for i in markers if not markers[i].empty}
    # fill gaps in marker detection
    for i in markers:
        markers[i] = marker.fill_gaps_in_marker_detection(markers[i], fill_value=False)
    # see where stretches of marker presence start and end
    marker_starts: dict[int,list[int]] = {}
    marker_ends  : dict[int,list[int]] = {}
    for i in markers:
        marker_starts[i], marker_ends[i] = _utils.get_marker_starts_ends(markers[i], study_config.auto_code_trial_episodes['max_gap_duration'], study_config.auto_code_trial_episodes['min_duration'])
    # find potential trial starts and ends
    if len(study_config.auto_code_trial_episodes['start_markers'])>1:
        starts = _utils.get_trial_from_markers(marker_starts, marker_ends, study_config.auto_code_trial_episodes['start_markers'], study_config.auto_code_trial_episodes['max_intermarker_gap_duration'], side='end')
    else:
        starts = marker_ends  [study_config.auto_code_trial_episodes['start_markers'][0]]
    if len(study_config.auto_code_trial_episodes[ 'end_markers' ])>1:
        ends   = _utils.get_trial_from_markers(marker_starts, marker_ends, study_config.auto_code_trial_episodes[ 'end_markers' ], study_config.auto_code_trial_episodes['max_intermarker_gap_duration'], side='start')
    else:
        ends   = marker_starts[study_config.auto_code_trial_episodes[ 'end_markers' ][0]]
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
        # NB: it cannot occur that there are no starts before this end, since we move e_idx above in that case
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
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_trial_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_TRIALS, process.State.Completed, study_config)