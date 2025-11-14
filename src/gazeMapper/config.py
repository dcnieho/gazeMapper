import pathlib
import inspect
import copy
import enum
import typeguard
import pathvalidate
import typing
from typing import Any, Literal

from glassesTools import annotation, aruco, camera_recording, data_types as _data_types, gaze_worldref, json, marker as gt_marker, utils as gt_utils

from . import marker, plane, session, typed_dict_defaults, type_utils
from .GUI._impl import utils as gui_utils


class AutoCodeSyncPoints(typed_dict_defaults.TypedDictDefault, total=False):
    markers         : set[gt_marker.MarkerID]
    max_gap_duration: int       = 4
    min_duration    : int       = 6

class AutoCodeEpisodes(typed_dict_defaults.TypedDictDefault, total=False):
    start_markers               : list[gt_marker.MarkerID]
    end_markers                 : list[gt_marker.MarkerID]
    max_gap_duration            : int       = 4
    max_intermarker_gap_duration: int       = 15
    min_duration                : int       = 6

class I2MCSettings(typed_dict_defaults.TypedDictDefault, total=False):
    # None where fields are set by code dynamically based on the data. When value applied here, it overrides this dynamic parameter setting
    freq            : float|None    = None
    windowtimeInterp: float         = .25       # s
    edgeSampInterp  : int           = 2
    maxdisp         : float         = 50        # mm
    windowtime      : float         = .2        # s
    steptime        : float         = .02       # s
    downsamples     : set[int]|None = None
    downsampFilter  : bool|None     = None
    chebyOrder      : int|None      = None
    maxerrors       : int           = 100
    cutoffstd       : float|None    = None
    onoffsetThresh  : float         = 3.
    maxMergeDist    : float         = 20        # mm
    maxMergeTime    : float         = 81        # ms
    minFixDur       : float         = 50        # ms

class CamMovementForEtSyncFunction(typed_dict_defaults.TypedDictDefault, total=False):
    module_or_file  : str
    function        : str
    parameters      : dict[str,Any] = typed_dict_defaults.Field(default_factory=lambda: {})

class EtSyncSetup(typed_dict_defaults.TypedDictDefault, total=False):
    get_cam_movement_method     : Literal['plane','function']       = 'plane'
    get_cam_movement_function   : CamMovementForEtSyncFunction|None = None
    use_average                 : bool                              = True

class ValidationSetup(typed_dict_defaults.TypedDictDefault, total=False):
    do_global_shift             : bool                      = True
    max_dist_fac                : float                     = .5
    data_types                  : set[_data_types.DataType]|None = None
    allow_data_type_fallback    : bool                      = False
    include_data_loss           : bool                      = False
    I2MC_settings               : I2MCSettings              = typed_dict_defaults.Field(default_factory=lambda: I2MCSettings())
    dynamic_skip_first_duration : float                     = .2
    dynamic_max_gap_duration    : int                       = 4
    dynamic_min_duration        : int                       = 6
    dynamic_split_consecutive   : bool                      = False

class GazeOffsetSetup(typed_dict_defaults.TypedDictDefault, total=False):
    data_types                  : set[_data_types.DataType]|None    = None
    viewing_distance_mm         : float|None                        = None
    allow_data_type_fallback    : bool                              = False
    which_targets               : dict[str,set[int]]|None           = None

class EventSetup(typed_dict_defaults.TypedDictDefault, total=False):
    event_type      : annotation.EventType
    name            : str
    description     : str|None = None
    hotkey          : str|None = None
    planes          : set[str] = typed_dict_defaults.Field(default_factory=lambda: set())
    which_recording : str|None = None
    auto_code       : AutoCodeSyncPoints|AutoCodeEpisodes|None = None
    sync_setup      : EtSyncSetup|None = None
    validation_setup: ValidationSetup|None = None
    gaze_offset_setup: GazeOffsetSetup|None = None
event_setup_field_order = [
    'event_type',
    'name',
    'description',
    'hotkey',
    'planes',
    'which_recording',
    'auto_code',
    'sync_setup',
    'validation_setup',
    'gaze_offset_setup'
]

class RgbColor(typing.NamedTuple):
    r: int = 0
    g: int = 0
    b: int = 0
json.register_type(json.TypeEntry(RgbColor, '__config.RgbColor__', lambda x: x._asdict(), lambda x: RgbColor(**x)))

