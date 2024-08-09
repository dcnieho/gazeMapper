import pathlib
import pandas as pd
from typing import overload, Any
from collections import defaultdict
import typeguard
import cv2

from glassesTools import marker as gt_marker, utils

from . import naming, types as _types


class Marker:
    @typeguard.typechecked
    def __init__(self,
                 id                 : int,
                 size               : float,
                 aruco_dict         : _types.ArucoDictType = cv2.aruco.DICT_4X4_250,
                 marker_border_bits : int                  = 1
                 ):
        self.id                 = id
        self.size               = size
        self.aruco_dict         = aruco_dict
        self.marker_border_bits = marker_border_bits
utils.register_type(utils.CustomTypeEntry(Marker,'__marker.Marker__',lambda x: {'id': x.id, 'size': x.size, 'aruco_dict': x.aruco_dict, 'marker_border_bits': x.marker_border_bits}, lambda x: Marker(**x)))

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
def code_marker_for_presence(markers: dict[Any, pd.DataFrame]) -> dict[Any, pd.DataFrame]: ...
def code_marker_for_presence(markers: pd.DataFrame|dict[Any, pd.DataFrame]) -> pd.DataFrame|dict[Any, pd.DataFrame]:
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

def fill_gaps_in_marker_detection(markers: pd.DataFrame, fill_value):
    min_fr_idx = markers['frame_idx'].min()
    max_fr_idx = markers['frame_idx'].max()
    new_index = pd.Index(range(min_fr_idx,max_fr_idx+1), name='frame_idx')
    return markers.set_index('frame_idx').reindex(new_index, fill_value=fill_value).reset_index()