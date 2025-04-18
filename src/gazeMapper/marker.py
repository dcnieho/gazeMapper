import pathlib
import pandas as pd
from typing import overload, Any
from collections import defaultdict
import typeguard
import cv2
import inspect

from glassesTools import aruco, json, marker as gt_marker

from . import naming, type_utils


class Marker:
    @typeguard.typechecked
    def __init__(self,
                 m_id               : int,
                 detect_only        : bool,         # if true, pose will not be determined and only marker presence is detected. That means marker size is not needed
                 size               : float|None                = None,
                 aruco_dict_id      : type_utils.ArucoDictType  = cv2.aruco.DICT_4X4_250,
                 marker_border_bits : int                       = 1
                 ):
        self.id                 = m_id
        self.detect_only        = detect_only
        self.size               = size
        self.aruco_dict_id      = aruco_dict_id
        self.marker_border_bits = marker_border_bits

    def _to_dict(self) -> dict[str,Any]:
        out = {'id': self.id, 'detect_only': self.detect_only}
        for f in ['size', 'aruco_dict_id', 'marker_border_bits']:
            if (val:=getattr(self,f))!=marker_defaults[f]:
                out[f] = val
        if 'aruco_dict_id' in out:
            # print dictionary names for markers instead of hard to understand id
            out['aruco_dict_id'] = aruco.dict_to_str[out['aruco_dict_id']]
        return out

    @staticmethod
    def _from_dict(kwargs: dict[str,Any]):
        # backwards compatibility
        if 'detect_only' not in kwargs:
            kwargs['detect_only'] = False
        if 'id' in kwargs:
            kwargs['m_id'] = kwargs.pop('id')
        if 'aruco_dict' in kwargs:
            kwargs['aruco_dict_id'] = kwargs.pop('aruco_dict')
        if 'aruco_dict_id' in kwargs:
            # dictionary names might be stored as strings, turn back into int
            if isinstance(kwargs['aruco_dict_id'],str):
                kwargs['aruco_dict_id'] = getattr(cv2.aruco,kwargs['aruco_dict_id'])
        return Marker(**kwargs)

json.register_type(json.TypeEntry(Marker,'__marker.Marker__', Marker._to_dict, Marker._from_dict))
# get defaults for default argument of Marker constructor
_params = inspect.signature(Marker.__init__).parameters
marker_defaults = {k:d for k in _params if (d:=_params[k].default)!=inspect._empty}
marker_parameter_types = {k:_params[k].annotation for k in _params if k!='self'}
del _params

def get_marker_setup(marker: Marker) -> aruco.MarkerSetup:
    return aruco.MarkerSetup(aruco_detector_params = {
                                'markerBorderBits': marker.marker_border_bits
                             },
                             detect_only = marker.detect_only,
                             size= marker.size
                             )

def get_marker_dict_from_list(markers: list[Marker]) -> dict[tuple[int,int],aruco.MarkerSetup]:
    out = {}
    for m in markers:
        out[(m.aruco_dict_id, m.id)] = get_marker_setup(m)
    return out

def get_file_name(marker_id: int, aruco_dict_id: int, folder: str|pathlib.Path) -> pathlib.Path:
    folder = pathlib.Path(folder)
    return folder / f'{naming.marker_pose_prefix}{aruco.dict_to_str[aruco_dict_id]}_{marker_id}.tsv'

def load_file(marker_id: int, aruco_dict_id: int, folder: str|pathlib.Path) -> pd.DataFrame:
    file = get_file_name(marker_id, aruco_dict_id, folder)
    return pd.read_csv(file,sep='\t', dtype=defaultdict(lambda: float, **gt_marker.Pose._non_float))

@overload
def code_marker_for_presence(markers: pd.DataFrame, allow_failed=False) -> pd.DataFrame: ...
def code_marker_for_presence(markers: dict[Any, pd.DataFrame], allow_failed=False) -> dict[Any, pd.DataFrame]: ...
def code_marker_for_presence(markers: pd.DataFrame|dict[Any, pd.DataFrame], allow_failed=False) -> pd.DataFrame|dict[Any, pd.DataFrame]:
    if isinstance(markers,dict):
        for i in markers:
            markers[i] = _code_marker_for_presence_impl(markers[i], f'{i}_', allow_failed)
    else:
        markers = _code_marker_for_presence_impl(markers,'', allow_failed)
    return markers

def _code_marker_for_presence_impl(markers: pd.DataFrame, lbl_extra:str, allow_failed=False) -> pd.DataFrame:
    new_col_lbl = f'marker_{lbl_extra}presence'
    markers.insert(len(markers.columns),
        new_col_lbl,
        True if allow_failed else markers[[c for c in markers.columns if c not in ['frame_idx']]].notnull().all(axis='columns')
    )
    markers = markers[['frame_idx',new_col_lbl]]
    markers = markers.astype({new_col_lbl: bool}) # ensure the new column is bool
    return markers

def fill_gaps_in_marker_detection(markers: pd.DataFrame, fill_value):
    min_fr_idx = markers['frame_idx'].min()
    max_fr_idx = markers['frame_idx'].max()
    new_index = pd.Index(range(min_fr_idx,max_fr_idx+1), name='frame_idx')
    return markers.set_index('frame_idx').reindex(new_index, fill_value=fill_value).reset_index()