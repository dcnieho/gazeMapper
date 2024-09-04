import enum
import json
import pathlib

from glassesTools import utils

class State(enum.IntEnum):
    Not_Started = enum.auto()
    Pending     = enum.auto()
    Running     = enum.auto()
    Completed   = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ")
utils.register_type(utils.CustomTypeEntry(State,'__enum.process.State__',str, lambda x: getattr(State, x.split('.')[1])))

class Action(enum.Flag):
    IMPORT = enum.auto()
    CODE_EPISODES = enum.auto()
    DETECT_MARKERS = enum.auto()
    GAZE_TO_PLANE = enum.auto()
    AUTO_CODE_SYNC = enum.auto()
    AUTO_CODE_TRIALS = enum.auto()
    SYNC_ET_TO_CAM = enum.auto()
    SYNC_TO_REFERENCE = enum.auto()
    RUN_VALIDATION = enum.auto()
    EXPORT_TRIALS = enum.auto()
    MAKE_VIDEO = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ").title()
utils.register_type(utils.CustomTypeEntry(Action,'__enum.process.Action__',str, lambda x: getattr(Action, x.split('.')[1])))

def is_action_session_level(action: Action) -> bool:
    return action in [Action.EXPORT_TRIALS, Action.MAKE_VIDEO]

def action_update_and_invalidate(action_states: dict[Action, State], action: Action, state: State, for_recording: bool) -> dict[Action, State]:
    # set status of indicated task
    action_states[action] = state
    # set all later tasks to not started as they would have to be rerun when an earlier tasks is rerun
    # TODO

    return action_states