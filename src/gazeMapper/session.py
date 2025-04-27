from enum import auto
import pathlib
import typeguard
import shutil

from glassesTools import camera_recording, importing, json, naming, process_pool, utils
from glassesTools.recording import Recording as EyeTrackerRecording
from glassesTools.camera_recording import Recording as CameraRecording

from . import process


class RecordingType(utils.AutoName):
    Eye_Tracker = auto()
    Camera      = auto()
json.register_type(json.TypeEntry(RecordingType,'__enum.session.RecordingType__', utils.enum_val_2_str, lambda x: getattr(RecordingType, x.split('.')[1])))
recording_types = [r for r in RecordingType]


class RecordingDefinition:
    cal_file_name = naming.scene_camera_calibration_fname
    @typeguard.typechecked
    def __init__(self, name:str, type:RecordingType):
        self.name = name
        self.type = type

    def set_default_cal_file(self, cal_path: str|pathlib.Path, rec_def_path: str|pathlib.Path):
        cal_path = pathlib.Path(cal_path)
        rec_def_path = pathlib.Path(rec_def_path)
        shutil.copyfile(str(cal_path), str(rec_def_path / f'{self.name}_{RecordingDefinition.cal_file_name}'))

    def get_default_cal_file(self, rec_def_path: str|pathlib.Path) -> pathlib.Path|None:
        cal_path = pathlib.Path(rec_def_path) / f'{self.name}_{RecordingDefinition.cal_file_name}'
        if cal_path.is_file():
            return cal_path
        return None

    def remove_default_cal_file(self, rec_def_path: str|pathlib.Path):
        cal_path = pathlib.Path(rec_def_path) / f'{self.name}_{RecordingDefinition.cal_file_name}'
        cal_path.unlink(missing_ok=True)

json.register_type(json.TypeEntry(RecordingDefinition,'__session.RecordingDefinition__',lambda x: {'name': x.name, 'type': x.type}, lambda x: RecordingDefinition(name=x['name'], type=RecordingType(x['type']))))


class Recording:
    status_file_name = 'recording.gazeMapper'

    @typeguard.typechecked
    def __init__(self, definition: RecordingDefinition, info:EyeTrackerRecording|CameraRecording|None=None):
        self.definition = definition
        self.info       = info

        self.state: dict[process.Action, process_pool.State] = {}
        if not self.info.working_directory:
            # don't create action states file if this recording only exists in memory (still needs to be imported)
            self.state = _get_not_run_action_states(True)
        else:
            self.load_action_states(True, True)

    def load_action_states(self, create_if_missing: bool, upgrade_if_needed: bool):
        self.state |= get_action_states(self.info.working_directory, for_recording=True, create_if_missing=create_if_missing, upgrade_if_needed=upgrade_if_needed)
json.register_type(json.TypeEntry(Recording,'__session.Recording__',lambda x: {'defition': x.defition, 'info': x.info}, lambda x: Recording(**x)))

def read_recording_info(working_dir: pathlib.Path, rec_type: RecordingType) -> tuple[EyeTrackerRecording|CameraRecording, pathlib.Path]:
    if rec_type==RecordingType.Camera:
        rec_info = CameraRecording.load_from_json(working_dir)
    elif rec_type==RecordingType.Eye_Tracker:
        rec_info = EyeTrackerRecording.load_from_json(working_dir)
    return rec_info, get_video_path(rec_info)

def get_video_path(rec_info: EyeTrackerRecording|CameraRecording) -> pathlib.Path:
    if isinstance(rec_info, CameraRecording):
        return rec_info.get_video_path()
    elif isinstance(rec_info, EyeTrackerRecording):
        return rec_info.get_scene_video_path()

class SessionDefinition:
    default_json_file_name = 'session_def.json'

    @typeguard.typechecked
    def __init__(self, recordings: list[RecordingDefinition]|None=None):
        if recordings is None:
            recordings = []
        self.recordings = recordings

    def add_recording_def(self, recording: RecordingDefinition):
        self.recordings.append(recording)

    def get_recording_def(self, which: str) -> RecordingDefinition:
        for r in self.recordings:
            if r.name==which:
                return r
        raise ValueError(f'recording "{which}" not found')

    def is_known_recording(self, which: str) -> bool:
        for r in self.recordings:
            if r.name==which:
                return True
        return False

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_')}
        json.dump(to_dump, path)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Session':
        path = pathlib.Path(path)
        if path.is_dir():
            path /= SessionDefinition.default_json_file_name
        kwds = json.load(path)
        return SessionDefinition(**kwds)
