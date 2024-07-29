import pathlib
import json
import inspect
import copy
import enum

from glassesTools import annotation, utils

from . import marker, plane, session


defaults = {
    'auto_code_sync_points.max_gap_duration': 4,
    'auto_code_sync_points.min_duration': 6,

    'auto_code_trials_episodes.max_gap_duration': 4,
    'auto_code_trials_episodes.max_intermarker_gap_duration': 15,
    'auto_code_trials_episodes.min_duration': 6,
}

class Study:
    default_json_file_name = 'study_def.json'

    def __init__(self,
                 session_def: session.SessionDefinition,
                 planes: list[plane.Definition],
                 individual_markers: list[marker.Marker],
                 working_directory: str|pathlib.Path,
                 planes_per_episode: dict[annotation.Event,list[str]],

                 episodes_to_code: list[annotation.Event],

                 get_cam_movement_for_et_sync_method: str,

                 sync_ref_recording: str,
                 do_time_stretch: bool,
                 stretch_which: str,
                 sync_average_recordings: list[str],

                 # setup with defaults
                 get_cam_movement_for_et_sync_function: dict[str,str|dict[str]]=None,

                 auto_code_sync_points: dict[str]=None,
                 auto_code_trials_episodes: dict[str]=None,

                 make_video_which: list[str]=None,
                 video_recording_colors: dict[str,tuple[int,int,int]]=None,
                 video_process_planes_for_all_frames=False,
                 video_process_annotations_for_all_recordings=True,
                 video_show_detected_markers=True,
                 video_show_board_axes=True,
                 video_process_individual_markers_for_all_frames=True,
                 video_show_individual_marker_axes=True,
                 video_show_sync_func_output=True,
                 video_show_unexpected_markers=False,
                 video_show_rejected_markers=False,
                 video_show_camera_in_ref=True,
                 video_show_camera_in_other=True,
                 video_show_gaze_vec_in_ref=True,
                 video_show_gaze_vec_in_other=False,
                 video_gaze_to_plane_margin=0.25):
        self.session_def            = session_def
        self.planes                 = planes
        self.planes_per_episode     = planes_per_episode
        self.episodes_to_code       = episodes_to_code
        self.working_directory      = working_directory

        self.get_cam_movement_for_et_sync_method    = get_cam_movement_for_et_sync_method
        self.get_cam_movement_for_et_sync_function  = get_cam_movement_for_et_sync_function

        self.sync_ref_recording     = sync_ref_recording
        self.do_time_stretch        = do_time_stretch
        self.stretch_which          = stretch_which
        self.sync_average_recordings= sync_average_recordings
        self.individual_markers     = individual_markers

        self.auto_code_sync_points      = auto_code_sync_points
        self.auto_code_trials_episodes  = auto_code_trials_episodes

        self.make_video_which                               = make_video_which
        self.video_recording_colors                         = video_recording_colors
        self.video_process_planes_for_all_frames            = video_process_planes_for_all_frames   # if True, all planes are processed for all frames, if False, only according to the planes_per_episode setup and the coding
        self.video_process_annotations_for_all_recordings   = video_process_annotations_for_all_recordings   # if True, all coded episodes for all planes of all recordings are processed (so e.g. if validation coded for one recording in the session, that plane is processed for all)
        self.video_show_detected_markers                    = video_show_detected_markers
        self.video_show_board_axes                          = video_show_board_axes
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

        self._check_all()

    def _check_all(self):
        self._check_planes_per_episode()
        self._check_auto_markers()
        self._check_recordings([self.sync_ref_recording], 'sync_ref_recording')
        self._check_recordings(self.sync_average_recordings, 'sync_average_recordings')
        self._check_recordings(self.make_video_which, 'make_video_which')
        self._check_recordings(self.video_recording_colors, 'video_recording_colors')
        assert self.sync_ref_recording not in self.sync_average_recordings, f'Recording {self.sync_ref_recording} is the reference recording for sync, should not be specified in sync_average_recordings'
        assert self.get_cam_movement_for_et_sync_method in ['','plane','function'], 'get_cam_movement_for_et_sync_method parameter should be an empty string, "plane", or "function"'
        if self.get_cam_movement_for_et_sync_method=='function':
            assert all([x in self.get_cam_movement_for_et_sync_function for x in ["module_or_file","function","parameters"]]), 'if get_cam_movement_for_et_sync_method is set to "function", get_cam_movement_for_et_sync_function should be a dict specifying "module_or_file", "function", and "parameters"'
        for e in self.planes_per_episode:
            assert e in self.episodes_to_code, f'Plane(s) are defined in planes_per_episode for {e.name} events, but {e.name} events are not set up to be coded in episodes_to_code. Fix episodes_to_code.'
        if self.auto_code_sync_points:
            assert annotation.Event.Sync_Camera in self.episodes_to_code, f'The auto_code_sync_points option is configured, but {annotation.Event.Sync_Camera} points are not set to be coded in episodes_to_code. Fix episodes_to_code.'
        if self.auto_code_trials_episodes:
            assert annotation.Event.Trial in self.episodes_to_code, f'The auto_code_trials_episodes option is configured, but {annotation.Event.Trial} episodes are not set to be coded in episodes_to_code. Fix episodes_to_code.'

        if self.auto_code_sync_points:
            if 'max_gap_duration' not in self.auto_code_sync_points:
                self.auto_code_sync_points['max_gap_duration'] = defaults['auto_code_sync_points.max_gap_duration']
            if 'min_duration' not in self.auto_code_sync_points:
                self.auto_code_sync_points['min_duration'] = defaults['auto_code_sync_points.min_duration']
        if self.auto_code_trials_episodes:
            if 'max_gap_duration' not in self.auto_code_trials_episodes:
                self.auto_code_trials_episodes['max_gap_duration'] = defaults['auto_code_trials_episodes.max_gap_duration']
            if 'max_intermarker_gap_duration' not in self.auto_code_trials_episodes:
                self.auto_code_trials_episodes['max_intermarker_gap_duration'] = defaults['auto_code_trials_episodes.max_intermarker_gap_duration']
            if 'min_duration' not in self.auto_code_trials_episodes:
                self.auto_code_trials_episodes['min_duration'] = defaults['auto_code_trials_episodes.min_duration']

    def _check_planes_per_episode(self):
        for e in self.planes_per_episode:
            for p in self.planes_per_episode[e]:
                if not any([p==pl.name for pl in self.planes]):
                    raise ValueError(f'Plane {p} not known')

    def _check_auto_markers(self):
        if self.auto_code_sync_points:
            for i in self.auto_code_sync_points['markers']:
                if not any([m.id==i for m in self.individual_markers]):
                    raise ValueError(f'Marker "{i}" specified in auto_code_sync_points.markers, but unknown because not present in individual_markers')
        if self.auto_code_trials_episodes:
            for f in ['start_markers','end_markers']:
                for i in self.auto_code_trials_episodes[f]:
                    if not any([m.id==i for m in self.individual_markers]):
                        raise ValueError(f'Marker "{i}" specified in auto_code_trials_episodes.{f}, but unknown because not present in individual_markers')

    def _check_recordings(self, which, field):
        if not which:
            return
        for w in which:
            if not any([r.name==w for r in self.session_def.recordings]):
                raise ValueError(f'Recording "{w}" not known, check {field} in the study configuration')

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        # this stores only the planes_per_episode variable to json, rest is read from other files
        # instead to remain flexible and make it easy for users to rename, etc
        if path.is_dir():
            path = path / self.default_json_file_name
        with open(path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_') and k not in ['session_def','planes','working_directory']}    # session_def and planes will be populated from contents in the provided folder, and working_directory as the provided path
            to_dump['planes_per_episode'] = [(k, to_dump['planes_per_episode'][k]) for k in to_dump['planes_per_episode']]   # pack as list of tuples for storage
            # optional arguments
            if self.get_cam_movement_for_et_sync_method=='function':
                to_dump['get_cam_movement_for_et_sync_function'] = self.get_cam_movement_for_et_sync_function
            # dump to file
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)
        # this doesn't store any files itself, but triggers the contained info to be stored
        self.session_def.store_as_json(self.working_directory / 'session_def.json')
        for p in self.planes:
            p_dir = self.working_directory / p.name
            if not p_dir.is_dir():
                p_dir.mkdir()
            p.store_as_json(p_dir)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Study':
        path = pathlib.Path(path)
        if path.is_dir():
            d_path = path / Study.default_json_file_name
        else:
            d_path = path
        # get kwds
        with open(d_path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        kwds['planes_per_episode'] = {k:v for k,v in kwds['planes_per_episode']}  # stored as list of tuples, unpack
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

        return Study(sess_def, planes, working_directory=path, **kwds)

class OverrideLevel(enum.Enum):
    Session     = enum.auto()
    Recording   = enum.auto()
    FunctionArgs= enum.auto()

class StudyOverride:
    default_json_file_name = 'study_def_override.json'

    def __init__(self, level: OverrideLevel, **kwargs):
        study_init = inspect.signature(Study.__init__)
        exclude = {'self', 'session_def', 'planes', 'individual_markers', 'working_directory', 'planes_per_episode'}
        # TODO: depending on level, disallow more
        self._params = set(study_init.parameters)-exclude
        for p in self._params:
            setattr(self,p,None)
        for p in kwargs:
            if p in exclude:
                raise TypeError(f"{StudyOverride.__name__}.__init__(): you are not allowed to override the '{p}' parameter of a {Study.__name__} class")
            if p not in self._params:
                raise TypeError(f"{StudyOverride.__name__}.__init__(): got an unknown parameter '{p}'")
            setattr(self,p,kwargs[p])

    def apply(self, study: Study) -> Study:
        study = copy.copy(study)
        for p in self._params:
            if (val:=getattr(self,p)) is not None:
                if isinstance(val,dict):
                    # overwrite existing and add new dict keys
                    setattr(study,p,getattr(study,p)|val)
                else:
                    setattr(study,p,val)
        # check resulting study is valid
        study._check_all()
        return study

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        # this stores only the planes_per_episode variable to json, rest is read from other files
        # instead to remain flexible and make it easy for users to rename, etc
        if path.is_dir():
            path = path / self.default_json_file_name
        with open(path, 'w') as f:
            to_dump = {p:v for p in self._params if (v:=getattr(self,p)) is not None}
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(level: OverrideLevel, path: str | pathlib.Path) -> 'StudyOverride':
        path = pathlib.Path(path)
        if path.is_dir():
            path = path / StudyOverride.default_json_file_name
        # get kwds
        with open(path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        return StudyOverride(level, **kwds)

def load_override_and_apply(study: Study, level: OverrideLevel, override_path: str|pathlib.Path) -> Study:
    override_path = pathlib.Path(override_path)
    if override_path.is_dir():
        override_path = override_path / StudyOverride.default_json_file_name
    if not override_path.is_file():
        return study

    study_override = StudyOverride.load_from_json(level, override_path)
    return study_override.apply(study)

def apply_kwarg_overrides(study: Study, **kwargs) -> Study:
    if not kwargs:
        return study
    overrides = StudyOverride(OverrideLevel.FunctionArgs, **kwargs)
    return overrides.apply(study)

def read_study_config_with_overrides(config_path: str|pathlib.Path, overrides: dict[OverrideLevel, str|pathlib.Path] = None, **kwargs) -> Study:
    study = Study.load_from_json(config_path)
    if overrides:
        for l in [OverrideLevel.Session, OverrideLevel.Recording]:
            if l in overrides:
                study = load_override_and_apply(study, l, overrides[l])
    if kwargs:
        study = apply_kwarg_overrides(study, **kwargs)
    return study

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