class Study:
    default_json_file_name = 'study_def.json'

    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 session_def                                : session.SessionDefinition,
                 planes                                     : list[plane.Definition],
                 individual_markers                         : list[marker.Marker],
                 coding_setup                               : list[EventSetup],
                 working_directory                          : str|pathlib.Path,

                 # setup with defaults
                 allow_duplicated_markers                   : bool                          = False,

                 import_do_copy_video                       : bool                          = True,
                 import_source_dir_as_relative_path         : bool                          = False,
                 import_known_custom_eye_trackers           : list[str]|None                = None,

                 head_attached_recordings_replace_et_scene  : set[str]|None                 = None,

                 overlay_video_gaze_vid_pos_color           : RgbColor                      = RgbColor(  0,255,  0),
                 overlay_video_gaze_world_pos_color         : RgbColor|None                 = RgbColor(255,  0,255),
                 overlay_video_gaze_vid_pos_radius          : int                           = 8,
                 overlay_video_gaze_world_pos_radius        : int                           = 5,
                 overlay_video_gaze_vid_pos_thickness       : int                           = 2,
                 overlay_video_gaze_world_pos_thickness     : int                           = -1,

                 sync_ref_recording                         : str|None                      = None,
                 sync_ref_do_time_stretch                   : bool|None                     = None,
                 sync_ref_stretch_which                     : Literal['ref','other']|None   = None,
                 sync_ref_average_recordings                : set[str]|None                 = None,

                 export_output3D                            : bool                          = False,
                 export_output2D                            : bool                          = True,
                 export_only_code_marker_presence           : bool                          = True,

                 mapped_video_make_which                               : set[str]|None                     = None,
                 mapped_video_recording_colors                         : dict[str,RgbColor]|None           = None,
                 mapped_video_projected_vidPos_color                   : RgbColor|None                     = RgbColor(255,255,  0),
                 mapped_video_projected_world_pos_color                : RgbColor|None                     = RgbColor(255,  0,255),
                 mapped_video_projected_left_ray_color                 : RgbColor|None                     = RgbColor(  0,  0,255),
                 mapped_video_projected_right_ray_color                : RgbColor|None                     = RgbColor(255,  0,  0),
                 mapped_video_projected_average_ray_color              : RgbColor|None                     = RgbColor(255,  0,255),
                 mapped_video_process_planes_for_all_frames            : bool                              = False,
                 mapped_video_process_annotations_for_all_recordings   : bool                              = True,
                 mapped_video_plane_marker_color                       : RgbColor|None                     = RgbColor(  0,255,  0),
                 mapped_video_recovered_plane_marker_color             : RgbColor|None                     = RgbColor(  0,255,255),
                 mapped_video_plane_axis_arm_length                    : float                             = 25,
                 mapped_video_individual_marker_color                  : RgbColor|None                     = RgbColor(255,  0,255),
                 mapped_video_individual_marker_axis_arm_length        : float                             = 25,
                 mapped_video_unexpected_marker_color                  : RgbColor|None                     = RgbColor(255,255,128),
                 mapped_video_rejected_marker_color                    : RgbColor|None                     = None,
                 mapped_video_show_sync_func_output                    : bool                              = True,
                 mapped_video_show_gaze_on_plane_in_which              : set[str]|None                     = None,
                 mapped_video_show_gaze_vec_in_which                   : set[str]|None                     = None,
                 mapped_video_show_camera_in_which                     : set[str]|None                     = None,
                 mapped_video_which_gaze_type_on_plane                 : gaze_worldref.Type                = gaze_worldref.Type.Scene_Video_Position,
                 mapped_video_which_gaze_type_on_plane_allow_fallback  : bool                              = True,
                 mapped_video_gaze_to_plane_margin                     : float                             = 0.25,

                 gui_num_workers                            : int                           = 2,

                 # not a class member
                 strict_check                               : bool                          = True
        ):
        self.session_def                                = session_def
        self.planes                                     = planes
        self.individual_markers                         = individual_markers
        self.coding_setup                               = coding_setup
        self.working_directory                          = working_directory

        self.allow_duplicated_markers                   = allow_duplicated_markers

        self.import_do_copy_video                       = import_do_copy_video
        self.import_source_dir_as_relative_path         = import_source_dir_as_relative_path
        self.import_known_custom_eye_trackers           = import_known_custom_eye_trackers

        self.head_attached_recordings_replace_et_scene  = head_attached_recordings_replace_et_scene

        self.overlay_video_gaze_vid_pos_color           = overlay_video_gaze_vid_pos_color
        self.overlay_video_gaze_world_pos_color         = overlay_video_gaze_world_pos_color
        self.overlay_video_gaze_vid_pos_radius          = overlay_video_gaze_vid_pos_radius
        self.overlay_video_gaze_world_pos_radius        = overlay_video_gaze_world_pos_radius
        self.overlay_video_gaze_vid_pos_thickness       = overlay_video_gaze_vid_pos_thickness
        self.overlay_video_gaze_world_pos_thickness     = overlay_video_gaze_world_pos_thickness

        self.sync_ref_recording                         = sync_ref_recording
        self.sync_ref_do_time_stretch                   = sync_ref_do_time_stretch
        self.sync_ref_stretch_which                     = sync_ref_stretch_which
        self.sync_ref_average_recordings                = sync_ref_average_recordings

        self.export_output3D                            = export_output3D
        self.export_output2D                            = export_output2D
        self.export_only_code_marker_presence           = export_only_code_marker_presence

        self.mapped_video_make_which                               = mapped_video_make_which
        self.mapped_video_recording_colors                         = mapped_video_recording_colors
        self.mapped_video_projected_vidPos_color                   = mapped_video_projected_vidPos_color
        self.mapped_video_projected_world_pos_color                = mapped_video_projected_world_pos_color
        self.mapped_video_projected_left_ray_color                 = mapped_video_projected_left_ray_color
        self.mapped_video_projected_right_ray_color                = mapped_video_projected_right_ray_color
        self.mapped_video_projected_average_ray_color              = mapped_video_projected_average_ray_color
        self.mapped_video_process_planes_for_all_frames            = mapped_video_process_planes_for_all_frames             # if True, all planes are processed for all frames, if False, only according to the planes_per_episode setup and the coding
        self.mapped_video_process_annotations_for_all_recordings   = mapped_video_process_annotations_for_all_recordings    # if True, all coded episodes for all planes of all recordings are processed (so e.g. if validation coded for one recording in the session, that plane is processed for all)
        self.mapped_video_plane_marker_color                       = mapped_video_plane_marker_color
        self.mapped_video_recovered_plane_marker_color             = mapped_video_recovered_plane_marker_color
        self.mapped_video_plane_axis_arm_length                    = mapped_video_plane_axis_arm_length
        self.mapped_video_individual_marker_color                  = mapped_video_individual_marker_color
        self.mapped_video_individual_marker_axis_arm_length        = mapped_video_individual_marker_axis_arm_length
        self.mapped_video_unexpected_marker_color                  = mapped_video_unexpected_marker_color
        self.mapped_video_rejected_marker_color                    = mapped_video_rejected_marker_color
        self.mapped_video_show_sync_func_output                    = mapped_video_show_sync_func_output
        self.mapped_video_show_gaze_on_plane_in_which              = mapped_video_show_gaze_on_plane_in_which
        self.mapped_video_show_gaze_vec_in_which                   = mapped_video_show_gaze_vec_in_which
        self.mapped_video_show_camera_in_which                     = mapped_video_show_camera_in_which
        self.mapped_video_which_gaze_type_on_plane                 = mapped_video_which_gaze_type_on_plane
        self.mapped_video_which_gaze_type_on_plane_allow_fallback  = mapped_video_which_gaze_type_on_plane_allow_fallback
        self.mapped_video_gaze_to_plane_margin                     = mapped_video_gaze_to_plane_margin                      # fraction of plane size, added to each side of the plane

        self.gui_num_workers                            = gui_num_workers

        self.check_valid(strict_check=strict_check)
        self._register_annotations()

    def check_valid(self, strict_check=True):
        # ensure typed dicts with defaults members are of the right class, and apply defaults
        cs_type = typing.get_args(gt_utils.unpack_none_union(study_parameter_types['coding_setup'])[0])[0]
        for i in range(len(self.coding_setup)):
            # ensure correct type and apply defaults for main container
            self.coding_setup[i] = cs_type(self.coding_setup[i])
            self.coding_setup[i].apply_defaults()
            cs = self.coding_setup[i]
            # ensure correct type and apply defaults for nested typed dicts
            if cs.get('auto_code') is not None:
                ac_type = AutoCodeSyncPoints if annotation.type_map[cs['event_type']]==annotation.Type.Point else AutoCodeEpisodes
                cs['auto_code'] = ac_type(cs['auto_code'])
            if cs.get('sync_setup') is not None:
                cs['sync_setup'] = EtSyncSetup(cs['sync_setup'])
                if cs['sync_setup'].get('get_cam_movement_function') is not None:
                    cs['sync_setup']['get_cam_movement_function'] = CamMovementForEtSyncFunction(cs['sync_setup']['get_cam_movement_function'])
                elif cs['sync_setup']['get_cam_movement_function']=='function':
                    # for ET sync events using function, ensure there is a get_cam_movement_function
                    cs['sync_setup']['get_cam_movement_function'] = CamMovementForEtSyncFunction()
            elif cs['event_type']==annotation.EventType.Sync_ET_Data:
                # for ET sync events, ensure there is a sync setup
                cs['sync_setup'] = EtSyncSetup()
            if cs.get('validation_setup') is not None:
                cs['validation_setup'] = ValidationSetup(cs['validation_setup'])
                if cs['validation_setup'].get('I2MC_settings') is not None:
                    cs['validation_setup']['I2MC_settings'] = I2MCSettings(cs['validation_setup']['I2MC_settings'])
            elif cs['event_type']==annotation.EventType.Validate:
                # for validation events, ensure there is a validation setup
                cs['validation_setup'] = ValidationSetup()
            if cs.get('gaze_offset_setup') is not None:
                cs['gaze_offset_setup'] = GazeOffsetSetup(cs['gaze_offset_setup'])

        if strict_check:
            self._check_session_def(strict_check)
            self._check_coding_setup(strict_check)
            self._check_auto_markers(strict_check)
            self._check_individual_markers(strict_check)
            self._check_head_attached_recordings(strict_check)
            self._check_sync_ref(strict_check)
            self._check_make_video(strict_check)

    def _register_annotations(self):
        annotation.unregister_all_annotation_types()
        for cs in self.coding_setup:
            annotation.register_event(annotation.Event(
                name        = cs['name'],
                event_type  = cs['event_type'],
                description = cs['description'],
                hotkey      = cs['hotkey']
            ))


    def _check_recordings(self, which: list[str]|None, field: str, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        if which is None:
            return problems
        missing_recs: list[str] = []
        for w in which:
            if not self._check_recording(w):
                if strict_check:
                    raise ValueError(f'Recording "{w}" not known, check {field} in the study configuration')
                else:
                    missing_recs.append(w)
        if missing_recs:
            problems[field] = (type_utils.ProblemLevel.Error, f'Recording(s) {missing_recs[0] if len(missing_recs)==1 else missing_recs} not known')
            if isinstance(getattr(self,field),dict):
                type_utils.merge_problem_dicts(problems,{field: {r:(type_utils.ProblemLevel.Error, f'Recording {r} not known') for r in missing_recs}})
        return problems

    def _check_recording(self, rec: str) -> bool:
        return any([r.name==rec for r in self.session_def.recordings])

    def _check_session_def(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        # require at least one eye tracker recording
        if not any(r.type==session.RecordingType.Eye_Tracker for r in self.session_def.recordings):
            if strict_check:
                raise ValueError('At least one recording should be an eye tracker recording')
            else:
                problems['session_def'] = (type_utils.ProblemLevel.Error, 'At least one recording should be an eye tracker recording')
        # additional checks for camera recordings
        for r in self.session_def.recordings:
            if r.type==session.RecordingType.Camera:
                msg = None
                if r.camera_recording_type is None:
                    # for camera recordings, require that camera recording type is set
                    msg = 'For a camera recording, the type of camera recording should be set'
                elif r.camera_recording_type==camera_recording.Type.External:
                    # for external camera recordings, associated recording should not be set
                    if r.associated_recording is not None:
                        msg = 'For an external camera recording, the associated recording field should not be set'
                elif r.camera_recording_type==camera_recording.Type.Head_attached:
                    # for head-attached camera recordings, require that the associated eye tracker recording is set,
                    # that the defined recording exists, and that it is an eye tracker recording
                    if r.associated_recording is None:
                        msg = 'For a head-attached camera recording, the associated recording field should be set'
                    elif not any(r2.name==r.associated_recording for r2 in self.session_def.recordings):
                        msg = f'The defined associated recording, "{r.associated_recording}" is not known'
                    elif not any(r2.name==r.associated_recording and r2.type==session.RecordingType.Eye_Tracker for r2 in self.session_def.recordings):
                        msg = f'The defined associated recording, "{r.associated_recording}" should be an eye tracker recording, but it is not'
                if msg is not None:
                    if strict_check:
                        raise ValueError(f'problem with set up of "{r.name}" recording in session_def: {msg}')
                    else:
                        type_utils.merge_problem_dicts(problems, {'session_def': {r.name: (type_utils.ProblemLevel.Error, msg)}})
        return problems

    def _check_coding_setup(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        for i, cs in enumerate(self.coding_setup):
            if not pathvalidate.is_valid_filename(cs['name'], "auto"):
                msg = f'Coding setup name "{cs["name"]}" is not valid. It should be a valid filename'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'name': (type_utils.ProblemLevel.Error, msg)}}})
            missing_planes: list[str] = []
            for p in cs['planes']:
                if not any([p==pl.name for pl in self.planes]):
                    if strict_check:
                        raise ValueError(f'Plane {p} not known')
                    else:
                        missing_planes.append(p)
            if missing_planes:
                mp = '", "'.join(missing_planes)
                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'planes': (type_utils.ProblemLevel.Error, f'Plane(s) "{mp}" not known.')}}})

            # check correct number of planes is defined for the episode
            allow_one_plane = False
            allow_more_than_one = False
            match (e:=cs['event_type']):
                case annotation.EventType.Sync_Camera:
                    allow_one_plane = allow_more_than_one = False
                case annotation.EventType.Sync_ET_Data:
                    if cs['sync_setup'] is not None and cs['sync_setup'].get('get_cam_movement_method','')=='plane':
                        allow_one_plane = True
                        allow_more_than_one = False
                    else:
                        allow_one_plane = allow_more_than_one = False
                case annotation.EventType.Validate:
                    allow_one_plane = True
                    allow_more_than_one = False
                case annotation.EventType.Trial:
                    allow_one_plane = allow_more_than_one = True
            if not allow_one_plane and cs['planes']:
                msg = f'No planes should be defined for a {annotation.tooltip_map[e]} episode.'
                if e==annotation.EventType.Sync_ET_Data:
                    msg += ' Alternatively, you may want to set the sync_setup.get_cam_movement_method for this episode to "plane".'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'planes': (type_utils.ProblemLevel.Error, msg)}}})
            elif allow_one_plane and not cs['planes']:
                msg = ('At least one' if allow_more_than_one else 'One')+f' plane should be defined for a {annotation.tooltip_map[e]}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'planes': (type_utils.ProblemLevel.Error, msg)}}})
            elif not allow_more_than_one and len(cs['planes'])>1:
                msg = f'Only one plane should be defined for a {annotation.tooltip_map[e]}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'planes': (type_utils.ProblemLevel.Error, msg)}}})
            if e==annotation.EventType.Validate and cs['planes']:
                pl_name = list(cs['planes'])[0]
                pl_def = [pl for pl in self.planes if pl.name==pl_name]
                if pl_def and pl_def[0].type!=plane.Type.GlassesValidator:
                    msg = f'Plane {pl_name} is not a {plane.Type.GlassesValidator.value} plane, cannot be used for validation.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'planes': (type_utils.ProblemLevel.Error, msg)}}})
            if cs.get('gaze_offset_setup') is not None:
                # check that this is a trial or validation episode
                if e not in (annotation.EventType.Trial, annotation.EventType.Validate):
                    msg = f'Gaze offset setup can only be set up for {annotation.EventType.Trial.value} or {annotation.EventType.Validate.value} episodes, but this is a {annotation.tooltip_map[e]} episode.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': (type_utils.ProblemLevel.Error, msg)}}})
                # check that at least one of the defined planes is a target plane
                if cs['planes']:
                    p_types = {p:pl_def.type for p in cs['planes'] for pl_def in self.planes if pl_def.name==p}
                    if not any(t in (plane.Type.Target_Plane_2D, plane.Type.GlassesValidator) for t in p_types.values()):
                        msg = f'Gaze offset setup can only be set up if at least one of the planes is a {plane.Type.Target_Plane_2D.value} or {plane.Type.GlassesValidator.value} planes, but the selected planes are not.'
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': (type_utils.ProblemLevel.Error, msg)}}})
                # check that which_targets is set, and that it has at least one entry
                if cs['gaze_offset_setup'].get('which_targets') is None:
                    msg = 'Gaze offset setup.which_targets should be defined when using a gaze offset setup.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': {'which_targets': (type_utils.ProblemLevel.Error, msg)}}}})
                elif not cs['gaze_offset_setup']['which_targets'] or all(not v for v in cs['gaze_offset_setup']['which_targets'].values()):
                    msg = 'Gaze offset setup.which_targets should have at least one non-empty entry.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': {'which_targets': (type_utils.ProblemLevel.Error, msg)}}}})
                if cs['gaze_offset_setup']['which_targets']:
                    for p in cs['gaze_offset_setup']['which_targets'].keys():
                        if not cs['gaze_offset_setup']['which_targets'][p]:
                            msg = f'Gaze offset setup.which_targets entry for plane "{p}" should have at least one target defined.'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': {'which_targets': {p: (type_utils.ProblemLevel.Error, msg)}}}}})
                # check that the entries in which_targets correspond to defined planes
                if cs['gaze_offset_setup'].get('which_targets') is not None:
                    for p in cs['gaze_offset_setup']['which_targets'].keys():
                        if p not in cs['planes']:
                            msg = f'Gaze offset setup.which_targets plane "{p}" is not among the defined planes for this episode.'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': {'which_targets': {p: (type_utils.ProblemLevel.Error, msg)}}}}})
                # check that viewing distance is set if data type viewpos_vidpos_homography is used
                if cs['gaze_offset_setup'].get('data_types') is not None and _data_types.DataType.viewpos_vidpos_homography in cs['gaze_offset_setup']['data_types']:
                    if cs['gaze_offset_setup'].get('viewing_distance_mm') is None:
                        msg = 'Gaze offset setup.viewing_distance_mm should be defined when using the viewpos_vidpos_homography data type.'
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'gaze_offset_setup': {'viewing_distance_mm': (type_utils.ProblemLevel.Error, msg)}}}})

            # check hotkey
            if cs.get('hotkey') is not None and not gui_utils.is_valid_imgui_key(cs['hotkey']):
                msg = f'Hotkey "{cs["hotkey"]}" is not a valid ImGui key name'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'hotkey': (type_utils.ProblemLevel.Error, msg)}}})

            # check sync setup for ET sync episodes
            if e==annotation.EventType.Sync_ET_Data:
                if cs['sync_setup'] is None:
                    msg = f'Sync setup should be defined for a {annotation.tooltip_map[e]} episode.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': (type_utils.ProblemLevel.Error, msg)}}})
                else:
                    cam_mov_possible_values = typing.get_args(EtSyncSetup.__annotations__['get_cam_movement_method'])
                    if cs['sync_setup'].get('get_cam_movement_method') not in cam_mov_possible_values:
                        values = list(cam_mov_possible_values)
                        values_str = '"' + '", "'.join(values) + '"'
                        temp = values_str.partition(f'"{values[-1]}"')
                        values_str = temp[0] + 'or ' + temp[1]
                        msg = f'sync_setup.get_cam_movement_method parameter should be {values_str}'
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': {'get_cam_movement_method': (type_utils.ProblemLevel.Error, msg)}}}})
                        # nothing further to check, return
                        return problems

                    msg = None
                    if cs['sync_setup'].get('get_cam_movement_method')=='function':
                        if cs['sync_setup'].get('get_cam_movement_function') is None:
                            msg = f'Camera movement function should be defined for a {annotation.tooltip_map[e]} episode when the get_cam_movement_method is set to "function".'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': (type_utils.ProblemLevel.Error, msg)}}})
                        else:
                            keys = CamMovementForEtSyncFunction.__required_keys__
                            this_problems = {k:f'sync_setup.get_cam_movement_function.{k} should be set when sync_setup.get_cam_movement_method is set to "function"' for k in keys if k not in cs['sync_setup'].get('get_cam_movement_function') or not cs['sync_setup'].get('get_cam_movement_function')[k]}
                            if this_problems:
                                if strict_check:
                                    raise ValueError('\n'.join(this_problems.values()))
                                else:
                                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': {'get_cam_movement_function': (type_utils.ProblemLevel.Error, this_problems)}}}})
                        if cs['planes']:
                            msg = f'No planes should be defined for a {annotation.tooltip_map[e]} episode unless the get_cam_movement_method is set to "plane".'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': (type_utils.ProblemLevel.Error, msg)}}})
                    elif cs['sync_setup'].get('get_cam_movement_method')=='plane':
                        if not cs['planes']:
                            msg = f'A plane should be defined for a {annotation.tooltip_map[e]} episode when the get_cam_movement_method is set to "plane".'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': (type_utils.ProblemLevel.Error, msg)}}})

            # check auto coding setup
            if cs.get('auto_code') is not None:
                if annotation.type_map[cs['event_type']]==annotation.Type.Point:
                    keys = AutoCodeSyncPoints.__required_keys__
                    fields = ['markers']
                else:
                    keys = AutoCodeEpisodes.__required_keys__
                    fields = ['start_markers','end_markers']

                this_problems = {k:f'auto_code.{k} should be set for a {annotation.tooltip_map[cs["event_type"]]} episode.' for k in keys if k not in cs['auto_code'] or not cs['auto_code'][k]}
                if this_problems:
                    if strict_check:
                        raise ValueError('\n'.join(this_problems.values()))
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'auto_code': (type_utils.ProblemLevel.Error, this_problems)}}})
                else:
                    for f in fields:
                        if f not in cs['auto_code'] or not cs['auto_code'][f]:
                            msg = f'auto_code.{f} cannot be empty for a {annotation.tooltip_map[cs["event_type"]]} episode.'
                            if strict_check:
                                raise ValueError(msg)
                            else:
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'auto_code': {f: (type_utils.ProblemLevel.Error, msg)}}}})
                        else:
                            missing_markers: list[gt_marker.MarkerID] = []
                            for m in cs['auto_code'][f]:
                                if not any([m.m_id==im.id and m.aruco_dict_id==im.aruco_dict_id for im in self.individual_markers]):
                                    missing_markers.append(m)
                            if missing_markers:
                                msg = f'Markers "{", ".join([gt_marker.marker_ID_to_str(m) for m in missing_markers])}" specified in auto_code.{f}, but unknown because not present in individual_markers.'
                                if strict_check:
                                    raise ValueError(msg)
                                else:
                                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'auto_code': {f: (type_utils.ProblemLevel.Error, msg)}}}})

            # check which_recording settings
            if cs['event_type']==annotation.EventType.Sync_Camera:
                # for camera sync episodes, which_recording should not be set
                if cs.get('which_recording') is not None:
                    msg = f'which_recording should not be set for a {annotation.tooltip_map[cs["event_type"]]} episode.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'which_recording': (type_utils.ProblemLevel.Error, msg)}}})
            else:
                if self.sync_ref_recording is not None and not cs.get('which_recording'):
                    msg = 'When a sync reference recording is defined, each coding setup.which_recording should also be defined.'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'which_recording': (type_utils.ProblemLevel.Error, msg)}}})
                if cs.get('which_recording') is not None:
                    if not self.sync_ref_recording:
                        msg = 'When no sync reference recording is defined, coding setup.which_recording should not be defined.'
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'which_recording': (type_utils.ProblemLevel.Error, msg)}}})
                    # check the defined recording exists
                    if not self._check_recording(cs['which_recording']):
                        msg = f'Recording "{cs["which_recording"]}" not known'
                        if strict_check:
                            raise ValueError(msg+ f', check coding_setup[{i}].which_recording in the study configuration')
                        else:
                            type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'which_recording': (type_utils.ProblemLevel.Error, msg)}}})

        sync_events = [(i, cs) for i, cs in enumerate(self.coding_setup) if cs['event_type']==annotation.EventType.Sync_ET_Data]
        use_average_settings = [cs['sync_setup'].get('use_average', False) for _, cs in sync_events if cs['sync_setup'] is not None]
        if use_average_settings and len(set(use_average_settings))!=1:
            msg = 'The setting "use_average" events is not consistent across configured ET sync events. Please set all to True or all to False.'
            if strict_check:
                raise ValueError(msg)
            else:
                for i, _ in sync_events:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {i: {'sync_setup': {'use_average': (type_utils.ProblemLevel.Error, msg)}}}})
        return problems

    def _check_auto_markers(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        used_markers: dict[tuple[str,int,annotation.EventType,str],list[gt_marker.MarkerID]] = {}
        for i, cs in enumerate(self.coding_setup):
            if cs['auto_code'] is None:
                continue
            e = cs['event_type']
            if annotation.type_map[cs['event_type']]==annotation.Type.Point and 'markers' in cs['auto_code']:
                used_markers[('point',i,e,'markers')] = list(cs['auto_code']['markers'])
            elif annotation.type_map[cs['event_type']]==annotation.Type.Interval and 'start_markers' in cs['auto_code'] and 'end_markers' in cs['auto_code']:
                used_markers[('episode',i,e,'start_markers')] = list(cs['auto_code']['start_markers'])
                used_markers[('episode',i,e,'end_markers')]   = list(cs['auto_code']['end_markers'])
        # check if markers or marker sequences are uniquely used:
        # 1. marker used for auto_code_sync_points cannot appear anywhere else
        # 2. marker sequences used for auto_code_episodes must be unique (markers can be reused)
        # first transform marker dict IDs to family so we can properly detect clashes
        used_markers_fam  : dict[tuple[str,int,annotation.EventType,str],list[tuple[int,int]]] = {k:[(m.m_id, aruco.dict_id_to_family[m.aruco_dict_id]) for m in used_markers[k]] for k in used_markers}
        seen_markers      : set[tuple[int,int]] = set()
        seen_markers_sets : set[tuple[tuple[int,int]]] = set()
        def _format_key(key: tuple[str,int,annotation.EventType,str]):
            return f'coding_setup[{key[1]}].auto_code' if key[3]=='markers' else f'coding_setup[{key[1]}].auto_code.{key[3]}'
        for s in used_markers_fam:
            # first check if used markers are unique at the family level
            seen: set[tuple[int,int]] = set()
            if (duplicates := {x for x in used_markers_fam[s] if x in seen or seen.add(x)}):
                msg = f'The markers defined for {_format_key(s)} are not unique. ' +('Please resolve' if not self.allow_duplicated_markers else 'There are') + f' the following duplicates: {gt_marker.format_duplicate_markers_msg(duplicates)}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'coding_setup': {s[1]: {'auto_code': {s[3]: (type_utils.ProblemLevel.Warning if self.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg)}}}})
            # then check if already used in another setup
            if seen_markers.intersection(used_markers_fam[s]):
                # markers not unique, make error. Find exactly where the overlap is
                # yes, this is bad algorithmic complexity, but it only runs in failure cases
                for s2 in used_markers_fam:
                    if s==s2 or s[0]=='episode' and s[0]==s2[0]:
                        # if both are different entries in 'episode', that is not an error
                        # for 'episode' we need to check marker sequences are unique, not
                        # individual entries. e.g. start is 80 81, and end is 81 80 is valid
                        continue
                    if (overlap:=set(used_markers_fam[s2]).intersection(used_markers_fam[s])):
                        msg = f'The following markers are encountered in the setup for both {_format_key(s)} and {_format_key(s2)}: {gt_marker.format_duplicate_markers_msg(overlap)}.' +(' Markers cannot be used more than once, fix this collision.' if not self.allow_duplicated_markers else '')
                        # emit error message
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            for sx in (s,s2):
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {sx[1]: {'auto_code': {sx[3]: (type_utils.ProblemLevel.Warning if self.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg)}}}})
            seen_markers.update(used_markers_fam[s])
            # check if marker sequence is already used
            if seen_markers_sets.intersection((tuple(used_markers_fam[s]),)):
                for s2 in used_markers_fam:
                    if s==s2:
                        continue
                    if set((tuple(used_markers_fam[s2]),)).intersection((tuple(used_markers_fam[s]),)):
                        msg = f'The marker sequence {gt_marker.format_marker_sequence_msg(used_markers_fam[s])} specified for {_format_key(s)} has already been used for {_format_key(s2)}.' +(' Markers sequences must be unique, please fix this collision.' if not self.allow_duplicated_markers else '')
                        # emit error message
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            for sx in (s,s2):
                                type_utils.merge_problem_dicts(problems, {'coding_setup': {sx[1]: {'auto_code': {sx[3]: (type_utils.ProblemLevel.Warning if self.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg)}}}})
            seen_markers_sets.add(tuple(used_markers_fam[s]))
        return problems

    def _check_individual_markers(self, strict_check):
        problems: type_utils.ProblemDict = {}
        for m in self.individual_markers:
            problem = ''
            if m.id>=(ds:=aruco.get_dict_size(m.aruco_dict_id)):
                problem = f'dictionary {aruco.dict_id_to_str[m.aruco_dict_id]} only has {ds} markers, which means that valid IDs are 0-{ds-1}. {m.id} is thus not a valid marker for this dictionary'
            elif m.detect_only and m.size is not None:
                problem = f'size should not be set for detect only markers'
            elif not m.detect_only and (m.size is None or m.size<=0):
                problem = f'size should be set to a value larger than 0'
            elif m.marker_border_bits<1:
                problem = 'marker_border_bits must be at least 1'
            if problem:
                if strict_check:
                    raise ValueError(f'individual_markers marker {m.id} ({aruco.dict_id_to_str[m.aruco_dict_id]}): {problem}')
                else:
                    problems = type_utils.merge_problem_dicts(problems, {'individual_markers': {(m.id, aruco.dict_id_to_family[m.aruco_dict_id]): (type_utils.ProblemLevel.Error, problem)}})
        return problems

    def _check_head_attached_recordings(self, strict_check):
        problems: type_utils.ProblemDict = {}
        if self.head_attached_recordings_replace_et_scene:
            # check listed recordings exist
            type_utils.merge_problem_dicts(problems, self._check_recordings(self.head_attached_recordings_replace_et_scene, 'head_attached_recordings_replace_et_scene', strict_check))
            # check listed recordings are head-attached camera recordings
            wrong = [r for r in self.head_attached_recordings_replace_et_scene if not any(r2.name==r and r2.type==session.RecordingType.Camera and r2.camera_recording_type==camera_recording.Type.Head_attached for r2 in self.session_def.recordings)]
            if wrong:
                msg = 'the following recordings are not head-attached camera recordings:\n- ' + ('\n- '.join(wrong))
                if strict_check:
                    raise ValueError(msg)
                else:
                    problems = type_utils.merge_problem_dicts(problems, {'head_attached_recordings_replace_et_scene': (type_utils.ProblemLevel.Error, msg)})
                    for r in wrong:
                        type_utils.merge_problem_dicts(problems, {'session_def': {r: (type_utils.ProblemLevel.Error, f'{r} is not a head-attached camera recording but is listed in head_attached_recordings_replace_et_scene')}})
            # check that there is not more than one head-attached recording overriding a given ET recording
            overridden = [(r, r2.associated_recording) for r in self.head_attached_recordings_replace_et_scene for r2 in self.session_def.recordings if r2.name==r]
            # get duplicates
            seen: set[str] = set()
            if (duplicates := {x[1] for x in overridden if x[1] in seen or seen.add(x[1])}):
                for d in duplicates:
                    recs = [r[0] for r in overridden if r[1]==d]
                    msg = 'the recordings:\n- ' + ('\n- '.join(recs)) + f'\nare all listed as overriding the scene camera of the "{d}" recording. That is not possible, only a single override is allowed per eye tracker recording'
                    if strict_check:
                        raise ValueError(msg)
                    else:
                        problems = type_utils.merge_problem_dicts(problems, {'head_attached_recordings_replace_et_scene': (type_utils.ProblemLevel.Error, msg)})
                    for r in recs:
                        type_utils.merge_problem_dicts(problems, {'session_def': {r: (type_utils.ProblemLevel.Error, f'{r} is listed as overriding the scene camera of the "{d}" recording but there is more than one override for that recording. Only a single override is allowed per eye tracker recording.')}})
        return problems

    def _check_sync_ref(self, strict_check):
        problems: type_utils.ProblemDict = {}
        if self.sync_ref_recording is None:
            if len(self.session_def.recordings)>1:
                problems['sync_ref_recording'] = (type_utils.ProblemLevel.Error, f'sync_ref_recording must be set when sessions consist of more than one recording')
            # nothing to do
            return problems
        elif len(self.session_def.recordings)==1:
            return {'sync_ref_recording': (type_utils.ProblemLevel.Error, f'sync_ref_recording must not be set when sessions consist of only one recording')}

        type_utils.merge_problem_dicts(problems, self._check_recordings([self.sync_ref_recording], 'sync_ref_recording', strict_check))
        type_utils.merge_problem_dicts(problems, self._check_recordings(self.sync_ref_average_recordings, 'sync_average_recordings', strict_check))
        # check if sync_ref_recording is a replaced recording
        if self.head_attached_recordings_replace_et_scene is not None and any(r.associated_recording==self.sync_ref_recording for r in self.session_def.recordings if r.name in self.head_attached_recordings_replace_et_scene):
            if strict_check:
                raise ValueError(f'sync_ref_recording cannot be a recording that is replaced by a head-attached camera recording')
            else:
                problems['sync_ref_recording'] = (type_utils.ProblemLevel.Error, f'sync_ref_recording cannot be a recording that is replaced by a head-attached camera recording')
        if self.sync_ref_do_time_stretch is None:
            if strict_check:
                raise ValueError(f'sync_ref_do_time_stretch should be set in the study setup when sync_ref_recording is set')
            else:
                problems['sync_ref_do_time_stretch'] = (type_utils.ProblemLevel.Error, f'sync_ref_do_time_stretch should be set when sync_ref_recording is set')
        if self.sync_ref_do_time_stretch:
            for a in ['sync_ref_stretch_which', 'sync_ref_average_recordings']:
                if getattr(self,a) is None:
                    if strict_check:
                        raise ValueError(f'{a} should be set in the study setup when sync_ref_recording is set and sync_ref_do_time_stretch is enabled')
                    else:
                        problems[a] = (type_utils.ProblemLevel.Error, f'{a} should be set when sync_ref_recording is set and sync_ref_do_time_stretch is enabled')
        if self.sync_ref_average_recordings and self.sync_ref_recording in self.sync_ref_average_recordings:
            if strict_check:
                raise ValueError(f'Recording {self.sync_ref_recording} is the reference recording for sync, should not be specified in sync_average_recordings')
            else:
                problems['sync_ref_average_recordings'] = (type_utils.ProblemLevel.Error, f'Recording {self.sync_ref_recording} is the reference recording for sync, cannot be specified in sync_average_recordings')
        if not any(cs['event_type']==annotation.EventType.Sync_Camera for cs in self.coding_setup):
            if strict_check:
                raise ValueError('When sync_ref_recording is set, coding of camera sync points should be set up in coding_setup')
            else:
                problems['coding_setup'] = (type_utils.ProblemLevel.Error, f'if sync_ref_recording is set, a {annotation.tooltip_map[annotation.EventType.Sync_Camera]}s should be set up to be coded')
                type_utils.merge_problem_dicts(problems, {'sync_ref_recording': (type_utils.ProblemLevel.Error, f'sync_ref_recording is set, but no {annotation.tooltip_map[annotation.EventType.Sync_Camera]}s are not set up to be coded in coding_setup')})
        return problems

    def _check_make_video(self, strict_check) -> type_utils.ProblemDict:
        problems = self._check_recordings(self.mapped_video_make_which, 'mapped_video_make_which', strict_check)
        type_utils.merge_problem_dicts(problems,
                   self._check_recordings(self.mapped_video_recording_colors, 'mapped_video_recording_colors', strict_check))
        type_utils.merge_problem_dicts(problems,
                   self._check_recordings(self.mapped_video_show_gaze_on_plane_in_which, 'mapped_video_show_gaze_on_plane_in_which', strict_check))
        type_utils.merge_problem_dicts(problems,
                   self._check_recordings(self.mapped_video_show_camera_in_which, 'mapped_video_show_camera_in_which', strict_check))
        type_utils.merge_problem_dicts(problems,
                   self._check_recordings(self.mapped_video_show_gaze_vec_in_which, 'mapped_video_show_gaze_vec_in_which', strict_check))
        if self.mapped_video_make_which:
            # check have colors for all eye tracker recordings
            all_recs = {r.name for r in self.session_def.recordings if r.type==session.RecordingType.Eye_Tracker}
            if self.mapped_video_recording_colors:
                missing = list(all_recs-set(self.mapped_video_recording_colors.keys()))
            else:
                missing = list(all_recs)
            if missing:
                msg = f'Colors need to be defined for all eye tracker recordings. Missing for {missing[0] if len(missing)==1 else missing}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems,{'mapped_video_recording_colors': (type_utils.ProblemLevel.Error, msg)})
        return problems

    def field_problems(self) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        type_utils.merge_problem_dicts(problems, self._check_session_def(False))
        type_utils.merge_problem_dicts(problems, self._check_coding_setup(False))
        type_utils.merge_problem_dicts(problems, self._check_auto_markers(False))
        type_utils.merge_problem_dicts(problems, self._check_individual_markers(False))
        type_utils.merge_problem_dicts(problems, self._check_head_attached_recordings(False))
        type_utils.merge_problem_dicts(problems, self._check_sync_ref(False))
        type_utils.merge_problem_dicts(problems, self._check_make_video(False))
        return problems

    def store_as_json(self, path: str|pathlib.Path|None=None):
        if not path:
            path = guess_config_dir(self.working_directory)
        path = pathlib.Path(path)
        f_path = path
        if f_path.is_dir():
            f_path /= self.default_json_file_name
        else:
            path = f_path.parent
            # prep for dump to file
        to_dump = {k:copy.deepcopy(getattr(self,k)) for k in vars(self) if not k.startswith('_') and k not in ['session_def','planes','working_directory']}    # session_def and planes will be populated from contents in the provided folder, and working_directory as the provided path
        # filter out defaulted
        to_dump = {k:to_dump[k] for k in to_dump if k not in study_defaults or study_defaults[k]!=to_dump[k]}
        # also filter out defaults in some subfields, and ensure they are not typeddicts (json encoder balks over that)
        def _remove_defaults_recursive(d: dict, defaults: dict, types: dict[str, typing.Type]) -> dict:
            # check defaults
            for k in defaults:
                if isinstance(defaults[k], typed_dict_defaults.Field):
                    defaults[k] = defaults[k].default_factory()

            # NB: this also converts to plain dict, so json encoder can handle it
            out = {}
            for k in d:
                if k in defaults and isinstance(d[k], dict):
                    # recurse
                    t_defaults = t_types = {}
                    if typed_dict_defaults.is_typeddictdefault(it:=gt_utils.unpack_none_union(types[k])[0]):
                        t_defaults = it._field_defaults
                        t_types    = it.__annotations__
                    elif typing.get_origin(it)==typing.Union and any((typed_dict_defaults.is_typeddictdefault(tt) for tt in typing.get_args(it))):
                        # the only case where this happens is the AutoCodeSyncPoints|AutoCodeEpisodes case, so special case to select the right one
                        if annotation.type_map[d['event_type']]==annotation.Type.Point:
                            tt = AutoCodeSyncPoints
                        else:
                            tt = AutoCodeEpisodes
                        t_defaults = tt._field_defaults
                        t_types    = tt.__annotations__
                    d[k] = _remove_defaults_recursive(d[k], t_defaults, t_types)
                    if not d[k] and not defaults[k] is None:
                        # all defaulted, skip
                        continue

                # now check if not equal to default. If not equal, store
                if k not in defaults or d[k]!=defaults[k]:
                    out[k] = d[k]
            return out

        to_dump['coding_setup'] = [_remove_defaults_recursive(cs, EventSetup._field_defaults, EventSetup.__annotations__) for cs in to_dump['coding_setup']]

        # dump to file
        json.dump(to_dump, f_path)
        # this doesn't store any files itself, but triggers the contained info to be stored
        self.session_def.store_as_json(path)
        for p in self.planes:
            p_dir = path / p.name
            if not p_dir.is_dir():
                p_dir.mkdir()
            p.store_as_json(p_dir)

    @staticmethod
    def get_empty(path: str | pathlib.Path) -> 'Study':
        # returns a minimally set up config, with every required argument set empty, and the rest default-initialized
        return Study(
            session.SessionDefinition(),
            [],[],[],path,
            strict_check=False
        )

    @staticmethod
    def load_from_json(path: str | pathlib.Path, strict_check: bool=True) -> 'Study':
        path = pathlib.Path(path)
        if path.is_dir():
            d_path = path / Study.default_json_file_name
        else:
            d_path = path
        # get kwds
        kwds = json.load(d_path)

        # backwards compatibility for version 1 project setups
        if 'coding_setup' not in kwds:
            # fix up 'video_' parameters for backwards compatibility
            for k in list(kwds.keys()):
                if k.startswith('video_'):
                    kwds[f'mapped_{k}'] = kwds.pop(k)
            # stored as list of tuples (with enum values as keys), unpack
            if 'planes_per_episode' in kwds:
                kwds['planes_per_episode'] = {annotation.EventType(k):v for k,v in kwds['planes_per_episode']}
                if 'auto_code_episodes' in kwds:
                    kwds['auto_code_episodes'] = {annotation.EventType(k):v for k,v in kwds['auto_code_episodes']}
            # help with enum roundtrip
            if 'episodes_to_code' in kwds:
                kwds['episodes_to_code'] = {annotation.EventType(e) for e in kwds['episodes_to_code']}
            if 'validate_dq_types' in kwds:
                kwds['validate_dq_types']= {_data_types.data_type_val_to_enum_val(d) for d in kwds['validate_dq_types']}
            if 'mapped_video_which_gaze_type_on_plane' in kwds:
                kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])
            # backward compatibility: ensure value are stored in sets, not lists
            if 'planes_per_episode' in kwds:
                kwds['planes_per_episode'] = {k:set(v) for k,v in kwds['planes_per_episode'].items()}
            if 'sync_ref_average_recordings' in kwds:
                kwds['sync_ref_average_recordings'] = set(kwds['sync_ref_average_recordings'])
            if 'mapped_video_make_which' in kwds:
                kwds['mapped_video_make_which'] = set(kwds['mapped_video_make_which'])
            # backwards compatibility, help with named tuple roundtrip
            for k in ('overlay_video_gaze_vid_pos_color','overlay_video_gaze_world_pos_color','mapped_video_projected_vidPos_color','mapped_video_projected_world_pos_color','mapped_video_projected_left_ray_color','mapped_video_projected_right_ray_color','mapped_video_projected_average_ray_color'):
                if k in kwds and kwds[k] is not None and not isinstance(kwds[k],RgbColor):
                    kwds[k] = RgbColor(*kwds[k])
            if 'mapped_video_recording_colors' in kwds and any((not isinstance(kwds['mapped_video_recording_colors'][k],RgbColor) for k in kwds['mapped_video_recording_colors'])):
                kwds['mapped_video_recording_colors'] = {k: RgbColor(*kwds['mapped_video_recording_colors'][k]) for k in kwds['mapped_video_recording_colors']}
            # backwards compatibility, rename 'auto_code_trial_episodes'
            if 'auto_code_trial_episodes' in kwds:
                kwds['auto_code_episodes'] = {annotation.EventType.Trial: kwds.pop('auto_code_trial_episodes')}
            # backwards compatibility, upgrade markers to markerIDs if they're bare ints
            if 'auto_code_sync_points' in kwds and kwds['auto_code_sync_points'] is not None and 'markers' in kwds['auto_code_sync_points']:
                markers = kwds['auto_code_sync_points']['markers']
                kwds['auto_code_sync_points']['markers'] = set()
                for m in markers:
                    if not isinstance(m,int):
                        kwds['auto_code_sync_points']['markers'].add(m)
                        continue
                    # find corresponding marker in individual_markers
                    im = [im for im in kwds['individual_markers'] if im.id==m]
                    if not im:
                        # incorrect setup, referring to a non-existing individual marker. ignore
                        continue
                    else:
                        kwds['auto_code_sync_points']['markers'].add(gt_marker.MarkerID(im[0].id, im[0].aruco_dict_id))
            if 'auto_code_episodes' in kwds:
                for e in kwds['auto_code_episodes']:
                    for f in ('start_markers','end_markers'):
                        if not f in kwds['auto_code_episodes'][e]:
                            continue
                        markers = kwds['auto_code_episodes'][e][f]
                        kwds['auto_code_episodes'][e][f] = []
                        for m in markers:
                            if not isinstance(m,int):
                                kwds['auto_code_episodes'][e][f].append(m)
                                continue
                            # find corresponding marker in individual_markers
                            im = [im for im in kwds['individual_markers'] if im.id==m]
                            if not im:
                                # incorrect setup, referring to a non-existing individual marker. ignore
                                continue
                            else:
                                kwds['auto_code_episodes'][e][f].append(gt_marker.MarkerID(im[0].id, im[0].aruco_dict_id))
            # backwards compatibility for mapped video marker and axes settings
            kwds.pop('mapped_video_process_individual_markers_for_all_frames',None) # setting doesn't exist anymore
            if 'mapped_video_show_detected_markers' in kwds:
                mapped_video_show_detected_markers = kwds.pop('mapped_video_show_detected_markers')
                kwds['mapped_video_plane_marker_color'] = study_defaults['mapped_video_plane_marker_color'] if mapped_video_show_detected_markers else None
                kwds['mapped_video_recovered_plane_marker_color'] = study_defaults['mapped_video_recovered_plane_marker_color'] if mapped_video_show_detected_markers else None
            if 'mapped_video_show_plane_axes' in kwds or 'mapped_video_show_board_axes' in kwds:
                mapped_video_show_plane_axes = kwds.pop('mapped_video_show_plane_axes') if 'mapped_video_show_plane_axes' in kwds else kwds.pop('mapped_video_show_board_axes')
                kwds['mapped_video_plane_axis_arm_length'] = study_defaults['mapped_video_plane_axis_arm_length'] if mapped_video_show_plane_axes else None
            if 'mapped_video_show_individual_marker_axes' in kwds:
                mapped_video_show_individual_marker_axes = kwds.pop('mapped_video_show_individual_marker_axes')
                kwds['mapped_video_individual_marker_axis_arm_length'] = study_defaults['mapped_video_individual_marker_axis_arm_length'] if mapped_video_show_individual_marker_axes else None
            if 'mapped_video_show_unexpected_markers' in kwds:
                mapped_video_show_unexpected_markers = kwds.pop('mapped_video_show_unexpected_markers')
                kwds['mapped_video_unexpected_marker_color'] = study_defaults['mapped_video_unexpected_marker_color'] if mapped_video_show_unexpected_markers else None
            if 'mapped_video_show_rejected_markers' in kwds:
                mapped_video_show_rejected_markers = kwds.pop('mapped_video_show_rejected_markers')
                kwds['mapped_video_rejected_marker_color'] = study_defaults['mapped_video_rejected_marker_color'] if mapped_video_show_rejected_markers else None

            # now build coding setup (port to v2 settings)
            kwds['coding_setup'] = []
            # now process episodes to code
            for e in kwds['episodes_to_code']:
                kwds['coding_setup'].append(EventSetup(
                    event_type  = e,
                    name        = e.value,
                    description = annotation.tooltip_map.get(e, ''),
                    hotkey      = annotation.default_hotkeys.get(e, None),
                    planes      = set(kwds['planes_per_episode'].get(e, set())),
                ))
                # check auto coding setup
                if annotation.type_map[e]==annotation.Type.Point:
                    if kwds.get('auto_code_sync_points', None) is not None:
                        kwds['coding_setup'][-1]['auto_code'] = kwds.pop('auto_code_sync_points')
                else:
                    if kwds.get('auto_code_episodes', None) is not None and e in kwds['auto_code_episodes']:
                        kwds['coding_setup'][-1]['auto_code'] = kwds['auto_code_episodes'].pop(e)
                # check ET sync setup
                if e==annotation.EventType.Sync_ET_Data and kwds.get('get_cam_movement_for_et_sync_method','') in ['plane','function']:
                    kwds['coding_setup'][-1]['sync_setup'] = EtSyncSetup(
                        get_cam_movement_method     = kwds.pop('get_cam_movement_for_et_sync_method', ''),
                        get_cam_movement_function   = kwds.pop('get_cam_movement_for_et_sync_function', None),
                    )
                    if 'sync_et_to_cam_use_average' in kwds:
                        kwds['coding_setup'][-1]['sync_setup'].use_average = kwds.pop('sync_et_to_cam_use_average')
                # check validation setup
                if e==annotation.EventType.Validate and any((k.startswith('validate_') for k in kwds)):
                    kwds['coding_setup'][-1]['validation_setup'] = ValidationSetup()
                    for k in ValidationSetup.__annotations__:
                        if (old_key := 'validate_'+k) in kwds:
                            kwds['coding_setup'][-1]['validation_setup'][k] = kwds.pop(old_key)
                    # two further fields that have been renamed
                    if 'validate_dq_types' in kwds:
                        kwds['coding_setup'][-1]['validation_setup']['data_types'] = kwds.pop('validate_dq_types')
                    if 'validate_allow_dq_fallback' in kwds:
                        kwds['coding_setup'][-1]['validation_setup']['allow_data_type_fallback'] = kwds.pop('validate_allow_dq_fallback')
            # dump some now unused keys
            kwds.pop('episodes_to_code', None)
            kwds.pop('planes_per_episode', None)
            kwds.pop('auto_code_episodes', None)
        else:
            # for v2 setups:
            # ensure enum round trip
            for i in range(len(kwds['coding_setup'])):
                kwds['coding_setup'][i]['event_type'] = annotation.EventType(kwds['coding_setup'][i]['event_type'])
                if 'validation_setup' in kwds['coding_setup'][i] and kwds['coding_setup'][i]['validation_setup'] is not None and 'data_types' in kwds['coding_setup'][i]['validation_setup']:
                    kwds['coding_setup'][i]['validation_setup']['data_types'] = {_data_types.data_type_val_to_enum_val(d) for d in kwds['coding_setup'][i]['validation_setup']['data_types']}
                if 'gaze_offset_setup' in kwds['coding_setup'][i] and kwds['coding_setup'][i]['gaze_offset_setup'] is not None and 'data_types' in kwds['coding_setup'][i]['gaze_offset_setup']:
                    kwds['coding_setup'][i]['gaze_offset_setup']['data_types'] = {_data_types.data_type_val_to_enum_val(d) for d in kwds['coding_setup'][i]['gaze_offset_setup']['data_types']}
            if 'mapped_video_which_gaze_type_on_plane' in kwds:
                kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])

        # get session def
        s_path = path / 'session_def.json'
        if not s_path.is_file():
            return None
        sess_def = session.SessionDefinition.load_from_json(s_path)

        # get planes
        planes: list[plane.Definition] = []
        for p_dir in path.iterdir():
            if not p_dir.is_dir():
                continue
            p_file = p_dir / plane.Definition.default_json_file_name
            if not p_file.is_file():
                continue
            planes.append(plane.Definition.load_from_json(p_file))

        return Study(sess_def, planes, working_directory=path.parent, **kwds, strict_check=strict_check)

