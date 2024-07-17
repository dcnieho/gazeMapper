import pathlib
import json

from glassesTools import utils

from . import episode, marker, plane, session


class Study:
    default_json_file_name = 'study_def.json'

    def __init__(self,
                 session_def: session.SessionDefinition,
                 planes: list[plane.Definition],
                 planes_per_episode: dict[episode.Event,list[str]],
                 episodes_to_code: list[episode.Event],
                 individual_markers: list[Marker],
                 get_cam_movement_for_et_sync_method: str,
                 sync_ref_recording: str,
                 do_time_stretch: bool,
                 stretch_which: str,
                 sync_average_recordings: list[str],
                 working_directory: str|pathlib.Path,
                 # optional arguments
                 get_cam_movement_for_et_sync_function: dict[str,str|dict[str]]=None):
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

        self._check_planes_per_episode()
        self._check_recordings([self.sync_ref_recording], 'sync_ref_recording')
        self._check_recordings(self.sync_average_recordings, 'sync_average_recordings')
        assert self.sync_ref_recording not in self.sync_average_recordings, f'Recording {self.sync_ref_recording} is the reference recording for sync, should not be specified sync_average_recordings'
        assert self.get_cam_movement_for_et_sync_method in ['','plane','function'], 'get_cam_movement_for_et_sync_method parameter should be an empty string, "plane", or "function"'
        if self.get_cam_movement_for_et_sync_method=='function':
            assert all([x in self.get_cam_movement_for_et_sync_function for x in ["module_or_file","function","parameters"]]), 'if get_cam_movement_for_et_sync_method is set to "function", get_cam_movement_for_et_sync_function should specify "module_or_file", "function", and "parameters"'

    def _check_planes_per_episode(self):
        for e in self.planes_per_episode:
            for p in self.planes_per_episode[e]:
                if not any([p==pl.name for pl in self.planes]):
                    raise ValueError(f'Plane {p} not known')

    def _check_recordings(self, which, field):
        for w in which:
            if not any([r.name==w for r in self.session_def.recordings]):
                raise ValueError(f'Recording "{w}" not known, check {field}')

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        # this stores only the planes_per_episode variable to json, rest is read from other files
        # instead to remain flexible and make it easy for users to rename, etc
        d_path = path / self.default_json_file_name
        with open(d_path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in ['planes_per_episode','episodes_to_code','get_cam_movement_for_et_sync_method','individual_markers','sync_ref_recording','do_time_stretch','stretch_which','sync_average_recordings']}    # only these fields. session_def and planes will be populated from contents in the provided folder, and working_directory as the provided path
            to_dump['planes_per_episode'] = [(k, to_dump['planes_per_episode'][k]) for k in to_dump['planes_per_episode']]   # pack as list of tuples for storage
            # optional arguments
            if self.get_cam_movement_for_et_sync_method=='function':
                to_dump['get_cam_movement_for_et_sync_function'] = self.get_cam_movement_for_et_sync_function
            # dump to file
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)
        # this doesn't story any files itself, but triggers the contained info to be stored
        self.session_def.store_as_json(self.working_directory / 'session_def.json')
        for p in self.planes:
            p_dir = self.working_directory / p.name
            if not p_dir.is_dir():
                p_dir.mkdir()
            p.store_as_json(p_dir)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Study':
        path = pathlib.Path(path)
        # get kwds
        d_path = path / Study.default_json_file_name
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

def guess_config_dir(working_dir: str|pathlib.Path, config_dir_name: str = "config", json_file_name: str = Study.default_json_file_name) -> pathlib.Path:
    # can be either in a session's working directory, or in a recording's in such
    # a session's working directory. So try two levels
    for _ in range(2):
        working_dir = working_dir.parent
        test_dir = working_dir / config_dir_name
        if not test_dir.is_dir():
            continue
        test_file = test_dir / json_file_name
        if test_file.is_file():
            return test_dir

    raise RuntimeError('config directory not found')