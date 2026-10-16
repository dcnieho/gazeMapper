import pathlib
from collections import defaultdict
import warnings

import numpy as np
import pandas as pd
from ETDQualitizer import data_loss_from_invalid, effective_frequency, precision_using_moving_window

from glassesTools import annotation, gaze_headref, gaze_worldref, naming as gt_naming, process_pool

from .. import config, episode, marker_assembly, naming, process, session


_base_columns = [
    'event', 'episode', 'start_frame', 'end_frame', 'n_samples', 'n_valid_gaze_samples',
    'duration_s', 'data_loss', 'effective_frequency', 'measured_gaze_frequency',
    'measured_camera_frequency',
]


def run(working_dir: str | pathlib.Path, config_dir: str | pathlib.Path | None = None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir = pathlib.Path(config_dir)
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type != session.RecordingType.Eye_Tracker:
        raise ValueError(f'Compute data quality only supports eye tracker recordings, not {rec_def.type}')

    trial_events = process.get_specific_event_types(study_config, annotation.EventType.Trial)
    if not trial_events:
        raise ValueError('No trial-type events are configured for this study')

    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {event['name'] for event in trial_events})[0]
    gazes, _ = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR', ''])
    camera_timestamps = _read_camera_timestamps(working_dir / gt_naming.frame_timestamps_fname)

    event_setups = {
        event['name']: event['trial_data_quality_setup']
        for event in trial_events
        if event.get('trial_data_quality_setup') and event['trial_data_quality_setup'].get('planes')
    }
    intervals_per_plane: dict[str, list[list[int]]] = defaultdict(list)
    plane_unit_to_mm = {}
    plane_defs = {plane.name: plane for plane in study_config.marker_assemblies}
    for event_name, setup in event_setups.items():
        for plane_name in setup['planes']:
            intervals_per_plane[plane_name].extend(episodes[event_name][1])
            plane_def = plane_defs[plane_name]
            plane_unit_to_mm[plane_name] = marker_assembly.unit_to_mm(plane_def.unit)

    plane_gazes = {}
    for plane_name, intervals in intervals_per_plane.items():
        if intervals:
            gaze_file = working_dir / f'{naming.world_gaze_prefix}{plane_name}.tsv'
            plane_gazes[plane_name] = gaze_worldref.read_dict_from_file(
                gaze_file,
                episodes=intervals,
                ts_column_suffixes=['VOR', ''],
            )
        else:
            plane_gazes[plane_name] = {}

    df = compute_trial_data_quality(gazes, episodes, event_setups, plane_gazes, plane_unit_to_mm, camera_timestamps)
    df.to_csv(working_dir / naming.data_quality_fname, index=False, sep='\t', na_rep='nan', float_format='%.6f')

    session.update_action_states(working_dir, process.Action.COMPUTE_DATA_QUALITY, process_pool.State.Completed, study_config)


def _read_camera_timestamps(file_name: str | pathlib.Path) -> dict[int, float]:
    frame_timestamps = pd.read_csv(file_name, delimiter='\t')
    if 'frame_idx' not in frame_timestamps.columns:
        index_column = frame_timestamps.columns[0]
        if str(index_column).startswith('Unnamed:'):
            frame_timestamps = frame_timestamps.rename(columns={index_column: 'frame_idx'})
        else:
            raise ValueError(f'{file_name} is missing the frame_idx column')
    if 'timestamp' not in frame_timestamps.columns:
        raise ValueError(f'{file_name} is missing the timestamp column')
    return dict(zip(frame_timestamps['frame_idx'].astype(int), frame_timestamps['timestamp'].astype(float)))


