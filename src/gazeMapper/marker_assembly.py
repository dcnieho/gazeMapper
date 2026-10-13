from enum import auto
import pathlib
import typeguard
import inspect
import typing

from glassesTools import (
    aruco as _aruco,
    json as _json,
    marker_assembly as _gt_marker_assembly,
    plane as _gt_plane,
    utils as _gt_utils,
    validation as _validation)

from . import type_utils



class Type(_gt_utils.AutoName):
    GlassesValidator= auto()
    Plane_2D        = auto()
    Target_Plane_2D = auto()
    MarkerAssembly_3D = auto()
_json.register_type(_json.TypeEntry(Type,'__enum.plane.Type__', _gt_utils.enum_val_2_str, lambda x: getattr(Type, x.split('.')[1])))
types = [p for p in Type]
type_display_names = {
    Type.GlassesValidator: 'glassesValidator Plane',
    Type.Plane_2D: '2D Plane',
    Type.Target_Plane_2D: '2D Target Plane',
    Type.MarkerAssembly_3D: '3D Marker Assembly',
}

class Definition:
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 type               : Type,
                 name               : str,
                 ref_image_size     : int = 1920
                 ):
        self.type               = type
        self.name               = name
        self.ref_image_size     = ref_image_size        # largest dimension

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        raise NotImplementedError()
    def fixed_fields(self) -> type_utils.NestedDict:
        raise NotImplementedError()
    def has_complete_setup(self, config_path: str|pathlib.Path|None = None) -> bool:
        return not self.field_problems(config_path)

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        to_dump = config.remove_defaults(self._to_dict(), definition_defaults[self.type])
        _json.dump(to_dump, path)

    def _to_dict(self):
        # name will be populated from the provided path
        return {k:getattr(self,k) for k in vars(self) if not k.startswith('_') and k not in ['name']+list(self.fixed_fields().keys())}

    @classmethod
    def _decode_dict(cls, kwds):
        return kwds

    @staticmethod
    def load_from_json(path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            assembly_file = path / Definition_MarkerAssembly_3D.default_json_file_name
            path = assembly_file if assembly_file.is_file() else path / Definition_Plane_Aruco.default_json_file_name
        kwds = _json.load(path)
        kwds['p_type'] = Type(kwds.pop('type'))
        return make_definition(path=path.parent, name=path.parent.name, **kwds)

class Definition_MarkerAssembly(Definition):
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 type               : Type,
                 name               : str,
                 aruco_settings     : 'config.ArucoSettings|None' = None,
                 marker_border_bits : int = 1,
                 aruco_dict_id      : type_utils.ArucoDictType = _aruco.default_dict,
                 min_num_markers    : int = 3,
                 marker_file        : str|pathlib.Path|None = None,
                 ref_image_size     : int = 1920,
                 unit               : str = ''
                 ):
        super().__init__(type, name, ref_image_size)
        self.marker_border_bits = marker_border_bits
        self.aruco_settings     = config.aruco_settings_with_defaults(aruco_settings)
        self.aruco_dict_id      = aruco_dict_id
        self.min_num_markers    = min_num_markers       # minimum number of markers required for pose estimation
        self.marker_file        = marker_file           # file specifying the marker layout
        self.unit               = unit

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        problems = config.aruco_settings_problems(self.aruco_settings)
        consistency = self.aruco_settings['pose_consistency']
        unit = self.unit.strip().lower()
        if consistency['enabled'] and consistency['temporal_enabled'] and unit not in ('m', 'cm', 'mm'):
            unit_error = (type_utils.ProblemLevel.Error, 'When temporal pose checking is enabled in the ArUco detection settings, the unit must be m, cm, or mm')
            problems = type_utils.merge_problem_dicts(problems, {
                'aruco_settings': {'pose_consistency': {'temporal_enabled': unit_error}},
                'unit': unit_error,
            })
        if type(self.marker_border_bits) is not int or self.marker_border_bits < 1:
            problems['marker_border_bits'] = (type_utils.ProblemLevel.Error, 'marker_border_bits must be a positive integer')
        if config_path is not None:
            for field in ('marker_file', 'target_file'):
                file_name = getattr(self, field, None)
                if file_name is None:
                    # skip, even if the field is required, because whether the required fields are set is checked in the relevant subclass
                    continue
                file_path = pathlib.Path(config_path) / file_name
                if not file_path.is_file():
                    problems[field] = (type_utils.ProblemLevel.Error, f'File does not exist: {file_path}')
        return problems

    def _to_dict(self):
        to_dump = super()._to_dict()
        if 'aruco_dict_id' in to_dump:
            # Always store the dictionary as a name, including when it is the default.
            to_dump['aruco_dict'] = _aruco.dict_id_to_str[to_dump.pop('aruco_dict_id')]
        settings = dict(self.aruco_settings or {})
        if isinstance(settings.get('detector_params'), dict):
            # Border width is stored separately on the plane.
            settings['detector_params'] = {k:v for k,v in settings['detector_params'].items() if k != 'markerBorderBits'}
        if settings := config.remove_defaults(settings, config.ArucoSettings()):
            to_dump['aruco_settings'] = settings
        else:
            to_dump.pop('aruco_settings', None)
        return to_dump

    @classmethod
    def _decode_dict(cls, kwds):
        kwds = super()._decode_dict(kwds)
        # backwards compatibility
        if 'aruco_dict' in kwds:
            kwds['aruco_dict_id'] = kwds.pop('aruco_dict')
        if 'aruco_dict_id' in kwds:
            # dictionary names might be stored as strings, turn back into int
            if isinstance(kwds['aruco_dict_id'],str):
                kwds['aruco_dict_id'] = _aruco.str_to_dict_id(kwds['aruco_dict_id'])
        settings = kwds.get('aruco_settings') or {}
        detector_params = settings.get('detector_params') or {}
        if 'markerBorderBits' in detector_params:
            kwds.setdefault('marker_border_bits', detector_params.pop('markerBorderBits'))
        return kwds


