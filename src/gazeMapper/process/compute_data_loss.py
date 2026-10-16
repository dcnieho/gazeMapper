import pathlib

import numpy as np
import pandas as pd
from ETDQualitizer import data_loss_from_invalid, effective_frequency

from glassesTools import annotation, gaze_headref, naming as gt_naming, process_pool

from .. import config, episode, naming, process, session


_columns = [
    'event', 'episode', 'start_frame', 'end_frame', 'n_samples', 'duration_s',
    'data_loss', 'effective_frequency', 'measured_frequency',
]


def run(working_dir: str | pathlib.Path, config_dir: str | pathlib.Path | None = None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir = pathlib.Path(config_dir)
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type != session.RecordingType.Eye_Tracker:
        raise ValueError(f'Compute Data Loss only supports eye tracker recordings, not {rec_def.type}')

    trial_events = process.get_specific_event_types(study_config, annotation.EventType.Trial)
    if not trial_events:
        raise ValueError('No trial-type events are configured for this study')

    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {event['name'] for event in trial_events})[0]
    gazes, _ = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR', ''])
    df = compute_trial_data_loss(gazes, episodes)
    df.to_csv(working_dir / naming.data_loss_fname, index=False, sep='\t', na_rep='nan', float_format='%.6f')

    session.update_action_states(working_dir, process.Action.COMPUTE_DATA_LOSS, process_pool.State.Completed, study_config)


def compute_trial_data_loss(gazes: dict[int, list[gaze_headref.Gaze]], episodes: episode.EpisodeMap) -> pd.DataFrame:
    rows = []
    for event_name, (event_type, intervals) in episodes.items():
        if event_type != annotation.EventType.Trial:
            continue
        for episode_number, interval in enumerate(intervals, start=1):
            if len(interval) < 2:
                continue
            start_frame, end_frame = interval[:2]
            samples = [gaze for frame_idx, frame_samples in gazes.items() if start_frame <= frame_idx <= end_frame for gaze in frame_samples]
            duration = _duration_from_timestamps(np.asarray([gaze.timestamp for gaze in samples], dtype=float))

            if samples:
                gaze_positions = np.asarray([gaze.gaze_pos_vid if gaze.gaze_pos_vid is not None else (np.nan, np.nan) for gaze in samples], dtype=float)
                horizontal = gaze_positions[:, 0].copy()
                vertical   = gaze_positions[:, 1].copy()
                horizontal[~np.isfinite(horizontal)] = np.nan
                vertical  [~np.isfinite(vertical)  ] = np.nan
                data_loss = data_loss_from_invalid(horizontal, vertical)
                effective_freq = effective_frequency(horizontal, vertical, duration) if np.isfinite(duration) and duration > 0 else np.nan
            else:
                data_loss = np.nan
                effective_freq = np.nan

            rows.append({
                'event': event_name,
                'episode': episode_number,
                'start_frame': start_frame,
                'end_frame': end_frame,
                'n_samples': len(samples),
                'duration_s': duration,
                'data_loss': data_loss,
                'effective_frequency': effective_freq,
                'measured_frequency': len(samples) / duration if np.isfinite(duration) and duration > 0 else np.nan,
            })
    return pd.DataFrame(rows, columns=_columns)


def _duration_from_timestamps(timestamps: np.ndarray) -> float:
    timestamps = np.sort(np.asarray(timestamps, dtype=float))
    timestamps = timestamps[np.isfinite(timestamps)]
    if len(timestamps) < 2:
        return np.nan
    duration_ms = timestamps[-1] - timestamps[0] + np.median(np.diff(timestamps))
    return float(duration_ms / 1000)