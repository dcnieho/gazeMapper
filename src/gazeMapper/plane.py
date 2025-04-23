from enum import auto
import pathlib
import typeguard
import inspect
import typing

from glassesTools import aruco, json, plane, utils, validation

from . import type_utils


class Type(utils.AutoName):
    GlassesValidator= auto()
    Plane_2D        = auto()
json.register_type(json.TypeEntry(Type,'__enum.plane.Type__', utils.enum_val_2_str, lambda x: getattr(Type, x.split('.')[1])))
types = [p for p in Type]

class Definition:
    default_json_file_name = 'plane_def.json'

    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 type               : Type,
                 name               : str
                 ):
        self.type               = type
        self.name               = name

    def field_problems(self) -> type_utils.ProblemDict:
        raise NotImplementedError()
    def fixed_fields(self) -> type_utils.NestedDict:
        raise NotImplementedError()
    def has_complete_setup(self) -> bool:
        raise NotImplementedError()

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_') and k not in ['name']+list(self.fixed_fields().keys())}    # name will be populated from the provided path
        if 'aruco_dict_id' in to_dump:
            # store as string (and always store, also if defaulted, this rename means the below line never filters it out)
            to_dump['aruco_dict'] = aruco.dict_id_to_str[to_dump.pop('aruco_dict_id')]
        # filter out defaulted
        to_dump = {k:v for k in to_dump if (v:=to_dump[k]) is not None and (k not in definition_defaults[self.type] or definition_defaults[self.type][k]!=v)}
        json.dump(to_dump, path)

    @staticmethod
    def load_from_json(path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= Definition.default_json_file_name
        kwds = json.load(path)
        # help with named tuple roundtrip
        for k in ['plane_size', 'origin']:
            if k in kwds:
                kwds[k] = plane.Coordinate(*kwds[k])
        kwds['p_type'] = Type(kwds.pop('type'))
        # backwards compatibility
        if 'aruco_dict' in kwds:
            kwds['aruco_dict_id'] = kwds.pop('aruco_dict')
        if 'aruco_dict_id' in kwds:
            # dictionary names might be stored as strings, turn back into int
            if isinstance(kwds['aruco_dict_id'],str):
                kwds['aruco_dict_id'] = aruco.str_to_dict_id(kwds['aruco_dict_id'])
        return make_definition(path=path.parent, name=path.parent.name, **kwds)

class Definition_GlassesValidator(Definition):
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 name               : str,
                 aruco_dict_id      : type_utils.ArucoDictType,
                 marker_border_bits : int,
                 min_num_markers    : int,
                 ref_image_size     : int,
                 marker_file        : str|pathlib.Path|None,
                 target_file        : str|pathlib.Path|None,
                 use_default        : bool = True,
                 is_dynamic         : bool = False,
                 ):
        super().__init__(Type.GlassesValidator, name)
        # These two together mean the following:
        #  use_default & !is_dynamic: the default/built-in static glassesValidator poster is used
        # !use_default & !is_dynamic: a non-default static glassesValidator poster is used. Custom settings files are expected in the plane folder
        #  use_default &  is_dynamic: the default/built-in dynamic validation procedure (using a PsychoPy script) is used. Settings files (converted from the default dynamic procedure setup) are expected in the plane folder
        # !use_default &  is_dynamic: a non-default dynamic validation procedure (using a PsychoPy script) is used. Custom settings files are expected in the plane folder (converted from a custom dynamic procedure setup)
        self.use_default        = use_default           # If True, denotes this is the default/built-in glassesValidator plane, if False, denotes custom settings are expected
        self.is_dynamic         = is_dynamic            # If True, indicates this is a dynamic (using PsychoPy script) validation plane, not a static (poster) one
        # custom settings
        self.aruco_dict_id      = aruco_dict_id
        self.marker_border_bits = marker_border_bits
        self.min_num_markers    = min_num_markers       # minimum number of markers that should be to run pose estimation w.r.t. the plane
        self.ref_image_size     = ref_image_size        # largest dimension
        self.marker_file        = marker_file
        self.target_file        = target_file

    def field_problems(self) -> type_utils.ProblemDict:
        problem = {}
        if self.marker_border_bits<1:
            problem['marker_border_bits'] = f'marker_border_bits must be at least 1'
        return problem

    def fixed_fields(self) -> type_utils.NestedDict:
        # these cannot be edited from the GUI, are for info only
        return {k:None for k in ['name', 'aruco_dict_id', 'marker_border_bits', 'min_num_markers', 'ref_image_size', 'marker_file', 'target_file']}

    def has_complete_setup(self) -> bool:
        return not self.field_problems()

