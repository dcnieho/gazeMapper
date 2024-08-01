from enum import auto
import pathlib
import cv2
import json
import typeguard
import inspect
import typing

from glassesTools import plane, utils
from glassesValidator.config import get_validation_setup
from glassesValidator.config.poster import Poster


class Type(utils.AutoName):
    GlassesValidator= auto()
    Plane_2D        = auto()
utils.register_type(utils.CustomTypeEntry(Type,'__enum.plane.Type__',str, lambda x: getattr(Type, x.split('.')[1])))
types = [p for p in Type]

class Definition:
    default_json_file_name = 'plane_def.json'

    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 type               : Type,
                 name               : str,
                 use_default        : bool|None                 = None,
                 marker_file        : str|pathlib.Path|None     = None,
                 marker_size        : float|None                = None,
                 marker_border_bits : int|None                  = None,
                 min_num_markers    : int|None                  = None,
                 plane_size         : list[float]|None          = None,
                 origin             : list[float]|None          = None,
                 unit               : str|None                  = None,
                 aruco_dict         : int|None                  = None,
                 ref_image_size     : int|None                  = None
                 ):
        self.type               = type
        self.name               = name
        self.use_default        = use_default           # applies only to glassesValidator planes. If False, denotes this is the default/built-in glassesValidator plane, if True, denotes custom settings are expected
        # the below are only for non-glassesValidator planes
        self.marker_file        = marker_file           # if str or Path: file from which to read markers. Else direction N_markerx4 array. Should contain centers of markers
        self.marker_size        = marker_size           # in "unit" units
        self.plane_size         = plane_size            # in "unit" units
        self.marker_border_bits = marker_border_bits
        self.min_num_markers    = min_num_markers       # minimum number of markers that should be to run pose estimation w.r.t. the plane
        self.origin             = origin                # center of plane, in coordinates of the input file
        self.unit               = unit
        self.aruco_dict         = aruco_dict
        self.ref_image_size     = ref_image_size        # largest dimension

        # check provided info
        if self.type==Type.GlassesValidator:
            # prevent bugs
            for a in definition_valid_fields[Type.Plane_2D]:
                if getattr(self,a) is not None:
                    raise ValueError(f"The {a} input argument should not be set when the plane is a GlassesValidator plane (would be ignored)")
            if self.use_default is None:
                self.use_default = definition_defaults[Type.GlassesValidator]['use_default']
        else:
            # prevent bugs
            for a in definition_valid_fields[Type.GlassesValidator]:
                if getattr(self,a) is not None:
                    raise ValueError(f"The {a} input argument is for GlassesValidator planes. It should not be set when the plane is not a GlassesValidator plane (would be ignored)")
            # set defaults
            for a in definition_defaults[Type.Plane_2D]:
                if getattr(self,a) is None:
                    setattr(self,a,definition_defaults[Type.Plane_2D][a])

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        with open(path, 'w') as f:
            other_type = Type.GlassesValidator if self.type==Type.Plane_2D else Type.Plane_2D
            to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_') and k not in ['name']+definition_valid_fields[other_type]}    # name will be populated from the provided path, fields for the other type should not be stored
            # filter out defaulted
            to_dump = {k:v for k in to_dump if (v:=to_dump[k]) is not None and (k not in definition_defaults[self.type] or definition_defaults[self.type][k]!=v)}
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Definition':
        path = pathlib.Path(path)
        if path.is_dir():
            path /= Definition.default_json_file_name
        with open(path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        return Definition(name=path.parent.name, **kwds)
definition_defaults = {Type.GlassesValidator: {'use_default': True}, Type.Plane_2D: {'marker_border_bits': 1, 'min_num_markers': 3, 'aruco_dict': cv2.aruco.DICT_4X4_250, 'ref_image_size': 1920}}
definition_valid_fields = {Type.GlassesValidator: ['use_default'], Type.Plane_2D: ['marker_file', 'marker_size', 'plane_size', 'marker_border_bits', 'min_num_markers', 'origin', 'unit', 'aruco_dict', 'ref_image_size']}
_params = inspect.signature(Definition.__init__).parameters
definition_parameter_types = {k:utils.unpack_none_union(_params[k].annotation) for k in _params if k!='self'}
del _params


def get_plane_from_path(path: str|pathlib.Path) -> plane.Plane:
    path = pathlib.Path(path)
    plane_def = Definition.load_from_json(path)
    return get_plane_from_definition(plane_def, path)

def get_plane_from_definition(plane_def: Definition, path: str | pathlib.Path) -> plane.Plane:
    # for loading a plane from a directory that doesn't contain a plane definition json file
    # use the provided definition instead
    if plane_def.type==Type.GlassesValidator:
        validator_config_dir = None # use glassesValidator built-in/default
        if not plane_def.use_default:
            validator_config_dir = path
        validation_setup = get_validation_setup(validator_config_dir)
        return Poster(validator_config_dir, validation_setup, ref_image_store_path=path / plane.Plane.default_ref_image_name)
    else:
        pl = plane.Plane(
            markers             = path / plane_def.marker_file,
            marker_size         = plane_def.marker_size,
            plane_size          = plane_def.plane_size,
            aruco_dict          = plane_def.aruco_dict,
            marker_border_bits  = plane_def.marker_border_bits,
            unit                = plane_def.unit,
            ref_image_store_path= path / plane.Plane.default_ref_image_name,
            ref_image_size      = plane_def.ref_image_size
        )
        if plane_def.origin is not None:
            pl.set_origin(plane_def.origin)
        return pl