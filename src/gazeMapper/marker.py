import pathlib
import pandas as pd
from typing import overload
from collections import defaultdict

from glassesTools import marker as gt_marker, utils

from . import naming


class Marker:
    def __init__(self, id:int, size:float):
        self.id     = id
        self.size   = size
utils.register_type(utils.CustomTypeEntry(Marker,'__config.Marker__',lambda x: {'id': x.id, 'size': x.size}, lambda x: Marker(**x)))

def get_marker_dict_from_list(markers: list[Marker]) -> dict[int,dict[str]]:
    out = {}
    for m in markers:
        out[m.id] = {'marker_size': m.size}
    return out

def load_file(marker: Marker, folder: str|pathlib.Path) -> pd.DataFrame:
    folder = pathlib.Path(folder)
    file = folder / f'{naming.marker_pose_prefix}{marker.id}.tsv'
    return pd.read_csv(file,sep='\t', dtype=defaultdict(lambda: float, **gt_marker.Pose._non_float))

@overload
def code_marker_for_presence(markers: pd.DataFrame) -> pd.DataFrame: ...
def code_marker_for_presence(markers: dict[int, pd.DataFrame]) -> dict[int, pd.DataFrame]: ...
def code_marker_for_presence(markers: pd.DataFrame|dict[int, pd.DataFrame]) -> pd.DataFrame|dict[int, pd.DataFrame]:
    if isinstance(markers,dict):
        for i in markers:
            markers[i] = _code_marker_for_presence_impl(markers[i], f'{i}_')
    else:
        markers = _code_marker_for_presence_impl(markers,'')
    return markers

def _code_marker_for_presence_impl(markers: pd.DataFrame, lbl_extra:str) -> pd.DataFrame:
    new_col_lbl = f'marker_{lbl_extra}presence'
    markers.insert(len(markers.columns),
        new_col_lbl,
        markers[[c for c in markers.columns if c not in ['frame_idx']]].notnull().all(axis='columns')
    )
    markers = markers[['frame_idx',new_col_lbl]]
    markers = markers.astype({new_col_lbl: bool}) # ensure the new column is bool
    return markers