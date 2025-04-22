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
