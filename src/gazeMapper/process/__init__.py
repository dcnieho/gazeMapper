import enum

from glassesTools import annotation, utils

class State(enum.IntEnum):
    Not_Run     = enum.auto()
    Pending     = enum.auto()
    Running     = enum.auto()
    Completed   = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ")
utils.register_type(utils.CustomTypeEntry(State,'__enum.process.State__',str, lambda x: getattr(State, x.split('.')[1])))

class Action(enum.IntEnum):
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
    def succ(self):
        v = self.value+1
        if v > Action.MAKE_VIDEO.value:
            raise StopIteration('Enumeration ended')
        return Action(v)
    def pred(self):
        v = self.value-1
        if v<Action.IMPORT.value:
            raise StopIteration('Enumeration ended')
        return Action(v)
    def next_values(self, inclusive=False) -> set['Action']:
        vals: set[Action] = set()
        a = self
        if inclusive:
            vals.add(a)
        try:
            while True:
                a = a.succ()
                vals.add(a)
        except StopIteration:
            pass    # we're done
        return vals
utils.register_type(utils.CustomTypeEntry(Action,'__enum.process.Action__',str, lambda x: getattr(Action, x.split('.')[1])))

def is_session_level_action(action: Action) -> bool:
    return action in [Action.SYNC_TO_REFERENCE, Action.EXPORT_TRIALS, Action.MAKE_VIDEO]

def is_action_possible_given_config(action: Action, study_config: 'config.Study') -> bool:
    match action:
        case Action.AUTO_CODE_SYNC:
            return not not study_config.auto_code_sync_points
        case Action.AUTO_CODE_TRIALS:
            return study_config.auto_code_trial_episodes and study_config.sync_ref_recording
        case Action.SYNC_ET_TO_CAM:
            return study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']
        case Action.SYNC_TO_REFERENCE:
            return not not study_config.sync_ref_recording
        case Action.RUN_VALIDATION:
            return annotation.Event.Validate in study_config.planes_per_episode
        case Action.MAKE_VIDEO:
            return not not study_config.video_make_which

        case _:
            # no config preconditions for the other actions
            return True

def is_action_possible_for_recording_type(action: Action, rec_type: 'session.RecordingType') -> bool:
    from .. import session
    if rec_type==session.RecordingType.Camera and action in [Action.GAZE_TO_PLANE, Action.SYNC_ET_TO_CAM, Action.RUN_VALIDATION]:
        return False
    return True

def get_actions_for_config(study_config: 'config.Study', exclude_session_level: bool=False) -> set[Action]:
    actions = {a for a in Action if is_action_possible_given_config(a, study_config)}
    if exclude_session_level:
        actions = {a for a in actions if not is_session_level_action(a)}
    return actions

def _determine_to_invalidate(action: Action, study_config: 'config.Study') -> set[Action]:
    match action:
        case Action.IMPORT:
            return action.next_values()
        case Action.CODE_EPISODES:
            actions = {a for a in action.next_values() if a not in [Action.AUTO_CODE_SYNC, Action.AUTO_CODE_TRIALS]}
            if study_config.auto_code_sync_points or study_config.auto_code_trial_episodes:
                # if there is some form of automatic coding configured, then the whole video will be processed for each recording in a session, and thus coding doesn't invalidate the processed video
                actions.discard(Action.DETECT_MARKERS)
            return actions
        case Action.DETECT_MARKERS:
            actions = {a for a in action.next_values() if a not in [Action.SYNC_TO_REFERENCE]}
            if study_config.video_process_planes_for_all_frames or study_config.video_process_individual_markers_for_all_frames or study_config.video_show_detected_markers or study_config.video_show_rejected_markers:
                # in this case MAKE_VIDEO processes each frame itself, so output of DETECT_MARKERS is not used
                actions.discard(Action.MAKE_VIDEO)
            return actions
        case Action.GAZE_TO_PLANE:
            # NB: SYNC_ET_TO_CAM and SYNC_TO_REFERENCE operate on gazeData not gaze on plane data, and MAKE_VIDEO does the job itself/doesn't use this file
            return {a for a in action.next_values() if a not in [Action.AUTO_CODE_SYNC, Action.AUTO_CODE_TRIALS, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE, Action.MAKE_VIDEO]}
        case Action.AUTO_CODE_SYNC:
            return {Action.CODE_EPISODES, Action.GAZE_TO_PLANE, Action.EXPORT_TRIALS, Action.MAKE_VIDEO}
        case Action.AUTO_CODE_TRIALS:
            actions = {a for a in Action.CODE_EPISODES.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE, Action.RUN_VALIDATION]}
            if study_config.auto_code_sync_points or study_config.auto_code_trial_episodes:
                # if there is some form of automatic coding configured, then the whole video will be processed for each recording in a session, and thus coding doesn't invalidate the processed video
                actions.discard(Action.DETECT_MARKERS)
            return actions
        case Action.SYNC_ET_TO_CAM:
            return {a for a in Action.GAZE_TO_PLANE.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM]}
        case Action.SYNC_TO_REFERENCE:
            return {a for a in Action.GAZE_TO_PLANE.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE]}
        case Action.RUN_VALIDATION:
            return set()
        case Action.EXPORT_TRIALS:
            return set()
        case Action.MAKE_VIDEO:
            return set()
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action}, major developer oversight! Let him know.')

