from enum import Enum, auto
import pathlib
import numpy as np
import pandas as pd
from collections import defaultdict

from glassesTools import utils



class Type(Enum):
    Point       = auto()
    Interval    = auto()


class Event(Enum):
    Validate    = auto()
    Sync        = auto()
    Map         = auto()
events = [x.value for x in Event]
utils.register_type(utils.CustomTypeEntry(Event,'__enum.Event__',str, lambda x: getattr(Event, x.split('.')[1])))

type_map = {
    Event.Validate  : Type.Interval,
    Event.Sync      : Type.Point,
    Event.Map       : Type.Interval,
}


class Episode:
    def __init__(self, name:str, event:Event, start_frame: int, end_frame:int=None):
        self.name           = name
        self.event          = event
        self.start_frame    = start_frame

        if type_map[event]==Type.Interval:
            assert end_frame is not None, f"end frame expected for an interval-type episode ({event.value}), but not provided"
        if type_map[event]==Type.Point:
            assert end_frame is None, f"end frame provided but not expected for a point-type episode ({event.value})"
        self.end_frame      = end_frame

    @staticmethod
    def read_from_file(fileName: str|pathlib.Path) -> list['Episode']:
        df = pd.read_csv(str(fileName), delimiter='\t', index_col=False, dtype=defaultdict(lambda: int, **defaultdict(lambda: float, name=str, event=str)))
        df['event']     = [getattr(Event, x.split('.')[1]) for x in df['event'].values]
        df['end_frame'] = [None if np.isnan(x) else x for x in df['end_frame'].values]
        return [Episode(**kwargs) for kwargs in df.to_dict(orient='records')]

    @staticmethod
    def write_list_to_file(objects         : list['Episode'],
                           fileName        : str|pathlib.Path):
        records = [{k:getattr(p,k) for k in vars(p) if not k.startswith('_')} for p in objects]
        df = pd.DataFrame.from_records(records)
        df['event'] = [str(x) for x in df['event'].values]

        # keep only columns to be written out and order them correctly
        df = df[['name','event','start_frame','end_frame']]

        df.to_csv(str(fileName), index=False, sep='\t', na_rep='nan')