import pathlib
import pandas as pd
import typing
from collections import defaultdict

from glassesTools import annotation

from . import config, naming


EpisodeIntervals = tuple[annotation.EventType, list[list[int]]]
EpisodeMap = dict[str, EpisodeIntervals]
EpisodeSourceRef = tuple[str|None, int]
EpisodeSourceRefs = dict[str, list[EpisodeSourceRef]]
EpisodeImportedMap = dict[str, dict[str, list[list[int]]]]


class Episode:
    def __init__(self, event:str, event_type:annotation.EventType, start_frame:int, end_frame:int|None=None):
        self.event          = event
        self.event_type     = event_type
        self.start_frame    = start_frame

        if annotation.type_map[self.event_type]==annotation.Type.Interval:
            if end_frame is None:
                raise ValueError(f"end frame expected for an interval-type episode ({annotation.tooltip_map[self.event_type]}), but not provided")
        if annotation.type_map[self.event_type]==annotation.Type.Point:
            if end_frame is not None:
                raise ValueError(f"end frame provided but not expected for a point-type episode ({annotation.tooltip_map[self.event_type]})")
        self.end_frame      = end_frame


# for dealing with lists of events
def read_list_from_file(fileName: str|pathlib.Path) -> list[Episode]:
    df = pd.read_csv(str(fileName), delimiter='\t', index_col=False, dtype=defaultdict(lambda: int, event=str, event_type=str, end_frame=pd.Int64Dtype()))
    if 'event_type' in df.columns:
        # v2 file
        df['event_type'] = [annotation.EventType(x) for x in df['event_type'].values]
    else:
        # backward compatibility for v1 files
        df['event_type'] = [annotation.EventType(x) for x in df['event'].values]
        for e in df['event_type'].unique():
            evts = annotation.get_events_by_type(e)
            if len(evts)==0:
                raise ValueError(f'No event found for event type {e}. Please update the coding file to include an explicit event_type column.')
            elif len(evts)>1:
                raise ValueError(f'Event type {e} is ambiguous, there are multiple events with the same type. I thus cannot determine the correct event names from the event types alone, and thereby cannot update the loaded coding file. Please manually update the coding file to include an explicit event column containing the event names matching those existing in the coding setup.')
            df.loc[df['event_type']==e, 'event'] = evts[0].name
    df['end_frame'] = df['end_frame'].astype('object')
    df.loc[pd.isnull(df['end_frame']),'end_frame'] = None   # set missing to None
    return [Episode(**kwargs) for kwargs in df.to_dict(orient='records')]

def write_list_to_file(episodes: list[Episode],
                       fileName: str|pathlib.Path):
    if not episodes:
        return

    records = [{k:getattr(p,k) for k in vars(p) if not k.startswith('_')} for p in episodes]
    df = pd.DataFrame.from_records(records)
    df['event_type'] = [x.value for x in df['event_type'].values]

    # keep only columns to be written out and order them correctly
    df = df[['event','event_type','start_frame','end_frame']]
    df.to_csv(str(fileName), index=False, sep='\t', na_rep='nan')

def _copy_intervals(intervals: list[list[int]]) -> list[list[int]]:
    return [iv.copy() for iv in intervals]

def copy_episode_map(episodes: EpisodeMap) -> EpisodeMap:
    return {nm: (episodes[nm][0], _copy_intervals(episodes[nm][1])) for nm in episodes}

def copy_episode_source_refs(source_refs: EpisodeSourceRefs) -> EpisodeSourceRefs:
    return {nm: refs.copy() for nm, refs in source_refs.items()}

def _get_local_episode_sources(episodes: EpisodeMap) -> EpisodeSourceRefs:
    return typing.cast(EpisodeSourceRefs, {nm: [(None, i) for i in range(len(episodes[nm][1]))] for nm in episodes})

def _get_empty_imported_episodes(episodes: EpisodeMap) -> EpisodeImportedMap:
    return typing.cast(EpisodeImportedMap, {nm: {} for nm in episodes})

