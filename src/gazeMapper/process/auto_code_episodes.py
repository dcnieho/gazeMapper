import pathlib
import numpy as np
import pandas as pd
import shutil
import copy
import warnings

from glassesTools import annotation, marker as gt_marker, naming as gt_naming, process_pool, timestamps, validation

from .. import config, episode, marker, naming, marker_assembly, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    events: list[config.EventSetup] = []
    for ev in [e for e in annotation.EventType if annotation.type_map[e]==annotation.Type.Interval]:
        events.extend(process.get_specific_event_types(study_config, ev, check_specific_fields=['auto_code']))
    # remove events that are not configured for this recording
    events = [cs for cs in events if cs['which_recordings'] is None or working_dir.name in cs['which_recordings']]
    if not events:
        raise ValueError('No auto-coded event start and ends are configured for the study, nothing to process')

    # get already coded interval(s), if any
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, load_from_other_recordings=False)[0]
    episodes = annotation.flatten_annotation_dict(episodes)
    episodes_original = copy.deepcopy(episodes)

    # get marker files
    for cs in events:
        start_sequence_ends_previous = cs['auto_code'].get('start_sequence_ends_previous', False)
        if not cs['auto_code'].get('start_markers'):
            raise ValueError(f'No start_markers configured for auto coding of event "{cs["name"]}"')
        configured_end_sequences = cs['auto_code'].get('end_markers') or []
        if not configured_end_sequences and not start_sequence_ends_previous:
            raise ValueError(f'No end_markers configured for auto coding of event "{cs["name"]}"')
        filtered_sequences, all_marker_ids = marker.filter_marker_sequences_with_marker_files(cs['name'], working_dir, {'start_markers': cs['auto_code']['start_markers'], 'end_markers': configured_end_sequences})
        start_marker_sequences = filtered_sequences['start_markers']
        end_marker_sequences = filtered_sequences['end_markers']
        if not start_marker_sequences:
            raise FileNotFoundError(f'None of the configured start marker sequences for auto-coded event "{cs["name"]}" were found, missing files for some markers')
        if not end_marker_sequences and not start_sequence_ends_previous:
            raise FileNotFoundError(f'None of the configured end marker sequences for auto-coded event "{cs["name"]}" were found, missing files for some markers')
        if not end_marker_sequences and start_sequence_ends_previous:
            warnings.warn(
                f'{cs["name"]}: No end marker sequences were found. Start sequences will end the previous episode, '
                'but the final episode will not be coded without an end sequence.',
                process_pool.ProcessingWarning,
            )
        marker_starts, marker_ends = marker.get_marker_appearance_starts_ends(all_marker_ids, working_dir, cs['auto_code']['max_gap_duration'], cs['auto_code']['min_duration'])

        def _resolve_sequence_points(sequences: marker.MarkerSequenceList, *, side: str) -> np.ndarray:
            points: list[int] = []
            for sequence in sequences:
                if len(sequence) > 1:
                    points.extend(gt_marker.get_sequence_interval(marker_starts, marker_ends, sequence, cs['auto_code']['max_intermarker_gap_duration'], side=side))
                elif side == 'end':
                    points.extend(marker_ends[sequence[0]])
                else:
                    points.extend(marker_starts[sequence[0]])
            # remove duplicate intervals, remain sorted
            return np.array(sorted({point for point in points}), dtype=int)

        # find potential interval starts and ends
        starts = _resolve_sequence_points(start_marker_sequences, side='end')
        ends   = _resolve_sequence_points(end_marker_sequences, side='start')
        start_sequence_onsets = _resolve_sequence_points(start_marker_sequences, side='start') if start_sequence_ends_previous else None
        # now match interval starts and ends
        # strategy: run through starts and find latest start that is before first end (discard ends that are before the start)
        # keep pointer into array keeping track of ends and start that have already been discarded or consumed
        # NB: this assumes starts and ends are sorted, which the above procedures should indeed deliver
        if start_sequence_onsets is not None:
            ends = np.unique(np.concatenate((ends, start_sequence_onsets)))
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
        # if no intervals found, see if there are user-coded intervals to fall back on
        if not intervals and (eps:=episodes.get(cs['name'], None)):
            intervals = [(eps[1][i], eps[1][i+1]) for i in range(0, len(eps[1]), 2)]
        # if this is a dynamic validation episode, and the option to split consecutive repetitions is on, refine the interval now
        if cs['event_type']==annotation.EventType.Validate and cs['validation_setup'] and cs['validation_setup']['dynamic_split_consecutive']:
            # This deals with multiple consecutive marker presentation without intervening segmentation markers. It is assumed that all targets are shown in
            # multiple runs of all individual targets (possibly random) order (so not fully randomized over all target presentations, then can't split).
            # Intervals are then split smaller after each run of all targets has been presented.
            coding: pd.DataFrame | None = None
            all_marker_observations_per_target: dict[int, pd.DataFrame] | None = None
            markers_per_target: dict[int, list[gt_marker.MarkerID]] | None = None
            if (fname:=working_dir/f'{naming.validation_prefix}{cs["name"]}_fixation_assignment_override.tsv').exists():
                coding = pd.read_csv(fname, delimiter='\t', dtype={'target':int, 'marker_interval':int},index_col=None)
                has_override = True
            else:
                p = list(cs['marker_assemblies'])[0]
                plane_def = [pl for pl in study_config.marker_assemblies if pl.name==p][0]
                if plane_def.type!=marker_assembly.Type.GlassesValidator:
                    raise ValueError(f'Plane {p} is not a {marker_assembly.Type.GlassesValidator.value} plane')
                validation_plane = marker_assembly.get_marker_assembly_from_definition(plane_def, config_dir/p)
                all_marker_observations_per_target, markers_per_target = validation.dynamic.get_marker_observations(validation_plane, working_dir, cs['name'])
                has_override = False
            for e in intervals.copy():
                if not has_override:
                    assert all_marker_observations_per_target is not None and markers_per_target is not None
                    # make local copy of marker_observations, containing only the current episode
                    marker_observations_per_target = {t:mo.loc[e[0]:e[1],:] for t,mo in all_marker_observations_per_target.items()}
                    # check we have data for at least one of the markers for a given target
                    failed = False
                    for t in marker_observations_per_target:
                        if marker_observations_per_target[t].empty:
                            missing_str  = '\n- '.join([gt_marker.marker_ID_to_str(m) for m in markers_per_target[t]])
                            warnings.warn(f'{cs["name"]}: None of the markers for target {t} were observed during the episode from frame {e[0]} to frame {e[1]}:\n- {missing_str}, skipping dynamic splitting of this episode', process_pool.ProcessingWarning)
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
                    target_counts = {t:0 for t in all_marker_observations_per_target}
                    for _,t in target_observation_map:
                        target_counts[t]+=1
                else:
                    assert coding is not None
                    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)
                    target_observation_map = []
                    for _, row in coding.iterrows():
                        start_frame = video_ts.find_frame(row['start_timestamp'])
                        end_frame   = video_ts.find_frame(row['end_timestamp'])
                        target_observation_map.append(((start_frame, end_frame), int(row['target'])))
                    target_counts = coding['target'].value_counts().to_dict()
                counts_set = set(target_counts.values())
                if len(counts_set)!=1:
                    warnings.warn(f'{cs["name"]}: Not all targets were presented equally often during the episode from frame {e[0]} to frame {e[1]}, cannot split consecutive repetitions', process_pool.ProcessingWarning)
                    continue
                # check each target is only presented once per consecutive run
                n_repetitions = counts_set.pop()
                n_targets     = len(target_counts)
                for rep in range(1, n_repetitions):
                    si, ei = (rep-1)*n_targets, rep*n_targets
                    targets_in_run = set(t for _,t in target_observation_map[si:ei])
                    if len(targets_in_run)!=n_targets:  # not all targets present in this run
                        warnings.warn(f'{cs["name"]}: Not all targets were presented during repetition {rep} in the episode from frame {e[0]} to frame {e[1]}, cannot split consecutive repetitions', process_pool.ProcessingWarning)
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
            episodes[cs['name']] = (cs['event_type'], [])
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