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

    # get marker files
    markers = [marker.load_file(m.id, working_dir) for m in study_config.individual_markers if m.id in study_config.auto_code_sync_points['markers']]
    # recode so we have a boolean with when markers are present
    markers = [marker.code_marker_for_presence(m, allow_failed=True) for m in markers if not m.empty]
    if not markers:
        raise RuntimeError(f'No markers found in the marker detection files for session "{working_dir.parent.name}", recording "{working_dir.name}"')
    # fill gaps in marker detection
    for i in range(len(markers)):
        markers[i] = marker.fill_gaps_in_marker_detection(markers[i], fill_value=False)
    # see where stretches of True (marker presence) start
    marker_starts = []
    for i in range(len(markers)):
        start_frames,_ = _utils.get_marker_starts_ends(markers[i], study_config.auto_code_sync_points['max_gap_duration'], study_config.auto_code_sync_points['min_duration'])
        marker_starts.extend(start_frames)
    # insert in episodes
    [episodes[annotation.Event.Sync_Camera].append(i) for i in marker_starts if i not in episodes[annotation.Event.Sync_Camera]]

    # back up coding file if it exists
    if coding_file.is_file():
        shutil.move(coding_file, coding_file.with_stem(f'{naming.coding_file.split(".")[0]}_backup_before_sync_points_auto_code'))
    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.AUTO_CODE_SYNC, process.State.Completed, study_config)