def get_source_labeled_episodes(episodes: EpisodeMap, imported_episodes: EpisodeImportedMap) -> EpisodeMap:
    labeled = copy_episode_map(episodes)
    for nm in imported_episodes:
        if nm not in episodes:
            continue
        for other_rec in imported_episodes[nm]:
            labeled[f'{nm} (from recording {other_rec})'] = (episodes[nm][0], _copy_intervals(imported_episodes[nm][other_rec]))
    return labeled

def load_episodes_from_all_recordings_with_info(study_config: config.Study, recording_dir: str|pathlib.Path, episode_subset: set[str]|None=None, load_from_other_recordings=True, empty_if_no_coding=True, error_if_unwanted_found=True, missing_other_coding_ok=False) -> tuple[EpisodeMap, set[str], EpisodeSourceRefs, EpisodeImportedMap]:
    from . import synchronization
    # loads episodes for both the current recording, and optionally also from other synced recordings in the session as set up in the study config
    recording_dir = pathlib.Path(recording_dir)
    rec_name = recording_dir.name
    coding_file = recording_dir / naming.coding_file
    if coding_file.is_file():
        episodes = list_to_marker_dict(read_list_from_file(coding_file))
        if episode_subset is not None:
            episodes = {k:v for k,v in episodes.items() if k in episode_subset}
    else:
        if not empty_if_no_coding:
            raise FileNotFoundError(f'No coding file found at {coding_file}')
        episodes = get_empty_marker_dict([(cs['name'],cs['event_type']) for cs in study_config.coding_setup if episode_subset is None or cs['name'] in episode_subset])

    # check what coding we expect for this file
    if len(study_config.session_def.recordings)==1:
        to_code = {cs['name'] for cs in study_config.coding_setup}
    else:
        to_code: set[str] = set()
        for cs in study_config.coding_setup:
            if cs['event_type']==annotation.EventType.Sync_Camera:
                # camera sync events are always coded for all recordings
                to_code.add(cs['name'])
                continue
            which_recs = cs.get('which_recordings')
            if which_recs is None or rec_name in which_recs:
                to_code.add(cs['name'])
    if episode_subset is not None:
        to_code = to_code.intersection(episode_subset)

    # add missing fields
    wanted_events = {cs['name'] for cs in study_config.coding_setup if episode_subset is None or cs['name'] in episode_subset}
    for evt in wanted_events:
        if evt not in episodes:
            cs = [cs for cs in study_config.coding_setup if cs['name']==evt][0]
            episodes[evt] = (cs['event_type'], [])

    episode_sources = _get_local_episode_sources(episodes)
    imported_episodes = _get_empty_imported_episodes(episodes)

    if not load_from_other_recordings:
        return episodes, to_code, episode_sources, imported_episodes

    # now check if there is coding to get from other recordings, or if there is coding that should not be there
    # checking for coding from other recordings is done using the study config setup, it can be switched off for specific events
    # check for unwanted coding
    to_remove = []
    for nm in episodes:
        if episodes[nm][1] and nm not in to_code:
            if error_if_unwanted_found:
                cs = [cs for cs in study_config.coding_setup if cs['name']==nm][0]
                raise ValueError(f'{nm} episodes are gotten from the recordings {", ".join(sorted(cs.get("which_recordings")))} and should not be coded for this recording ({rec_name})')
            else:
                to_remove.append(nm)
    for nm in to_remove:
        del episodes[nm]
    for evt in wanted_events:
        if evt not in episodes:
            cs = [cs for cs in study_config.coding_setup if cs['name']==evt][0]
            episodes[evt] = (cs['event_type'], [])
    episode_sources = _get_local_episode_sources(episodes)
    imported_episodes = _get_empty_imported_episodes(episodes)

    ref_rec = study_config.sync_ref_recording
    if ref_rec is None:
        return episodes, to_code, episode_sources, imported_episodes
    all_recs = [r.name for r in study_config.session_def.recordings if r.name!=ref_rec]

    # check for coding to get from other recordings
    for cs in study_config.coding_setup:
        nm = cs['name']
        if episode_subset is not None and nm not in episode_subset:
            continue
        which_recs = cs.get('which_recordings')
        if which_recs is None:
            continue
        candidate_recs = [r.name for r in study_config.session_def.recordings if r.name in which_recs and r.name!=rec_name]
        if not candidate_recs:
            continue
        should_get_from_other = cs.get('load_from_other_recordings')
        should_use_other_for_base = should_get_from_other and not episodes[nm][1]
        gotten_from_other = not should_use_other_for_base
        for other_rec in candidate_recs:
            eps = synchronization.get_episode_frame_indices_from_other_video(recording_dir, nm, rec_name, other_rec, ref_rec, all_recs, bool(study_config.sync_ref_do_time_stretch), list(study_config.sync_ref_average_recordings or []), study_config.sync_ref_stretch_which or 'other', missing_other_coding_ok=True)
            if not eps:
                continue
            imported_episodes[nm][other_rec] = _copy_intervals(eps)
            if not should_use_other_for_base or gotten_from_other:
                continue
            episodes[nm] = (cs['event_type'], _copy_intervals(eps))
            episode_sources[nm] = [(other_rec, i) for i in range(len(eps))]
            gotten_from_other = True
        if not gotten_from_other and should_get_from_other and not missing_other_coding_ok:
            other_recs = ', '.join(sorted(candidate_recs))
            if which_recs is not None and rec_name in which_recs:
                msg_part = f'Coding for {nm} is expected to be coded for this recording ({rec_name}), but not found in this or any other recording that it may be found in ({other_recs}).'
            else:
                msg_part = f'Coding for {nm} (not expected for this recording, {rec_name}) was not found in any other recording for which it may be expected ({other_recs}).'
            raise ValueError(f'{msg_part} Please ensure coding for {nm} is present.')

    return episodes, to_code, episode_sources, imported_episodes

