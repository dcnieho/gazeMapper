import pathlib
import json
import inspect
import copy
import enum
import typeguard
import typing
from typing import Any, Literal, TypedDict

from glassesTools import annotation, utils
from glassesValidator import process as gv_process

from . import marker, plane, session, typed_dict_defaults, type_utils



class AutoCodeSyncPoints(typed_dict_defaults.TypedDictDefault, total=False):
    markers         : set[int]
    max_gap_duration: int       = 4
    min_duration    : int       = 6

class AutoCodeTrialEpisodes(typed_dict_defaults.TypedDictDefault, total=False):
    start_markers               : list[int]
    end_markers                 : list[int]
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

class CamMovementForEtSyncFunction(TypedDict):
    module_or_file  : str
    function        : str
    parameters      : dict[str,Any]

class RgbColor(typing.NamedTuple):
    r: int = 0
    g: int = 0
    b: int = 0

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

                 sync_ref_recording                             : str|None                          = None,
                 sync_ref_do_time_stretch                       : bool|None                         = None,
                 sync_ref_stretch_which                         : Literal['ref','other']|None       = None,
                 sync_ref_average_recordings                    : set[str]|None                     = None,

                 get_cam_movement_for_et_sync_method            : Literal['','plane','function']    = '',
                 get_cam_movement_for_et_sync_function          : CamMovementForEtSyncFunction|None = None,
                 sync_et_to_cam_use_average                     : bool                              = True,

                 auto_code_sync_points                          : AutoCodeSyncPoints|None           = None,
                 auto_code_trial_episodes                       : AutoCodeTrialEpisodes|None        = None,

                 export_output3D                                : bool                              = False,
                 export_output2D                                : bool                              = True,
                 export_only_code_marker_presence               : bool                              = True,

                 validate_do_global_shift                       : bool                              = True,
                 validate_max_dist_fac                          : float                             = .5,
                 validate_dq_types                              : set[gv_process.DataQualityType]|None = None,
                 validate_allow_dq_fallback                     : bool                              = False,
                 validate_include_data_loss                     : bool                              = False,
                 validate_I2MC_settings                         : I2MCSettings                      = I2MCSettings(),

                 video_make_which                               : set[str]|None                     = None,
                 video_recording_colors                         : dict[str,RgbColor]|None           = None,
                 video_process_planes_for_all_frames            : bool                              = False,
                 video_process_annotations_for_all_recordings   : bool                              = True,
                 video_show_detected_markers                    : bool                              = True,
                 video_show_plane_axes                          : bool                              = True,
                 video_process_individual_markers_for_all_frames: bool                              = True,
                 video_show_individual_marker_axes              : bool                              = True,
                 video_show_sync_func_output                    : bool                              = True,
                 video_show_unexpected_markers                  : bool                              = False,
                 video_show_rejected_markers                    : bool                              = False,
                 video_show_camera_in_ref                       : bool                              = True,
                 video_show_camera_in_other                     : bool                              = True,
                 video_show_gaze_vec_in_ref                     : bool                              = True,
                 video_show_gaze_vec_in_other                   : bool                              = False,
                 video_gaze_to_plane_margin                     : float                             = 0.25,

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

        self.get_cam_movement_for_et_sync_method            = get_cam_movement_for_et_sync_method
        self.get_cam_movement_for_et_sync_function          = get_cam_movement_for_et_sync_function
        self.sync_et_to_cam_use_average                     = sync_et_to_cam_use_average

        self.sync_ref_recording                             = sync_ref_recording
        self.sync_ref_do_time_stretch                       = sync_ref_do_time_stretch
        self.sync_ref_stretch_which                         = sync_ref_stretch_which
        self.sync_ref_average_recordings                    = sync_ref_average_recordings

        self.auto_code_sync_points                          = auto_code_sync_points
        self.auto_code_trial_episodes                       = auto_code_trial_episodes

        self.export_output3D                                = export_output3D
        self.export_output2D                                = export_output2D
        self.export_only_code_marker_presence               = export_only_code_marker_presence

        self.validate_do_global_shift                       = validate_do_global_shift
        self.validate_max_dist_fac                          = validate_max_dist_fac
        self.validate_dq_types                              = validate_dq_types
        self.validate_allow_dq_fallback                     = validate_allow_dq_fallback
        self.validate_include_data_loss                     = validate_include_data_loss
        self.validate_I2MC_settings                         = validate_I2MC_settings

        self.video_make_which                               = video_make_which
        self.video_recording_colors                         = video_recording_colors
        self.video_process_planes_for_all_frames            = video_process_planes_for_all_frames   # if True, all planes are processed for all frames, if False, only according to the planes_per_episode setup and the coding
        self.video_process_annotations_for_all_recordings   = video_process_annotations_for_all_recordings   # if True, all coded episodes for all planes of all recordings are processed (so e.g. if validation coded for one recording in the session, that plane is processed for all)
        self.video_show_detected_markers                    = video_show_detected_markers
        self.video_show_plane_axes                          = video_show_plane_axes
        self.video_process_individual_markers_for_all_frames= video_process_individual_markers_for_all_frames   # if True, all frames are processed in search of individual markers, if False, individual markers are only searched for when in a coded episode of any of the planes specified in planes_per_episode setup
        self.video_show_individual_marker_axes              = video_show_individual_marker_axes
        self.video_show_sync_func_output                    = video_show_sync_func_output
        self.video_show_unexpected_markers                  = video_show_unexpected_markers
        self.video_show_rejected_markers                    = video_show_rejected_markers
        self.video_show_camera_in_ref                       = video_show_camera_in_ref
        self.video_show_camera_in_other                     = video_show_camera_in_other
        self.video_show_gaze_vec_in_ref                     = video_show_gaze_vec_in_ref
        self.video_show_gaze_vec_in_other                   = video_show_gaze_vec_in_other
        self.video_gaze_to_plane_margin                     = video_gaze_to_plane_margin    # fraction of plane size, added to each side of the plane

        self.gui_num_workers                                = gui_num_workers

        self.check_valid(strict_check=strict_check)

    def check_valid(self, strict_check=True):
        if strict_check:
            self._check_session_def(strict_check)
            self._check_planes_per_episode(strict_check)
            self._check_episodes_to_code(strict_check)
            self._check_sync_ref(strict_check)
            self._check_et_sync_method(strict_check)
            self._check_auto_coding_setup(strict_check)
            self._check_make_video(strict_check)

        # ensure some members are of the right class, and apply defaults
        if self.auto_code_sync_points:
            self.auto_code_sync_points = AutoCodeSyncPoints(self.auto_code_sync_points)
            self.auto_code_sync_points.apply_defaults()
        if self.auto_code_trial_episodes:
            self.auto_code_trial_episodes = AutoCodeTrialEpisodes(self.auto_code_trial_episodes)
            self.auto_code_trial_episodes.apply_defaults()
        self.validate_I2MC_settings = I2MCSettings(self.validate_I2MC_settings)
        self.validate_I2MC_settings.apply_defaults()

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
        for e in self.planes_per_episode:
            missing_planes: list[str] = []
            for p in self.planes_per_episode[e]:
                if not any([p==pl.name for pl in self.planes]):
                    if strict_check:
                        raise ValueError(f'Plane {p} not known')
                    else:
                        missing_planes.append(p)
            if missing_planes:
                if 'planes_per_episode' not in problems:
                    problems['planes_per_episode'] = {}
                problems['planes_per_episode'][e] = f'Plane(s) {missing_planes[0] if len(missing_planes)==1 else missing_planes} not known.'

        if annotation.Event.Validate in self.episodes_to_code and annotation.Event.Validate not in self.planes_per_episode:
            if strict_check:
                raise ValueError('Validation episodes are set up to be coded for this study, but no planes are set up to be used for validation in planes_per_episode')
            else:
                type_utils.merge_problem_dicts(problems, {'planes_per_episode': 'Validation episodes are set up to be coded for this study, but no planes are set up to be used for validation'})
        return problems

    def _check_episodes_to_code(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        for e in self.planes_per_episode:
            if e not in self.episodes_to_code:
                if strict_check:
                    raise ValueError(f'Plane(s) are defined in planes_per_episode for {e.name} events, but {e.name} events are not set up to be coded in episodes_to_code. Fix episodes_to_code.')
                else:
                    msg = f'Plane(s) are defined in planes_per_episode for {e.value} events, but {e.value} events are not set up to be coded.'
                    if 'episodes_to_code' in problems:
                        problems['episodes_to_code'] = '\n'.join([problems['episodes_to_code'], msg])
                    else:
                        problems['episodes_to_code'] = msg
                    if 'planes_per_episode' not in problems:
                        problems['planes_per_episode'] = {}
                    problems['planes_per_episode'][e] = f'Plane(s) are defined in planes_per_episode for {e.value} events, but {e.value} events are not set up to be coded. Remove {e.value} from planes_per_episode.'
        return problems

    def _check_auto_coding_setup(self, strict_check) -> type_utils.ProblemDict:
        problems = self._check_auto_markers(strict_check)
        this_problems: type_utils.ProblemDict = {}
        if self.auto_code_sync_points:
            if annotation.Event.Sync_Camera not in self.episodes_to_code:
                if strict_check:
                    raise ValueError(f'The auto_code_sync_points option is configured, but {annotation.Event.Sync_Camera} points are not set to be coded in episodes_to_code. Fix episodes_to_code.')
                else:
                    this_problems['episodes_to_code'] = f'The auto_code_sync_points option is configured, but {annotation.Event.Sync_Camera.value} points are not set to be coded in episodes_to_code.'
                    this_problems['auto_code_sync_points'] = f'The auto_code_sync_points option is configured, but {annotation.Event.Sync_Camera.value} points are not set to be coded in episodes_to_code. Fix episodes_to_code or remove auto_code_sync_points setup.'
        if self.auto_code_trial_episodes:
            if annotation.Event.Trial not in self.episodes_to_code:
                if strict_check:
                    raise ValueError(f'The auto_code_trial_episodes option is configured, but {annotation.Event.Trial} episodes are not set to be coded in episodes_to_code. Fix episodes_to_code.')
                else:
                    this_problems['episodes_to_code'] = f'The auto_code_trial_episodes option is configured, but {annotation.Event.Trial.value} episodes are not set to be coded in episodes_to_code.'
                    this_problems['auto_code_trial_episodes'] = f'The auto_code_trial_episodes option is configured, but {annotation.Event.Trial.value} episodes are not set to be coded in episodes_to_code. Fix episodes_to_code or remove auto_code_sync_points setup.'
        return type_utils.merge_problem_dicts(problems,this_problems)

    def _check_auto_markers(self, strict_check) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        if self.auto_code_sync_points:
            if 'markers' not in self.auto_code_sync_points:
                if strict_check:
                    raise ValueError('auto_code_sync_points.markers cannot be empty or unspecified')
                else:
                    problems['auto_code_sync_points'] = {}
                    problems['auto_code_sync_points']['markers'] = 'auto_code_sync_points.markers cannot be empty or unspecified'
            else:
                missing_markers: list[int] = []
                for i in self.auto_code_sync_points['markers']:
                    if not any([m.id==i for m in self.individual_markers]):
                        if strict_check:
                            raise ValueError(f'Marker "{i}" specified in auto_code_sync_points.markers, but unknown because not present in individual_markers')
                        else:
                            missing_markers.append(i)
                if missing_markers:
                    problems['auto_code_sync_points'] = {}
                    problems['auto_code_sync_points']['markers'] = f'The marker(s) {missing_markers[0] if len(missing_markers)==1 else missing_markers} are not defined in individual_markers'
        if self.auto_code_trial_episodes:
            for f in ['start_markers','end_markers']:
                if f not in self.auto_code_trial_episodes:
                    if strict_check:
                        raise ValueError(f'auto_code_trial_episodes.{f} cannot be empty or unspecified')
                    else:
                        if 'auto_code_trial_episodes' not in problems:
                            problems['auto_code_trial_episodes'] = {}
                        problems['auto_code_trial_episodes'][f] = f'auto_code_trial_episodes.{f} cannot be empty or unspecified'
                else:
                    missing_markers: list[int] = []
                    for i in self.auto_code_trial_episodes[f]:
                        if not any([m.id==i for m in self.individual_markers]):
                            if strict_check:
                                raise ValueError(f'Marker "{i}" specified in auto_code_trial_episodes.{f}, but unknown because not present in individual_markers')
                            else:
                                missing_markers.append(i)
                    if missing_markers:
                        if 'auto_code_trial_episodes' not in problems:
                            problems['auto_code_trial_episodes'] = {}
                        problems['auto_code_trial_episodes'][f] = f'The marker(s) {missing_markers[0] if len(missing_markers)==1 else missing_markers} are not defined in individual_markers'
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
        for a in ['sync_ref_do_time_stretch', 'sync_ref_stretch_which', 'sync_ref_average_recordings']:
            if getattr(self,a) is None:
                if strict_check:
                    raise ValueError(f'{a} should be set in the study setup when sync_ref_recording is set')
                else:
                    problems[a] = f'{a} should be set when sync_ref_recording is set'
        if self.sync_ref_average_recordings and self.sync_ref_recording in self.sync_ref_average_recordings:
            if strict_check:
                raise ValueError(f'Recording {self.sync_ref_recording} is the reference recording for sync, should not be specified in sync_average_recordings')
            else:
                problems['sync_ref_average_recordings'] = f'Recording {self.sync_ref_recording} is the reference recording for sync, cannot be specified in sync_average_recordings'
        if annotation.Event.Sync_Camera not in self.episodes_to_code:
            if strict_check:
                raise ValueError('when sync_ref_recording is set, coding of camera sync points should be set up in episodes_to_code')
            else:
                problems['episodes_to_code'] = f'if sync_ref_recording is set, {annotation.Event.Sync_Camera.value} events should be set up to be coded'
                type_utils.merge_problem_dicts(problems, {'sync_ref_recording': f'sync_ref_recording is set, but {annotation.Event.Sync_Camera.value} events are not set up to be coded in episodes_to_code'})
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
                raise ValueError(f'if get_cam_movement_for_et_sync_method is set to "plane" or "function", {annotation.Event.Sync_ET_Data.value} events should be set up to be coded in episodes_to_code')
            else:
                problems['episodes_to_code'] = f'if get_cam_movement_for_et_sync_method is set to "plane" or "function", {annotation.Event.Sync_ET_Data.value} events should be set up to be coded'
                problems['get_cam_movement_for_et_sync_method'] = f'get_cam_movement_for_et_sync_method is set to "{self.get_cam_movement_for_et_sync_method}", but {annotation.Event.Sync_ET_Data.value} events are not set up to be coded in episodes_to_code'
        if self.get_cam_movement_for_et_sync_method=='function':
            if strict_check:
                if not self.get_cam_movement_for_et_sync_function or not all([x in self.get_cam_movement_for_et_sync_function for x in ["module_or_file","function","parameters"]]):
                    raise ValueError('if get_cam_movement_for_et_sync_method is set to "function", get_cam_movement_for_et_sync_function should be a dict specifying "module_or_file", "function", and "parameters"')
            else:
                t = utils.unpack_none_union(study_parameter_types['get_cam_movement_for_et_sync_function'])[0]
                problems['get_cam_movement_for_et_sync_function'] = {k:f'{k} should be set when get_cam_movement_for_et_sync_function is set to "function"' for k in t.__required_keys__ if not self.get_cam_movement_for_et_sync_function or k not in self.get_cam_movement_for_et_sync_function or (k!='parameters' and not self.get_cam_movement_for_et_sync_function[k])}
        elif self.get_cam_movement_for_et_sync_method=='plane':
            if annotation.Event.Sync_ET_Data not in self.planes_per_episode:
                if strict_check:
                    raise ValueError(f'if get_cam_movement_for_et_sync_method is set to "plane", a plane should be set up to be used for processing {annotation.Event.Sync_ET_Data.value} events in planes_per_episode')
                else:
                    problems['planes_per_episode'] = f'if get_cam_movement_for_et_sync_method is set to "plane", a plane should be set up to be used for processing {annotation.Event.Sync_ET_Data.value} events'
                    type_utils.merge_problem_dicts(problems, {'get_cam_movement_for_et_sync_method': f'get_cam_movement_for_et_sync_method is set to "plane", but no plane specified for syncing eye tracker data to the scene cam (i.e., for {annotation.Event.Sync_ET_Data.value} events) in planes_per_episode'})
        return problems

    def _check_make_video(self, strict_check) -> type_utils.ProblemDict:
        problems = self._check_recordings(self.video_make_which, 'video_make_which', strict_check)
        type_utils.merge_problem_dicts(problems,
                   self._check_recordings(self.video_recording_colors, 'video_recording_colors', strict_check))
        if self.video_make_which:
            # check have colors for all eye tracker recordings
            all_recs = {r.name for r in self.session_def.recordings if r.type==session.RecordingType.Eye_Tracker}
            if self.video_recording_colors:
                missing = list(all_recs-set(self.video_recording_colors.keys()))
            else:
                missing = list(all_recs)
            if missing:
                msg = f'Colors need to be defined for all eye tracker recordings. Missing for {missing[0] if len(missing)==1 else missing}'
                if strict_check:
                    raise ValueError(msg)
                else:
                    type_utils.merge_problem_dicts(problems,{'video_recording_colors': msg})
        return problems

    def field_problems(self) -> type_utils.ProblemDict:
        problems: type_utils.ProblemDict = {}
        type_utils.merge_problem_dicts(problems, self._check_session_def(False))
        type_utils.merge_problem_dicts(problems, self._check_planes_per_episode(False))
        type_utils.merge_problem_dicts(problems, self._check_episodes_to_code(False))
        type_utils.merge_problem_dicts(problems, self._check_auto_coding_setup(False))
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
        with open(f_path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_') and k not in ['session_def','planes','working_directory']}    # session_def and planes will be populated from contents in the provided folder, and working_directory as the provided path
            to_dump['planes_per_episode'] = [(k, to_dump['planes_per_episode'][k]) for k in to_dump['planes_per_episode']]   # pack as list of tuples for storage
            # filter out defaulted
            to_dump = {k:to_dump[k] for k in to_dump if k not in study_defaults or study_defaults[k]!=to_dump[k]}
            # also filter out defaults in some subfields
            for k in ['auto_code_sync_points','auto_code_trial_episodes','validate_I2MC_settings']:
                if k in to_dump:
                    to_dump[k] = {kk:to_dump[k][kk] for kk in to_dump[k] if kk not in to_dump[k]._field_defaults or to_dump[k]._field_defaults[kk]!=to_dump[k][kk]}
                    if not to_dump[k]:
                        to_dump.pop(k)
            # dump to file
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)
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
        with open(d_path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        kwds['planes_per_episode'] = {k:v for k,v in kwds['planes_per_episode']}  # stored as list of tuples, unpack
        # help with named tuple roundtrip
        if 'video_recording_colors' in kwds:
            kwds['video_recording_colors'] = {k: RgbColor(*kwds['video_recording_colors'][k]) for k in kwds['video_recording_colors']}
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
del _params

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
        exclude = {'self', 'session_def', 'planes', 'individual_markers', 'working_directory', 'planes_per_episode'}
        # above is Session-level disallowed parameters. Depending on level, disallow more
        if level in [OverrideLevel.Recording, OverrideLevel.FunctionArgs]:
            # these make no sense on a recording level as they are settings for
            # processing functions that run on a whole session at once. As function
            # arguments they may make sense depending on the processing function that
            # is being called, but we cannot differentiate, so reject to be conservative
            # use whitelist
            if recording_type==session.RecordingType.Camera:
                # if for a camera recording, almost no parameters make sense to set
                include = {'auto_code_sync_points', 'auto_code_trial_episodes'}
            else:
                include = {'get_cam_movement_for_et_sync_method','get_cam_movement_for_et_sync_function',
                           'auto_code_sync_points', 'auto_code_trial_episodes',
                           'validate_do_global_shift', 'validate_max_dist_fac', 'validate_dq_types', 'validate_allow_dq_fallback', 'validate_include_data_loss', 'validate_I2MC_settings'}
            exclude = set(all_params)-include
        allowed_params = [a for a in all_params if a not in exclude]
        return allowed_params, exclude

    def __init__(self, level: OverrideLevel, recording_type: session.RecordingType|None = None, **kwargs):
        self.override_level = level
        self.recording_type = recording_type
        self._allowed_params, exclude = self.get_allowed_parameters(level, recording_type)
        self._overridden_params: list[str] = []
        for p in self._allowed_params:
            setattr(self,p,None)
        def typecheck_exception_handler(exc: typeguard.TypeCheckError, key: str):
            e = typeguard.TypeCheckError(*exc.args)
            e.append_path_element(f'argument "{key}" {self.get_err_msg()} ({exc._path[0]})')
            raise e from None
        for p in kwargs:
            if p in exclude:
                raise TypeError(f"{StudyOverride.__name__}.__init__(): you are not allowed to override the '{p}' parameter of a {Study.__name__} class {self.get_err_msg()}")
            if p not in self._allowed_params:
                raise TypeError(f"{StudyOverride.__name__}.__init__(): got an unknown parameter '{p}'")
            typeguard.check_type(kwargs[p], study_parameter_types[p], typecheck_fail_callback=lambda x,_: typecheck_exception_handler(x,p), collection_check_strategy=typeguard.CollectionCheckStrategy.ALL_ITEMS)
            setattr(self,p,kwargs[p])
            self._overridden_params.append(p)

    def _get_err_msg(self):
        if self.override_level==OverrideLevel.FunctionArgs:
            err_text = 'in the parameter overrides provided as extra arguments to the processing function'
        else:
            err_text = f'in the {self.override_level.name}-level parameter overrides'
        if self.recording_type is not None:
            err_text += f' for a {self.recording_type.value} recording'
        return err_text

    def apply(self, study: Study, strict_check=True) -> Study:
        study = copy.copy(study)
        for p in self._overridden_params:
            val = getattr(self,p)
            if isinstance(val,dict):
                # overwrite existing and add new dict keys
                setattr(study,p,current|val if (current:=getattr(study,p)) is not None else val)
            else:
                setattr(study,p,val)
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
        with open(path, 'w') as f:
            to_dump = {p:getattr(self,p) for p in self._overridden_params}
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(level: OverrideLevel, path: str | pathlib.Path, recording_type: session.RecordingType|None = None) -> 'StudyOverride':
        path = pathlib.Path(path)
        if path.is_dir():
            path = path / StudyOverride.default_json_file_name
        with open(path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        return StudyOverride(level, recording_type, **kwds)

    @staticmethod
    def from_study_diff(config: Study, parent_config: Study, level: OverrideLevel, recording_type: session.RecordingType|None = None) -> 'StudyOverride':
        fields = StudyOverride.get_allowed_parameters(level, recording_type)[0]
        kwds = _study_diff_impl(config, parent_config, fields)
        return StudyOverride(level, recording_type, **kwds)

def _study_diff_impl(config: Study, parent_config: Study, fields: list[str]) -> dict[str,Any]:
    kwds: dict[str,Any] = {}
    for f in fields:
        val        =        config.get(f) if isinstance(       config,dict) else getattr(       config,f)
        parent_val = parent_config.get(f) if isinstance(parent_config,dict) else getattr(parent_config,f)
        if val!=parent_val:
            if isinstance(val,dict) or typing.is_typeddict(val) or typed_dict_defaults.is_typeddictdefault(val) or type_utils.is_NamedTuple_type(val):
                # need to recurse into object
                val = _study_diff_impl(val, parent_val, type_utils.get_fields(val))
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