from enum import Enum, auto
import pathlib
import json

from glassesTools import importing, utils
from glassesTools.recording import Recording as EyeTrackerRecording

from . import camera_recording


class RecordingType(Enum):
    EyeTracker  = auto()
    Camera      = auto()
utils.register_type(utils.CustomTypeEntry(RecordingType,'__enum.session.RecordingType__',str, lambda x: getattr(RecordingType, x.split('.')[1])))



class RecordingDefinition:
    def __init__(self, name:str, type:RecordingType):
        self.name = name
        self.type = type
utils.register_type(utils.CustomTypeEntry(RecordingDefinition,'__session.RecordingDefinition__',lambda x: {'name': x.name, 'type': x.type}, lambda x: RecordingDefinition(**x)))


class Recording:
    def __init__(self, defition: RecordingDefinition, info:EyeTrackerRecording|camera_recording.Recording=None):
        self.defition   = defition
        self.info       = info
utils.register_type(utils.CustomTypeEntry(Recording,'__session.Recording__',lambda x: {'defition': x.defition, 'info': x.info}, lambda x: Recording(**x)))

def read_recording_info(working_dir: pathlib.Path, rec_type: RecordingType) -> tuple[EyeTrackerRecording|camera_recording.Recording, pathlib.Path]:
    if rec_type==RecordingType.Camera:
        rec_info = camera_recording.Recording.load_from_json(working_dir)
        in_video = rec_info.get_video_path()
    elif rec_type==RecordingType.EyeTracker:
        rec_info = EyeTrackerRecording.load_from_json(working_dir)
        in_video = rec_info.get_scene_video_path()
    return rec_info, in_video

class SessionDefinition:
    def __init__(self, recordings: list[RecordingDefinition]=None, sync_ref: str=None):
        if recordings is None:
            recordings = []
        self.recordings = recordings

        self.sync_ref: str = None
        self.set_sync_ref(sync_ref)

    def add_recording(self, recording: RecordingDefinition):
        self.recordings.append(recording)

    def set_sync_ref(self, which: str):
        if not any([r.name==which for r in self.recordings]):
            raise ValueError(f'recording "{which}" not known')
        self.sync_ref = which

    def get_recording(self, which: str) -> RecordingDefinition:
        for r in self.recordings:
            if r.name==which:
                return r
        raise ValueError(f'recording "{which}" not found')

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        with open(path, 'w') as f:
            json.dump(self, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Session':
        path = pathlib.Path(path)
        with open(path, 'r') as f:
            return json.load(f, object_hook=utils.json_reconstitute)
utils.register_type(utils.CustomTypeEntry(SessionDefinition,'__session.SessionDefinition__',lambda x: {'recordings': x.recordings, 'sync_ref': x.sync_ref}, lambda x: SessionDefinition(**x)))


class Session:
    default_json_file_name = 'session_info.json'

    def __init__(self, definition: SessionDefinition, name: str, working_directory: str|pathlib.Path = None, recordings: dict[str,Recording] = None):
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

    def import_and_add_recording(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording, copy_video = True) -> Recording:
        rec_def = self.definition.get_recording(which)
        self.check_recording_info(which, rec_info)

        # do import
        rec_info.working_directory = self.working_directory / rec_def.name
        if rec_def.type==RecordingType.EyeTracker:
            rec_info = importing.do_import(rec_info=rec_info, copy_scene_video=copy_video)
        else:
            rec_info = camera_recording.do_import(rec_info=rec_info, copy_video=copy_video)

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
        rec_def = self.definition.get_recording(which)
        if rec_def.type==RecordingType.EyeTracker:
            rec_info = EyeTrackerRecording.load_from_json(r_fold)
        else:
            rec_info = camera_recording.Recording.load_from_json(r_fold)

        # add recording
        self.add_recording_from_info(which, rec_info)
        return self.recordings[which]

    def check_recording_info(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording):
        rec_def = self.definition.get_recording(which)
        if rec_def.type==RecordingType.EyeTracker:
            assert isinstance(rec_info,EyeTrackerRecording), f"The provided rec_info is not for an eye tracker recording, but {which} is an eye tracker recording"
        elif rec_def.type==RecordingType.Camera:
            assert isinstance(rec_info,camera_recording.Recording), f"The provided rec_info is not for a camera recording, but {which} is a camera recording"

    def add_recording_from_info(self, which: str, rec_info: EyeTrackerRecording|camera_recording.Recording):
        rec_def = self.definition.get_recording(which)
        self.check_recording_info(which, rec_info)
        self.recordings[which] = Recording(rec_def, rec_info)

    def has_all_recordings(self) -> bool:
        return all([r.name in self.recordings for r in self.definition.recordings])

    def store_as_json(self, path: str | pathlib.Path = None):
        if path is None:
            path = self.working_directory
        path = pathlib.Path(path)
        if path.is_dir():
            path /= self.default_json_file_name
        with open(path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in ['definition']}    # only this field. Name will be populated from name of session/provided folder, recordings from each subfolder in the session/provided folder, and working_directory as the provided path
            # dump to file
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Session':
        path = pathlib.Path(path)
        if path.is_dir():
            path /= Session.default_json_file_name
        # load session setup
        with open(path, 'r') as f:
            sess = Session(**json.load(f, object_hook=utils.json_reconstitute), name=path.parent.name, working_directory=path.parent)
        # load recordings that are present
        sess.load_existing_recordings()

        return sess

    @staticmethod
    def from_definition(definition: SessionDefinition, path: str | pathlib.Path) -> 'Session':
        # for loading a recording directory that doesn't contain a session json file
        # use the provided definition instead
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