class Definition_Plane_Aruco(Definition_MarkerAssembly):
    default_json_file_name = 'plane_def.json'


class Definition_GlassesValidator(Definition_Plane_Aruco):
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
                 aruco_settings     : 'config.ArucoSettings|None' = None,
                 ):
        super().__init__(Type.GlassesValidator, name, aruco_settings, marker_border_bits, aruco_dict_id=aruco_dict_id, min_num_markers=min_num_markers, marker_file=marker_file, ref_image_size=ref_image_size)
        # These two together mean the following:
        #  use_default & !is_dynamic: the default/built-in static glassesValidator poster is used
        # !use_default & !is_dynamic: a non-default static glassesValidator poster is used. Custom settings files are expected in the plane folder
        #  use_default &  is_dynamic: the default/built-in dynamic validation procedure (using a PsychoPy script) is used. Settings files (converted from the default dynamic procedure setup) are expected in the plane folder
        # !use_default &  is_dynamic: a non-default dynamic validation procedure (using a PsychoPy script) is used. Custom settings files are expected in the plane folder (converted from a custom dynamic procedure setup)
        self.use_default        = use_default           # If True, denotes this is the default/built-in glassesValidator plane, if False, denotes custom settings are expected
        self.is_dynamic         = is_dynamic            # If True, indicates this is a dynamic (using PsychoPy script) validation plane, not a static (poster) one
        self.target_file        = target_file

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        uses_builtin_static_setup = self.use_default and not self.is_dynamic
        problems = super().field_problems(None if uses_builtin_static_setup else config_path)
        if not uses_builtin_static_setup:
            for field in ('marker_file', 'target_file'):
                if not getattr(self, field):
                    problems[field] = (type_utils.ProblemLevel.Error, f'A {field.removesuffix("_file")} file is required')
        return problems

    def fixed_fields(self) -> type_utils.NestedDict:
        # these cannot be edited from the GUI, are for info only
        return {k:None for k in ['name', 'aruco_dict_id', 'marker_border_bits', 'min_num_markers', 'ref_image_size', 'marker_file', 'target_file', 'unit']}