# get defaults for default argument of Study constructor
_params = inspect.signature(Study.__init__).parameters
study_defaults = {k:d for k in _params if (d:=_params[k].default)!=inspect._empty}
study_parameter_types = {k:_params[k].annotation for k in _params if k not in ['self','strict_check']}
def _get_gv_data_type_doc(dt: _data_types.DataType):
    t,doc = _data_types.get_explanation(dt)
    return (dt, type_utils.GUIDocInfo(t,doc))
def _get_annotation_event_doc(a: annotation.EventType, children: dict = None):
    t = annotation.tooltip_map[a]
    doc = {
        annotation.EventType.Trial: 'Denotes an episode for which to map gaze to plane(s). This determines for which segments there will be gaze data when running the Export Trials.',
        annotation.EventType.Validate: 'Denotes an episode during which a participant looked at a validation poster, to be used to run glassesValidator to compute data quality of the gaze data.',
        annotation.EventType.Sync_Camera: 'Time point (frame from video) when a synchronization event happened, used for synchronizing different recordings.',
        annotation.EventType.Sync_ET_Data: 'Episode to be used for synchronization of eye tracker data to scene camera (e.g. using VOR).'
    }.get(a)
    if children is None:
        children = {}
    return (a, type_utils.GUIDocInfo(t,doc, children))
