import pathlib
import numpy as np
import shutil
import copy

from glassesTools import annotation, marker as gt_marker, process_pool, validation

from .. import config, episode, naming, plane, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    events: list[config.EventSetup] = []
    for ev in [e for e in annotation.EventType if annotation.type_map[e]==annotation.Type.Interval]:
        events.extend(process.get_specific_event_types(study_config, ev, check_specific_fields=['auto_code']))
    if not events:
        raise ValueError('No auto-coded event start and ends are configured for the study, nothing to process')

    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        # Trial events are only coded for the reference recording, so they should be discarded here
        events = [cs for cs in events if cs['event_type']!=annotation.EventType.Trial]
        if not events:
            raise RuntimeError(f'Nothing to do, auto-coding of event start and ends is defined only for trial events and you have a sync_ref_recording ({study_config.sync_ref_recording}), but this recording ({rec_def.name}) is another one')

    # get already coded interval(s), if any
    episodes_to_code = [cs['name'] for cs in events if not (wr:=cs.get('which_recordings',set())) or working_dir.name in wr]
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, episodes_to_code, load_from_other_recordings=False)[0]
    episodes = annotation.flatten_annotation_dict(episodes)
    episodes_original = copy.deepcopy(episodes)

    # get marker files
    for cs in events:
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
        # if this is a dynamic validation episode, and the option to split consecutive repetitions is on, refine the interval now
        if cs['event_type']==annotation.EventType.Validate and cs['validation_setup'] and cs['validation_setup']['dynamic_split_consecutive']:
            # This deals with multiple consecutive marker presentation without intervening segmentation markers. It is assumed that all targets are shown in
            # multiple runs of all individual targets (possibly random) order (so not fully randomized over all target presentations, then can't split).
            # Intervals are then split smaller after each run of all targets has been presented.
            p = list(cs['planes'])[0]
            plane_def = [pl for pl in study_config.planes if pl.name==p][0]
            if plane_def.type!=plane.Type.GlassesValidator:
                raise ValueError(f'Plane {p} is not a {plane.Type.GlassesValidator.value} plane')
            validation_plane = plane.get_plane_from_definition(plane_def, config_dir/p)
            all_marker_observations_per_target, markers_per_target = validation.dynamic.get_marker_observations(validation_plane, working_dir)
            for e in intervals:
                # make local copy of marker_observations, containing only the current episode
                marker_observations_per_target = {t:mo.loc[e[0]:e[1],:] for t,mo in all_marker_observations_per_target.items()}
                # check we have data for at least one of the markers for a given target
                failed = False
                for t in marker_observations_per_target:
                    if marker_observations_per_target[t].empty:
                        missing_str  = '\n- '.join([gt_marker.marker_ID_to_str(m) for m in markers_per_target[t]])
                        print(f'None of the markers for target {t} were observed during the episode from frame {e[0]} to frame {e[1]}:\n- {missing_str}')
                        failed = True
                        break
                if failed:
                    continue

                # marker presence signal only contains marker detections (True). We need to fill the gaps in between detections with False (not detected) so we have a continuous signal without gaps
                marker_observations_per_target = {t: gt_marker.expand_detection(marker_observations_per_target[t], fill_value=False) for t in marker_observations_per_target}

                # for each target, see when it is presented using the marker presence signal
                target_observation_map: list[tuple[tuple[int,int],int]] = []  # ((start_frame,end_frame), target_id)
                for t in marker_observations_per_target:
                    start, end = gt_marker.get_appearance_starts_ends(marker_observations_per_target[t], cs['validation_setup']['dynamic_max_gap_duration'], cs['validation_setup']['dynamic_min_duration'])
                    for s,en in zip(start,end):
                        target_observation_map.append(((s,en), t))
                # sort on start frame
                target_observation_map.sort(key=lambda x: x[0][0])
                # check each target occurs equally often
                target_counts = {t:0 for t in markers_per_target}
                for _,t in target_observation_map:
                    target_counts[t]+=1
                counts_set = set(target_counts.values())
                if len(counts_set)!=1:
                    print(f'Not all targets were presented equally often during the episode from frame {e[0]} to frame {e[1]}, cannot split consecutive repetitions')
                    continue
                # check each target is only presented once per consecutive run
                n_repetitions = counts_set.pop()
                n_targets     = len(markers_per_target)
                for rep in range(1, n_repetitions):
                    si, ei = (rep-1)*n_targets, rep*n_targets
                    targets_in_run = set(t for _,t in target_observation_map[si:ei])
                    if len(targets_in_run)!=n_targets:  # not all targets present in this run
                        print(f'Not all targets were presented during repetition {rep} in the episode from frame {e[0]} to frame {e[1]}, cannot split consecutive repetitions')
                        failed = True
                        break
                # find break points between consecutive runs
                for rep in range(1, n_repetitions):
                    # find the index in target_observation_map where the next repetition starts
                    break_idx = rep * n_targets
                    # get the frame to split at: halfway between the end of the last target in the previous run and the start of the first target in this run
                    split_frame = (target_observation_map[break_idx-1][0][1] + target_observation_map[break_idx][0][0]) // 2
                    # now adjust intervals accordingly
                    # find which interval this split frame falls into
                    for int_idx in range(len(intervals)):
                        if intervals[int_idx][0]<=split_frame<=intervals[int_idx][1]:
                            # split here
                            old_int = intervals[int_idx]
                            intervals[int_idx] = (old_int[0], split_frame)
                            intervals.insert(int_idx+1, (split_frame+1, old_int[1]))
                            break
        # now insert into coding file. This just overwrites whatever is there
        if cs['name'] not in episodes:
            episodes[cs['name']] = []
        episodes[cs['name']] = (cs['event_type'], [int(y) for x in intervals for y in x])

    # early exit if nothing has changed
    if episodes==episodes_original:
        if session.get_action_states(working_dir, True)[process.Action.AUTO_CODE_EPISODES]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.AUTO_CODE_EPISODES, process_pool.State.Completed, study_config, unchanged=True)
        return

    # back up coding file if it exists
    if (coding_file := working_dir/naming.coding_file).is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_episode_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_EPISODES, process_pool.State.Completed, study_config)