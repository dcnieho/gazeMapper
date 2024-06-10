from enum import Enum, auto
import pathlib
import pandas as pd
from collections import defaultdict

from glassesTools import utils



class Type(Enum):
    Point       = auto()
    Interval    = auto()


class Event(utils.AutoName):
    Validate    = auto()    # interval to be used for running glassesValidator
    Sync_Camera = auto()    # point to be used for synchronizing different cameras
    Sync_VOR    = auto()    # episode to be used for VOR synchronization
    Map         = auto()    # episode for which to map gaze to plane(s): output for files to be provided to user
events = [x.value for x in Event]
utils.register_type(utils.CustomTypeEntry(Event,'__enum.Event__',str, lambda x: getattr(Event, x.split('.')[1])))

type_map = {
    Event.Validate    : Type.Interval,
    Event.Sync_Camera : Type.Point,
    Event.Sync_VOR    : Type.Interval,
    Event.Map         : Type.Interval,
}


class Episode:
    def __init__(self, event:Event, start_frame:int, end_frame:int=None):
        self.event          = event
        self.start_frame    = start_frame

        if type_map[event]==Type.Interval:
            assert end_frame is not None, f"end frame expected for an interval-type episode ({event.value}), but not provided"
        if type_map[event]==Type.Point:
            assert end_frame is None, f"end frame provided but not expected for a point-type episode ({event.value})"
        self.end_frame      = end_frame


# for dealing with lists of events
def read_list_from_file(fileName: str|pathlib.Path) -> list[Episode]:
    df = pd.read_csv(str(fileName), delimiter='\t', index_col=False, dtype=defaultdict(lambda: int, event=str, end_frame=pd.Int64Dtype()))
    df['event']     = [Event(x) for x in df['event'].values]
    df['end_frame'] = df['end_frame'].astype('object')
    df.loc[pd.isnull(df['end_frame']),'end_frame'] = None   # set missing to None
    return [Episode(**kwargs) for kwargs in df.to_dict(orient='records')]

def write_list_to_file(episodes: list[Episode],
                       fileName: str|pathlib.Path):
    if not episodes:
        return

    records = [{k:getattr(p,k) for k in vars(p) if not k.startswith('_')} for p in episodes]
    df = pd.DataFrame.from_records(records)
    df['event'] = [x.value for x in df['event'].values]

    # keep only columns to be written out and order them correctly
    df = df[['event','start_frame','end_frame']]
    df.to_csv(str(fileName), index=False, sep='\t', na_rep='nan')


def get_empty_marker_dict() -> dict[Event,list[int]]:
    return {e:[] for e in Event}

def list_to_marker_dict(episodes: list[Episode]) -> dict[Event,list[list[int]]]:
    e_dict = get_empty_marker_dict()
    for e in episodes:
        if e.end_frame is not None:
            e_dict[e.event].append([e.start_frame, e.end_frame])
        else:
            e_dict[e.event].append([e.start_frame])
    return e_dict

def marker_dict_to_list(episodes: dict[Event,list[int]]|dict[Event,list[list[int]]]) -> list[Episode]:
    e_list: list[Episode] = []
    for e in episodes:
        if not episodes[e]:
            continue
        if isinstance(episodes[e][0],list):
            e_list.extend([Episode(e, *v) for v in episodes[e]])
        else:
            if type_map[e]==Type.Interval:
                for m in range(0,len(episodes[e])-1,2): # read in batches of two, and run until -1 to make sure we don't pick up incomplete intervals
                    e_list.append(Episode(e, *episodes[e][m:m+2]))
            else:
                e_list.extend([Episode(e,m) for m in episodes[e]])

    return sorted(e_list, key=lambda x: x.start_frame)


def is_in_interval(episodes: dict[Event,list[int]]|list[Episode], idx: int) -> dict[Event, bool]:
    if isinstance(episodes,dict):
        episodes = marker_dict_to_list(episodes)

    e_dict: dict[Event,bool] = {e:False for e in Event}
    for e in episodes:
        if type_map[e.event]==Type.Interval:
            if idx>=e.start_frame and idx<=e.end_frame:
                e_dict[e.event] = True
        else:
            if idx==e.start_frame:
                e_dict[e.event] = True
    return e_dict