_gaze_type_doc = {
    gaze_worldref.Type.Scene_Video_Position : type_utils.GUIDocInfo('Gaze position on scene video', 'Gaze position on the scene video, projected to the plane.'),
    gaze_worldref.Type.World_3D_Point       : type_utils.GUIDocInfo('3D gaze position', '3D gaze position in the world provided by the eye tracker, projected to the plane.'),
    gaze_worldref.Type.Left_Eye_Gaze_Vector : type_utils.GUIDocInfo('Left eye gaze vector', 'Projection of the left eye\'s gaze vector to the plane.'),
    gaze_worldref.Type.Right_Eye_Gaze_Vector: type_utils.GUIDocInfo('Right eye gaze vector', 'Projection of the right eye\'s gaze vector to the plane.'),
    gaze_worldref.Type.Average_Gaze_Vector  : type_utils.GUIDocInfo('Average of gaze vectors', 'Average of the projections of the left and right eyes\' gaze vectors to the plane.'),
}
study_parameter_doc = {
    'allow_duplicated_markers': type_utils.GUIDocInfo('Allow duplicated markers?', 'If enabled, the same marker can be used in multiple places in the coding setup (e.g. for auto coding and/or in planes). If disabled, each marker can only be used once. Enabling this may be ok, if the duplicate markers never occur at the same time. Still, use at your own risk.'),
    'import_do_copy_video': type_utils.GUIDocInfo('Copy video during import?', 'If not enabled, the scene video of an eye tracker recording, or the video of an external camera is not copied to the gazeMapper recording directory during import. Instead, the video will be loaded from the recording\'s source directory (so do not move it). Ignored when the video must be transcoded to be processed with gazeMapper.'),
    'import_source_dir_as_relative_path': type_utils.GUIDocInfo('Store source directory as relative path?', 'Specifies whether the path to the source directory stored in the recording info file is an absolute path (this option is not enabled) or a relative path (enabled). If a relative path is used, the imported recording and the source directory can be moved to another location, and the source directory can still be found as long as the relative path (e.g., one folder up and in the directory "original recordings": "../original recordings") doesn\'t change.'),
    'import_known_custom_eye_trackers': type_utils.GUIDocInfo('Registered custom eye trackers', 'gazeMapper allows importing generic eye trackers for which no specific support is implemented, if their recording data is preprocessed to conform to glassesTools\' generic data format. Here you can define specific known generic eye tracker names that you may import.'),
    'head_attached_recordings_replace_et_scene': type_utils.GUIDocInfo('Head-attached recording: override scene camera', 'gazeMapper allows using recordings from a head-attached camera to replace pose determination done from the scene camera image. It might make sense to enable this when the image quality of the scene camera is not good enough. Requires instrinsics and extrinsics (transformation from the head-attached camera to the scene camera) of the head-attached camera to be known.'),
    'overlay_video_gaze_vid_pos_color': type_utils.GUIDocInfo('Gaze overlay video: Color for gaze position on video', 'Color used for drawing the recorded gaze position on the scene video.'),
    'overlay_video_gaze_world_pos_color': type_utils.GUIDocInfo('Gaze overlay video: Color for 3D gaze position', 'Color used for drawing the recorded 3D gaze position in the world. Not drawn if value is not set.'),
    'overlay_video_gaze_vid_pos_radius': type_utils.GUIDocInfo('Gaze overlay video: Radius for gaze position on video', 'Radius of circle used for drawing the recorded gaze position on the scene video.'),
    'overlay_video_gaze_world_pos_radius': type_utils.GUIDocInfo('Gaze overlay video: Radius for 3D gaze position', 'Radius of circle used for drawing the recorded 3D gaze position in the world.'),
    'overlay_video_gaze_vid_pos_thickness': type_utils.GUIDocInfo('Gaze overlay video: Thickness for gaze position on video', 'Line thickness of circle used for drawing the recorded gaze position on the scene video.'),
    'overlay_video_gaze_world_pos_thickness': type_utils.GUIDocInfo('Gaze overlay video: Thickness for 3D gaze position', 'Line thickness of circle used for drawing the recorded 3D gaze position in the world.'),
    'sync_ref_recording': type_utils.GUIDocInfo('Synchronization: Reference recording', 'If there are multiple recordings, sets to which recording all other recordings will be synchronized.'),
    'sync_ref_do_time_stretch': type_utils.GUIDocInfo('Synchronization: Do time stretch?', 'If enabled, multiple sync points are used to calculate a time stretch factor to compensate for clock drift when synchronizing multiple recordings.'),
    'sync_ref_stretch_which': type_utils.GUIDocInfo('Synchronization: Stretch which recording', 'Which recording(s) should be corrected for clock drift if "Synchronization: Do time stretch?" is enabled.',{
        None: {     # indicates the doc specification applies to the contained values
            'ref': type_utils.GUIDocInfo('Reference recording', 'The time signal of the reference recording is stretched to compensate for clock drift.'),
            'other': type_utils.GUIDocInfo('Other recording(s)', 'The time signal of the other recording(s) is stretched to compensate for clock drift.')
        }
    }),
    'sync_ref_average_recordings': type_utils.GUIDocInfo('Synchronization: Average recordings?', 'Whether to average the clock drifts for multiple recordings if "Synchronization: Do time stretch?" is enabled.'),
    'export_output3D': type_utils.GUIDocInfo('Mapped data export: include 3D fields', 'Determines whether gaze positions on the plane in the scene camera reference frame are exported when invoking the Export Trials action.'),
    'export_output2D': type_utils.GUIDocInfo('Mapped data export: include 2D fields', 'Determines whether gaze positions on the plane in the plane\'s reference frame are exported when invoking the Export Trials action.'),
    'export_only_code_marker_presence': type_utils.GUIDocInfo('Mapped data export: only include marker presence?', 'If enabled, for each marker only a single column is added to the export created by the Export Trials action, indicating whether the given marker was detected or not on a given frame. If not enabled, marker pose information is included in the export.'),
    'mapped_video_make_which': type_utils.GUIDocInfo('Mapped video: Which recordings', 'Indicates one or multiple recordings for which to make videos of the eye tracker scene camera or external camera (synchronized to one of the recordings if there are multiple) showing detected plane origins, detected individual markers and gaze from any other recordings eye tracker recordings. Also shown for eye tracker recordings are gaze on the scene video from the eye tracker, gaze projected to the detected planes. Each only if available, and enabled in the below video generation settings.'),
    'mapped_video_recording_colors': type_utils.GUIDocInfo('Mapped video: Recording colors', 'Colors used for drawing each recording\'s gaze point, scene camera and gaze vector (depending on settings).'),
    'mapped_video_projected_vidPos_color': type_utils.GUIDocInfo('Mapped video: Color for gaze position on plane', 'Color used for drawing the recorded gaze position on the scene video transformed to the plane. Not drawn if value is not set.'),
    'mapped_video_projected_world_pos_color': type_utils.GUIDocInfo('Mapped video: Color for 3D gaze position on plane', 'Color used for drawing the projection on a plane of the recorded 3D gaze position in the world. Not drawn if value is not set.'),
    'mapped_video_projected_left_ray_color': type_utils.GUIDocInfo('Mapped video: Color for left eye gaze vector projected to plane', 'Color used for drawing the projection to a plane of the recorded left eye\'s gaze vector. Not drawn if value is not set.'),
    'mapped_video_projected_right_ray_color': type_utils.GUIDocInfo('Mapped video: Color for right eye gaze vector projected to plane', 'Color used for drawing the projection to a plane of the recorded right eye\'s gaze vector. Not drawn if value is not set.'),
    'mapped_video_projected_average_ray_color': type_utils.GUIDocInfo('Mapped video: Color for average of gaze vectors projected to plane', 'Color used for drawing the average projection to a plane of the recorded left and right eyes\' gaze vectors. Not drawn if value is not set.'),
    'mapped_video_process_planes_for_all_frames': type_utils.GUIDocInfo('Mapped video: Process all planes for all frames?', 'If enabled, shows detection results for all planes for all frames. If not enabled, detection of each plane is only shown during the episode(s) to which it is assigned.'),
    'mapped_video_process_annotations_for_all_recordings': type_utils.GUIDocInfo('Mapped video: Process all annotations for all recordings?', 'Episode annotations are shown in a bar on the bottom of the screen. If enabled, annotations for not only the recording for which the video is made, but also for the other recordings are shown in this bar.'),
    'mapped_video_plane_marker_color': type_utils.GUIDocInfo('Mapped video: Plane marker color', 'Color used for drawing the detected markers belonging to planes. Not drawn if value is not set.'),
    'mapped_video_recovered_plane_marker_color': type_utils.GUIDocInfo('Mapped video: Plane marker color (recovered)', 'Color used for drawing the detected markers belonging to planes that were identified during a second stage in the ArUco pipeline where markers are found based on their expected position extrapolated from identified nearby markers on the plane. Not drawn if value is not set.'),
    'mapped_video_plane_axis_arm_length': type_utils.GUIDocInfo('Mapped video: Plane axis arm length','Length used for drawing axes that indicate the orientation of a detected plane. In the same unit as the plane (usually mm). Not drawn if value is not set.'),
    'mapped_video_individual_marker_color': type_utils.GUIDocInfo('Mapped video: Individual marker color', 'Color used for drawing the detected individual markers. Not drawn if value is not set.'),
    'mapped_video_individual_marker_axis_arm_length': type_utils.GUIDocInfo('Mapped video: Individual marker axis arm length','Length used for drawing axes that indicate the orientation of a detected individual marker (if its size is set). In the same unit as the markers (usually mm). Not drawn if value is not set.'),
    'mapped_video_unexpected_marker_color': type_utils.GUIDocInfo('Mapped video: Unexpected marker color', 'Color used for drawing the detected markers belonging to planes. Not drawn if value is not set.'),
    'mapped_video_rejected_marker_color': type_utils.GUIDocInfo('Mapped video: Rejected marker color', 'Color used for drawing marker candidates that were rejected at some stage in the ArUco pipeline. For (setup) debug purposes. Not drawn if value is not set. (255,0,0) may be a good color.'),
    'mapped_video_show_sync_func_output': type_utils.GUIDocInfo('Mapped video: Show sync function output?', 'If enabled, draw the output of the function on the output video. Applies if the "Gaze data synchronization: Function for camera movement" setting is set to "function".'),
    'mapped_video_show_gaze_on_plane_in_which': type_utils.GUIDocInfo('Mapped video: Videos on which to draw gaze projected to plane', 'For the listed recordings, gaze projected to the plane (both the gaze point on the scene video, and the left and right eyes\' gaze vectors if available) is drawn on the video for eye tracker recordings. If there are multiple recordings in a session, gaze positions for the other recordings (if available) will also be drawn on the indicated video(s).'),
    'mapped_video_show_gaze_vec_in_which': type_utils.GUIDocInfo('Mapped video: Videos on which to draw gaze vectors(s)', 'For the listed recordings, a line is drawn between the positions of the cameras of other recordings and the gaze position of these recordings projected to the plane in the generated video. Only for eye tracker recordings.'),
    'mapped_video_show_camera_in_which': type_utils.GUIDocInfo('Mapped video: Videos on which to draw camera position(s)', 'For the listed recordings, the position of the cameras of other recordings is marked in the generated video.'),
    'mapped_video_which_gaze_type_on_plane': type_utils.GUIDocInfo('Mapped video: Which gaze on plane to show?', 'Sets which gaze-on-plane (e.g. from gaze position on the scene video or from gaze vectors projected to the plane) is used for the gaze positions shown in the generated videos.', _gaze_type_doc),
    'mapped_video_which_gaze_type_on_plane_allow_fallback': type_utils.GUIDocInfo('Mapped video: Allow fallback to showing gaze on plane based on scene video gaze?', 'Sets if it is allowed to fall back to using the projection of the gaze position on the plane derived from the gaze position on the video if the gaze-on-plane type specified in the "Mapped video: Which gaze on plane to show?" setting is not available.'),
    'mapped_video_gaze_to_plane_margin': type_utils.GUIDocInfo('Mapped video: Gaze position margin','Gaze position more than this factor outside a defined plane will not be drawn.'),
    'gui_num_workers': type_utils.GUIDocInfo('Number of workers','Each action is processed by a worker and each worker can handle one action at a time. Having more workers means more actions are processed simultaneously, but having too many will not provide any gain and might freeze the program and your whole computer. Since much of the processing utilizes more than one processor thread, set this value to significantly less than the number of threads available in your system. NB: If you currently have running or enqueued jobs, the number of workers will only be changed once all have completed or are cancelled.'),
}
event_setup_doc = {
    'event_type': type_utils.GUIDocInfo('Coded event type', 'Type of the event to be coded.',{
        None: # None indicates the doc specification applies to the contained values
            dict([_get_annotation_event_doc(a) for a in annotation.EventType])
    }),
    'name': type_utils.GUIDocInfo('Event name', 'Name of the event to be shown in the coding GUI.'),
    'description': type_utils.GUIDocInfo('Event description', 'Description of the event to be shown in the coding GUI as tooltip.'),
    'hotkey': type_utils.GUIDocInfo('Event hotkey', 'Hotkey to be used for coding this event in the coding GUI.'),
    'planes': type_utils.GUIDocInfo('Planes for event', 'Set of planes which will be looked for and gaze mapped to during the episode.'),
    'which_recording': type_utils.GUIDocInfo('Which recording', 'Recording for which you should code this event. If not set, the event is taken from the reference recording.'),
    'auto_code': type_utils.GUIDocInfo('Auto-coding setup', 'Setup for automatically coding this event based on individual marker detections.',{
        'markers': type_utils.GUIDocInfo('Marker(s)', 'Set of marker IDs whose appearance indicates a synchronization timepoint.'),
        'start_markers': type_utils.GUIDocInfo('Start marker(s)', 'A single marker ID or a sequence of marker IDs that indicate the start of an episode.'),
        'end_markers': type_utils.GUIDocInfo('End marker(s)', 'A single marker ID or a sequence of marker IDs that indicate the end of an episode.'),
        'max_gap_duration': type_utils.GUIDocInfo('Maximum gap duration', 'Maximum gap (number of frames) in marker detections that will be filled in (ignored).'),
        'max_intermarker_gap_duration': type_utils.GUIDocInfo('Maximum intermarker gap duration', 'Maximum gap (number of frames) that is allowed between the detection of two markers in a sequence.'),
        'min_duration': type_utils.GUIDocInfo('Minimum duration', 'Minimum duration (number of frames) that a marker should be detected. Shorter runs are removed.')
    }),
    'sync_setup': type_utils.GUIDocInfo('Eye tracker synchronization setup', 'Setup for synchronizing eye tracker data to the scene camera based on plane tracking data.',{
        'get_cam_movement_method': type_utils.GUIDocInfo('Gaze data synchronization: Method to get camera movement', 'Method used to derive the head motion for synchronizing eye tracker data and scene camera.',{
        None: {     # indicates the doc specification applies to the contained values
            'plane': type_utils.GUIDocInfo('Plane', 'Head movement is represented by the position of the origin of the plane in the scene camera video (the plane that is set up to be used for "Sync ET Data" episodes), as extracted through pose estimation or homography using a gazeMapper plane.'),
            'function': type_utils.GUIDocInfo('Function', 'A user-specified function (configured using the "Gaze data synchronization: Function for camera movement" setting) will be called for each frame of the scene video in a "Sync ET Data" episode and is expected to return the location of the target the participant was looking at.')
        }
        }),
        'get_cam_movement_function': type_utils.GUIDocInfo('Gaze data synchronization: Function for camera movement', 'Setup for function to use for deriving the head motion when synchronizing eye tracker data and scene camera if "Gaze data synchronization: Method to get camera movement" is set to "function".',{
            'module_or_file': type_utils.GUIDocInfo('Module or file', 'Importable module or file (can be a full path) that contains the function to run.'),
            'function': type_utils.GUIDocInfo('Function', 'Name of the function to run.'),
            'parameters': type_utils.GUIDocInfo('Parameters', 'Set of parameters and values to pass to the function. The frame to process (np.ndarray) is the first (positional) input passed to the function, and should not be specified in this set.'),
        }),
        'use_average': type_utils.GUIDocInfo('Gaze data synchronization: Use average?', 'Whether to use the average offset of multiple sync episodes. If not enabled, the offset for the first sync episode is used, the rest are ignored.'),
    }),
    'validation_setup': type_utils.GUIDocInfo('glassesValidator setup', 'Setup for determining the data quality of gaze data based on looking at a validation poster during this event (using glassesValidator).',{
        'do_global_shift': type_utils.GUIDocInfo('Apply global shift?', 'If enabled, for each validation interval the median position will be removed from the gaze data and the mean from the targets, removing any overall shift of the data. This improves the matching of fixations to targets when there is a significant overall offset in the data. It may fail (backfire) if there are data samples far outside the range of the validation targets, or if there is no data for some targets.'),
        'max_dist_fac': type_utils.GUIDocInfo('Maximum distance factor', 'Factor for determining distance limit when assigning fixation points to validation targets. If for a given target the closest fixation point is further away than <factor>*[minimum intertarget distance], then no fixation point will be assigned to this target, i.e., it will not be matched to any fixation point. Set to a large value to essentially disable.'),
        'data_types': type_utils.GUIDocInfo('Data types', 'Selects the data types for which you would like to calculate data quality for each of the recordings. When none are selected, a good default is used for each recording. When none of the selected types is available, depending on the `allow_data_type_fallback` setting, either an error is thrown or default chosen depending on what is available is used instead. Whether a data type is available depends on what type of gaze information is available for a recording, as well as whether the camera is calibrated.',{
            None: # None indicates the doc specification applies to the contained values
                dict([_get_gv_data_type_doc(dt) for dt in _data_types.DataType])
        }),
        'allow_data_type_fallback': type_utils.GUIDocInfo('Allow fallback data type?', 'If not enabled, an error is raised when the data type(s) indicated in "Data types" are not available. If enabled, a sensible default other data type will be used instead. Does not apply if the "Data types" is not set.'),
        'include_data_loss': type_utils.GUIDocInfo('Include data loss?', 'If enabled, the data quality report will include data loss during the episode selected for each target on the validation poster. This is NOT the data loss of the whole recording and thus not what you want to report in your paper.'),
        'I2MC_settings': type_utils.GUIDocInfo('I2MC settings','Settings for the I2MC fixation classifier used as part of determining the fixation that are assigned to validation targets. Settings that are "<not set>" will be determined based on the provided eye tracking data.',{
            'freq': type_utils.GUIDocInfo('Sampling frequency', 'Sampling frequency of the eye tracking data.'),
            'windowtimeInterp': type_utils.GUIDocInfo('Maximum gap duration for interpolation', 'Maximum duration (s) of gap in the data that is interpolated.'),
            'edgeSampInterp': type_utils.GUIDocInfo('# Edge samples','Amount of data (number of samples) at edges needed for interpolation.'),
            'maxdisp': type_utils.GUIDocInfo('Maximum dispersion', 'Maximum distance (mm) between the two edges of a gap below which the missing data is interpolated.'),
            'windowtime': type_utils.GUIDocInfo('Moving window duration','Length of the moving window (s) used by I2MC to calculate 2-means clustering when processing the data.'),
            'steptime': type_utils.GUIDocInfo('Moving window step', 'Step size (s) by which the moving window is moved.'),
            'downsamples': type_utils.GUIDocInfo('Downsample factors', 'Set of integer decimation factors used to downsample the gaze data as part of I2MC processing.'),
            'downsampFilter': type_utils.GUIDocInfo('Apply Chebyshev filter?', 'If enabled, a Chebyshev low-pass filter is applied when downsampling.'),
            'chebyOrder': type_utils.GUIDocInfo('Chebyshev filter order','Order of the Chebyshev low-pass filter.'),
            'maxerrors': type_utils.GUIDocInfo('Maximum # errors', 'Maximum number of errors before processing of a trial is aborted.'),
            'cutoffstd': type_utils.GUIDocInfo('Fixation cutoff factor', 'Number of standard deviations above mean k-means weights that will be used as fixation cutoff.'),
            'onoffsetThresh': type_utils.GUIDocInfo('Onset/offset Threshold', 'Number of MAD away from median fixation duration. Will be used to walk forward at fixation starts and backward at fixation ends to refine their placement and stop algorithm from eating into saccades.'),
            'maxMergeDist': type_utils.GUIDocInfo('Maximum merging distance', 'Maximum Euclidean distance (mm) between fixations for merging to be possible.'),
            'maxMergeTime': type_utils.GUIDocInfo('Maximum gap duration for merging', 'Maximum time (ms) between fixations for merging to be possible.'),
            'minFixDur': type_utils.GUIDocInfo('Minimum fixation duration', 'Minimum fixation duration (ms) after merging, fixations with shorter duration are removed from output.'),
        }),
        'dynamic_skip_first_duration': type_utils.GUIDocInfo('Dynamic, skip first duration', 'For a glassesValidator plane that is marked as dynamic (i.e. for a validation procedure using the PsychoPy script), how many seconds of data to not use from the beginning of each target interval.'),
        'dynamic_max_gap_duration': type_utils.GUIDocInfo('Dynamic, maximum gap duration', 'For a glassesValidator plane that is marked as dynamic (i.e. for a validation procedure using the PsychoPy script), maximum gap (number of frames) in marker detections that will be filled in (ignored).'),
        'dynamic_min_duration': type_utils.GUIDocInfo('Dynamic, minimum duration', 'Minimum duration (number of frames) that a marker should be detected. Shorter runs are removed.'),
        'dynamic_split_consecutive': type_utils.GUIDocInfo('Dynamic, split consecutive repetitions', 'For a glassesValidator plane that is marked as dynamic (i.e. for a validation procedure using the PsychoPy script), there is the option to show multiple repetitions without intervening segmentation markers. Split these into multiple intervals.'),
    }),
    'gaze_offset_setup': type_utils.GUIDocInfo('Gaze offset calculation setup', 'Setup for calculating the angular gaze offset to specified targets on a plane.',{
        'data_types': type_utils.GUIDocInfo('Data types', 'Selects the data types for which you would like to calculate the gaze offset. When none are selected, a good default is used for each recording. When none of the selected types is available, depending on the `allow_data_type_fallback` setting, either an error is thrown or default depending on what is available is used instead. Whether a data quality type is available depends on what type of gaze information is available for a recording, as well as whether the camera is calibrated.',{
            None: # None indicates the doc specification applies to the contained values
                dict([_get_gv_data_type_doc(dt) for dt in _data_types.DataType])
        }),
        'viewing_distance_mm': type_utils.GUIDocInfo('Viewing distance (mm)', 'Distance from the participant\'s eyes to the plane, in millimeters. Used to calculate angular offsets when using the viewpos_vidpos_homography data type.'),
        'allow_data_type_fallback': type_utils.GUIDocInfo('Allow fallback data type?', 'If not enabled, an error is raised when the data type(s) indicated in "Data types" are not available. If enabled, a sensible default other data type will be used instead. Does not apply if the "Data types" is not set.'),
        'which_targets': type_utils.GUIDocInfo('Which targets', 'Specifies which targets on the plane to calculate the gaze offset for. If not set, all targets defined on the plane are used.')
    }),
}
if _missing_params:=[k for k in _params if k not in study_parameter_doc and k not in ['self','session_def','planes','individual_markers','working_directory','coding_setup','strict_check']]:
    raise NotImplementedError('Documentation missing for parameters:\n- '+'\n- '.join(_missing_params))