def compute_trial_data_quality(
    gazes: dict[int, list[gaze_headref.Gaze]],
    episodes: episode.EpisodeMap,
    event_setups: dict[str, config.TrialDataQualitySetup] | None = None,
    plane_gazes: dict[str, dict[int, list[gaze_worldref.Gaze]]] | None = None,
    plane_unit_to_mm: dict[str, float] | None = None,
    camera_timestamps: dict[int, float] | None = None,
) -> pd.DataFrame:
    event_setups = event_setups or {}
    plane_gazes = plane_gazes or {}
    plane_unit_to_mm = plane_unit_to_mm or {}
    plane_names = sorted({p for setup in event_setups.values() for p in setup.get('planes', set())})
    plane_columns = [f'{plane}_{metric}' for plane in plane_names for metric in ('mapped_data_loss_percent', 'mapped_data_loss_valid_percent', 'rms_mm', 'std_mm')]
    plane_points = {plane: _index_plane_points(data) for plane, data in plane_gazes.items()}
    rows = []
    for event_name, (event_type, intervals) in episodes.items():
        if event_type != annotation.EventType.Trial:
            continue
        for episode_number, interval in enumerate(intervals, start=1):
            if len(interval) < 2:
                continue
            start_frame, end_frame = interval[:2]
            samples = sorted(
                [(frame_idx, gaze) for frame_idx, frame_samples in gazes.items() if start_frame <= frame_idx <= end_frame for gaze in frame_samples],
                key=lambda sample: sample[1].timestamp,
            )
            gaze_timestamps = np.asarray([gaze.timestamp for _, gaze in samples], dtype=float)
            episode_camera_timestamps = np.asarray([
                timestamp
                for frame_idx, timestamp in (camera_timestamps or {}).items()
                if start_frame <= frame_idx <= end_frame
            ], dtype=float)
            duration = _duration_from_timestamps(
                episode_camera_timestamps if camera_timestamps is not None else gaze_timestamps
            )

            if samples:
                gaze_positions = np.asarray([gaze.gaze_pos_vid if gaze.gaze_pos_vid is not None else (np.nan, np.nan) for _, gaze in samples], dtype=float)
                horizontal = gaze_positions[:, 0].copy()
                vertical   = gaze_positions[:, 1].copy()
                horizontal[~np.isfinite(horizontal)] = np.nan
                vertical  [~np.isfinite(vertical)  ] = np.nan
                data_loss = data_loss_from_invalid(horizontal, vertical)
                effective_freq = effective_frequency(horizontal, vertical, duration) if np.isfinite(duration) and duration > 0 else np.nan
            else:
                horizontal = np.asarray([], dtype=float)
                vertical = np.asarray([], dtype=float)
                data_loss = np.nan
                effective_freq = np.nan

            valid_head_gaze = np.isfinite(horizontal) & np.isfinite(vertical)
            measured_gaze_frequency = len(samples) / duration if np.isfinite(duration) and duration > 0 else np.nan
            row = {
                'event': event_name,
                'episode': episode_number,
                'start_frame': start_frame,
                'end_frame': end_frame,
                'n_samples': len(samples),
                'n_valid_gaze_samples': int(np.count_nonzero(valid_head_gaze)),
                'duration_s': duration,
                'data_loss': data_loss,
                'effective_frequency': effective_freq,
                'measured_gaze_frequency': measured_gaze_frequency,
                'measured_camera_frequency': len(episode_camera_timestamps) / duration if len(episode_camera_timestamps) and np.isfinite(duration) and duration > 0 else np.nan,
            }

            setup = event_setups.get(event_name)
            for plane_name in setup.get('planes', set()) if setup else ():
                points_by_sample = plane_points.get(plane_name, {})
                use_count = defaultdict(int)
                positions = []
                for frame_idx, gaze in samples:
                    key = _sample_key(frame_idx, gaze.timestamp)
                    sample_points = points_by_sample.get(key, ())
                    point_idx = use_count[key]
                    use_count[key] += 1
                    positions.append(sample_points[point_idx] if point_idx < len(sample_points) else (np.nan, np.nan))

                positions = np.asarray(positions, dtype=float).reshape((-1, 2))
                mapped_valid = np.isfinite(positions).all(axis=1) if len(positions) else np.asarray([], dtype=bool)
                mapped_samples = mapped_valid & valid_head_gaze
                valid_count = int(np.count_nonzero(valid_head_gaze))
                row[f'{plane_name}_mapped_data_loss_percent']       = 100 * (len(samples) - np.count_nonzero(mapped_valid)) / len(samples) if samples else np.nan
                row[f'{plane_name}_mapped_data_loss_valid_percent'] = 100 * (valid_count - np.count_nonzero(mapped_samples)) / valid_count if valid_count else np.nan

                coordinates_mm = positions * plane_unit_to_mm[plane_name] if len(positions) else positions
                coordinates_mm[~mapped_samples] = np.nan
                setup = event_setups[event_name]
                window_length = _window_length(measured_gaze_frequency, setup['window_duration_s'])
                window_step = _window_step_length(measured_gaze_frequency, setup['window_step_s'])
                if window_length is not None and window_step is not None:
                    row[f'{plane_name}_rms_mm'] = _precision_moving_window(coordinates_mm, window_length, window_step, 'RMS-S2S')
                    row[f'{plane_name}_std_mm'] = _precision_moving_window(coordinates_mm, window_length, window_step, 'STD')
                else:
                    row[f'{plane_name}_rms_mm'] = np.nan
                    row[f'{plane_name}_std_mm'] = np.nan
            rows.append(row)
    return pd.DataFrame(rows, columns=_base_columns + plane_columns)


