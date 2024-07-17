import pathlib
import numpy as np
import pandas as pd

from .. import config, episode, marker, naming, session


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    study_config = config.Study.load_from_json(config_dir)
    assert study_config.auto_code_sync_points or study_config.auto_code_trials_episodes, f'No automatic sync point detection or trial episode coding is defined for this study, nothing to do'
    rec_def = study_config.session_def.get_recording_def(working_dir.name)

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
            min_fr_idx, max_fr_idx = markers[i]['frame_idx'].min(), markers[i]['frame_idx'].max()
            new_index = pd.Index(range(min_fr_idx,max_fr_idx+1), name='frame_idx')
            markers[i] = markers[i].set_index('frame_idx').reindex(new_index, fill_value=False).reset_index()
        # see where stretches of True (marker presence) start
        marker_starts = []
        for i in range(len(markers)):
            vals = np.pad(markers[i]['marker_presence'].values.astype(int), (1, 1), 'constant', constant_values=(0, 0))
            d    = np.diff(vals)
            starts = np.where(d == 1)[0]
            ends   = np.where(d == -1)[0]
            gaps   = starts[1:]-ends[:-1]
            # fill gaps in marker detection
            gapi   = np.where(gaps<=study_config.auto_code_sync_points['max_gap_duration'])[0]
            starts = np.delete(starts,gapi+1)
            ends   = np.delete(ends,gapi)
            # remove too short
            lengths= ends-starts
            shorti = np.where(lengths<=study_config.auto_code_sync_points['min_duration'])[0]
            starts = np.delete(starts,shorti)
            ends   = np.delete(ends,shorti)
            # first frame is turn into frame_idx value
            marker_starts.extend(markers[i].loc[starts,'frame_idx'])
        # insert in episodes
        [episodes[episode.Event.Sync_Camera].append(i) for i in marker_starts if i not in episodes[episode.Event.Sync_Camera]]

    # automatic trial episode coding
    if study_config.auto_code_trials_episodes and (not study_config.sync_ref_recording or rec_def.name==study_config.sync_ref_recording):
        pass

    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)