class Definition_Plane_2D(Definition_Plane_Aruco):
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 name               : str,
                 marker_file        : str|pathlib.Path|None     = None, # should be set, no suitable default
                 marker_size        : float|None                = None, # should be set, no suitable default
                 marker_border_bits : int                       = 1,
                 min_num_markers    : int                       = 3,
                 plane_size         : _gt_plane.Coordinate      = _gt_plane.Coordinate(0., 0.), # should be set to something non-zero
                 origin             : _gt_plane.Coordinate      = _gt_plane.Coordinate(0., 0.),
                 unit               : str                       = '',
                 aruco_dict_id      : type_utils.ArucoDictType  = _aruco.default_dict,
                 ref_image_size     : int                       = 1920,
                 aruco_settings     : 'config.ArucoSettings|None' = None,
                 **kwargs
                 ):
        super().__init__(Type.Plane_2D if 'type' not in kwargs else kwargs['type'], name, aruco_settings, marker_border_bits, aruco_dict_id=aruco_dict_id, min_num_markers=min_num_markers, marker_file=marker_file, ref_image_size=ref_image_size, unit=unit)
        self.marker_size        = marker_size           # in "unit" units
        self.plane_size         = plane_size            # in "unit" units
        self.origin             = origin                # center of plane, in coordinates of the input file

    @classmethod
    def _decode_dict(cls, kwds):
        kwds = super()._decode_dict(kwds)
        # Restore coordinates after their JSON roundtrip.
        for k in ['plane_size', 'origin']:
            if k in kwds:
                kwds[k] = _gt_plane.Coordinate(*kwds[k])
        return kwds

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        problem = super().field_problems(config_path)
        for a in ['marker_file','marker_size','plane_size']:
            if not getattr(self,a):
                problem[a] = (type_utils.ProblemLevel.Error, None)
            elif a=='plane_size' and any(missing:=[c==0 for c in self.plane_size]):
                problem[a] = {k:(type_utils.ProblemLevel.Error, None) for k,m in zip(self.plane_size._fields,missing) if m}
        return problem

    def fixed_fields(self) -> type_utils.NestedDict:
        return {k:None for k in ['name']}


class Definition_Target_Plane_2D(Definition_Plane_2D):
    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 name               : str,
                 marker_file        : str|pathlib.Path|None     = None, # should be set, no suitable default
                 target_file        : str|pathlib.Path|None     = None, # should be set, no suitable default
                 marker_size        : float|None                = None, # should be set, no suitable default
                 marker_border_bits : int                       = 1,
                 min_num_markers    : int                       = 3,
                 plane_size         : _gt_plane.Coordinate      = _gt_plane.Coordinate(0., 0.), # should be set to something non-zero
                 origin             : _gt_plane.Coordinate      = _gt_plane.Coordinate(0., 0.),
                 unit               : str                       = '',
                 aruco_dict_id      : type_utils.ArucoDictType  = _aruco.default_dict,
                 ref_image_size     : int                       = 1920,
                 aruco_settings     : 'config.ArucoSettings|None' = None
                 ):
        super().__init__(name, marker_file, marker_size, marker_border_bits, min_num_markers, plane_size, origin, unit, aruco_dict_id, ref_image_size, aruco_settings, type=Type.Target_Plane_2D)
        self.target_file        = target_file           # if str or Path: file from which to read targets. Else direction N_targetx2 array. Should contain centers of targets

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        problem = super().field_problems(config_path)
        if not self.target_file:
            problem['target_file'] = (type_utils.ProblemLevel.Error, None)
        return problem

    def fixed_fields(self) -> type_utils.NestedDict:
        return {k:None for k in ['name']}


class Definition_MarkerAssembly_3D(Definition_MarkerAssembly):
    default_json_file_name = 'marker_assembly_def.json'

    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 name               : str,
                 marker_file        : str|pathlib.Path|None     = None, # should be set, no suitable default
                 marker_border_bits : int                       = 1,
                 min_num_markers    : int                       = 3,
                 unit               : str                       = '',
                 aruco_dict_id      : type_utils.ArucoDictType  = _aruco.default_dict,
                 ref_image_size     : int                       = 1920,
                 aruco_settings     : 'config.ArucoSettings|None' = None):
        super().__init__(Type.MarkerAssembly_3D, name, aruco_settings, marker_border_bits, aruco_dict_id=aruco_dict_id, min_num_markers=min_num_markers, marker_file=marker_file, ref_image_size=ref_image_size, unit=unit)

    def field_problems(self, config_path: str|pathlib.Path|None = None) -> type_utils.ProblemDict:
        problems = super().field_problems(config_path)
        if not self.marker_file:
            problems['marker_file'] = (type_utils.ProblemLevel.Error, 'A marker corner CSV is required')
        if not isinstance(self.unit, str) or not self.unit.strip():
            problems['unit'] = (type_utils.ProblemLevel.Error, 'A coordinate unit is required')
        return problems

    def fixed_fields(self) -> type_utils.NestedDict:
        return {'name': None}


