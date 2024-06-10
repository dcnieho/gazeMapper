from enum import auto
import pathlib
import cv2
import json
import numpy as np

from glassesTools import plane, utils
from glassesValidator.config import get_validation_setup
from glassesValidator.config.poster import Poster


class Type(utils.AutoName):
    GlassesValidator= auto()
    Plane_2D        = auto()
utils.register_type(utils.CustomTypeEntry(Type,'__enum.plane.Type__',str, lambda x: getattr(Type, x.split('.')[1])))


class Definition:
    default_json_file_name = 'plane_def.json'

    def __init__(self, type:Type, name:str=None, use_default:bool=False, marker_file: str|pathlib.Path=None, marker_size: float=None, marker_border_bits: int=1, min_num_markers: int=3, center: np.ndarray=None, unit: str=None, aruco_dict: int=cv2.aruco.DICT_4X4_250, ref_image_width: int=1920):
        self.type                                       = type
        self.name                                       = name
        self.use_default            : bool              = use_default           # applies only to glassesValidator planes. If False, denotes this is the default/built-in glassesValidator plane, if True, denotes custom settings are expected
        # the below are only for non-glassesValidator planes
        self.marker_file            : str|pathlib.Path  = marker_file           # if str or Path: file from which to read markers. Else direction N_markerx4 array. Should contain centers of markers
        self.marker_size            : float             = marker_size           # in "unit" units
        self.marker_border_bits     : int               = marker_border_bits
        self.min_num_markers        : int               = min_num_markers       # minimum number of markers that should be to run pose estimation w.r.t. the plane
        self.center                 : np.ndarray        = center                # center of plane, in coordinates of the input file
        self.unit                   : str               = unit
        self.aruco_dict             : int               = aruco_dict
        self.ref_image_width        : int               = ref_image_width

        if isinstance(self.center,list):
            self.center = np.array(self.center)

        # check provided info
        if self.type==Type.GlassesValidator:
            # prevent bugs
            assert self.marker_file is None, "The marker_file input argument should not be set when the plane is a GlassesValidator plane (would be ignored)"
            assert self.marker_size is None, "The marker_size input argument should not be set when the plane is a GlassesValidator plane (would be ignored)"
            # NB: all the other parameters are also ignored, but have meaningful defaults, so can't be checked
        else:
            assert self.marker_file is not None, "The marker_file input argument should be provided"
            assert self.marker_size is not None, "The marker_size input argument should be provided"
            # prevent bugs
            assert self.use_default is False, "The use_default input argument is for GlassesValidator planes. It should be set to False (default) when the plane is not a GlassesValidator plane (would be ignored)"

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        with open(path, 'w') as f:
            json.dump(self, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Definition':
        path = pathlib.Path(path)
        if path.is_dir():
            path /= Definition.default_json_file_name
        with open(path, 'r') as f:
            dfntn = json.load(f, object_hook=utils.json_reconstitute)
        dfntn.name=path.parent.name
        return dfntn

    def _to_dict(self):
        return {
            'type': self.type,
            'use_default': self.use_default,
            'marker_file': self.marker_file,
            'marker_size': self.marker_size,
            'marker_border_bits': self.marker_border_bits,
            'min_num_markers': self.min_num_markers,
            'center': self.center,
            'unit': self.unit,
            'aruco_dict': self.aruco_dict,
            'ref_image_width': self.ref_image_width
        }
utils.register_type(utils.CustomTypeEntry(Definition,'__plane.Definition',lambda x: x._to_dict(), lambda x: Definition(**x)))


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
            aruco_dict          = plane_def.aruco_dict,
            marker_border_bits  = plane_def.marker_border_bits,
            unit                = plane_def.unit,
            ref_image_store_path= path / plane.Plane.default_ref_image_name,
            ref_image_width     = plane_def.ref_image_width
        )
        if plane_def.center is not None:
            pl.set_center(plane_def.center)
        return pl