json.register_type(json.TypeEntry(SessionDefinition,'__session.SessionDefinition__',lambda x: {'recordings': x.recordings}, lambda x: SessionDefinition(**x)))


class Session:
    status_file_name = 'session.gazeMapper'

    @typeguard.typechecked
    def __init__(self, definition: SessionDefinition, name: str, working_directory: str|pathlib.Path|None = None, recordings: dict[str,Recording]|None = None):
        self.definition = definition
        self.name = name
        self.working_directory: pathlib.Path = pathlib.Path(working_directory) if working_directory else None
        if not recordings:
            recordings = {}
        self.recordings = recordings

        self.state: dict[process.Action, process_pool.State] = {}
        self.load_action_states(True, True)

    def create_working_directory(self, parent_directory: str|pathlib.Path):
        self.working_directory = pathlib.Path(parent_directory) / self.name
        if not self.working_directory.is_dir():
            self.working_directory.mkdir()
        if not (self.working_directory/Session.status_file_name).is_file():
            _create_action_states_file(self.working_directory, False)

    def import_recording(self, which: str, cam_cal_file: str|pathlib.Path=None, generic_device_name: str=None, **kwargs):
        from . import config
        rec_def = self.definition.get_recording_def(which)
        config_dir = config.guess_config_dir(self.working_directory)
        study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: self.working_directory}, **kwargs)

        # do import
        rec_info = self.recordings[which].info
        rec_info.working_directory = self.working_directory / rec_def.name
        if cam_cal_file is None:
            # get default calibration file, if there is one
            cam_cal_file = self.recordings[which].definition.get_default_cal_file(config_dir)
        if rec_def.type==RecordingType.Eye_Tracker:
            rec_info = importing.do_import(rec_info=rec_info, copy_scene_video=study_config.import_do_copy_video, source_dir_as_relative_path=study_config.import_source_dir_as_relative_path, cam_cal_file=cam_cal_file, device_name=generic_device_name)
        else:
            rec_info = camera_recording.do_import(rec_info=rec_info, copy_video=study_config.import_do_copy_video, source_dir_as_relative_path=study_config.import_source_dir_as_relative_path, cam_cal_file=cam_cal_file)
        self.update_recording_info(which, rec_info)    # the import call may have updated the info (e.g. filled in recording length that wasn't known from metadata). Update what we hold in memory
        # denote import finished
        _create_action_states_file(rec_info.working_directory, True)
        update_action_states(rec_info.working_directory, process.Action.IMPORT, process_pool.State.Completed, study_config)
        self.recordings[which].load_action_states(False, False)

    def add_recording_and_import(self, which: str, rec_info: EyeTrackerRecording|CameraRecording, cam_cal_file: str|pathlib.Path=None, **kwargs) -> Recording:
        rec = self.add_recording_from_info(which, rec_info)
        self.import_recording(which, cam_cal_file, **kwargs)
        return rec

    def load_existing_recordings(self):
        # load recordings that are present
        for r in self.definition.recordings:
            if (self.working_directory / r.name).is_dir():
                self.add_existing_recording(r.name)

    def load_recording_info(self, which) -> EyeTrackerRecording|CameraRecording:
        r_fold = self.working_directory / which
        if not r_fold.is_dir():
            return

        rec_def = self.definition.get_recording_def(which)
        if rec_def.type==RecordingType.Eye_Tracker:
            return EyeTrackerRecording.load_from_json(r_fold)
        else:
            return CameraRecording.load_from_json(r_fold)

    def add_existing_recording(self, which: str) -> Recording:
        if which in self.recordings:
            return  # nothing to do
        r_fold = self.working_directory / which
        if not r_fold.is_dir():
            return

        # get info about recording
        rec_info = self.load_recording_info(which)

        # add recording
        self.add_recording_from_info(which, rec_info)
        return self.recordings[which]

    def check_recording_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording):
        rec_def = self.definition.get_recording_def(which)
        if rec_def.type==RecordingType.Eye_Tracker:
            if not isinstance(rec_info,EyeTrackerRecording):
                raise TypeError(f"The provided rec_info is not for an eye tracker recording, but {which} is an eye tracker recording")
        elif rec_def.type==RecordingType.Camera:
            if not isinstance(rec_info,CameraRecording):
                raise TypeError(f"The provided rec_info is not for a camera recording, but {which} is a camera recording")

    def update_recording_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording):
        if which not in self.recordings:
            return
        self.check_recording_info(which, rec_info)
        self.recordings[which].info = rec_info

    def add_recording_from_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording) -> Recording:
        rec_def = self.definition.get_recording_def(which)
        self.check_recording_info(which, rec_info)
        self.recordings[which] = Recording(rec_def, rec_info)
        return self.recordings[which]

    def num_present_recordings(self) -> int:
        return sum((r.name in self.recordings for r in self.definition.recordings))

    def has_all_recordings(self) -> bool:
        return all((r.name in self.recordings for r in self.definition.recordings))

    def missing_recordings(self, rec_type: RecordingType|None=None) -> list[str]:
        return [r.name for r in self.definition.recordings if r.name not in self.recordings and (rec_type is None or r.type==rec_type)]

    # state of processing actions on recordings in a session
    def load_action_states(self, create_if_missing: bool, upgrade_if_needed: bool):
        if self.working_directory.is_dir():
            self.state |= get_action_states(self.working_directory, for_recording=False, create_if_missing=create_if_missing, upgrade_if_needed=upgrade_if_needed)

    def is_action_completed(self, action: process.Action) -> bool:
        if process.is_session_level_action(action):
            return self.state[action]==process_pool.State.Completed
        else:
            return all((self.recordings[r].state[action]==process_pool.State.Completed for r in self.recordings))

    def action_completed_num_recordings(self, action: process.Action) -> int:
        if process.is_session_level_action(action):
            raise ValueError('The status of session-level actions cannot be listed per recording')

        return sum([self.recordings[r].state[action]==process_pool.State.Completed for r in self.recordings])

    @staticmethod
    def from_definition(definition: SessionDefinition|None, path: str | pathlib.Path) -> 'Session':
        # for loading a recording directory that doesn't contain a session json file
        # use the provided definition instead. If no session definition, try to load it
        if definition is None:
            from . import config
            config_dir = config.guess_config_dir(path)
            definition = config.Study.load_from_json(config_dir).session_def
        sess = Session(definition, name=path.name, working_directory=path)
        sess.load_existing_recordings()
        return sess