def action_update_and_invalidate(action: Action, state: State, study_config: 'config.Study') -> tuple[dict[Action, State], dict[Action, State]]:
    # set status of indicated task
    action_state_mutations = {action: state}

    # determine what other (later) actions are invalidated (and should thus be reset to not started state) by this action
    # being performed. There may be a better way of doing this, but i prefer to actively, per case, think this through
    # and explicitly write it out
    for a in _determine_to_invalidate(action, study_config):
        action_state_mutations[a] = State.Not_Run

    return action_state_mutations

def _is_recording_action_possible(action_states: dict[Action, State], study_config: 'config.Study', rec_type: 'session.RecordingType', action: Action):
    from .. import session
    if not is_action_possible_given_config(action, study_config):
        return False
    elif not is_action_possible_for_recording_type(action, rec_type):
        return False

    preconditions: set[Action] = set(Action.IMPORT) # IMPORT is a precondition for all actions except IMPORT itself
    match action:
        case Action.IMPORT:
            return action_states[Action.IMPORT]==State.Not_Run  # possible if not already imported
        case Action.CODE_EPISODES:
            pass    # nothing besides import
        case Action.DETECT_MARKERS:
            if not (study_config.auto_code_sync_points or study_config.auto_code_trial_episodes):
                preconditions.add(Action.CODE_EPISODES)
        case Action.GAZE_TO_PLANE:
            preconditions.update([Action.CODE_EPISODES, Action.DETECT_MARKERS])
        case Action.AUTO_CODE_SYNC:
            preconditions.update([Action.DETECT_MARKERS])
        case Action.AUTO_CODE_TRIALS:
            preconditions.update([Action.DETECT_MARKERS])
        case Action.SYNC_ET_TO_CAM:
            preconditions.update([Action.CODE_EPISODES, Action.DETECT_MARKERS])
        case Action.RUN_VALIDATION:
            preconditions.update([Action.CODE_EPISODES, Action.GAZE_TO_PLANE])
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action}, major developer oversight! Let him know.')

    # check that preconditions are met
    return all((action_states[p]==State.Completed for p in preconditions))

def _is_session_action_possible(session_action_states: dict[Action, State], recording_action_states: dict[str,dict[Action, State]], study_config: 'config.Study', action: Action):
    if not is_action_possible_given_config(action, study_config):
        return False

    preconditions: set[Action] = set(Action.IMPORT) # IMPORT is a precondition for all actions
    match action:
        case Action.SYNC_TO_REFERENCE:
            preconditions.update([Action.CODE_EPISODES])
            if study_config.auto_code_sync_points:
                preconditions.add(Action.AUTO_CODE_SYNC)
            if study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']:
                preconditions.add(Action.SYNC_ET_TO_CAM)
        case Action.EXPORT_TRIALS:
            preconditions.update([Action.CODE_EPISODES, Action.GAZE_TO_PLANE])
            if study_config.auto_code_trial_episodes:
                preconditions.add(Action.AUTO_CODE_TRIALS)
            if study_config.sync_ref_recording:
                preconditions.add(Action.SYNC_TO_REFERENCE)
            elif study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']:
                preconditions.add(Action.SYNC_ET_TO_CAM)
        case Action.MAKE_VIDEO:
            preconditions.update([Action.CODE_EPISODES])
            if study_config.auto_code_sync_points:
                preconditions.add(Action.AUTO_CODE_SYNC)
            if study_config.auto_code_trial_episodes:
                preconditions.add(Action.AUTO_CODE_TRIALS)
            if study_config.sync_ref_recording:
                preconditions.add(Action.SYNC_TO_REFERENCE)
            elif study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']:
                preconditions.add(Action.SYNC_ET_TO_CAM)
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action}, major developer oversight! Let him know.')

    precond_met = []
    for p in preconditions:
        if is_session_level_action(p):
            precond_met.append(session_action_states[p]==State.Completed)
        else:
            precond_met.append(all((recording_action_states[r][p]==State.Completed for r in recording_action_states)))

    return all(precond_met.values())

def get_possible_actions(session_action_states: dict[Action, State], recording_action_states: dict[str,dict[Action, State]], actions_to_check: set[Action], study_config: 'config.Study', rec_type: 'session.RecordingType') -> dict[Action,bool|list[str]]:
    # determine based on actions_states which actions have all their preconditions met. Return a set containing just
    # those possible actions
    # actions_to_check can be a subset of all actions, if user e.g. knows some actions aren't possible or wanted due to settings
    # this function doesn't check that
    merged_states = {r:session_action_states|recording_action_states[r] for r in recording_action_states}

    possible_actions: dict[Action,bool|list[str]] = {}
    for a in actions_to_check:
        if is_session_level_action(a):
            if _is_session_action_possible(session_action_states, recording_action_states, study_config, a):
                possible_actions[a] = True
        else:
            possible_recs = [r for r in merged_states if _is_recording_action_possible(merged_states[r], study_config, rec_type, a)]
            if possible_recs:
                possible_actions[a] = possible_recs

    return possible_actions