del _params
del _missing_params

def guess_config_dir(working_dir: str|pathlib.Path, config_dir_name: str = "config", json_file_name: str = Study.default_json_file_name) -> pathlib.Path:
    # can be invoked with either:
    # 1. the project folder;
    # 2. a session's working directory; or
    # 3. a recording's directory in a session's working directory.
    # So try three levels
    working_dir = pathlib.Path(working_dir).resolve()
    for i in range(3):
        if i>0:
            # try again in parent directory
            working_dir = working_dir.parent
        test_dir = working_dir / config_dir_name
        if not test_dir.is_dir():
            continue
        test_file = test_dir / json_file_name
        if test_file.is_file():
            return test_dir

    raise RuntimeError('config directory not found')


class OverrideLevel(enum.Enum):
    Session     = enum.auto()
    Recording   = enum.auto()
    FunctionArgs= enum.auto()

class StudyOverride:
    default_json_file_name = 'study_def_override.json'

    @staticmethod
    def get_allowed_parameters(level: OverrideLevel, recording_type: session.RecordingType|None = None, for_event_setup: bool = False) -> tuple[list[str],set[str]]:
        # NB: list instead of set as want to keep ordering
        if for_event_setup:
            all_params = event_setup_field_order
            if level==OverrideLevel.Recording and recording_type==session.RecordingType.Camera:
                # if for a camera recording, almost no parameters make sense to set
                allowed_params = ['auto_code']
            else:
                # same allowed parameters for a session- or eye-tracker recording-level override
                allowed_params = ['auto_code', 'sync_setup', 'validation_setup', 'gaze_offset_setup']
            exclude = set(all_params)-set(allowed_params)
        else:
            all_params = list(study_parameter_types.keys())
            if level in [OverrideLevel.Recording, OverrideLevel.FunctionArgs]:
                # these make no sense on a recording level as they are settings for
                # processing functions that run on a whole session at once. As function
                # arguments they may make sense depending on the processing function that
                # is being called, but we cannot differentiate, so reject to be conservative
                # use whitelist
                exclude = set(all_params)
            else:
                # Session-level disallowed parameters
                exclude = {'self', 'session_def', 'planes', 'individual_markers', 'coding_setup', 'working_directory', 'import_known_custom_eye_trackers'}
            allowed_params = [a for a in all_params if a not in exclude]
        return allowed_params, exclude

    def __init__(self, level: OverrideLevel, recording_type: session.RecordingType|None = None, for_event_setup: bool = False, **kwargs):
        self.override_level = level
        self.recording_type = recording_type
        self.for_event_setup = for_event_setup
        self._allowed_params, self._excluded_parameters = self.get_allowed_parameters(level, recording_type, self.for_event_setup)
        self._overridden_params: list[str] = []
        for p in self._allowed_params:
            self.clear_override(p)
        def typecheck_exception_handler(exc: typeguard.TypeCheckError, key: str):
            e = typeguard.TypeCheckError(*exc.args)
            e.append_path_element(f'argument "{key}" {self._get_err_msg()} ({exc._path[0]})')
            raise e from None
        kwargs = StudyOverride._fix_typing(kwargs)
        self._allow_name = self.for_event_setup
        types = EventSetup.__annotations__ if self.for_event_setup else study_parameter_types
        for p in kwargs:
            self._check_parameter(p, f"{StudyOverride.__name__}.__init__(): ")
            # special case: for dict-like object we can unset specific fields, so allow those by skipping check for them
            check_val = kwargs[p]
            if isinstance(check_val,dict) or typing.is_typeddict(check_val) or typed_dict_defaults.is_typeddictdefault(check_val) or type_utils.is_NamedTuple_type(check_val):
                check_val = {k:check_val[k] for k in check_val if check_val[k] is not None}
            typeguard.check_type(check_val, types[p], typecheck_fail_callback=lambda x,_: typecheck_exception_handler(x,p), collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
            setattr(self,p,kwargs[p])
        self._allow_name = False

    def __setattr__(self, name, value):
        if name.startswith('_') or name in {'override_level', 'recording_type', 'for_event_setup'}:
            super(StudyOverride, self).__setattr__(name, value)
            return

        self._check_parameter(name)
        super(StudyOverride, self).__setattr__(name, value)
        if name not in self._overridden_params and (name!='name' or not self.for_event_setup):
            self._overridden_params.append(name)

    def clear_override(self, name: str):
        self._check_parameter(name)
        setattr(self,name,None)
        if name in self._overridden_params:
            self._overridden_params.remove(name)

    def _check_parameter(self, name: str, error_prefix=''):
        if name not in self._allowed_params and (name!='name' or not self._allow_name):
            if name in (event_setup_field_order if self.for_event_setup else study_parameter_types.keys()):
                raise ValueError(f"{error_prefix}You are not allowed to override the '{name}' parameter of a {EventSetup.__name__ if self.for_event_setup else Study.__name__} class {self._get_err_msg()}")
            raise ValueError(f"{error_prefix}Got an unknown parameter '{name}' to override for a {EventSetup.__name__ if self.for_event_setup else Study.__name__} class {self._get_err_msg()}")

    def _get_err_msg(self):
        if self.override_level==OverrideLevel.FunctionArgs:
            err_text = 'in the parameter overrides provided as extra arguments to the processing function'
        else:
            err_text = f'in the {self.override_level.name}-level parameter overrides'
        if self.recording_type is not None:
            err_text += f' for a {self.recording_type.value} recording'
        return err_text

    def apply(self, obj: Study, strict_check=True) -> Study:
        obj = copy.deepcopy(obj)
        if self.for_event_setup:
            the_obj = [(i,cs) for i, cs in enumerate(obj.coding_setup) if cs['name']==getattr(self,'name')]
            if not the_obj:
                raise ValueError(f'Could not find event setup with name "{getattr(self,"name")}" to apply overrides to {self._get_err_msg()}')
            idx,the_obj = the_obj[0]
            the_obj = _apply_impl(the_obj, {p: getattr(self,p) for p in self._overridden_params}, EventSetup.__annotations__)
            obj.coding_setup[idx] = the_obj
        else:
            obj = _apply_impl(obj, {p: getattr(self,p) for p in self._overridden_params}, study_parameter_types)
        # check resulting study is valid
        try:
            obj.check_valid(strict_check)
        except Exception as oe:
            raise ValueError(f'Study setup became invalid {self._get_err_msg()}: {str(oe)}').with_traceback(oe.__traceback__) from None
        return obj

    def get_dump(self) -> dict[str,Any]:
        kwds = {p:getattr(self,p) for p in self._overridden_params}
        if self.for_event_setup and not not kwds:
            kwds['name'] = getattr(self,'name')
        return kwds

    @staticmethod
    def load_from_json(level: OverrideLevel, path: str|pathlib.Path, recording_type: session.RecordingType|None = None) -> tuple['StudyOverride', dict[str,'StudyOverride']]:
        path = pathlib.Path(path)
        if path.is_dir():
            path = path / StudyOverride.default_json_file_name
        kwds = json.load(path)
        # help with enum roundtrip
        if 'validate_dq_types' in kwds:
            kwds['validate_dq_types']= {_data_types.data_type_val_to_enum_val(d) for d in kwds['validate_dq_types']}
        if 'mapped_video_which_gaze_type_on_plane' in kwds:
            kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])
        # backwards compatibility
        compat_fields = ['auto_code_sync_points', 'auto_code_episodes', 'get_cam_movement_for_et_sync_method', 'get_cam_movement_for_et_sync_function', 'sync_et_to_cam_use_average']
        if any((k in kwds for k in compat_fields)) or any((k.startswith('validate_') for k in kwds)):
            kwds['coding_setup'] = []
            if 'auto_code_sync_points' in kwds:
                kwds['coding_setup'].append(dict(
                    name        = annotation.EventType.Sync_Camera.value,
                    auto_code   = kwds.pop('auto_code_sync_points'),
                ))
            if 'auto_code_episodes' in kwds:
                for e in kwds['auto_code_episodes']:
                    kwds['coding_setup'].append(dict(
                        name        = e.value,
                        auto_code   = kwds['auto_code_episodes'][e],
                    ))
                kwds.pop('auto_code_episodes')
            if 'get_cam_movement_for_et_sync_method' in kwds or 'get_cam_movement_for_et_sync_function' in kwds or 'sync_et_to_cam_use_average' in kwds:
                et_sync_setup = EtSyncSetup(
                    get_cam_movement_method     = kwds.pop('get_cam_movement_for_et_sync_method', ''),
                    get_cam_movement_function   = kwds.pop('get_cam_movement_for_et_sync_function', None),
                    use_average                 = kwds.pop('sync_et_to_cam_use_average', False)
                )
                found = False
                for e in kwds['coding_setup']:
                    if e['event_type']==annotation.EventType.Sync_ET_Data:
                        e['sync_setup'] = et_sync_setup
                        found = True
                        break
                if not found:
                    kwds['coding_setup'].append(dict(
                        name        = annotation.EventType.Sync_ET_Data.value,
                        sync_setup  = et_sync_setup,
                ))
            if any((k.startswith('validate_') for k in kwds)):
                # collect all validation settings into a single validation setup
                val_keys = {k:kwds[k] for k in kwds if k.startswith('validate_')}
                for k in val_keys:
                    kwds.pop(k)
                rename_keys = {'dq_types':'data_types', 'allow_dq_type_fallback':'allow_data_type_fallback'}
                val_keys = {rename_keys.get((key:=k.removeprefix('validate_')),key):val_keys[k] for k in val_keys}
                validation_setup = ValidationSetup(**{k: v for k, v in val_keys.items()})
                found = False
                for e in kwds['coding_setup']:
                    if e['event_type']==annotation.EventType.Validate:
                        e['validation_setup'] = validation_setup
                        found = True
                        break
                if not found:
                    kwds['coding_setup'].append(dict(
                        name            = annotation.EventType.Validate.value,
                        validation_setup= validation_setup,
                ))

        # split off coding setup overrides if present
        coding_setup_overrides: dict[str, StudyOverride] = {}
        if 'coding_setup' in kwds:
            for cs in kwds.pop('coding_setup'):
                name = cs.get('name')
                # enum roundtrip
                if 'validation_setup' in cs and cs['validation_setup'] is not None and 'data_types' in cs['validation_setup'] and cs['validation_setup']['data_types']:
                    cs['validation_setup']['data_types'] = {_data_types.data_type_val_to_enum_val(d) for d in cs['validation_setup']['data_types']}
                if 'gaze_offset_setup' in cs and cs['gaze_offset_setup'] is not None and 'data_types' in cs['gaze_offset_setup'] and cs['gaze_offset_setup']['data_types']:
                    cs['gaze_offset_setup']['data_types'] = {_data_types.data_type_val_to_enum_val(d) for d in cs['gaze_offset_setup']['data_types']}
                coding_setup_overrides[name] = StudyOverride(level, recording_type, for_event_setup=True, **cs)
        # enum roundtrip
        if 'mapped_video_which_gaze_type_on_plane' in kwds:
            kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])
        return StudyOverride(level, recording_type, **kwds), coding_setup_overrides

    @staticmethod
    def from_study_diff(config: Study|EventSetup, parent_config: Study|EventSetup, level: OverrideLevel, recording_type: session.RecordingType|None = None, which_coding_setups: list[str]|None = None) -> tuple['StudyOverride', dict[str, 'StudyOverride']]:
        fields = (StudyOverride.get_allowed_parameters(level, recording_type, False)[0]+['coding_setup'], (which_coding_setups,StudyOverride.get_allowed_parameters(level, recording_type, True)[0]))
        kwds = _study_diff_impl(config, parent_config, fields)
        # split off coding setup overrides if present
        coding_setup_overrides: dict[str, StudyOverride] = {}
        if 'coding_setup' in kwds:
            for cs in kwds.pop('coding_setup'):
                name = cs.get('name')
                coding_setup_overrides[name] = StudyOverride(level, recording_type, for_event_setup=True, **cs)
        return StudyOverride(level, recording_type, **kwds), coding_setup_overrides

    @staticmethod
    def _fix_typing(kwds: dict[str,Any]) -> dict[str,Any]:
        if 'mapped_video_recording_colors' in kwds and kwds['mapped_video_recording_colors'] is not None:
            kwds['mapped_video_recording_colors'] = {k: None if kwds['mapped_video_recording_colors'][k] is None else RgbColor(*kwds['mapped_video_recording_colors'][k]) for k in kwds['mapped_video_recording_colors']}
        for k in ['mapped_video_projected_vidPos_ray_color','mapped_video_projected_world_pos_color','mapped_video_projected_left_ray_color','mapped_video_projected_right_ray_color','mapped_video_projected_average_ray_color']:
            if k in kwds and kwds[k] is not None:
                kwds[k] = RgbColor(**kwds[k])
        return kwds