class Definition_Plane_2D(Definition):
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 name               : str,
                 marker_file        : str|pathlib.Path|None     = None, # should be set, no suitable default
                 marker_size        : float|None                = None, # should be set, no suitable default
                 marker_border_bits : int                       = 1,
                 min_num_markers    : int                       = 3,
                 plane_size         : plane.Coordinate          = plane.Coordinate(0., 0.), # should be set to something non-zero
                 origin             : plane.Coordinate          = plane.Coordinate(0., 0.),
                 unit               : str                       = '',
                 aruco_dict_id      : type_utils.ArucoDictType  = aruco.default_dict,
                 ref_image_size     : int                       = 1920
                 ):
        super().__init__(Type.Plane_2D, name)
        self.marker_file        = marker_file           # if str or Path: file from which to read markers. Else direction N_markerx4 array. Should contain centers of markers
        self.marker_size        = marker_size           # in "unit" units
        self.plane_size         = plane_size            # in "unit" units
        self.marker_border_bits = marker_border_bits
        self.min_num_markers    = min_num_markers       # minimum number of markers that should be to run pose estimation w.r.t. the plane
        self.origin             = origin                # center of plane, in coordinates of the input file
        self.unit               = unit
        self.aruco_dict_id      = aruco_dict_id
        self.ref_image_size     = ref_image_size        # largest dimension

    def field_problems(self) -> type_utils.ProblemDict:
        problem: dict[str,None|dict[str,None]] = {}
        for a in ['marker_file','marker_size','plane_size']:
            if not getattr(self,a):
                problem[a] = None
            elif a=='plane_size' and any(missing:=[c==0 for c in self.plane_size]):
                problem[a] = {k:None for k,m in zip(self.plane_size._fields,missing) if m}
        if self.marker_border_bits<1:
            problem['marker_border_bits'] = f'marker_border_bits must be at least 1'
        return problem

    def fixed_fields(self) -> type_utils.NestedDict:
        return {k:None for k in ['name']}

    def has_complete_setup(self) -> bool:
        return not self.field_problems()

def make_definition(p_type: Type, name: str, path: pathlib.Path|None, **kwargs) -> Definition_GlassesValidator|Definition_Plane_2D:
    cls = Definition_GlassesValidator if p_type==Type.GlassesValidator else Definition_Plane_2D
    if p_type==Type.GlassesValidator:
        validator_config_dir = None # use glassesValidator built-in/default static poster
        if ('use_default' in kwargs and not kwargs['use_default']) or ('is_dynamic' in kwargs and kwargs['is_dynamic']):
            validator_config_dir = path
        validation_setup = validation.config.get_validation_setup(validator_config_dir)
        kwargs['aruco_dict_id'] = validation_setup['arucoDictionary']
        kwargs['marker_border_bits'] = validation_setup['markerBorderBits']
        kwargs['min_num_markers'] = validation_setup['minNumMarkers']
        kwargs['ref_image_size'] = validation_setup['referencePosterSize']
        kwargs['marker_file'] = validation_setup['markerPosFile']
        kwargs['target_file'] = validation_setup['targetPosFile']
    return cls(name=name, **kwargs)


definition_defaults: dict[Type, dict['str', typing.Any]] = {}
definition_parameter_types: dict[Type, dict['str', typing.Type]] = {}
for _t,_cls in zip([Type.GlassesValidator, Type.Plane_2D],[Definition_GlassesValidator, Definition_Plane_2D]):
    _params = inspect.signature(_cls.__init__).parameters
    definition_defaults[_t]        = {k:d for k in _params if (d:=_params[k].default)!=inspect._empty}
    definition_parameter_types[_t] = {k:_params[k].annotation for k in _params if k!='self'}
    del _params
