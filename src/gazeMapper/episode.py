import pathlib
import pandas as pd
import typing
from collections import defaultdict

from glassesTools import annotation


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
                raise ValueError(f'Event type {e} is ambiguous, cannot determine event names from event types alone. Please update the coding file to include an explicit event_type column.')
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