import typing
import typeguard
import inspect
import pathlib
import warnings

import numpy as np

from glassesTools import aruco, json, marker as gt_marker, process_pool

from . import type_utils

MarkerSequence = list[gt_marker.MarkerID]
MarkerSequenceList = list[MarkerSequence]


class Marker:
    @typeguard.typechecked
    def __init__(self,
                 m_id               : int,
                 detect_only        : bool,         # if true, pose will not be determined and only marker presence is detected. That means marker size is not needed
                 size               : float|None                = None,
                 aruco_dict_id      : type_utils.ArucoDictType  = aruco.default_dict,
                 marker_border_bits : int                       = 1,
                 detection_settings : str                       = 'general'
                 ):
        self.id                 = m_id
        self.detect_only        = detect_only
        self.size               = size
        self.aruco_dict_id      = aruco_dict_id
        self.marker_border_bits = marker_border_bits
        self.detection_settings = detection_settings

    def field_problems(self) -> type_utils.ProblemDict:
        problems = {}
        if type(self.marker_border_bits) is not int or self.marker_border_bits < 1:
            problems['marker_border_bits'] = (type_utils.ProblemLevel.Error, 'marker_border_bits must be a positive integer')
        return problems

    def _to_dict(self) -> dict[str,typing.Any]:
        # N.B.: print dictionary names for markers instead of hard to understand integer id
        # always store dictionary even if its the default, so its easier to read the config file by eye
        out = {'id': self.id, 'aruco_dict': aruco.dict_id_to_str[self.aruco_dict_id], 'detect_only': self.detect_only}
        for f in ('size', 'marker_border_bits', 'detection_settings'):
            if (val:=getattr(self,f))!=marker_defaults[f]:
                out[f] = val
        return out

    @staticmethod
    def _from_dict(kwargs: dict[str,typing.Any]):
        # backwards compatibility
        if 'detect_only' not in kwargs:
            kwargs['detect_only'] = False
        if 'id' in kwargs:
            kwargs['m_id'] = kwargs.pop('id')
        if 'aruco_dict' in kwargs:
            kwargs['aruco_dict_id'] = kwargs.pop('aruco_dict')
        if 'aruco_dict_id' in kwargs:
            # dictionary names might be stored as strings, turn back into int
            if isinstance(kwargs['aruco_dict_id'],str):
                kwargs['aruco_dict_id'] = aruco.str_to_dict_id(kwargs['aruco_dict_id'])
        return Marker(**kwargs)

json.register_type(json.TypeEntry(Marker,'__marker.Marker__', Marker._to_dict, Marker._from_dict))
# get defaults for default argument of Marker constructor
_params = inspect.signature(Marker.__init__).parameters
marker_defaults = {k:d for k in _params if (d:=_params[k].default)!=inspect._empty}
marker_parameter_types = {k:_params[k].annotation for k in _params if k!='self'}
del _params

def get_marker_setup(marker: Marker, settings_groups: dict[str, dict]) -> aruco.MarkerSetup:
    from . import config
    settings = config.aruco_settings_with_border({'detector_params': settings_groups[marker.detection_settings]}, marker.marker_border_bits)
    return aruco.MarkerSetup(detector_params = aruco.resolve_settings(settings)['detector_params'],
                             detect_only = marker.detect_only,
                             size = marker.size)

def get_setup_for_markers(markers: list[Marker], settings_groups: dict[str, dict]|None = None) -> dict[gt_marker.MarkerID,aruco.MarkerSetup]:
    return {gt_marker.MarkerID(m.id, m.aruco_dict_id): get_marker_setup(m, settings_groups) for m in markers}

def filter_marker_sequences_with_marker_files(event_name: str, working_dir: str | pathlib.Path, sequence_sets: dict[str, MarkerSequenceList]) -> tuple[dict[str, MarkerSequenceList], set[gt_marker.MarkerID]]:
    """Filter sequences with missing marker files and return their marker IDs."""
    working_dir = pathlib.Path(working_dir)
    field_display_names = {
        'markers': 'marker',
        'start_markers': 'start marker',
        'end_markers': 'end marker',
    }

    valid_sequence_sets: dict[str, MarkerSequenceList] = {}
    all_marker_ids: set[gt_marker.MarkerID] = set()
    for field, sequences in sequence_sets.items():
        valid_sequence_sets[field] = []
        for sequence in sequences:
            missing_markers = [marker_id for marker_id in sequence if not gt_marker.get_file_name(marker_id.m_id, marker_id.aruco_dict_id, working_dir).is_file()]
            if missing_markers:
                sequence_str = ' -> '.join(gt_marker.marker_ID_to_str(marker_id) for marker_id in sequence)
                missing_str = '\n- '.join(str(gt_marker.get_file_name(marker_id.m_id, marker_id.aruco_dict_id, None)) for marker_id in missing_markers)
                warnings.warn(f'{event_name}: Missing marker file(s) for {field_display_names.get(field, field.replace("_", " "))} sequence {sequence_str}:\n- {missing_str}\nSkipping this sequence for auto coding.', process_pool.ProcessingWarning)
                continue
            valid_sequence_sets[field].append(sequence)
            all_marker_ids.update(sequence)

    return valid_sequence_sets, all_marker_ids


def get_marker_appearance_starts_ends(
    marker_ids: set[gt_marker.MarkerID],
    working_dir: str | pathlib.Path,
    max_gap_duration: int,
    min_duration: int,
) -> tuple[dict[gt_marker.MarkerID, np.ndarray], dict[gt_marker.MarkerID, np.ndarray]]:
    """Load marker files and find the start and end frames of each marker appearance."""
    working_dir = pathlib.Path(working_dir)
    raw_markers = {m: gt_marker.read_dataframe_from_file(m.m_id, m.aruco_dict_id, working_dir) for m in marker_ids}
    present_markers = {marker_id: gt_marker.code_for_presence(marker_data, allow_failed=True) for marker_id, marker_data in raw_markers.items() if not marker_data.empty}
    if not present_markers:
        raise RuntimeError(
            f'No markers found in the marker detection files for session "{working_dir.parent.name}", '
            f'recording "{working_dir.name}"'
        )
    detected_markers = {marker_id: gt_marker.expand_detection(marker_data, fill_value=False) for marker_id, marker_data in present_markers.items()}

    marker_starts = {marker_id: np.array([], dtype=int) for marker_id in marker_ids}
    marker_ends = {marker_id: np.array([], dtype=int) for marker_id in marker_ids}
    for marker_id, marker_data in detected_markers.items():
        starts, ends = gt_marker.get_appearance_starts_ends(marker_data, max_gap_duration, min_duration)
        marker_starts[marker_id] = starts
        marker_ends[marker_id] = ends

    return marker_starts, marker_ends
