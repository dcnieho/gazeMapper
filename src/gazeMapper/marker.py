import typing
import typeguard
import inspect

from glassesTools import aruco, json, marker as gt_marker

from . import naming, type_utils


class Marker:
    @typeguard.typechecked
    def __init__(self,
                 m_id               : int,
                 detect_only        : bool,         # if true, pose will not be determined and only marker presence is detected. That means marker size is not needed
                 size               : float|None                = None,
                 aruco_dict_id      : type_utils.ArucoDictType  = aruco.default_dict,
                 marker_border_bits : int                       = 1
                 ):
        self.id                 = m_id
        self.detect_only        = detect_only
        self.size               = size
        self.aruco_dict_id      = aruco_dict_id
        self.marker_border_bits = marker_border_bits

    def _to_dict(self) -> dict[str,typing.Any]:
        # N.B.: print dictionary names for markers instead of hard to understand integer id
        # always store dictionary even if its the default, so its easier to read the config file by eye
        out = {'id': self.id, 'aruco_dict': aruco.dict_id_to_str[self.aruco_dict_id], 'detect_only': self.detect_only}
        for f in ['size', 'marker_border_bits']:
            if (val:=getattr(self,f))!=marker_defaults[f]:
                out[f] = val
        return out

    @staticmethod
    def _from_dict(kwargs: dict[str,typing.Any]):
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
                kwargs['aruco_dict_id'] = aruco.str_to_dict_id(kwargs['aruco_dict_id'])
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

def get_setup_for_markers(markers: list[Marker]) -> dict[gt_marker.MarkerID,aruco.MarkerSetup]:
    out = {}
    for m in markers:
        out[gt_marker.MarkerID(m.id, m.aruco_dict_id)] = get_marker_setup(m)
    return out