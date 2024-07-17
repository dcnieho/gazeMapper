from glassesTools import utils


class Marker:
    def __init__(self, id:int, size:float):
        self.id     = id
        self.size   = size
utils.register_type(utils.CustomTypeEntry(Marker,'__config.Marker__',lambda x: {'id': x.id, 'size': x.size}, lambda x: Marker(**x)))

def get_marker_dict_from_list(markers: list[Marker]) -> dict[int,dict[str]]:
    out = {}
    for m in markers:
        out[m.id] = {'marker_size': m.size}
    return out