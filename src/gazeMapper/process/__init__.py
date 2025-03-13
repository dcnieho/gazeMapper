import enum
import typing

from glassesTools import annotation, utils

class State(enum.IntEnum):
    Not_Run     = enum.auto()
    Pending     = enum.auto()
    Running     = enum.auto()
    Completed   = enum.auto()
    # two more states needed by process_pool
    Canceled    = enum.auto()
    Failed      = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ")
utils.register_type(utils.CustomTypeEntry(State,'__enum.process.State__', utils.enum_val_2_str, lambda x: getattr(State, x.split('.')[1])))

class Action(enum.IntEnum):
    IMPORT = enum.auto()
    MAKE_GAZE_OVERLAY_VIDEO = enum.auto()
    CODE_EPISODES = enum.auto()
    DETECT_MARKERS = enum.auto()
    GAZE_TO_PLANE = enum.auto()
    AUTO_CODE_SYNC = enum.auto()
    AUTO_CODE_TRIALS = enum.auto()
    SYNC_ET_TO_CAM = enum.auto()
    SYNC_TO_REFERENCE = enum.auto()
    VALIDATE = enum.auto()
    EXPORT_TRIALS = enum.auto()
    MAKE_MAPPED_GAZE_VIDEO = enum.auto()
    @property
    def displayable_name(self):
        return self.name.replace("_", " ").title()
    @property
    def needs_GUI(self):
        return self in [Action.CODE_EPISODES, Action.SYNC_ET_TO_CAM]
    @property
    def has_options(self):
        return self in [Action.MAKE_GAZE_OVERLAY_VIDEO, Action.DETECT_MARKERS, Action.GAZE_TO_PLANE, Action.MAKE_MAPPED_GAZE_VIDEO]
    def succ(self):
        v = self.value+1
        if v > Action.MAKE_MAPPED_GAZE_VIDEO.value:
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
def action_str_to_enum_val(x: str) -> Action:
    return utils.enum_str_2_val(x, Action, {'MAKE_VIDEO':'MAKE_MAPPED_GAZE_VIDEO', 'RUN_VALIDATION':'VALIDATE'})
utils.register_type(utils.CustomTypeEntry(Action, '__enum.process.Action__', utils.enum_val_2_str, action_str_to_enum_val))

def action_to_func(action: Action) -> typing.Callable[..., None]:
    # Returns function to perform the provided action. NB: not for Action.IMPORT,
    # needs its own special handling by caller instead
    from .make_gaze_overlay_video import run as make_gaze_overlay_video
    from .code_episodes import run as do_coding
    from .detect_markers import run as detect_markers
    from .sync_et_to_cam import run as sync_et_to_cam
    from .sync_to_ref import run as sync_to_ref
    from .gaze_to_plane import run as gaze_to_plane
    from .export_trials import run as export_trials
    from .run_validation import run as run_validation
    from .auto_code_sync_points import run as auto_code_sync_points
    from .auto_code_trials import run as auto_code_trials
    from .make_mapped_gaze_video import run as make_mapped_gaze_video

    match action:
        case Action.IMPORT:
            return None # Needs a special case handled by the caller
        case Action.MAKE_GAZE_OVERLAY_VIDEO:
            return make_gaze_overlay_video
        case Action.CODE_EPISODES:
            return do_coding
        case Action.DETECT_MARKERS:
            return detect_markers
        case Action.GAZE_TO_PLANE:
            return gaze_to_plane
        case Action.AUTO_CODE_SYNC:
            return auto_code_sync_points
        case Action.AUTO_CODE_TRIALS:
            return auto_code_trials
        case Action.SYNC_ET_TO_CAM:
            return sync_et_to_cam
        case Action.SYNC_TO_REFERENCE:
            return sync_to_ref
        case Action.VALIDATE:
            return run_validation
        case Action.EXPORT_TRIALS:
            return export_trials
        case Action.MAKE_MAPPED_GAZE_VIDEO:
            return make_mapped_gaze_video
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action.displayable_name} ({action}), major developer oversight! Let him know.')

