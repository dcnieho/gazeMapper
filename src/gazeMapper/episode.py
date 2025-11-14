import pathlib
import pandas as pd
import typing
from collections import defaultdict

from glassesTools import annotation

from . import config, naming


class Episode:
    def __init__(self, event:str, start_frame:int, end_frame:int|None=None, event_type:annotation.EventType|None=None):
        # check event is known
        if (evt:=annotation.get_event_by_name(event)) is None:
            raise ValueError(f'Event "{event}" is not registered. Cannot create Episode instance.')
        self.event          = event
        if event_type is not None:
            if event_type != evt.event_type:
                raise ValueError(f'Event type mismatch for event "{event}". Provided: {event_type}, expected from registry: {evt.event_type}')
        self.event_type     = evt.event_type
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

def load_episodes_from_all_recordings(study_config: config.Study, recording_dir: str|pathlib.Path, empty_if_no_coding=True, error_if_unwanted_found=True) -> tuple[dict[str, list[list[int]]], set[str]]:
    from . import synchronization
    # loads episodes for both the current recording, and from other synced recordings in the session as set up in the study config
    recording_dir = pathlib.Path(recording_dir)
    coding_file = recording_dir / naming.coding_file
    if coding_file.is_file():
        episodes = list_to_marker_dict(read_list_from_file(coding_file))
    else:
        if not empty_if_no_coding:
            raise FileNotFoundError(f'No coding file found at {coding_file}')
        episodes = get_empty_marker_dict([])

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
            if recording_dir.name in which_recs:
                to_code.add(cs['name'])

    # add missing fields
    for evt in to_code:
        if evt not in episodes:
            episodes[evt] = []

    # now check if there is coding to get from other recordings, or if there is coding that should not be there
    rec_name = recording_dir.name
    # check for unwanted coding
    for nm in episodes:
        if episodes[nm] and nm not in to_code:
            if error_if_unwanted_found:
                cs = [cs for cs in study_config.coding_setup if cs['name']==nm][0]
                raise ValueError(f'{nm} episodes are gotten from the recordings {cs.get("which_recordings")} and should not be coded for this recording ({rec_name})')
            else:
                del episodes[nm]
    # check for coding to get from other recordings
    all_recs = [r.name for r in study_config.session_def.recordings if r.name!=study_config.sync_ref_recording]
    for cs in study_config.coding_setup:
        which_recs = cs.get('which_recordings')
        if which_recs is None or rec_name in which_recs:
            continue    # this coding is for this recording, so we already have it
        # get coding from other recordings
        single_rec = len(which_recs)==1
        for other_rec in which_recs:
            # NB: don't error if we don't need trial episodes for coding.
            extra = '' if single_rec else f' (from recording {other_rec})'
            eps = synchronization.get_episode_frame_indices_from_other_video(recording_dir, cs['name'], rec_name, other_rec, study_config.sync_ref_recording, all_recs, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, study_config.sync_ref_stretch_which, missing_other_coding_ok=True)
            if eps:
                episodes[cs['name']+extra] = eps

    return episodes, to_code


def get_empty_marker_dict(episodes: list[str]) -> dict[str,list[list[int]]]:
    return {e:[] for e in sorted(episodes)}

def list_to_marker_dict(episodes: list[Episode], expected_types: list[str]|None=None) -> dict[str,list[list[int]]]:
    e_dict = get_empty_marker_dict(expected_types or list(set(e.event for e in episodes)))
    for e in episodes:
        if e.event not in e_dict:
            #raise ValueError(f'{e.event} (a {annotation.tooltip_map[e.event_type]}) found, but not expected (e.g. should not be coded for this study according to the study setup)')
            continue    # ignore unexpected events
        if e.end_frame is not None:
            e_dict[e.event].append([e.start_frame, e.end_frame])
        else:
            e_dict[e.event].append([e.start_frame])
    return e_dict

def marker_dict_to_list(episodes: typing.Mapping[str,list[int]|list[list[int]]]) -> list[Episode]:
    e_list: list[Episode] = []
    for e in episodes:
        if not episodes[e]:
            continue
        if isinstance(episodes[e][0],list):
            e_list.extend([Episode(e, *v) for v in episodes[e]])
        else:
            ev = annotation.get_event_by_name(e)
            if annotation.type_map[ev.event_type]==annotation.Type.Interval:
                for m in range(0,len(episodes[e])-1,2): # read in batches of two, and run until -1 to make sure we don't pick up incomplete intervals
                    e_list.append(Episode(e, *episodes[e][m:m+2]))
            else:
                e_list.extend([Episode(e,m) for m in episodes[e]])

    return sorted(e_list, key=lambda x: x.start_frame)


def is_in_interval(episodes: dict[str,list[int]|list[list[int]]]|list[Episode], idx: int) -> dict[str, bool]:
    if isinstance(episodes,dict):
        episodes = marker_dict_to_list(episodes)

    e_dict: dict[str,bool] = {e.event:False for e in episodes}
    for e in episodes:
        if annotation.type_map[e.event_type]==annotation.Type.Interval:
            if idx>=e.start_frame and idx<=e.end_frame:
                e_dict[e.event] = True
        else:
            if idx==e.start_frame:
                e_dict[e.event] = True
    return e_dict