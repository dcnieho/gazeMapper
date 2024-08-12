import pandas as pd
import numpy as np
from typing import Any

from glassesTools import data_files


def insert_ts_fridx_in_df(df: pd.DataFrame, object: Any, suffix: str, ts: np.ndarray, fridxs: np.ndarray):
    # figure out where
    cols = [c for cs in data_files.uncompress_columns(object._columns_compressed) for c in cs if c in df.columns or c in [f'timestamp_{suffix}',f'frame_idx_{suffix}']]
    ts_idx = cols.index(f'timestamp_{suffix}')
    fr_idx = cols.index(f'frame_idx_{suffix}')
    # figure out what order
    cols = ['timestamp','frame_idx']
    if ts_idx>fr_idx:
        cols = [cols[1], cols[0]]
    # insert
    for c in cols:
        v = ts if c=='timestamp' else fridxs
        i = ts_idx if c=='timestamp' else fr_idx
        if f'{c}_{suffix}' in df.columns:
            df[f'{c}_{suffix}'] = v
        else:
            df.insert(i, f'{c}_{suffix}', v)

    return df

def get_marker_starts_ends(m: pd.DataFrame, max_gap_duration: int, min_duration: int):
    vals   = np.pad(m['marker_presence'].values.astype(int), (1, 1), 'constant', constant_values=(0, 0))
    d      = np.diff(vals)
    starts = np.nonzero(d == 1)[0]
    ends   = np.nonzero(d == -1)[0]
    gaps   = starts[1:]-ends[:-1]
    # fill gaps in marker detection
    gapi   = np.nonzero(gaps<=max_gap_duration)[0]
    starts = np.delete(starts,gapi+1)
    ends   = np.delete(ends,gapi)
    # remove too short
    lengths= ends-starts
    shorti = np.nonzero(lengths<=min_duration)[0]
    starts = np.delete(starts,shorti)
    ends   = np.delete(ends,shorti)
    # turn first and last frames into frame_idx values
    return m.loc[starts,'frame_idx'].values, m.loc[ends-1,'frame_idx'].values # NB: -1 so that ends point to last frame during which marker was last seen (and to not index out of the array)

def get_trial_from_markers(starts: dict[int,list[int]], ends: dict[int,list[int]], pattern: list[int], max_intermarker_gap_duration: int, side='start') -> np.ndarray:
    # find marker pattern (sequence of markers following in right order with gap no longer than max_intermarker_gap_duration)
    pairs: list[tuple[int,int]] = []
    for i in range(len(ends[pattern[0]])):
        end_idx = i
        for j in range(len(pattern)-1):
            if end_idx is None:
                break
            end     = ends[pattern[j]][end_idx]
            gaps    = starts[pattern[j+1]]-end
            end_idx = get_minimum_gap_marker(gaps,max_intermarker_gap_duration)
        if end_idx is not None:
            pairs.append((starts[pattern[0]][i], ends[pattern[-1]][end_idx]))

    idx = 0 if side=='start' else 1
    return np.array([p[idx] for p in pairs])

def get_minimum_gap_marker(gaps: np.ndarray, max_intermarker_gap_duration: int):
    gapi    = np.nonzero(np.logical_and(gaps>=0, gaps<=max_intermarker_gap_duration))[0]
    if gapi.size:
        # if there are multiple that qualify, take the smallest gap
        mini    = np.argmin(gaps[gapi])
        return gapi[mini]
    return None