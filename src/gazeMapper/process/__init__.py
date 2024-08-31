import enum

class State(enum.IntEnum):
    Not_Started = enum.auto()
    Pending     = enum.auto()
    Running     = enum.auto()
    Completed   = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ")

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

def is_action_session_level(action: Action) -> bool:
    return action in [Action.EXPORT_TRIALS, Action.MAKE_VIDEO]