def _apply_impl(obj, overrides: dict[str,Any], annotations: dict[str,typing.Type]|None):
    for p in overrides:
        ori_val = obj.get(p,None) if isinstance(obj,dict) else getattr(obj,p)
        val = overrides[p]
        not_found= isinstance(obj,dict) and p not in obj
        just_set = ori_val is None and not not_found
        if not just_set and (isinstance(val,dict) or type_utils.is_NamedTuple_type(type(val))):
            # dict-like object: recurse
            val = _apply_impl(ori_val, {p2: val[p2] if isinstance(val,dict) else getattr(val,p2) for p2 in type_utils.get_fields(val) if (isinstance(val,dict) and p2 in val) or hasattr(val,p2)}, type_utils.get_annotations(ori_val))

        # special case: for dict-like object we can unset specific fields, so allow those by skipping check for them
        if not just_set and val is None and (annotations is None or p not in annotations or not gt_utils.unpack_none_union(annotations[p])[1]):
            if isinstance(obj,dict):
                if p in obj:
                    del obj[p]
            else:
                delattr(obj,p)
        else:
            if isinstance(obj,dict):
                # overwrite existing and add new dict keys
                obj[p] = val
            elif type_utils.is_NamedTuple_type(type(obj)):
                # named tuples are immutable, have to return new instance
                obj = obj._replace(**{p:val})
            else:
                setattr(obj,p,val)
    return obj

