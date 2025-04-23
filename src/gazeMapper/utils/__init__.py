
from glassesTools import aruco
from collections import defaultdict

def format_duplicate_markers_msg(markers: set[tuple[int,int]]):
    # NB: input should be dictionary families, not dicts themselves
    # organize per dictionary family
    dict_markers: dict[int,list[int]] = defaultdict(list)
    for m,d in markers:
        dict_markers[d].append(m)
    dict_markers = {d:sorted(dict_markers[d]) for d in dict_markers}
    out = ''
    for i,d in enumerate(dict_markers):
        s = 's' if len(dict_markers[d])>1 else ''
        ids = ', '.join((str(x) for x in dict_markers[d]))
        d_str,is_family = aruco.family_to_str[d]
        f_str = ' family' if is_family else ''
        msg = f'marker{s} {ids} for the {d_str} dictionary{f_str}'
        if i==0:
            out = msg
        elif i==len(dict_markers)-1:
            out += f' and {msg}'
        else:
            out += f', {msg}'
    return out

def format_marker_sequence_msg(marker_set: list[tuple[int,int]]):
    # NB: input should be dictionary families, not dicts themselves
    # turn each dict into a string/family
    marker_set_str: list[tuple[str,bool,int]] = []
    all_same_family_or_dict = len(set((x[0] for x in marker_set)))==1
    marker_set.sort(key=lambda x: x[0])
    if not all_same_family_or_dict:
        marker_set.sort(key=lambda x: x[1])
    for m in marker_set:
        d_str,is_family = aruco.family_to_str[m[1]]
        marker_set_str.append((d_str, is_family, m[0]))
    if all_same_family_or_dict:
        m_str = ', '.join((str(m[2]) for m in marker_set_str))
        m_str += ' from the ' + (f'{marker_set_str[0][0]} family' if marker_set_str[0][1] else f'{marker_set_str[0][0]} dict')
    else:
        m_str = ', '.join((f'{m[2]} ({m[0] + (" family" if m[1] else "")})' for m in marker_set_str))
    return m_str