def _sample_key(frame_idx: int, timestamp: float) -> tuple[int, float]:
    return int(frame_idx), float(timestamp)


def _index_plane_points(gazes: dict[int, list[gaze_worldref.Gaze]]) -> dict[tuple[int, float], list[np.ndarray]]:
    points = defaultdict(list)
    for frame_idx, frame_samples in gazes.items():
        for gaze in frame_samples:
            point = _mapped_plane_position(gaze)
            points[_sample_key(frame_idx, gaze.timestamp)].append(point)
    return points


def _mapped_plane_position(gaze: gaze_worldref.Gaze) -> np.ndarray:
    point = gaze.get_gaze_point(gaze_worldref.Type.Scene_Video_Position, reference_frame='plane')
    if point is not None:
        point = np.asarray(point, dtype=float)
        if point.shape == (2,) and np.isfinite(point).all():
            return point
    return np.asarray([np.nan, np.nan])


def _window_length(measured_gaze_frequency: float, window_duration_s: float) -> int | None:
    if not np.isfinite(measured_gaze_frequency) or measured_gaze_frequency <= 0:
        return None
    requested_length = int(round(window_duration_s * measured_gaze_frequency))
    if requested_length < 8:
        warnings.warn(
            f'Moving window duration corresponds to {requested_length} samples given the measured gaze frequency of {measured_gaze_frequency} Hz; using the minimum of 8 samples.',
            UserWarning,
            stacklevel=2,
        )
    return max(8, requested_length)


def _window_step_length(measured_gaze_frequency: float, window_step_s: float) -> int | None:
    if not np.isfinite(measured_gaze_frequency) or measured_gaze_frequency <= 0:
        return None
    return max(1, int(round(window_step_s * measured_gaze_frequency)))


def _precision_moving_window(coordinates: np.ndarray, window_length: int, window_step: int, metric: str) -> float:
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=RuntimeWarning)
        return precision_using_moving_window(coordinates[:, 0], coordinates[:, 1], window_length, metric)


def _duration_from_timestamps(timestamps: np.ndarray) -> float:
    timestamps = np.sort(np.asarray(timestamps, dtype=float))
    timestamps = timestamps[np.isfinite(timestamps)]
    if len(timestamps) < 2:
        return np.nan
    duration_ms = timestamps[-1] - timestamps[0] + np.median(np.diff(timestamps))
    return float(duration_ms / 1000)