def _study_diff_impl(config: Study, parent_config: Study, fields: tuple[list[str],tuple[list[str]|None,list[str]]|None]) -> dict[str,Any]:
    kwds: dict[str,Any] = {}
    for f in fields[0]:
        if f=='coding_setup' and fields[1] and fields[1][0] is not None:
            # special case: coding setup is a list of dict-like objects, need to diff per event setup
            val = getattr(config,f)
            parent_val = getattr(parent_config,f)
            kwds[f] = []
            for ve,pve in zip(val,parent_val):
                if ve['name'] not in fields[1][0]:
                    continue
                diff = _study_diff_impl(ve, pve, (fields[1][1],None))
                diff['name'] = ve['name']
                kwds[f].append(diff)
            continue
        val        =        config.get(f) if isinstance(       config,dict) else getattr(       config,f)
        parent_val = parent_config.get(f) if isinstance(parent_config,dict) else getattr(parent_config,f)
        if val!=parent_val:
            if parent_val is not None and (isinstance(val,dict) or typing.is_typeddict(val) or typed_dict_defaults.is_typeddictdefault(val) or type_utils.is_NamedTuple_type(val)):
                # need to recurse into object
                if isinstance(val,list):
                    # lists: per item check for differences
                    kwds[f] = []
                    for v,pv in zip(val,parent_val):
                        kwds[f].append(_study_diff_impl(v, pv, (list(set(type_utils.get_fields(v))|set(type_utils.get_fields(pv))),None)))
                else:
                    # dict-like object: only include fields that differ
                    val = _study_diff_impl(val, parent_val, (list(set(type_utils.get_fields(val))|set(type_utils.get_fields(parent_val))),None))
            kwds[f] = val
    return kwds