def make_definition(p_type: Type, name: str, path: pathlib.Path|None, **kwargs) -> Definition_GlassesValidator|Definition_Plane_2D|Definition_Target_Plane_2D|Definition_MarkerAssembly_3D:
    match p_type:
        case Type.GlassesValidator:
            cls = Definition_GlassesValidator
        case Type.Plane_2D:
            cls = Definition_Plane_2D
        case Type.Target_Plane_2D:
            cls = Definition_Target_Plane_2D
        case Type.MarkerAssembly_3D:
            cls = Definition_MarkerAssembly_3D
    kwargs = cls._decode_dict(kwargs)
    if p_type==Type.GlassesValidator:
        validator_config_dir = None # use glassesValidator built-in/default static poster
        if ('use_default' in kwargs and not kwargs['use_default']) or ('is_dynamic' in kwargs and kwargs['is_dynamic']):
            validator_config_dir = path
        validation_setup = _validation.config.get_validation_setup(validator_config_dir)
        kwargs['aruco_dict_id'] = validation_setup['arucoDictionary']
        kwargs.setdefault('marker_border_bits', validation_setup['markerBorderBits'])
        kwargs['min_num_markers'] = validation_setup['minNumMarkers']
        kwargs['ref_image_size'] = validation_setup['referencePosterSize']
        kwargs['marker_file'] = validation_setup['markerPosFile']
        kwargs['target_file'] = validation_setup['targetPosFile']
    return cls(name=name, **kwargs)


# Import after the definitions so config can also import this module.
from . import config


definition_defaults: dict[Type, dict['str', typing.Any]] = {}
definition_parameter_types: dict[Type, dict['str', typing.Type]] = {}
for _t,_cls in zip([Type.GlassesValidator, Type.Plane_2D, Type.Target_Plane_2D, Type.MarkerAssembly_3D],[Definition_GlassesValidator, Definition_Plane_2D, Definition_Target_Plane_2D, Definition_MarkerAssembly_3D]):
    _params = inspect.signature(_cls.__init__).parameters
    definition_defaults[_t]        = {k:d for k in _params if (d:=_params[k].default)!=inspect._empty}
    definition_parameter_types[_t] = {k:v for k,v in inspect.get_annotations(_cls.__init__, eval_str=True).items() if k not in ['return', 'kwargs']}
    del _params
definition_parameter_doc = {
    'aruco_settings': config.aruco_settings_doc,
    'marker_border_bits': config.aruco_marker_border_doc,
    'name': type_utils.GUIDocInfo('Name','The name of the plane or marker assembly.'),
    'aruco_dict_id': type_utils.GUIDocInfo('ArUco dictionary','The ArUco dictionary (see cv::aruco::PREDEFINED_DICTIONARY_NAME) of the markers.'),
    'min_num_markers': type_utils.GUIDocInfo('Minimum number of markers','Minimum number of markers belonging to the plane or marker assembly that should be detected to attempt to determine pose (and homography transformation in case of a plane).'),
    'ref_image_size': type_utils.GUIDocInfo('Reference image size','The size in pixels of the image that is generated of the plane with fiducial markers.'),
    'use_default': type_utils.GUIDocInfo('Use default setup','If enabled, the default glassesValidator plane is used. When not enabled, a custom configuration can be used by editing the files containing the plane setup in the plane configuration folder.'),
    'is_dynamic': type_utils.GUIDocInfo('Dynamic validation procedure?','If enabled, this indicates that a dynamic validation procedure run with the PsychoPy script was used.'),
    'marker_file': type_utils.GUIDocInfo('Marker file','Name of the file specifying marker geometry (a planar layout CSV for planes or ordered 3D corners for marker assemblies).'),
    'target_file': type_utils.GUIDocInfo('Target file','Name of the file specifying the targets positions on the plane (e.g., targetPositions.csv).'),
    'marker_size': type_utils.GUIDocInfo('Marker size','Length of the edge of a marker (mm, excluding the white edge, only the black part).'),
    'plane_size': type_utils.GUIDocInfo('Plane size','Total size of the plane (mm). Can be larger than the area spanned by the fiducial markers.',{
        'x': type_utils.GUIDocInfo('X', 'Horizontal size of the plane in the plane\'s units (e.g., mm).'),
        'y': type_utils.GUIDocInfo('Y', 'Vertical size of the plane in the plane\'s units (e.g., mm).'),
    }),
    'origin': type_utils.GUIDocInfo('Origin','The position of the origin of the plane or marker assembly origin in the assembly\'s units (e.g., mm).',{
        'x': type_utils.GUIDocInfo('X','Horizontal coordinate of the plane\'s or marker assembly\'s origin in the assembly\'s units (e.g., mm).'),
        'y': type_utils.GUIDocInfo('Y','Vertical coordinate of the plane\'s or marker assembly\'s origin in the assembly\'s units (e.g., mm).'),
    }),
    'unit': type_utils.GUIDocInfo('Unit','Unit used for marker coordinates. Coordinates are not rescaled, output is in this same unit. In most cases, this is merely informational, but when temporal pose checking is enabled the unit is used to convert estimated translation to meters. The unit must then be m, cm, or mm.'),
}


