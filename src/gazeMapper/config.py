import pathlib
import json
from typing import Any

from glassesTools import utils

from . import episode, plane, session


class Marker:
    def __init__(self, id:int, size:float):
        self.id     = id
        self.size   = size
utils.register_type(utils.CustomTypeEntry(Marker,'__config.Marker__',lambda x: {'id': x.id, 'size': x.size}, lambda x: Marker(**x)))

def get_marker_dict_from_list(markers: list[Marker]) -> dict[int,dict[str,Any]]:
    out = {}
    for m in markers:
        out[m.id] = {'marker_size': m.size}
    return out

class Study:
    default_json_file_name = 'study_def.json'

    def __init__(self, session_def: session.SessionDefinition, planes: list[plane.Definition], planes_per_interval: dict[episode.Event,list[str]], individual_markers: list[Marker], working_directory: str|pathlib.Path):
        self.session_def        = session_def
        self.planes             = planes
        self.planes_per_interval= planes_per_interval
        self.working_directory  = working_directory
        self.individual_markers = individual_markers

        self._check_planes_per_interval()

    def _check_planes_per_interval(self):
        for e in self.planes_per_interval:
            for p in self.planes_per_interval[e]:
                if not any([p==pl.name for pl in self.planes]):
                    raise ValueError(f'plane {p} not known')

    def store_as_json(self, path: str | pathlib.Path):
        path = pathlib.Path(path)
        # this stores only the planes_per_interval variable to json, rest is read from other files
        # instead to remain flexible and make it easy for users to rename, etc
        d_path = path / self.default_json_file_name
        with open(d_path, 'w') as f:
            to_dump = {k:getattr(self,k) for k in ['planes_per_interval','individual_markers']}    # only these fields. Name will be populated from name of session/provided folder, recordings from each subfolder in the session/provided folder, and working_directory as the provided path
            to_dump['planes_per_interval'] = [(k, to_dump['planes_per_interval'][k]) for k in to_dump['planes_per_interval']]   # pack as list of tuples for storage
            # dump to file
            json.dump(to_dump, f, cls=utils.CustomTypeEncoder, indent=2)
        # this doesn't story any files itself, but triggers the contained info to be stored
        self.session_def.store_as_json(self.working_directory / 'session_def.json')
        for p in self.planes:
            p_dir = self.working_directory / p.name
            if not p_dir.is_dir():
                p_dir.mkdir()
            p.store_as_json(p_dir)

    @staticmethod
    def load_from_json(path: str | pathlib.Path) -> 'Study':
        path = pathlib.Path(path)
        # get kwds
        d_path = path / Study.default_json_file_name
        with open(d_path, 'r') as f:
            kwds = json.load(f, object_hook=utils.json_reconstitute)
        kwds['planes_per_interval'] = {k:v for k,v in kwds['planes_per_interval']}  # stored as list of tuples, unpack
        # get session def
        s_path = path / 'session_def.json'
        if not s_path.is_file():
            return None
        sess_def = session.SessionDefinition.load_from_json(s_path)

        # get planes
        planes: list[plane.Definition] = []
        for p_dir in path.iterdir():
            if not p_dir.is_dir():
                continue
            p_file = p_dir / plane.Definition.default_json_file_name
            if not p_file.is_file():
                continue
            planes.append(plane.Definition.load_from_json(p_file))

        return Study(sess_def, planes, working_directory=path, **kwds)