def get_session_from_directory(path: str|pathlib.Path, session_def: SessionDefinition|None=None) -> Session:
    # try to get config, we'll need that to load recording sessions
    if session_def is None:
        from . import config
        config_dir = config.guess_config_dir(path)
        session_def = config.Study.load_from_json(config_dir).session_def

    if not path.is_dir():
        raise RuntimeError('The provided path should be a directory')

    f = path / Session.status_file_name
    if not f.is_file():
        raise RuntimeError('The provided path is not a session, cannot load')

    return Session.from_definition(session_def, path)

def get_sessions_from_project_directory(path: str|pathlib.Path, session_def: SessionDefinition|None=None) -> list[Session]:
    path = pathlib.Path(path)

    # try to get config, we'll need that to load recording sessions
    if session_def is None:
        from . import config
        config_dir = config.guess_config_dir(path)
        session_def = config.Study.load_from_json(config_dir).session_def

    # iterate through all folders in the provided path and check if the folder contains
    # a session marker file. If so, try to to load the folder as a session, ignoring errors
    sessions: list[Session] = []
    for d in path.iterdir():
        try:
            sess = get_session_from_directory(d, session_def)
        except:
            pass
        else:
            sessions.append(sess)

    return sessions


def _get_action_status_fname(for_recording: bool) -> str:
    if for_recording:
        return Recording.status_file_name
    else:
        return Session.status_file_name

def _get_not_run_action_states(for_recording: bool) -> dict[process.Action, process_pool.State]:
    if for_recording:
        filt = lambda x: not process.is_session_level_action(x)
    else:
        filt = lambda x:     process.is_session_level_action(x)
    return {k:process_pool.State.Not_Run for k in process.Action if filt(k)}

def _create_action_states_file(file: pathlib.Path, for_recording: bool):
    if file.is_dir():
        file /= _get_action_status_fname(for_recording)
    action_states = _get_not_run_action_states(for_recording)
    _write_action_states_to_file(file, action_states)