def get_marker_assembly_from_path(path: str|pathlib.Path) -> _gt_marker_assembly.MarkerAssembly:
    path = pathlib.Path(path)
    assembly_def = Definition.load_from_json(path)
    return get_marker_assembly_from_definition(assembly_def, path if path.is_dir() else path.parent)

def get_marker_assembly_from_definition(assembly_def: Definition, path: str|pathlib.Path) -> _gt_marker_assembly.MarkerAssembly:
    path = pathlib.Path(path)
    if assembly_def.type==Type.GlassesValidator:
        assembly_def = typing.cast(Definition_GlassesValidator, assembly_def)
        validator_config_dir = None # use glassesValidator built-in/default
        if not assembly_def.use_default or assembly_def.is_dynamic:
            validator_config_dir = path
        validation_config = _validation.config.get_validation_setup(validator_config_dir)
        return _validation.Plane(validator_config_dir, validation_config, is_dynamic=assembly_def.is_dynamic, aruco_settings=config.aruco_settings_with_border(assembly_def.aruco_settings, assembly_def.marker_border_bits), ref_image_store_path=path/_gt_plane.Plane.default_ref_image_name)

    if assembly_def.type in (Type.Plane_2D, Type.Target_Plane_2D):
        assembly_def = typing.cast(Definition_Plane_2D, assembly_def)
        if assembly_def.marker_file is None or assembly_def.marker_size is None:
            raise ValueError(f'Plane "{assembly_def.name}" requires a marker file and marker size')
        plane_kwargs: dict[str, typing.Any] = dict(
            markers             = path/assembly_def.marker_file,
            marker_size         = assembly_def.marker_size,
            plane_size          = assembly_def.plane_size,
            aruco_dict_id       = assembly_def.aruco_dict_id,
            aruco_settings      = config.aruco_settings_with_border(assembly_def.aruco_settings, assembly_def.marker_border_bits),
            min_num_markers     = assembly_def.min_num_markers,
            unit                = assembly_def.unit,
            ref_image_store_path= path/_gt_plane.Plane.default_ref_image_name,
            ref_image_size      = assembly_def.ref_image_size
        )
        if assembly_def.type==Type.Plane_2D:
            assembly = _gt_plane.Plane(**plane_kwargs)
        else:
            assembly_def = typing.cast(Definition_Target_Plane_2D, assembly_def)
            if assembly_def.target_file is None:
                raise ValueError(f'Plane "{assembly_def.name}" requires a target file')
            assembly = _gt_plane.TargetPlane(targets=path/assembly_def.target_file, **plane_kwargs)
        if assembly_def.origin is not None:
            assembly.set_origin(assembly_def.origin)
        return assembly

    if assembly_def.type == Type.MarkerAssembly_3D:
        assembly_def = typing.cast(Definition_MarkerAssembly_3D, assembly_def)
        if assembly_def.marker_file is None:
            raise ValueError(f'Marker assembly "{assembly_def.name}" requires a marker-corner file')
        return _gt_marker_assembly.MarkerAssembly.from_file(
            marker_file     = path/assembly_def.marker_file,
            aruco_dict_id   = assembly_def.aruco_dict_id,
            aruco_settings  = config.aruco_settings_with_border(assembly_def.aruco_settings, assembly_def.marker_border_bits),
            min_num_markers = assembly_def.min_num_markers,
            unit            = assembly_def.unit,
            ref_image_store_path = path/_gt_marker_assembly.MarkerAssembly.default_ref_image_name,
            ref_image_size  = assembly_def.ref_image_size,
        )

    raise ValueError(f'Unsupported marker assembly type: {assembly_def.type}')