def is_session_level_action(action: Action) -> bool:
    return action in [Action.SYNC_TO_REFERENCE, Action.EXPORT_TRIALS, Action.MAKE_MAPPED_GAZE_VIDEO]

def is_action_possible_given_config(action: Action, study_config: 'config.Study') -> bool:
    match action:
        case Action.AUTO_CODE_SYNC:
            return not not study_config.auto_code_sync_points
        case Action.AUTO_CODE_TRIALS:
            return study_config.auto_code_trial_episodes
        case Action.SYNC_ET_TO_CAM:
            return study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']
        case Action.SYNC_TO_REFERENCE:
            return not not study_config.sync_ref_recording
        case Action.VALIDATE:
            return annotation.Event.Validate in study_config.planes_per_episode
        case Action.EXPORT_TRIALS:
            return True     # always possible (in terms of config), since gaze overlay videos are always possible
        case Action.MAKE_MAPPED_GAZE_VIDEO:
            return not not study_config.video_make_which

        case _:
            # no config preconditions for the other actions
            return True

def is_action_possible_for_recording(rec: str, rec_type: 'session.RecordingType', action: Action, study_config: 'config.Study') -> bool:
    from .. import session
    if rec_type==session.RecordingType.Camera and action in [Action.MAKE_GAZE_OVERLAY_VIDEO, Action.GAZE_TO_PLANE, Action.SYNC_ET_TO_CAM, Action.VALIDATE]:
        return False
    elif action==Action.AUTO_CODE_TRIALS and study_config.sync_ref_recording and rec!=study_config.sync_ref_recording:
        # if we have a sync_ref_recording, automatic coding of trials is only possible for the sync_ref_recording, not the other recordings
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
        case Action.MAKE_GAZE_OVERLAY_VIDEO:
            return {Action.EXPORT_TRIALS}
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
                actions.discard(Action.MAKE_MAPPED_GAZE_VIDEO)
            return actions
        case Action.GAZE_TO_PLANE:
            # NB: SYNC_ET_TO_CAM and SYNC_TO_REFERENCE operate on gazeData not gaze on plane data, and MAKE_VIDEO does the job itself/doesn't use this file
            return {a for a in action.next_values() if a not in [Action.AUTO_CODE_SYNC, Action.AUTO_CODE_TRIALS, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE, Action.MAKE_MAPPED_GAZE_VIDEO]}
        case Action.AUTO_CODE_SYNC:
            return {Action.CODE_EPISODES, Action.GAZE_TO_PLANE, Action.SYNC_TO_REFERENCE, Action.EXPORT_TRIALS, Action.MAKE_MAPPED_GAZE_VIDEO}
        case Action.AUTO_CODE_TRIALS:
            actions = {a for a in Action.CODE_EPISODES.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE, Action.VALIDATE]}
            if study_config.auto_code_sync_points or study_config.auto_code_trial_episodes:
                # if there is some form of automatic coding configured, then the whole video will be processed for each recording in a session, and thus coding doesn't invalidate the processed video
                actions.discard(Action.DETECT_MARKERS)
            return actions
        case Action.SYNC_ET_TO_CAM:
            return {a for a in Action.GAZE_TO_PLANE.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM]}
        case Action.SYNC_TO_REFERENCE:
            return {a for a in Action.GAZE_TO_PLANE.next_values(inclusive=True) if a not in [Action.AUTO_CODE_TRIALS, Action.AUTO_CODE_SYNC, Action.SYNC_ET_TO_CAM, Action.SYNC_TO_REFERENCE]}
        case Action.VALIDATE:
            return {Action.EXPORT_TRIALS}
        case Action.EXPORT_TRIALS:
            return set()
        case Action.MAKE_MAPPED_GAZE_VIDEO:
            return set()
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action.displayable_name} ({action}), major developer oversight! Let him know.')

