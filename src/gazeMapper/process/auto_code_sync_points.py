import pathlib
import shutil
import copy
from typing import cast

import numpy as np
import pandas as pd

from glassesTools import annotation, marker as gt_marker, naming as gt_naming, process_pool, timestamps

from .. import config, episode, naming, process, session


def _get_transition_frames(event_type: annotation.EventType, starts, ends, last_frame: int) -> list[int]:
    if event_type == annotation.EventType.Sync_Camera:
        return [int(frame) for frame in starts]
    if event_type == annotation.EventType.Sync_Camera_Offset:
        return [int(frame) + 1 for frame in ends if frame < last_frame]
    raise ValueError(f'Unsupported event type for automatic sync coding: {event_type}')


def _get_auto_sync_transition_events(cs, starts, ends, last_frame: int):
    if cs['event_type'] == annotation.EventType.Sync_Camera:
        transition = cs['auto_code'].get('transition', 'onset')
        events = []
        if transition in ('onset', 'both'):
            events.append((cs['name'], annotation.EventType.Sync_Camera, [int(frame) for frame in starts]))
        if transition in ('offset', 'both'):
            name = config.sync_offset_event_name(cs['name']) if transition == 'both' else cs['name']
            event_type = annotation.EventType.Sync_Camera_Offset if transition == 'both' else annotation.EventType.Sync_Camera
            events.append((name, event_type, _get_transition_frames(annotation.EventType.Sync_Camera_Offset, starts, ends, last_frame)))
        return events
    return [(cs['name'], cs['event_type'], _get_transition_frames(cs['event_type'], starts, ends, last_frame))]


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    sync_events = process.get_specific_event_types(study_config, annotation.EventType.Sync_Camera, check_specific_fields=['auto_code'])
    if not sync_events:
        raise ValueError('No auto-coded camera sync events are configured for the study, nothing to process')

    # get already coded interval(s), if any
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, load_from_other_recordings=False)[0]
    episodes = annotation.flatten_annotation_dict(episodes)
    episodes_original = copy.deepcopy(episodes)
    last_frame = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname).indices[-1]

    # get marker files
    for cs in sync_events:
        if 'markers' not in cs['auto_code'] or not cs['auto_code']['markers']:
            raise ValueError(f'No markers configured for auto coding of sync event "{cs["name"]}"')
        filtered_sequences, all_marker_ids = process._filter_marker_sequences_with_marker_files(cs['name'], working_dir, {'markers': cs['auto_code']['markers']})
        marker_sequences = filtered_sequences['markers']
        if not marker_sequences:
            raise FileNotFoundError(f'No configured marker sequence for sync event "{cs["name"]}" has files for all markers')
        raw_markers: dict[gt_marker.MarkerID, pd.DataFrame] = {m: gt_marker.read_dataframe_from_file(m.m_id, m.aruco_dict_id, working_dir) for m in all_marker_ids}
        # recode so we have a boolean with when markers are present
        present_markers: dict[gt_marker.MarkerID, pd.DataFrame] = {}
        for marker_id, marker_data in raw_markers.items():
            coded_marker = gt_marker.code_for_presence(marker_data, allow_failed=True)
            if isinstance(coded_marker, dict):
                continue
            present_markers[marker_id] = coded_marker
        if not present_markers:
            raise RuntimeError(f'No markers found in the marker detection files for session "{working_dir.parent.name}", recording "{working_dir.name}"')
        # marker presence signal only contains marker detections (True). We need to fill the gaps in between detections with False (not detected) so we have a continuous signal without gaps
        detected_markers: dict[gt_marker.MarkerID, pd.DataFrame] = {}
        for marker_id, marker_data in present_markers.items():
            if marker_data.empty:
                continue
            detected_markers[marker_id] = gt_marker.expand_detection(marker_data, fill_value=False)
        if not detected_markers:
            raise RuntimeError(f'No markers found in the marker detection files for session "{working_dir.parent.name}", recording "{working_dir.name}"')

        marker_starts: dict[gt_marker.MarkerID, np.ndarray] = {m: np.array([], dtype=int) for m in all_marker_ids}
        marker_ends  : dict[gt_marker.MarkerID, np.ndarray] = {m: np.array([], dtype=int) for m in all_marker_ids}
        for marker_id, marker_data in detected_markers.items():
            appearance = gt_marker.get_appearance_starts_ends(marker_data, cs['auto_code']['max_gap_duration'], cs['auto_code']['min_duration'])
            if appearance is None:
                continue
            starts, ends = appearance
            marker_starts[marker_id] = starts
            marker_ends[marker_id] = ends

        # The helper's list annotations are stale; its implementation uses NumPy subtraction.
        sequence_starts = cast(dict[gt_marker.MarkerID, list[int]], marker_starts)
        sequence_ends = cast(dict[gt_marker.MarkerID, list[int]], marker_ends)
        marker_transitions: dict[str, tuple[annotation.EventType, set[int]]] = {}
        for sequence in marker_sequences:
            if len(sequence) > 1:
                starts = gt_marker.get_sequence_interval(sequence_starts, sequence_ends, sequence, cs['auto_code']['max_intermarker_gap_duration'], side='start')
                ends = gt_marker.get_sequence_interval(sequence_starts, sequence_ends, sequence, cs['auto_code']['max_intermarker_gap_duration'], side='end')
            else:
                starts = marker_starts[sequence[0]]
                ends = marker_ends[sequence[0]]
            for name, event_type, transitions in _get_auto_sync_transition_events(cs, starts, ends, last_frame):
                marker_transitions.setdefault(name, (event_type, set()))[1].update(transitions)

        for name, (event_type, transitions) in marker_transitions.items():
            if name not in episodes:
                episodes[name] = (event_type, [])
            episodes[name][1].extend(i for i in sorted(transitions) if i not in episodes[name][1])

    # early exit if nothing has changed
    if episodes==episodes_original:
        if session.get_action_states(working_dir, True)[process.Action.AUTO_CODE_SYNC]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.AUTO_CODE_SYNC, process_pool.State.Completed, study_config, unchanged=True)
        return

    # back up coding file if it exists
    if (coding_file := working_dir/naming.coding_file).is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_sync_points_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_SYNC, process_pool.State.Completed, study_config)