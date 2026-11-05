import copy
import math

import numpy as np

from glassesTools import gaze_headref


def _aggregate_position(gazes: list[gaze_headref.Gaze], field: str, method: str) -> np.ndarray|None:
    values = [np.asarray(value, dtype=float) for gaze in gazes if (value:=getattr(gaze, field)) is not None]
    if not values:
        return None

    stacked = np.stack(values)
    if method=='mean':
        finite = np.isfinite(stacked)
        total = np.where(finite, stacked, 0).sum(axis=0)
        count = finite.sum(axis=0)
        aggregated = np.full(stacked.shape[1:], np.nan)
        np.divide(total, count, out=aggregated, where=count>0)
        return aggregated

    # if not mean, then must be median
    return np.ma.median(np.ma.masked_invalid(stacked), axis=0).filled(np.nan)


def reduce_gaze_samples(gazes: list[gaze_headref.Gaze], method: str, frame_timestamp: float) -> list[gaze_headref.Gaze]:
    if method not in ('all', 'mean', 'median', 'middle', 'closest_time'):
        raise ValueError(f'Unknown gaze sample method: {method}')
    if not gazes or len(gazes)==1 or method=='all':
        return gazes
    if method=='middle':
        return [gazes[len(gazes)//2]]
    if method=='closest_time':
        if not math.isfinite(frame_timestamp):
            raise ValueError('Cannot select the gaze sample closest to a non-finite frame timestamp.')
        timestamped_gazes = [(abs(gaze.timestamp-frame_timestamp), i, gaze) for i, gaze in enumerate(gazes) if math.isfinite(gaze.timestamp)]
        if not timestamped_gazes:
            raise ValueError('Cannot select the gaze sample closest to the frame timestamp: no samples have a finite timestamp.')
        return [min(timestamped_gazes, key=lambda item: (item[0], item[1]))[2]]

    # take mean or median. Do not reduce gaze sample metadata (e.g. timestamp, VOR, etc.), only gaze positions. For the other (timestamps, gaze vectors, etc), we keep the values from the middle sample in the list
    representative = copy.copy(gazes[len(gazes)//2])
    for field in ('gaze_pos_vid', 'gaze_pos_3d'):
        aggregated = _aggregate_position(gazes, field, method)
        if aggregated is not None:
            setattr(representative, field, aggregated)
    return [representative]