def load_episodes_from_all_recordings(study_config: config.Study, recording_dir: str|pathlib.Path, episode_subset: set[str]|None=None, load_from_other_recordings=True, empty_if_no_coding=True, error_if_unwanted_found=True, missing_other_coding_ok=False) -> tuple[EpisodeMap, set[str]]:
    episodes, to_code, _, _ = load_episodes_from_all_recordings_with_info(study_config, recording_dir, episode_subset, load_from_other_recordings, empty_if_no_coding, error_if_unwanted_found, missing_other_coding_ok)
    return episodes, to_code


def get_empty_marker_dict(episodes: list[tuple[str, annotation.EventType]]) -> EpisodeMap:
    return {e:(et, []) for e,et in sorted(episodes)}

def list_to_marker_dict(episodes: list[Episode], expected_events: list[tuple[str, annotation.EventType]]|None=None) -> EpisodeMap:
    e_dict = get_empty_marker_dict(expected_events or list(set((e.event, e.event_type) for e in episodes)))
    for e in episodes:
        if e.event not in e_dict:
            continue    # ignore unexpected events
        if e.end_frame is not None:
            e_dict[e.event][1].append([e.start_frame, e.end_frame])
        else:
            e_dict[e.event][1].append([e.start_frame])
    return e_dict

def marker_dict_to_list(episodes: typing.Mapping[str, tuple[annotation.EventType, list[int]|list[list[int]]]]) -> list[Episode]:
    e_list: list[Episode] = []
    for e in episodes:
        if not episodes[e] or not episodes[e][1]:
            continue
        if isinstance(episodes[e][1][0],list):
            e_list.extend([Episode(e, episodes[e][0], *v) for v in episodes[e][1]])
        else:
            if annotation.type_map[episodes[e][0]]==annotation.Type.Interval:
                for m in range(0,len(episodes[e][1])-1,2): # read in batches of two, and run until -1 to make sure we don't pick up incomplete intervals
                    e_list.append(Episode(e, episodes[e][0], *episodes[e][1][m:m+2]))
            else:
                e_list.extend([Episode(e, episodes[e][0], m) for m in episodes[e][1]])

    return sorted(e_list, key=lambda x: x.start_frame)