import pathlib
import shutil
import copy

from glassesTools import annotation, marker as gt_marker, process_pool

from .. import config, episode, naming, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    if not study_config.auto_code_sync_points:
        raise ValueError(f'No automatic sync point detection is defined for this study, nothing to do')

    # get already coded interval(s), if any
    coding_file = working_dir / naming.coding_file
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), study_config.episodes_to_code)
        # flatten
        for e in episodes:
            episodes[e] = [i for iv in episodes[e] for i in iv]
    else:
        episodes = episode.get_empty_marker_dict(study_config.episodes_to_code)
    episodes_original = copy.deepcopy(episodes)

    # get marker files
    markers = [gt_marker.read_dataframe_from_file(m.m_id, m.aruco_dict_id, working_dir) for m in study_config.auto_code_sync_points['markers'] if gt_marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir).is_file()]
    if not markers:
        missing_str = '\n- '.join([gt_marker.get_file_name(m.m_id, m.aruco_dict_id, None) for m in study_config.auto_code_sync_points['markers']])
        raise FileNotFoundError(f'None of the following marker files were found:\n- {missing_str}')
    # recode so we have a boolean with when markers are present
    markers = [gt_marker.code_for_presence(m, allow_failed=True) for m in markers if not m.empty]
    if not markers:
        raise RuntimeError(f'No markers found in the marker detection files for session "{working_dir.parent.name}", recording "{working_dir.name}"')
    # marker presence signal only contains marker detections (True). We need to fill the gaps in between detections with False (not detected) so we have a continuous signal without gaps
    markers = [gt_marker.expand_detection(m, fill_value=False) for m in markers]
    # see where stretches of True (marker presence) start
    marker_starts = [s for m in markers for s in gt_marker.get_appearance_starts_ends(m, study_config.auto_code_sync_points['max_gap_duration'], study_config.auto_code_sync_points['min_duration'])[0]]
    # insert in episodes
    [episodes[annotation.Event.Sync_Camera].append(i) for i in marker_starts if i not in episodes[annotation.Event.Sync_Camera]]

    # early exit if nothing has changed
    if episodes==episodes_original:
        if session.get_action_states(working_dir, True)[process.Action.AUTO_CODE_SYNC]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.AUTO_CODE_SYNC, process_pool.State.Completed, study_config, unchanged=True)
        return

    # back up coding file if it exists
    if coding_file.is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_sync_points_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_SYNC, process_pool.State.Completed, study_config)