def action_update_and_invalidate(action: Action, state: State, study_config: 'config.Study') -> dict[Action, State]:
    # set status of indicated task
    action_state_mutations = {action: state}

    # determine what other (later) actions are invalidated (and should thus be reset to not started state) by this action
    # being performed. There may be a better way of doing this, but i prefer to actively, per case, think this through
    # and explicitly write it out
    for a in _determine_to_invalidate(action, study_config):
        action_state_mutations[a] = State.Not_Run

    return action_state_mutations

def _is_recording_action_possible(rec: str, action_states: dict[Action, State], study_config: 'config.Study', rec_type: 'session.RecordingType', action: Action) -> tuple[bool,list[Action]]:
    if not is_action_possible_given_config(action, study_config):
        return False, None
    elif not is_action_possible_for_recording(rec, rec_type, action, study_config):
        return False, None

    preconditions = {Action.IMPORT} # IMPORT is a precondition for all actions except IMPORT itself
    match action:
        case Action.IMPORT:
            return action_states[Action.IMPORT]==State.Not_Run, []  # possible if not already imported
        case Action.MAKE_GAZE_OVERLAY_VIDEO:
            pass    # nothing besides import
        case Action.CODE_EPISODES:
            pass    # nothing besides import
        case Action.DETECT_MARKERS:
            if not (study_config.auto_code_sync_points or study_config.auto_code_trial_episodes):
                preconditions.add(Action.CODE_EPISODES)
        case Action.GAZE_TO_PLANE:
            preconditions.update([Action.CODE_EPISODES, Action.DETECT_MARKERS])
            if study_config.sync_ref_recording:
                preconditions.add(Action.SYNC_TO_REFERENCE)
        case Action.AUTO_CODE_SYNC:
            preconditions.update([Action.DETECT_MARKERS])
        case Action.AUTO_CODE_TRIALS:
            preconditions.update([Action.DETECT_MARKERS])
        case Action.SYNC_ET_TO_CAM:
            preconditions.update([Action.CODE_EPISODES, Action.DETECT_MARKERS])
        case Action.VALIDATE:
            preconditions.update([Action.CODE_EPISODES, Action.GAZE_TO_PLANE])
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action.displayable_name} ({action}), major developer oversight! Let him know.')

    # check that preconditions are met
    states = [action_states[p]==State.Completed for p in preconditions]
    missing = [p for p,s in zip(preconditions,states) if not s]
    return all(states), [p for p in Action if p in missing] # NB: ensure stable order

def _is_session_action_possible(session_action_states: dict[Action, State], recording_action_states: dict[str,dict[Action, State]], study_config: 'config.Study', rec_types: dict[str,'session.RecordingType'], action: Action) -> tuple[bool, list[Action]]:
    if not is_action_possible_given_config(action, study_config):
        return False, None

    preconditions = set()
    preconditions_test = 'and'
    match action:
        case Action.SYNC_TO_REFERENCE:
            preconditions.update([Action.CODE_EPISODES])
            # NB: even if study_config.auto_code_sync_points is defined, user may decide to code sync manually. So don't add Action.AUTO_CODE_SYNC to preconditions
            if study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']:
                preconditions.add(Action.SYNC_ET_TO_CAM)
        case Action.EXPORT_TRIALS:
            preconditions.update([Action.MAKE_GAZE_OVERLAY_VIDEO, Action.GAZE_TO_PLANE, Action.VALIDATE, Action.MAKE_MAPPED_GAZE_VIDEO])
            preconditions_test = 'or'
        case Action.MAKE_MAPPED_GAZE_VIDEO:
            preconditions.update([Action.CODE_EPISODES])
            # NB: even if study_config.auto_code_sync_points is defined, user may decide to code sync manually. So don't add Action.AUTO_CODE_SYNC to preconditions
            # NB: even if study_config.auto_code_trial_episodes is defined, user may decide to code trials manually. So don't add Action.AUTO_CODE_TRIALS to preconditions
            if study_config.sync_ref_recording:
                preconditions.add(Action.SYNC_TO_REFERENCE)
            elif study_config.get_cam_movement_for_et_sync_method in ['plane', 'function']:
                preconditions.add(Action.SYNC_ET_TO_CAM)
        case _:
            raise NotImplementedError(f'Logic is not implemented for {action.displayable_name} ({action}), major developer oversight! Let him know.')

    precond_met: dict[Action,tuple[bool,list[str]]] = {}
    for p in [p for p in Action if p in preconditions]: # NB: ensure stable order
        if is_session_level_action(p):
            met = session_action_states[p]==State.Completed
            precond_met[p] = (met,None)
        else:
            met1 = [is_action_possible_for_recording(r, rec_types[r], p, study_config) for r in recording_action_states]
            met2 = [recording_action_states[r][p]==State.Completed for r in recording_action_states]
            met = [(not m1) or m2 for m1,m2 in zip(met1,met2)]    # not m1 because ignore if action isn't possible for that recording anyway
            precond_met[p] = (all(met), [] if all(met) else [r for r,m1,m2 in zip(recording_action_states,met1,met2) if m1 and not m2])

    if preconditions_test=='and':
        ok = all((precond_met[p][0] for p in precond_met))
    else:
        ok = any((precond_met[p][0] for p in precond_met))
    return ok, {p: precond_met[p][1] for p in precond_met if not precond_met[p][0]} if not ok else {}

