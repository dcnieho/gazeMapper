import pathlib
import numpy as np
import shutil
import copy

from glassesTools import annotation, marker as gt_marker, process_pool

from .. import config, episode, naming, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    sync_events: list[config.EventSetup] = []
    for ev in [e for e in annotation.EventType if annotation.type_map[e]==annotation.Type.Interval]:
        sync_events.extend(process.get_specific_event_types(study_config, ev, check_specific_fields=['auto_code']))
    if not sync_events:
        raise ValueError('No auto-coded event start and ends are configured for the study, nothing to process')

    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        # Trial events are only coded for the reference recording, so they should be discarded here
        sync_events = [cs for cs in sync_events if cs['event_type']!=annotation.EventType.Trial]
        if not sync_events:
            raise RuntimeError(f'Nothing to do, auto-coding of event start and ends is defined only for trial events and you have a sync_ref_recording ({study_config.sync_ref_recording}), but this recording ({rec_def.name}) is another one')

    # get already coded interval(s), if any
    episodes_to_code = [cs['name'] for cs in sync_events]
    coding_file = working_dir / naming.coding_file
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), episodes_to_code)
        # flatten
        for e in episodes:
            episodes[e] = [i for iv in episodes[e] for i in iv]
    else:
        episodes = episode.get_empty_marker_dict(episodes_to_code)
    episodes_original = copy.deepcopy(episodes)

    # get marker files
    for cs in sync_events:
        for m in ('start_markers','end_markers'):
            if m not in cs['auto_code'] or not cs['auto_code'][m]:
                raise ValueError(f'No {m} configured for auto coding of sync event "{cs["name"]}"')
        all_marker_ids = set(cs['auto_code']['start_markers']+cs['auto_code']['end_markers'])
        file_missing   = [not gt_marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir).is_file() for m in all_marker_ids]
        if any(file_missing):
            file_missing = [gt_marker.get_file_name(m.m_id, m.aruco_dict_id, None) for m,miss in zip(all_marker_ids,file_missing) if miss]
            missing_str  = '\n- '.join(file_missing)
            raise FileNotFoundError(f'The following marker files were not found:\n- {missing_str}')
        markers = {m: gt_marker.read_dataframe_from_file(m.m_id, m.aruco_dict_id, working_dir) for m in all_marker_ids}
        # recode so we have a boolean with when markers are present
        markers = {m: gt_marker.code_for_presence(markers[m], allow_failed=True) for m in markers if not markers[m].empty}
        # marker presence signal only contains marker detections (True). We need to fill the gaps in between detections with False (not detected) so we have a continuous signal without gaps
        markers = {m: gt_marker.expand_detection(markers[m], fill_value=False) for m in markers}
        # now auto code indicated intervals
        # see where stretches of marker presence start and end
        marker_starts: dict[gt_marker.MarkerID,list[int]] = {}
        marker_ends  : dict[gt_marker.MarkerID,list[int]] = {}
        for m in markers:
            marker_starts[m], marker_ends[m] = gt_marker.get_appearance_starts_ends(markers[m], cs['auto_code']['max_gap_duration'], cs['auto_code']['min_duration'])
        # find potential interval starts and ends
        if len(cs['auto_code']['start_markers'])>1:
            starts = gt_marker.get_sequence_interval(marker_starts, marker_ends, cs['auto_code']['start_markers'], cs['auto_code']['max_intermarker_gap_duration'], side='end')
        else:
            starts = marker_ends  [cs['auto_code']['start_markers'][0]]
        if len(cs['auto_code'][ 'end_markers' ])>1:
            ends   = gt_marker.get_sequence_interval(marker_starts, marker_ends, cs['auto_code'][ 'end_markers' ], cs['auto_code']['max_intermarker_gap_duration'], side='start')
        else:
            ends   = marker_starts[cs['auto_code'][ 'end_markers' ][0]]
        # now match interval starts and ends
        # strategy: run through starts and find latest start that is before first end (discard ends that are before the start)
        # keep pointer into array keeping track of ends and start that have already been discarded or consumed
        # NB: this assumes starts and ends are sorted, which the above procedures should indeed deliver
        intervals: list[tuple[int,int]] = []
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
            # NB: intervals start the frame *after* the marker is last observed, and intervals end the frame *before* the marker is first observed
            intervals.append((starts[s_idx+mini]+1, ends[e_idx]-1))
            # these are consumed
            s_idx+=mini+1
            e_idx+=1
        # now insert into coding file. This just overwrites whatever is there
        if cs['name'] not in episodes:
            episodes[cs['name']] = []
        episodes[cs['name']] = [y for x in intervals for y in x]

    # early exit if nothing has changed
    if episodes==episodes_original:
        if session.get_action_states(working_dir, True)[process.Action.AUTO_CODE_EPISODES]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.AUTO_CODE_EPISODES, process_pool.State.Completed, study_config, unchanged=True)
        return

    # back up coding file if it exists
    if coding_file.is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_episode_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_EPISODES, process_pool.State.Completed, study_config)