definition_parameter_doc = {
    'name': type_utils.GUIDocInfo('Name','The name of the plane.'),
    'aruco_dict_id': type_utils.GUIDocInfo('ArUco dictionary','The ArUco dictionary (see cv::aruco::PREDEFINED_DICTIONARY_NAME) of the markers.'),
    'marker_border_bits': type_utils.GUIDocInfo('Marker border bits','Width of the black border around each marker.'),
    'min_num_markers': type_utils.GUIDocInfo('Minimum number of markers','Minimum number of markers belonging to the plane that should be detected to attempt to determine pose and homography transformation.'),
    'ref_image_size': type_utils.GUIDocInfo('Reference image size','The size in pixels of the image that is generated of the plane with fiducial markers.'),
    'use_default': type_utils.GUIDocInfo('Use default setup','If enabled, the default glassesValidator plane is used. When not enabled, a custom configuration can be used by editing the files containing the plane setup in the plane configuration folder.'),
    'is_dynamic': type_utils.GUIDocInfo('Dynamic validation procedure?','If enabled, this indicates that a dynamic validation procedure run with the PsychoPy script was used.'),
    'marker_file': type_utils.GUIDocInfo('Marker file','Name of the file specifying the marker layout on the plane (e.g., markerPositions.csv).'),
    'marker_size': type_utils.GUIDocInfo('Marker size','Length of the edge of a marker (mm, excluding the white edge, only the black part).'),
    'target_file': type_utils.GUIDocInfo('Target file','Name of the file specifying the targets positions on the plane (e.g., targetPositions.csv).'),
    'plane_size': type_utils.GUIDocInfo('Plane size','Total size of the plane (mm). Can be larger than the area spanned by the fiducial markers.',{
        'x': type_utils.GUIDocInfo('X', 'Horizontal size of the plane (mm).'),
        'y': type_utils.GUIDocInfo('Y', 'Vertical size of the plane (mm).'),
    }),
    'origin': type_utils.GUIDocInfo('Origin','The position of the origin of the plane (mm).',{
        'x': type_utils.GUIDocInfo('X', 'Horizontal coordinate of the plane\'s origin (mm).'),
        'y': type_utils.GUIDocInfo('Y', 'Vertical coordinate of the plane\'s origin (mm).'),
    }),
    'unit': type_utils.GUIDocInfo('Unit','Unit in which sizes and coordinates are expressed. Purely for informational purposes, not used in the software. Should be mm.'),
}


def get_plane_from_path(path: str|pathlib.Path) -> plane.Plane:
    path = pathlib.Path(path)
    plane_def = Definition.load_from_json(path)
    return get_plane_from_definition(plane_def, path)

def get_plane_from_definition(plane_def: Definition, path: str|pathlib.Path) -> plane.Plane:
    # for loading a plane from a directory that doesn't contain a plane definition json file
    # use the provided definition instead
    if plane_def.type==Type.GlassesValidator:
        validator_config_dir = None # use glassesValidator built-in/default
        if not plane_def.use_default or plane_def.is_dynamic:
            validator_config_dir = path
        validation_config = validation.config.get_validation_setup(validator_config_dir)
        return validation.Plane(validator_config_dir, validation_config, is_dynamic=plane_def.is_dynamic, ref_image_store_path=path/plane.Plane.default_ref_image_name)
    else:
        pl = plane.Plane(
            markers             = path/plane_def.marker_file,
            marker_size         = plane_def.marker_size,
            plane_size          = plane_def.plane_size,
            aruco_dict_id       = plane_def.aruco_dict_id,
            marker_border_bits  = plane_def.marker_border_bits,
            min_num_markers     = plane_def.min_num_markers,
            unit                = plane_def.unit,
            ref_image_store_path= path/plane.Plane.default_ref_image_name,
            ref_image_size      = plane_def.ref_image_size
        )
        if plane_def.origin is not None:
            pl.set_origin(plane_def.origin)
        return pl