def load_override_and_apply(study: Study, level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None, strict_check=True) -> Study:
    override_path = pathlib.Path(override_path)
    if override_path.is_dir():
        override_path = override_path / StudyOverride.default_json_file_name
    if not override_path.is_file():
        return study

    overrides = StudyOverride.load_from_json(level, override_path, recording_type)
    return apply_all_overrides(study, overrides, strict_check)

def apply_all_overrides(study: Study, overrides: tuple[StudyOverride, dict[str, StudyOverride]], strict_check=True) -> Study:
    so, eos = overrides
    study = so.apply(study, strict_check)
    for eo in eos.values():
        study = eo.apply(study, strict_check)
    return study

def store_overrides_to_json(overrides: tuple[StudyOverride, dict[str, StudyOverride]], path: str|pathlib.Path):
    path = pathlib.Path(path)
    if path.is_dir():
        path = path / StudyOverride.default_json_file_name
    so, eos = overrides
    to_dump = so.get_dump()
    if eos:
        to_dump['coding_setup'] = [dump for eo in eos.values() if (dump:=eo.get_dump())]
        if not to_dump['coding_setup']:
            del to_dump['coding_setup']
    json.dump(to_dump, path)

def load_or_create_override(level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None) -> tuple[StudyOverride, dict[str,StudyOverride]]:
    override_path = pathlib.Path(override_path)
    if override_path.is_dir():
        override_path = override_path / StudyOverride.default_json_file_name

    if not override_path.is_file():
        return StudyOverride(level, recording_type), {}
    else:
        return StudyOverride.load_from_json(level, override_path, recording_type)

def apply_kwarg_overrides(study: Study, strict_check=True, **kwargs) -> Study:
    if not kwargs:
        return study
    overrides = StudyOverride(OverrideLevel.FunctionArgs, **kwargs)
    return overrides.apply(study, strict_check)

def read_study_config_with_overrides(config_path: str|pathlib.Path, overrides: dict[OverrideLevel, str|pathlib.Path]|None=None, recording_type: session.RecordingType|None = None, strict_check=True, **kwargs) -> Study:
    study = Study.load_from_json(config_path)
    if overrides:
        for l in [OverrideLevel.Session, OverrideLevel.Recording]:
            if l in overrides:
                study = load_override_and_apply(study, l, overrides[l], recording_type, strict_check)
    if kwargs:
        study = apply_kwarg_overrides(study, strict_check, **kwargs)
    return study