def _write_action_states_to_file(file: pathlib.Path, action_states: dict[process.Action, process_pool.State]):
    action_states = {utils.enum_val_2_str(k):action_states[k] for k in action_states}    # turn key into string so it can be stored in a json file
    json.dump(action_states, file)

def _read_action_states(file: pathlib.Path) -> dict[process.Action, process_pool.State]:
    if not file.is_file():
        return None

    action_states = json.load(file)
    return {process.action_str_to_enum_val(k): process_pool.State(action_states[k]) for k in action_states}  # turn key from string back into enum instance, and same for state value

def _upgrade_action_states(file: pathlib.Path, action_states: dict[process.Action, process_pool.State], for_recording: bool) -> dict[process.Action, process_pool.State]:
    if file.is_dir():
        file /= _get_action_status_fname(for_recording)
    expected_action_states = _get_not_run_action_states(for_recording)
    # remove no longer available action_states
    upgraded_action_states = {a:action_states[a] for a in action_states if a in expected_action_states}
    # add missing action_states
    upgraded_action_states |= {a:expected_action_states[a] for a in expected_action_states if a not in upgraded_action_states}
    # store if anything has changed
    if set(upgraded_action_states.keys())!=set(action_states.keys()):
        _write_action_states_to_file(file, upgraded_action_states)
    return upgraded_action_states

def get_action_states(working_dir: str|pathlib.Path, for_recording: bool, create_if_missing = False, skip_if_missing=False, upgrade_if_needed=False) -> dict[process.Action, process_pool.State]:
    working_dir = pathlib.Path(working_dir)
    file = working_dir / _get_action_status_fname(for_recording)
    action_states = _read_action_states(file)

    if action_states is not None:
        if upgrade_if_needed:
            action_states = _upgrade_action_states(file, action_states, for_recording)
    else:
        if create_if_missing:
            _create_action_states_file(file, for_recording)
            return _read_action_states(file)
        elif not skip_if_missing:
            raise FileNotFoundError(f'Action states file {file} was not found')
    return action_states

def _apply_mutations_and_store(file, action_state_mutations, skip_if_missing=False):
    action_states = _read_action_states(file)
    if action_states is None and not skip_if_missing:
        raise FileNotFoundError(f'Action states file {file} was not found')

    # apply mutations
    for a in action_state_mutations:
        action_states[a] = action_state_mutations[a]

    _write_action_states_to_file(file, action_states)

def update_action_states(working_dir: str|pathlib.Path, action: process.Action, state: process_pool.State, study_config: 'config.Study', skip_if_missing=False, unchanged=False) -> dict[process.Action, process_pool.State]:
    for_recording = not process.is_session_level_action(action)

    if unchanged:
        # just update state of this task, don't cascade
        action_state_mutations = {action: state}
        if for_recording:
            file = working_dir / _get_action_status_fname(True)
            _apply_mutations_and_store(file, action_state_mutations, skip_if_missing=skip_if_missing)
        else:
            file = working_dir / _get_action_status_fname(False)
            _apply_mutations_and_store(file, action_state_mutations, skip_if_missing=skip_if_missing)
        return

    # determine state mutations
    action_state_mutations, for_all_recs = process.action_update_and_invalidate(action, state, study_config)
    # split in session-level and recording-level actions, report them separately
    session_state_mutations   = {a:action_state_mutations[a] for a in action_state_mutations if     process.is_session_level_action(a)}
    recording_state_mutations = {a:action_state_mutations[a] for a in action_state_mutations if not process.is_session_level_action(a)}

    # get which recordings to apply to
    session_dir = working_dir.parent if for_recording else working_dir
    if not for_recording or for_all_recs:
        sess = get_session_from_directory(session_dir)
        recs = list(sess.recordings.keys())
    else:
        recs = [working_dir.name]
    # and apply and store recording-level mutations
    for r in recs:
        f = session_dir / r / _get_action_status_fname(True)
        _apply_mutations_and_store(f, recording_state_mutations, skip_if_missing=skip_if_missing)
    # also apply and store session-level mutations
    if session_state_mutations:
        f = session_dir /     _get_action_status_fname(False)
        _apply_mutations_and_store(f,   session_state_mutations, skip_if_missing=skip_if_missing)

    return session_state_mutations, recording_state_mutations