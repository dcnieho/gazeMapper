from enum import Enum, auto
import pathlib
import json
import typeguard

from glassesTools import importing, utils
from glassesTools.recording import Recording as EyeTrackerRecording

from . import camera_recording


class RecordingType(utils.AutoName):
    Eye_Tracker = auto()
    Camera      = auto()
utils.register_type(utils.CustomTypeEntry(RecordingType,'__enum.session.RecordingType__',str, lambda x: getattr(RecordingType, x.split('.')[1])))
recording_types = [r for r in RecordingType]


class RecordingDefinition:
    @typeguard.typechecked
    def __init__(self, name:str, type:RecordingType):
        self.name = name
        self.type = type
utils.register_type(utils.CustomTypeEntry(RecordingDefinition,'__session.RecordingDefinition__',lambda x: {'name': x.name, 'type': x.type}, lambda x: RecordingDefinition(**x)))


class Recording:
    @typeguard.typechecked
    def __init__(self, defition: RecordingDefinition, info:EyeTrackerRecording|camera_recording.Recording|None=None):
        self.defition   = defition
        self.info       = info
utils.register_type(utils.CustomTypeEntry(Recording,'__session.Recording__',lambda x: {'defition': x.defition, 'info': x.info}, lambda x: Recording(**x)))

def read_recording_info(working_dir: pathlib.Path, rec_type: RecordingType) -> tuple[EyeTrackerRecording|camera_recording.Recording, pathlib.Path]:
    if rec_type==RecordingType.Camera:
        rec_info = camera_recording.Recording.load_from_json(working_dir)
    elif rec_type==RecordingType.Eye_Tracker:
        rec_info = EyeTrackerRecording.load_from_json(working_dir)
    return rec_info, get_video_path(rec_info)

def get_video_path(rec_info: EyeTrackerRecording|camera_recording.Recording) -> pathlib.Path:
    if isinstance(rec_info, camera_recording.Recording):
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

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        with open(path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in vars(self) if not k.startswith('_')}
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Session':
        path = pathlib.Path(path)
        if path.is_dir():
            path /= SessionDefinition.default_json_file_name
        with open(path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        return SessionDefinition(**kwds)
utils.register_type(utils.CustomTypeEntry(SessionDefinition,'__session.SessionDefinition__',lambda x: {'recordings': x.recordings}, lambda x: SessionDefinition(**x)))


class Session:
    default_json_file_name = 'session_info.json'

    @typeguard.typechecked
    def __init__(self, definition: SessionDefinition, name: str, working_directory: str|pathlib.Path|None = None, recordings: dict[str,Recording]|None = None):
        self.definition = definition
        self.name = name
        self.working_directory: pathlib.Path = pathlib.Path(working_directory) if working_directory else None
        if not recordings:
            recordings = {}
        self.recordings = recordings

    def create_working_directory(self, parent_directory: str|pathlib.Path):
        self.working_directory = pathlib.Path(parent_directory) / self.name
        if not self.working_directory.is_dir():
            self.working_directory.mkdir()

    def import_and_add_recording(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording, copy_video = True, source_dir_as_relative_path = False, cam_cal_file: str|pathlib.Path=None) -> Recording:
        rec_def = self.definition.get_recording_def(which)
        self.check_recording_info(which, rec_info)

        # do import
        rec_info.working_directory = self.working_directory / rec_def.name
        if rec_def.type==RecordingType.Eye_Tracker:
            rec_info = importing.do_import(rec_info=rec_info, copy_scene_video=copy_video, source_dir_as_relative_path=source_dir_as_relative_path, cam_cal_file=cam_cal_file)
        else:
            rec_info = camera_recording.do_import(rec_info=rec_info, copy_video=copy_video, source_dir_as_relative_path=source_dir_as_relative_path, cam_cal_file=cam_cal_file)

        # add recording
        self.add_recording_from_info(which, rec_info)
        return self.recordings[which]

    def load_existing_recordings(self):
        # load recordings that are present
        for r in self.definition.recordings:
            if (self.working_directory / r.name).is_dir():
                self.add_existing_recording(r.name)

    def add_existing_recording(self, which: str) -> Recording:
        r_fold = self.working_directory / which
        if not r_fold.is_dir():
            return

        # get info about recording
        rec_def = self.definition.get_recording_def(which)
        if rec_def.type==RecordingType.Eye_Tracker:
            rec_info = EyeTrackerRecording.load_from_json(r_fold)
        else:
            rec_info = camera_recording.Recording.load_from_json(r_fold)

        # add recording
        self.add_recording_from_info(which, rec_info)
        return self.recordings[which]

    def check_recording_info(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording):
        rec_def = self.definition.get_recording_def(which)
        if rec_def.type==RecordingType.Eye_Tracker:
            if not isinstance(rec_info,EyeTrackerRecording):
                raise TypeError(f"The provided rec_info is not for an eye tracker recording, but {which} is an eye tracker recording")
        elif rec_def.type==RecordingType.Camera:
            if not isinstance(rec_info,camera_recording.Recording):
                raise TypeError(f"The provided rec_info is not for a camera recording, but {which} is a camera recording")

    def add_recording_from_info(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording):
        rec_def = self.definition.get_recording_def(which)
        self.check_recording_info(which, rec_info)
        self.recordings[which] = Recording(rec_def, rec_info)

    def has_all_recordings(self) -> bool:
        return all([r.name in self.recordings for r in self.definition.recordings])

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


def get_sessions_from_directory(path: str|pathlib.Path, session_json_name=Session.default_json_file_name) -> list[Session]:
    path = pathlib.Path(path)

    # iterate through all folders in the provided path and check if the folder contains a file with the session_json_name
    # then its potentially a session
    sessions: list[Session] = []
    for d in path.iterdir():
        if not d.is_dir():
            continue

        f = d / session_json_name
        if not f.is_file():
            continue

        sessions.append(Session.load_from_json(f))

    return sessions