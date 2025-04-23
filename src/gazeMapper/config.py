import pathlib
import inspect
import copy
import enum
import typeguard
import typing
from typing import Any, Literal

from glassesTools import annotation, aruco, gaze_worldref, json, marker as gt_marker, utils as gt_utils
from glassesTools.validation import DataQualityType, get_DataQualityType_explanation

from . import marker, plane, session, typed_dict_defaults, type_utils, utils


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
    parameters      : dict[str,Any]|None = None

class RgbColor(typing.NamedTuple):
    r: int = 0
    g: int = 0
    b: int = 0
json.register_type(json.TypeEntry(RgbColor, '__config.RgbColor__', lambda x: x._asdict(), lambda x: RgbColor(**x)))

class Study:
    default_json_file_name = 'study_def.json'

    @typeguard.typechecked(collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
    def __init__(self,
                 session_def                                    : session.SessionDefinition,
                 planes                                         : list[plane.Definition],
                 planes_per_episode                             : dict[annotation.Event,set[str]],
                 episodes_to_code                               : set[annotation.Event],
                 individual_markers                             : list[marker.Marker],
                 working_directory                              : str|pathlib.Path,

                 # setup with defaults
                 import_do_copy_video                           : bool                              = True,
                 import_source_dir_as_relative_path             : bool                              = False,
                 import_known_custom_eye_trackers               : list[str]|None                    = None,

                 overlay_video_gaze_vid_pos_color               : RgbColor                          = RgbColor(  0,255,  0),
                 overlay_video_gaze_world_pos_color             : RgbColor|None                     = RgbColor(255,  0,255),
                 overlay_video_gaze_vid_pos_radius              : int                               = 8,
                 overlay_video_gaze_world_pos_radius            : int                               = 5,
                 overlay_video_gaze_vid_pos_thickness           : int                               = 2,
                 overlay_video_gaze_world_pos_thickness         : int                               = -1,

                 sync_ref_recording                             : str|None                          = None,
                 sync_ref_do_time_stretch                       : bool|None                         = None,
                 sync_ref_stretch_which                         : Literal['ref','other']|None       = None,
                 sync_ref_average_recordings                    : set[str]|None                     = None,

                 get_cam_movement_for_et_sync_method            : Literal['','plane','function']    = '',
                 get_cam_movement_for_et_sync_function          : CamMovementForEtSyncFunction|None = None,
                 sync_et_to_cam_use_average                     : bool                              = True,

                 auto_code_sync_points                          : AutoCodeSyncPoints|None           = None,
                 auto_code_episodes                             : dict[Literal[
                                                                    annotation.Event.Sync_ET_Data,
                                                                    annotation.Event.Trial,
                                                                    annotation.Event.Validate],
                                                                  AutoCodeEpisodes]|None            = None,

                 export_output3D                                : bool                              = False,
                 export_output2D                                : bool                              = True,
                 export_only_code_marker_presence               : bool                              = True,

                 validate_do_global_shift                       : bool                              = True,
                 validate_max_dist_fac                          : float                             = .5,
                 validate_dq_types                              : set[DataQualityType]|None         = None,
                 validate_allow_dq_fallback                     : bool                              = False,
                 validate_include_data_loss                     : bool                              = False,
                 validate_I2MC_settings                         : I2MCSettings|None                 = None,
                 validate_dynamic_skip_first_duration           : float                             = .2,
                 validate_dynamic_max_gap_duration              : int                               = 4,
                 validate_dynamic_min_duration                  : int                               = 6,

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

                 gui_num_workers                                : int                               = 2,

                 # not a class member
                 strict_check                                   : bool                              = True
                 ):
        self.session_def                                    = session_def
        self.planes                                         = planes
        self.planes_per_episode                             = planes_per_episode
        self.episodes_to_code                               = episodes_to_code
        self.individual_markers                             = individual_markers
        self.working_directory                              = working_directory

        self.import_do_copy_video                           = import_do_copy_video
        self.import_source_dir_as_relative_path             = import_source_dir_as_relative_path
        self.import_known_custom_eye_trackers               = import_known_custom_eye_trackers

        self.overlay_video_gaze_vid_pos_color               = overlay_video_gaze_vid_pos_color
        self.overlay_video_gaze_world_pos_color             = overlay_video_gaze_world_pos_color
        self.overlay_video_gaze_vid_pos_radius              = overlay_video_gaze_vid_pos_radius
        self.overlay_video_gaze_world_pos_radius            = overlay_video_gaze_world_pos_radius
        self.overlay_video_gaze_vid_pos_thickness           = overlay_video_gaze_vid_pos_thickness
        self.overlay_video_gaze_world_pos_thickness         = overlay_video_gaze_world_pos_thickness

        self.get_cam_movement_for_et_sync_method            = get_cam_movement_for_et_sync_method
        self.get_cam_movement_for_et_sync_function          = get_cam_movement_for_et_sync_function
        self.sync_et_to_cam_use_average                     = sync_et_to_cam_use_average

        self.sync_ref_recording                             = sync_ref_recording
        self.sync_ref_do_time_stretch                       = sync_ref_do_time_stretch
        self.sync_ref_stretch_which                         = sync_ref_stretch_which
        self.sync_ref_average_recordings                    = sync_ref_average_recordings

        self.auto_code_sync_points                          = auto_code_sync_points
        self.auto_code_episodes                             = auto_code_episodes

        self.export_output3D                                = export_output3D
        self.export_output2D                                = export_output2D
        self.export_only_code_marker_presence               = export_only_code_marker_presence

        self.validate_do_global_shift                       = validate_do_global_shift
        self.validate_max_dist_fac                          = validate_max_dist_fac
        self.validate_dq_types                              = validate_dq_types
        self.validate_allow_dq_fallback                     = validate_allow_dq_fallback
        self.validate_include_data_loss                     = validate_include_data_loss
        self.validate_I2MC_settings                         = validate_I2MC_settings
        self.validate_dynamic_skip_first_duration           = validate_dynamic_skip_first_duration
        self.validate_dynamic_max_gap_duration              = validate_dynamic_max_gap_duration
        self.validate_dynamic_min_duration                  = validate_dynamic_min_duration

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

        self.gui_num_workers                                = gui_num_workers

        self.check_valid(strict_check=strict_check)

    def check_valid(self, strict_check=True):
        if strict_check:
            self._check_session_def(strict_check)
            self._check_planes_per_episode(strict_check)
            self._check_episodes_to_code(strict_check)
            self._check_individual_markers(strict_check)
            self._check_sync_ref(strict_check)
            self._check_et_sync_method(strict_check)
            self._check_auto_coding_setup(strict_check)
            self._check_make_video(strict_check)

        # ensure typed dicts with defaults members are of the right class, and apply defaults
        to_check = {k:t for k in study_parameter_types if typed_dict_defaults.is_typeddictdefault(t:=gt_utils.unpack_none_union(study_parameter_types[k])[0])}
        for k in to_check:
            if getattr(self,k) is not None:
                setattr(self,k, to_check[k](getattr(self,k)))
                getattr(self,k).apply_defaults()
        if self.auto_code_episodes is not None:
            self.auto_code_episodes = {e: AutoCodeEpisodes(self.auto_code_episodes[e]) for e in self.auto_code_episodes}
            for e in self.auto_code_episodes:
                self.auto_code_episodes[e].apply_defaults()

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
            problems[field] = f'Recording(s) {missing_recs[0] if len(missing_recs)==1 else missing_recs} not known'
            if isinstance(getattr(self,field),dict):
                type_utils.merge_problem_dicts(problems,{field: {r:f'Recording {r} not known' for r in missing_recs}})
        return problems

    def _check_recording(self, rec: str) -> bool:
        return any([r.name==rec for r in self.session_def.recordings])

    def _check_session_def(self, strict_check) -> type_utils.ProblemDict:
        # require at least one eye tracker recording
        if not any(r.type==session.RecordingType.Eye_Tracker for r in self.session_def.recordings):
            if strict_check:
                raise ValueError('At least one recording should be an eye tracker recording')
            else:
                return {'session_def': 'At least one recording should be an eye tracker recording'}
        return {}

    def _check_planes_per_episode(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        if not self.planes_per_episode:
            type_utils.merge_problem_dicts(problems, {'planes_per_episode': 'At minimum one episode should have a corresponding plane defined'})
        for e in self.planes_per_episode:
            missing_planes: list[str] = []
            for p in self.planes_per_episode[e]:
                if not any([p==pl.name for pl in self.planes]):
                    if strict_check:
                        raise ValueError(f'Plane {p} not known')
                    else:
                        missing_planes.append(p)
            if missing_planes:
                mp = '", "'.join(missing_planes)
                type_utils.merge_problem_dicts(problems, {'planes_per_episode': {e: f'Plane(s) "{mp}" not known.'}})

            # check correct number of planes is defined for the episode
            match e:
                case annotation.Event.Sync_Camera:
                    allow_one_plane = allow_more_than_one = False
                case annotation.Event.Sync_ET_Data:
                    if self.get_cam_movement_for_et_sync_method=='plane':
                        allow_one_plane = True
                        allow_more_than_one = False
                    else:
                        allow_one_plane = allow_more_than_one = False
                case annotation.Event.Validate:
                    allow_one_plane = True
                    allow_more_than_one = False
                case annotation.Event.Trial:
                    allow_one_plane = allow_more_than_one = True
            if not allow_one_plane:
                msg = f'No planes should be defined for a {annotation.tooltip_map[e]}. Remove entry, even if its empty.'
                if e==annotation.Event.Sync_ET_Data:
                    msg += ' Alternatively, you may want to set the get_cam_movement_for_et_sync_method on the main options panel to "Plane".'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'planes_per_episode': {e: msg}})
            elif not self.planes_per_episode[e]:
                msg = ('At least one' if allow_more_than_one else 'One')+f' plane should be defined for a {annotation.tooltip_map[e]}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'planes_per_episode': {e: msg}})
            if not allow_more_than_one and len(self.planes_per_episode[e])>1:
                msg = f'Only one plane should be defined for a {annotation.tooltip_map[e]}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'planes_per_episode': {e: msg}})

        for e in self.episodes_to_code:
            if e not in self.planes_per_episode and e!=annotation.Event.Sync_Camera and (e==annotation.Event.Sync_ET_Data and self.get_cam_movement_for_et_sync_method=='plane'):
                msg = f'{annotation.tooltip_map[e]}s are set up to be coded and require an associated plane, but no plane(s) are defined in planes_per_episode for {annotation.tooltip_map[e]}s'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {'planes_per_episode': msg})
        return problems

    def _check_episodes_to_code(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        if not self.episodes_to_code:
            type_utils.merge_problem_dicts(problems, {'episodes_to_code': 'At minimum one episode should be selected to be coded'})
        if annotation.Event.Sync_ET_Data in self.episodes_to_code and self.get_cam_movement_for_et_sync_method=='':
            type_utils.merge_problem_dicts(problems, {'episodes_to_code': f'{annotation.tooltip_map[annotation.Event.Sync_ET_Data]} should not be listed in the episodes to be coded if there is no method for plane synchronization (get_cam_movement_for_et_sync_method) specified.'})

        for e in self.planes_per_episode:
            if e not in self.episodes_to_code:
                if strict_check:
                    raise ValueError(f'Plane(s) are defined in planes_per_episode for {annotation.tooltip_map[e]}s, but {annotation.tooltip_map[e]}s are not set up to be coded in episodes_to_code. Fix episodes_to_code.')
                else:
                    type_utils.merge_problem_dicts(problems, {'episodes_to_code': f'Plane(s) are defined in planes_per_episode for {annotation.tooltip_map[e]}s, but {annotation.tooltip_map[e]}s are not set up to be coded'})
                    type_utils.merge_problem_dicts(problems, {'planes_per_episode': {e: f'{annotation.tooltip_map[e]}s are not set up to be coded in episodes_to_code, so no plane(s) should be set up for {annotation.tooltip_map[e]}s.'}})
        return problems

    def _check_auto_coding_setup(self, strict_check) -> type_utils.ProblemDict:
        problems = self._check_auto_markers(strict_check)
        this_problems: type_utils.ProblemDict = {}
        if self.auto_code_sync_points:
            if annotation.Event.Sync_Camera not in self.episodes_to_code:
                if strict_check:
                    raise ValueError(f'The auto_code_sync_points option is configured, but {annotation.tooltip_map[annotation.Event.Sync_Camera]}s are not set to be coded in episodes_to_code. Fix episodes_to_code.')
                else:
                    this_problems['episodes_to_code'] = f'The auto_code_sync_points option is configured, but {annotation.tooltip_map[annotation.Event.Sync_Camera]}s are not set to be coded in episodes_to_code.'
                    this_problems['auto_code_sync_points'] = f'The auto_code_sync_points option is configured, but {annotation.tooltip_map[annotation.Event.Sync_Camera]}s are not set to be coded in episodes_to_code. Fix episodes_to_code or remove auto_code_sync_points setup.'
        if self.auto_code_episodes:
            for e in self.auto_code_episodes:
                if e not in self.episodes_to_code:
                    if strict_check:
                        raise ValueError(f'Coding of {annotation.tooltip_map[e]}s is configured in the auto_code_episodes option, but {annotation.tooltip_map[e]}s are not set to be coded in episodes_to_code. Fix episodes_to_code.')
                    else:
                        this_problems['episodes_to_code'] = f'Coding of {annotation.tooltip_map[e]}s is configured in the auto_code_episodes option, but {annotation.tooltip_map[e]}s are not set to be coded in episodes_to_code.'
                        this_problems['auto_code_episodes'] = f'Coding of {annotation.tooltip_map[e]}s is configured in the auto_code_episodes option, but {annotation.tooltip_map[e]}s are not set to be coded in episodes_to_code. Fix episodes_to_code or remove {annotation.tooltip_map[e]}s from the auto_code_episodes setup.'
        return type_utils.merge_problem_dicts(problems,this_problems)

    def _check_auto_markers(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        used_markers: dict[tuple[str,str]|tuple[str,annotation.Event,str],list[gt_marker.MarkerID]] = {}
        if self.auto_code_sync_points:
            if 'markers' not in self.auto_code_sync_points:
                if strict_check:
                    raise ValueError('auto_code_sync_points.markers cannot be empty or unspecified')
                else:
                    problems['auto_code_sync_points'] = {}
                    problems['auto_code_sync_points']['markers'] = 'auto_code_sync_points.markers cannot be empty or unspecified'
            else:
                missing_markers: list[gt_marker.MarkerID] = []
                for i in self.auto_code_sync_points['markers']:
                    if not any([m.id==i.m_id and m.aruco_dict_id==i.aruco_dict_id for m in self.individual_markers]):
                        if strict_check:
                            raise ValueError(f'Marker "{gt_marker.marker_ID_to_str(i)}" specified in auto_code_sync_points.markers, but unknown because not present in individual_markers')
                        else:
                            missing_markers.append(i)
                if missing_markers:
                    problems['auto_code_sync_points'] = {}
                    missing_markers = ', '.join((gt_marker.marker_ID_to_str(m) for m in missing_markers))
                    problems['auto_code_sync_points']['markers'] = f'The marker(s) {missing_markers} are not defined in individual_markers'
                used_markers[('auto_code_sync_points','markers')] = self.auto_code_sync_points['markers']
        if self.auto_code_episodes:
            for e in self.auto_code_episodes:
                for f in ['start_markers','end_markers']:
                    if f not in self.auto_code_episodes[e] or not self.auto_code_episodes[e][f]:
                        if strict_check:
                            raise ValueError(f'auto_code_episodes[{e}].{f} cannot be empty or unspecified')
                        else:
                            type_utils.merge_problem_dicts(problems, {'auto_code_episodes': {e: {f: f'auto_code_episodes[{e}].{f} cannot be empty or unspecified'}}})
                    else:
                        missing_markers: list[gt_marker.MarkerID] = []
                        for i in self.auto_code_episodes[e][f]:
                            if not any([m.id==i.m_id and m.aruco_dict_id==i.aruco_dict_id for m in self.individual_markers]):
                                if strict_check:
                                    raise ValueError(f'Marker "{gt_marker.marker_ID_to_str(i)}" specified in auto_code_episodes.[{e}].{f}, but unknown because not present in individual_markers')
                                else:
                                    missing_markers.append(i)
                        if missing_markers:
                            missing_markers = ', '.join((gt_marker.marker_ID_to_str(m) for m in missing_markers))
                            type_utils.merge_problem_dicts(problems, {'auto_code_episodes': {e: {f: f'The marker(s) {missing_markers} are not defined in individual_markers'}}})
                        used_markers[('auto_code_episodes',e,f)] = self.auto_code_episodes[e][f]
        # check if markers or marker sequences are uniquely used:
        # 1. marker used for auto_code_sync_points cannot appear anywhere else
        # 2. marker sequences used for auto_code_episodes must be unique (markers can be reused)
        # first transform marker IDs to family so we can properly detect clashes
        used_markers      : dict[tuple[str,str]|tuple[str,annotation.Event,str],list[tuple[int,int]]] = {k:[(m.m_id, aruco.dict_id_to_family[m.aruco_dict_id]) for m in used_markers[k]] for k in used_markers}
        seen_markers      : set[tuple[int,int]] = set()
        seen_markers_sets : set[tuple[tuple[int,int]]] = set()
        def _format_key(key: tuple[str,str]|tuple[str,annotation.Event,str]):
            return f'{key[0]}.{key[1]}' if len(key)==2 else f'{key[0]}[{key[1]}].{key[2]}'
        for s in used_markers:
            # first check if used markers are unique at the family level
            seen: set[tuple[int,int]] = set()
            if (duplicates := {x for x in used_markers[s] if x in seen or seen.add(x)}):
                msg = f'The markers defined for {_format_key(s)} are not unique. Please resolves the following duplicates: {utils.format_duplicate_markers_msg(duplicates)}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems, {s[0]: ({s[1]: msg} if len(s)==2 else {s[1]: {s[2]: msg}})})
            # then check if already used in another setup
            if seen_markers.intersection(used_markers[s]):
                # markers not unique, make error. Find exactly where the overlap is
                # yes, this is bad algorithmic complexity, but it only runs in failure cases
                for s2 in used_markers:
                    if s==s2 or s[0]=='auto_code_episodes' and s[0]==s2[0]:
                        # if both are different entries in 'auto_code_episodes', that is not an error
                        # for 'auto_code_episodes' we need to check marker sequences are unique, not
                        # individual entries. e.g. start is 80 81, and end is 81 80 is valid
                        continue
                    if (overlap:=set(used_markers[s2]).intersection(used_markers[s])):
                        msg = f'The following markers are encountered in the setup for both {_format_key(s)} and {_format_key(s2)}: {utils.format_duplicate_markers_msg(overlap)}. Markers cannot be used more than once, fix this collision.'
                        # emit error message
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            for sx in (s,s2):
                                type_utils.merge_problem_dicts(problems, {sx[0]: ({sx[1]: msg} if len(sx)==2 else {sx[1]: {sx[2]: msg}})})
            seen_markers.update(used_markers[s])
            # check if marker sequence is already used
            if seen_markers_sets.intersection((tuple(used_markers[s]),)):
                for s2 in used_markers:
                    if s==s2:
                        continue
                    if set((tuple(used_markers[s2]),)).intersection((tuple(used_markers[s]),)):
                        msg = f'The marker sequence {utils.format_marker_sequence_msg(used_markers[s])} specified for {_format_key(s)} has already been used for {_format_key(s2)}. Markers sequences must be unique, please fix this collision.'
                        # emit error message
                        if strict_check:
                            raise ValueError(msg)
                        else:
                            for sx in (s,s2):
                                type_utils.merge_problem_dicts(problems, {sx[0]: {sx[1]: {sx[2]: msg}}})
            seen_markers_sets.add(tuple(used_markers[s]))
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
                    problems = type_utils.merge_problem_dicts(problems, {'individual_markers': {(m.id, aruco.dict_id_to_family[m.aruco_dict_id]): problem}})
        return problems

    def _check_sync_ref(self, strict_check):
        problems: type_utils.ProblemDict = {}
        if self.sync_ref_recording is None:
            if len(self.session_def.recordings)>1:
                problems['sync_ref_recording'] = f'sync_ref_recording must be set when sessions consist of more than one recording'
            # nothing to do
            return problems
        elif len(self.session_def.recordings)==1:
            return {'sync_ref_recording': f'sync_ref_recording must not be set when sessions consist of only one recording'}

        type_utils.merge_problem_dicts(problems, self._check_recordings([self.sync_ref_recording], 'sync_ref_recording', False))
        type_utils.merge_problem_dicts(problems, self._check_recordings(self.sync_ref_average_recordings, 'sync_average_recordings', False))
        if self.sync_ref_do_time_stretch is None:
            if strict_check:
                raise ValueError(f'sync_ref_do_time_stretch should be set in the study setup when sync_ref_recording is set')
            else:
                problems['sync_ref_do_time_stretch'] = f'sync_ref_do_time_stretch should be set when sync_ref_recording is set'
        if self.sync_ref_do_time_stretch:
            for a in ['sync_ref_stretch_which', 'sync_ref_average_recordings']:
                if getattr(self,a) is None:
                    if strict_check:
                        raise ValueError(f'{a} should be set in the study setup when sync_ref_recording is set and sync_ref_do_time_stretch is enabled')
                    else:
                        problems[a] = f'{a} should be set when sync_ref_recording is set and sync_ref_do_time_stretch is enabled'
        if self.sync_ref_average_recordings and self.sync_ref_recording in self.sync_ref_average_recordings:
            if strict_check:
                raise ValueError(f'Recording {self.sync_ref_recording} is the reference recording for sync, should not be specified in sync_average_recordings')
            else:
                problems['sync_ref_average_recordings'] = f'Recording {self.sync_ref_recording} is the reference recording for sync, cannot be specified in sync_average_recordings'
        if annotation.Event.Sync_Camera not in self.episodes_to_code:
            if strict_check:
                raise ValueError('when sync_ref_recording is set, coding of camera sync points should be set up in episodes_to_code')
            else:
                problems['episodes_to_code'] = f'if sync_ref_recording is set, {annotation.tooltip_map[annotation.Event.Sync_Camera]}s should be set up to be coded'
                type_utils.merge_problem_dicts(problems, {'sync_ref_recording': f'sync_ref_recording is set, but {annotation.tooltip_map[annotation.Event.Sync_Camera]}s are not set up to be coded in episodes_to_code'})
        return problems

    def _check_et_sync_method(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        cam_mov_possible_values = typing.get_args(study_parameter_types['get_cam_movement_for_et_sync_method'])
        if self.get_cam_movement_for_et_sync_method not in cam_mov_possible_values:
            values = list(cam_mov_possible_values)
            values.remove('')
            values_str = '"' + '", "'.join(values) + '"'
            temp = values_str.partition(f'"{values[-1]}"')
            values_str = ('' if len(values)==1 else ', ') + temp[0] + 'or ' + temp[1]
            if strict_check:
                raise ValueError(f'get_cam_movement_for_et_sync_method parameter should be an empty string{values_str}')
            else:
                problems['get_cam_movement_for_et_sync_method'] = f'get_cam_movement_for_et_sync_method parameter should be an empty string{values_str}'

        if self.get_cam_movement_for_et_sync_method not in ['plane', 'function']:
            # nothing to do
            return problems
        if annotation.Event.Sync_ET_Data not in self.episodes_to_code:
            if strict_check:
                raise ValueError(f'if get_cam_movement_for_et_sync_method is set to "plane" or "function", {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s should be set up to be coded in episodes_to_code')
            else:
                problems['episodes_to_code'] = f'if get_cam_movement_for_et_sync_method is set to "plane" or "function", {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s should be set up to be coded'
                problems['get_cam_movement_for_et_sync_method'] = f'get_cam_movement_for_et_sync_method is set to "{self.get_cam_movement_for_et_sync_method}", but {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s are not set up to be coded in episodes_to_code'
        if self.get_cam_movement_for_et_sync_method=='function':
            if strict_check:
                if not self.get_cam_movement_for_et_sync_function or not all([x in self.get_cam_movement_for_et_sync_function for x in ["module_or_file","function","parameters"]]):
                    raise ValueError('if get_cam_movement_for_et_sync_method is set to "function", get_cam_movement_for_et_sync_function should be a dict specifying "module_or_file", "function", and "parameters"')
            else:
                t = gt_utils.unpack_none_union(study_parameter_types['get_cam_movement_for_et_sync_function'])[0]
                keys = t.__required_keys__|t.__optional_keys__
                this_problems = {k:f'{k} should be set when get_cam_movement_for_et_sync_function is set to "function"' for k in keys if not self.get_cam_movement_for_et_sync_function or (k not in t._field_defaults and (k not in self.get_cam_movement_for_et_sync_function or not self.get_cam_movement_for_et_sync_function[k]))}
                if this_problems:
                    problems['get_cam_movement_for_et_sync_function'] = this_problems
        elif self.get_cam_movement_for_et_sync_method=='plane':
            if annotation.Event.Sync_ET_Data not in self.planes_per_episode:
                if strict_check:
                    raise ValueError(f'if get_cam_movement_for_et_sync_method is set to "plane", a plane should be set up to be used for processing {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s in planes_per_episode')
                else:
                    problems['planes_per_episode'] = f'if get_cam_movement_for_et_sync_method is set to "plane", a plane should be set up to be used for processing {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s'
                    type_utils.merge_problem_dicts(problems, {'get_cam_movement_for_et_sync_method': f'get_cam_movement_for_et_sync_method is set to "plane", but no plane specified for syncing eye tracker data to the scene cam (i.e., for {annotation.tooltip_map[annotation.Event.Sync_ET_Data]}s) in planes_per_episode'})
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
                    type_utils.merge_problem_dicts(problems,{'mapped_video_recording_colors': msg})
        return problems

    def field_problems(self) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        type_utils.merge_problem_dicts(problems, self._check_session_def(False))
        type_utils.merge_problem_dicts(problems, self._check_planes_per_episode(False))
        type_utils.merge_problem_dicts(problems, self._check_episodes_to_code(False))
        type_utils.merge_problem_dicts(problems, self._check_auto_coding_setup(False))
        type_utils.merge_problem_dicts(problems, self._check_individual_markers(False))
        type_utils.merge_problem_dicts(problems, self._check_sync_ref(False))
        type_utils.merge_problem_dicts(problems, self._check_et_sync_method(False))
        type_utils.merge_problem_dicts(problems, self._check_make_video(False))
        return problems

    def store_as_json(self, path: str|pathlib.Path|None=None):
        if not path:
            path = guess_config_dir(self.working_directory)
        path = pathlib.Path(path)
        # this stores only the planes_per_episode variable to json, rest is read from other files
        # instead to remain flexible and make it easy for users to rename, etc
        f_path = path
        if f_path.is_dir():
            f_path /= self.default_json_file_name
        else:
            path = f_path.parent
            # prep for dump to file
        to_dump = {k:copy.deepcopy(getattr(self,k)) for k in vars(self) if not k.startswith('_') and k not in ['session_def','planes','working_directory']}    # session_def and planes will be populated from contents in the provided folder, and working_directory as the provided path
        to_dump['planes_per_episode'] = [(k, to_dump['planes_per_episode'][k]) for k in to_dump['planes_per_episode']]   # pack as list of tuples for storage
        # filter out defaulted
        to_dump = {k:to_dump[k] for k in to_dump if k not in study_defaults or study_defaults[k]!=to_dump[k]}
        # also filter out defaults in some subfields
        typed_dict_default_fields = [k for k in to_dump if typed_dict_defaults.is_typeddictdefault(gt_utils.unpack_none_union(study_parameter_types[k])[0])]
        for k in typed_dict_default_fields:
            to_dump[k] = {kk:to_dump[k][kk] for kk in to_dump[k] if kk not in to_dump[k]._field_defaults or to_dump[k]._field_defaults[kk]!=to_dump[k][kk]}
            if not to_dump[k] and k in study_defaults and study_defaults[k] is not None:
                to_dump.pop(k)
        if 'auto_code_episodes' in to_dump:
            for e in to_dump['auto_code_episodes']:
                to_dump['auto_code_episodes'][e] = {kk:to_dump['auto_code_episodes'][e][kk] for kk in to_dump['auto_code_episodes'][e] if kk not in to_dump['auto_code_episodes'][e]._field_defaults or to_dump['auto_code_episodes'][e]._field_defaults[kk]!=to_dump['auto_code_episodes'][e][kk]}
            # pack as list of tuples for storage
            to_dump['auto_code_episodes'] = [(e, to_dump['auto_code_episodes'][e]) for e in to_dump['auto_code_episodes']]   # pack as list of tuples for storage
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
            [],{},set(),[],path,
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
        # fix up 'video_' parameters for backwards compatibility
        for k in list(kwds.keys()):
            if k.startswith('video_'):
                kwds[f'mapped_{k}'] = kwds.pop(k)
        # stored as list of tuples (with enum values as keys), unpack
        kwds['planes_per_episode'] = {annotation.Event(k):v for k,v in kwds['planes_per_episode']}
        if 'auto_code_episodes' in kwds:
            kwds['auto_code_episodes'] = {annotation.Event(k):v for k,v in kwds['auto_code_episodes']}
        # help with enum roundtrip
        kwds['episodes_to_code'] = {annotation.Event(e) for e in kwds['episodes_to_code']}
        if 'validate_dq_types' in kwds:
            kwds['validate_dq_types']= {DataQualityType(d) for d in kwds['validate_dq_types']}
        if 'mapped_video_which_gaze_type_on_plane' in kwds:
            kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])
        # backward compatibility: ensure value are stored in sets, not lists
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
            kwds['auto_code_episodes'] = {annotation.Event.Trial: kwds.pop('auto_code_trial_episodes')}
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
def _get_gv_data_quality_type_doc(dq: DataQualityType):
    t,doc = get_DataQualityType_explanation(dq)
    return (dq, type_utils.GUIDocInfo(t,doc))
def _get_annotation_event_doc(a: annotation.Event, children: dict = None):
    t = annotation.tooltip_map[a]
    doc = {
        annotation.Event.Trial: 'Denotes an episode for which to map gaze to plane(s). This determines for which segments there will be gaze data when running the Export Trials.',
        annotation.Event.Validate: 'Denotes an episode during which a participant looked at a validation poster, to be used to run glassesValidator to compute data quality of the gaze data.',
        annotation.Event.Sync_Camera: 'Time point (frame from video) when a synchronization event happened, used for synchronizing different recordings.',
        annotation.Event.Sync_ET_Data: 'Episode to be used for synchronization of eye tracker data to scene camera (e.g. using VOR).'
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
    'planes_per_episode': type_utils.GUIDocInfo('Planes per episode', 'For each episode that is enabled to be coded in the project, sets which planes will be looked for and gaze mapped to during the episode.',dict([_get_annotation_event_doc(a) for a in annotation.Event])),
    'episodes_to_code': type_utils.GUIDocInfo('Episodes to code', 'Sets which episodes can be coded for this project.',{
        None: # None indicates the doc specification applies to the contained values
            dict([_get_annotation_event_doc(a) for a in annotation.Event])
    }),
    'import_do_copy_video': type_utils.GUIDocInfo('Copy video during import?', 'If not enabled, the scene video of an eye tracker recording, or the video of an external camera is not copied to the gazeMapper recording directory during import. Instead, the video will be loaded from the recording\'s source directory (so do not move it). Ignored when the video must be transcoded to be processed with gazeMapper.'),
    'import_source_dir_as_relative_path': type_utils.GUIDocInfo('Store source directory as relative path?', 'Specifies whether the path to the source directory stored in the recording info file is an absolute path (this option is not enabled) or a relative path (enabled). If a relative path is used, the imported recording and the source directory can be moved to another location, and the source directory can still be found as long as the relative path (e.g., one folder up and in the directory "original recordings": "../original recordings") doesn\'t change.'),
    'import_known_custom_eye_trackers': type_utils.GUIDocInfo('Registered custom eye trackers', 'gazeMapper allows importing generic eye trackers for which no specific support is implemented, if their recording data is preprocessed to conform to glassesTools\' generic data format. Here you can define specific known generic eye tracker names that you may import.'),
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
    'get_cam_movement_for_et_sync_method': type_utils.GUIDocInfo('Gaze data synchronization: Method to get camera movement', 'Method used to derive the head motion for synchronizing eye tracker data and scene camera.',{
        None: {     # indicates the doc specification applies to the contained values
            '': type_utils.GUIDocInfo('None', 'No gaze data synchronization'),
            'plane': type_utils.GUIDocInfo('Plane', 'Head movement is represented by the position of the origin of the plane in the scene camera video (the plane that is set up to be used for "Sync ET Data" episodes), as extracted through pose estimation or homography using a gazeMapper plane.'),
            'function': type_utils.GUIDocInfo('Function', 'A user-specified function (configured using the "Gaze data synchronization: Function for camera movement" setting) will be called for each frame of the scene video in a "Sync ET Data" episode and is expected to return the location of the target the participant was looking at.')
        }
    }),
    'get_cam_movement_for_et_sync_function': type_utils.GUIDocInfo('Gaze data synchronization: Function for camera movement', 'Setup for function to use for deriving the head motion when synchronizing eye tracker data and scene camera if "Gaze data synchronization: Method to get camera movement" is set to "function".',{
        'module_or_file': type_utils.GUIDocInfo('Module or file', 'Importable module or file (can be a full path) that contains the function to run.'),
        'function': type_utils.GUIDocInfo('Function', 'Name of the function to run.'),
        'parameters': type_utils.GUIDocInfo('Parameters', 'Set of parameters and values to pass to the function. The frame to process (np.ndarray) is the first (positional) input passed to the function, and should not be specified in this set.'),
    }),
    'sync_et_to_cam_use_average': type_utils.GUIDocInfo('Gaze data synchronization: Use average?', 'Whether to use the average offset of multiple sync episodes. If not enabled, the offset for the first sync episode is used, the rest are ignored.'),
    'auto_code_sync_points': type_utils.GUIDocInfo('Automated coding of synchronization points','Setup for automatic coding of synchronization timepoints.',{
        'markers': type_utils.GUIDocInfo('Marker(s)', 'Set of marker IDs whose appearance indicates a synchronization timepoint.'),
        'max_gap_duration': type_utils.GUIDocInfo('Maximum gap duration', 'Maximum gap (number of frames) in marker detections that will be filled in (ignored).'),
        'min_duration': type_utils.GUIDocInfo('Minimum duration', 'Minimum duration (number of frames) that a marker should be detected. Shorter runs are removed.')
    }),
    'auto_code_episodes': type_utils.GUIDocInfo('Automated coding of episodes','Setup for automatic coding of episodes.',{
        None: # None indicates the doc specification applies to the contained values
            dict([_get_annotation_event_doc(a,{
                'start_markers': type_utils.GUIDocInfo('Start marker(s)', 'A single marker ID or a sequence of marker IDs that indicate the start of an episode.'),
                'end_markers': type_utils.GUIDocInfo('End marker(s)', 'A single marker ID or a sequence of marker IDs that indicate the end of an episode.'),
                'max_gap_duration': type_utils.GUIDocInfo('Maximum gap duration', 'Maximum gap (number of frames) in marker detections that will be filled in (ignored).'),
                'max_intermarker_gap_duration': type_utils.GUIDocInfo('Maximum intermarker gap duration', 'Maximum gap (number of frames) that is allowed between the detection of two markers in a sequence.'),
                'min_duration': type_utils.GUIDocInfo('Minimum duration', 'Minimum duration (number of frames) that a marker should be detected. Shorter runs are removed.')
                }
            ) for a in annotation.Event if a!=annotation.Event.Sync_Camera])
    }),
    'export_output3D': type_utils.GUIDocInfo('Mapped data export: include 3D fields', 'Determines whether gaze positions on the plane in the scene camera reference frame are exported when invoking the Export Trials action.'),
    'export_output2D': type_utils.GUIDocInfo('Mapped data export: include 2D fields', 'Determines whether gaze positions on the plane in the plane\'s reference frame are exported when invoking the Export Trials action.'),
    'export_only_code_marker_presence': type_utils.GUIDocInfo('Mapped data export: only include marker presence?', 'If enabled, for each marker only a single column is added to the export created by the Export Trials action, indicating whether the given marker was detected or not on a given frame. If not enabled, marker pose information is included in the export.'),
    'validate_do_global_shift': type_utils.GUIDocInfo('glassesValidator: Apply global shift?', 'If enabled, for each validation interval the median position will be removed from the gaze data and the mean from the targets, removing any overall shift of the data. This improves the matching of fixations to targets when there is a significant overall offset in the data. It may fail (backfire) if there are data samples far outside the range of the validation targets, or if there is no data for some targets.'),
    'validate_max_dist_fac': type_utils.GUIDocInfo('glassesValidator: Maximum distance factor', 'Factor for determining distance limit when assigning fixation points to validation targets. If for a given target the closest fixation point is further away than <factor>*[minimum intertarget distance], then no fixation point will be assigned to this target, i.e., it will not be matched to any fixation point. Set to a large value to essentially disable.'),
    'validate_dq_types': type_utils.GUIDocInfo('glassesValidator: Data quality types', 'Selects the types of data quality you would like to calculate for each of the recordings. When none are selected, a good default is used for each recording. When none of the selected types is available, depending on the `validate_allow_dq_fallback` setting, either an error is thrown or that same default is used instead. Whether a data quality type is available depends on what type of gaze information is available for a recording, as well as whether the camera is calibrated.',{
        None: # None indicates the doc specification applies to the contained values
            dict([_get_gv_data_quality_type_doc(dq) for dq in DataQualityType])
    }),
    'validate_allow_dq_fallback': type_utils.GUIDocInfo('glassesValidator: Allow fallback data quality type?', 'If not enabled, an error is raised when the data quality type(s) indicated in "glassesValidator: Data quality types" are not available. If enabled, a sensible default other data type will be used instead. Does not apply if the "glassesValidator: Data quality types" is not set.'),
    'validate_include_data_loss': type_utils.GUIDocInfo('glassesValidator: Include data loss?', 'If enabled, the data quality report will include data loss during the episode selected for each target on the validation poster. This is NOT the data loss of the whole recording and thus not what you want to report in your paper.'),
    'validate_I2MC_settings': type_utils.GUIDocInfo('glassesValidator: I2MC settings','Settings for the I2MC fixation classifier used as part of determining the fixation that are assigned to validation targets. Settings that are "<not set>" will be determined based on the provided eye tracking data.',{
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
    'validate_dynamic_skip_first_duration': type_utils.GUIDocInfo('glassesValidator: Dynamic skip first duration', 'For a glassesValidator plane that is marked as dynamic (i.e. for a validation procedure using the PsychoPy script), how many seconds of data to not use from the beginning of each target interval.'),
    'validate_dynamic_max_gap_duration': type_utils.GUIDocInfo('glassesValidator: Dynamic, maximum gap duration', 'For a glassesValidator plane that is marked as dynamic (i.e. for a validation procedure using the PsychoPy script), maximum gap (number of frames) in marker detections that will be filled in (ignored).'),
    'validate_dynamic_min_duration': type_utils.GUIDocInfo('glassesValidator: Dynamic, minimum duration', 'Minimum duration (number of frames) that a marker should be detected. Shorter runs are removed.'),
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
if _missing_params:=[k for k in _params if k not in study_parameter_doc and k not in ['self','session_def','planes','individual_markers','working_directory','strict_check']]:
    raise NotImplementedError('Documentation missing for parameters:\n- '+'\n- '.join(_missing_params))
del _params
del _missing_params

def guess_config_dir(working_dir: str|pathlib.Path, config_dir_name: str = "config", json_file_name: str = Study.default_json_file_name) -> pathlib.Path:
    # can be invoked with either:
    # 1. the project folder;
    # 2. a session's working directory; or
    # 3. a recording's directory in a session's working directory.
    # So try three levels
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
    def get_allowed_parameters(level: OverrideLevel, recording_type: session.RecordingType|None = None) -> tuple[list[str],set[str]]:
        # NB: list instead of set as want to keep ordering
        all_params = list(study_parameter_types.keys())
        exclude = {'self', 'session_def', 'planes', 'individual_markers', 'working_directory', 'import_known_custom_eye_trackers'}
        # above is Session-level disallowed parameters. Depending on level, disallow more
        if level in [OverrideLevel.Recording, OverrideLevel.FunctionArgs]:
            # these make no sense on a recording level as they are settings for
            # processing functions that run on a whole session at once. As function
            # arguments they may make sense depending on the processing function that
            # is being called, but we cannot differentiate, so reject to be conservative
            # use whitelist
            if recording_type==session.RecordingType.Camera:
                # if for a camera recording, almost no parameters make sense to set
                include = {'auto_code_sync_points', 'auto_code_episodes'}
            else:
                include = {'get_cam_movement_for_et_sync_method','get_cam_movement_for_et_sync_function',
                           'auto_code_sync_points', 'auto_code_episodes',
                           'validate_do_global_shift', 'validate_max_dist_fac', 'validate_dq_types', 'validate_allow_dq_fallback', 'validate_include_data_loss', 'validate_I2MC_settings', 'validate_dynamic_skip_first_duration', 'validate_dynamic_max_gap_duration', 'validate_dynamic_min_duration'}
            exclude = set(all_params)-include
        allowed_params = [a for a in all_params if a not in exclude]
        return allowed_params, exclude

    def __init__(self, level: OverrideLevel, recording_type: session.RecordingType|None = None, **kwargs):
        self.override_level = level
        self.recording_type = recording_type
        self._allowed_params, self._excluded_parameters = self.get_allowed_parameters(level, recording_type)
        self._overridden_params: list[str] = []
        for p in self._allowed_params:
            self.clear_override(p)
        def typecheck_exception_handler(exc: typeguard.TypeCheckError, key: str):
            e = typeguard.TypeCheckError(*exc.args)
            e.append_path_element(f'argument "{key}" {self._get_err_msg()} ({exc._path[0]})')
            raise e from None
        kwargs = StudyOverride._fix_typing(kwargs)
        for p in kwargs:
            self._check_parameter(p, f"{StudyOverride.__name__}.__init__(): ")
            # special case: for dict-like object we can unset specific fields, so allow those by skipping check for them
            check_val = kwargs[p]
            if isinstance(check_val,dict) or typing.is_typeddict(check_val) or typed_dict_defaults.is_typeddictdefault(check_val) or type_utils.is_NamedTuple_type(check_val):
                check_val = {k:check_val[k] for k in check_val if check_val[k] is not None}
            typeguard.check_type(check_val, study_parameter_types[p], typecheck_fail_callback=lambda x,_: typecheck_exception_handler(x,p), collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
            setattr(self,p,kwargs[p])

    def __setattr__(self, name, value):
        if name.startswith('_') or name in {'override_level', 'recording_type'}:
            super(StudyOverride, self).__setattr__(name, value)
            return

        self._check_parameter(name)
        super(StudyOverride, self).__setattr__(name, value)
        if name not in self._overridden_params:
            self._overridden_params.append(name)

    def clear_override(self, name):
        self._check_parameter(name)
        setattr(self,name,None)
        if name in self._overridden_params:
            self._overridden_params.remove(name)

    def _check_parameter(self, name: str, error_prefix=''):
        if name in self._excluded_parameters:
            raise ValueError(f"{error_prefix}You are not allowed to override the '{name}' parameter of a {Study.__name__} class {self._get_err_msg()}")
        if name not in self._allowed_params:
            raise ValueError(f"{error_prefix}Got an unknown parameter '{name}'")

    def _get_err_msg(self):
        if self.override_level==OverrideLevel.FunctionArgs:
            err_text = 'in the parameter overrides provided as extra arguments to the processing function'
        else:
            err_text = f'in the {self.override_level.name}-level parameter overrides'
        if self.recording_type is not None:
            err_text += f' for a {self.recording_type.value} recording'
        return err_text

    def apply(self, study: Study, strict_check=True) -> Study:
        study = copy.deepcopy(study)
        study = _apply_impl(study, {p: getattr(self,p) for p in self._overridden_params}, study_parameter_types)
        # check resulting study is valid
        try:
            study.check_valid(strict_check)
        except Exception as oe:
            raise ValueError(f'Study setup became invalid {self._get_err_msg()}: {str(oe)}').with_traceback(oe.__traceback__) from None
        return study

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path = path / self.default_json_file_name
        to_dump = {p:getattr(self,p) for p in self._overridden_params}
        if 'planes_per_episode' in to_dump:
            to_dump['planes_per_episode'] = [(k, to_dump['planes_per_episode'][k]) for k in to_dump['planes_per_episode']]   # pack as list of tuples for storage
        json.dump(to_dump, path)

    @staticmethod
    def load_from_json(level: OverrideLevel, path: str | pathlib.Path, recording_type: session.RecordingType|None = None) -> 'StudyOverride':
        path = pathlib.Path(path)
        if path.is_dir():
            path = path / StudyOverride.default_json_file_name
        kwds = json.load(path)
        if 'planes_per_episode' in kwds:
            # stored as list of tuples, unpack
            kwds['planes_per_episode'] = {k:v for k,v in kwds['planes_per_episode']}
        # help with enum roundtrip
        if 'episodes_to_code' in kwds:
            kwds['episodes_to_code'] = {annotation.Event(e) for e in kwds['episodes_to_code']}
        if 'validate_dq_types' in kwds:
            kwds['validate_dq_types']= {DataQualityType(d) for d in kwds['validate_dq_types']}
        if 'mapped_video_which_gaze_type_on_plane' in kwds:
            kwds['mapped_video_which_gaze_type_on_plane'] = gaze_worldref.Type(kwds['mapped_video_which_gaze_type_on_plane'])
        return StudyOverride(level, recording_type, **kwds)

    @staticmethod
    def from_study_diff(config: Study, parent_config: Study, level: OverrideLevel, recording_type: session.RecordingType|None = None) -> 'StudyOverride':
        fields = StudyOverride.get_allowed_parameters(level, recording_type)[0]
        kwds = _study_diff_impl(config, parent_config, fields)
        return StudyOverride(level, recording_type, **kwds)

    @staticmethod
    def _fix_typing(kwds: dict[str,Any]) -> dict[str,Any]:
        if 'get_cam_movement_for_et_sync_function' in kwds and kwds['get_cam_movement_for_et_sync_function'] is not None:
            kwds['get_cam_movement_for_et_sync_function'] = CamMovementForEtSyncFunction(**kwds['get_cam_movement_for_et_sync_function'])
        if 'auto_code_sync_points' in kwds and kwds['auto_code_sync_points'] is not None:
            kwds['auto_code_sync_points'] = AutoCodeSyncPoints(**kwds['auto_code_sync_points'])
        if 'auto_code_episodes' in kwds and kwds['auto_code_episodes'] is not None:
            kwds['auto_code_episodes'] = {e: AutoCodeEpisodes(**kwds['auto_code_episodes'][e]) for e in kwds['auto_code_episodes']}
        if 'validate_I2MC_settings' in kwds and kwds['validate_I2MC_settings'] is not None:
            kwds['validate_I2MC_settings'] = I2MCSettings(**kwds['validate_I2MC_settings'])
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

def _study_diff_impl(config: Study, parent_config: Study, fields: list[str]) -> dict[str,Any]:
    kwds: dict[str,Any] = {}
    for f in fields:
        val        =        config.get(f) if isinstance(       config,dict) else getattr(       config,f)
        parent_val = parent_config.get(f) if isinstance(parent_config,dict) else getattr(parent_config,f)
        if val!=parent_val:
            if parent_val is not None and (isinstance(val,dict) or typing.is_typeddict(val) or typed_dict_defaults.is_typeddictdefault(val) or type_utils.is_NamedTuple_type(val)):
                # need to recurse into object
                val = _study_diff_impl(val, parent_val, list(set(type_utils.get_fields(val))|set(type_utils.get_fields(parent_val))))
            kwds[f] = val
    return kwds

def load_override_and_apply(study: Study, level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None, strict_check=True) -> Study:
    override_path = pathlib.Path(override_path)
    if override_path.is_dir():
        override_path = override_path / StudyOverride.default_json_file_name
    if not override_path.is_file():
        return study

    study_override = StudyOverride.load_from_json(level, override_path, recording_type)
    return study_override.apply(study, strict_check)

def load_or_create_override(level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None) -> StudyOverride:
    override_path = pathlib.Path(override_path)
    if override_path.is_dir():
        override_path = override_path / StudyOverride.default_json_file_name

    if not override_path.is_file():
        return StudyOverride(level, recording_type)
    else:
        return StudyOverride.load_from_json(level, override_path, recording_type)

def apply_kwarg_overrides(study: Study, strict_check=True, **kwargs) -> Study:
    if not kwargs:
        return study
    overrides = StudyOverride(OverrideLevel.FunctionArgs, **kwargs)
    return overrides.apply(study, strict_check)

def read_study_config_with_overrides(config_path: str|pathlib.Path, overrides: dict[OverrideLevel, str|pathlib.Path]=None, recording_type: session.RecordingType|None = None, strict_check=True, **kwargs) -> Study:
    study = Study.load_from_json(config_path)
    if overrides:
        for l in [OverrideLevel.Session, OverrideLevel.Recording]:
            if l in overrides:
                study = load_override_and_apply(study, l, overrides[l], recording_type, strict_check)
    if kwargs:
        study = apply_kwarg_overrides(study, strict_check, **kwargs)
    return study