def get_possible_actions(session_action_states: dict[Action, State], recording_action_states: dict[str,dict[Action, State]], actions_to_check: set[Action], study_config: 'config.Study') -> dict[Action, tuple[bool|list[str], dict[Action,list[str]]]]:
    # determine based on actions_states which actions have all their preconditions met. Return a set containing just
    # those possible actions
    # actions_to_check can be a subset of all actions, if user e.g. knows some actions aren't possible or wanted due to settings
    # this function doesn't check that
    merged_states = {r:session_action_states|recording_action_states[r] for r in recording_action_states}
    rec_types = {r.name:r.type for r in study_config.session_def.recordings}

    possible_actions: dict[Action,tuple[bool|list[str],dict[Action,list[str]]]] = {}  # per action list if possible/recordings for which its possible, and also which preconditions are unmet
    for a in actions_to_check:
        if is_session_level_action(a):
            states = _is_session_action_possible(session_action_states, recording_action_states, study_config, rec_types, a)
            if states[0] or states[1]:
                possible_actions[a] = states
        else:
            states = {r:_is_recording_action_possible(r, merged_states[r], study_config, rec_types[r], a) for r in merged_states}
            possible_recs = [r for r in merged_states if states[r][0]]
            fails = _get_precond_fails_for_rec(states)
            if possible_recs or fails:
                possible_actions[a] = possible_recs, fails

    return possible_actions

def _get_precond_fails_for_rec(states: dict[str,tuple[bool,list[Action]]]) -> dict[Action,None|list[str]]:
    out: dict[Action,None|list[str]] = {}
    for r in states:
        if states[r][0] or states[r][1] is None:
            continue
        for a in states[r][1]:
            if is_session_level_action(a):
                out[a] = None
            elif a not in out:
                out[a] = [r]
            else:
                out[a].append(r)
    return out

def get_impossible_reason_text(action: Action, states: dict[str, dict[Action,tuple[bool|list[str],dict[Action,list[str]]]]], for_recording=False, for_single=False) -> str:
    reason = f'Cannot run {action.displayable_name} because required actions have not yet been run'
    if for_single:
        reason += f':\n{_get_impossible_reason_text_impl(states[action][1], "")}'
    else:
        reason += ' for the following '
        if for_recording:
            reason += 'recording(s):\n'
        else:
            reason += 'session(s):\n'
        for s in states:
            if action not in states[s]:
                continue
            ac = states[s][action][1]
            reason += f'- {s}:\n{_get_impossible_reason_text_impl(ac, s)}'
    return reason

def _get_impossible_reason_text_impl(fails: dict[Action,list[str]], ref: str) -> str:
    reason = ''
    for a in fails:
        reason += f'  - {a.displayable_name}'
        if fails[a] is not None and (len(fails[a])>1 or fails[a][0]!=ref):
            reason += f' ({", ".join(fails[a])})'
        reason += '\n'
    return reason