import pathlib
import typing
import numpy as np
import cv2

from imgui_bundle import imgui

from glassesTools import annotation, aruco, drawing

from . import image_helper
from ... import config, marker_assembly, process, session


def is_project_folder(path: str | pathlib.Path):
    path = pathlib.Path(path)
    if not path.is_dir():
        return False
    # a project directory should contain a 'config'
    # folder and inside that are 'study_def.json' and 'session_def.json' file
    return (path/'config').is_dir() and \
        (path/'config'/config.Study.default_json_file_name).is_file() and \
        (path/'config'/session.SessionDefinition.default_json_file_name).is_file()

def init_project_folder(path: str | pathlib.Path, template: typing.Literal['glassesValidator']|None=None):
    path = pathlib.Path(path)
    if not path.is_dir():
        raise ValueError(f'The provided path is not a folder. Provided path: {path}')

    if template is None:
        study_config = config.Study.get_empty(path)
    elif template=='glassesValidator':
        recording_def = session.RecordingDefinition('et', session.RecordingType.Eye_Tracker, None, None)
        validation_plane = marker_assembly.make_definition(marker_assembly.Type.GlassesValidator, 'validate', None)
        validation_event = config.EventSetup(
            name=annotation.EventType.Validate.value,
            event_type=annotation.EventType.Validate,
            marker_assemblies={'validate'},
        )
        study_config = config.Study(
            session_def=session.SessionDefinition([recording_def]),
            marker_assemblies=[validation_plane],
            individual_markers=[],
            coding_setup=[validation_event],
            working_directory=path,
            mapped_video_make_which={'et'},
            mapped_video_recording_colors={'et': drawing.RgbColor(255, 127, 0)},
            strict_check=False,
        )
    else:
        raise ValueError(f'Unknown project template: {template}')

    config_dir = path/'config'
    config_dir.mkdir(exist_ok=True)
    study_config.store_as_json(config_dir)


def get_aruco_marker_image(sz: int, m_id: int, dictionary_id: int, marker_border_bits: int):
    marker_image = aruco.get_marker_image(sz, m_id, dictionary_id, marker_border_bits)
    if marker_image is None:
        return None
    return image_helper.ImageHelper(marker_image)

def load_image_with_helper(path_or_image: pathlib.Path|np.ndarray):
    if isinstance(path_or_image, pathlib.Path):
        return image_helper.ImageHelper(cv2.cvtColor(cv2.imread(path_or_image, cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB))
    else:
        return image_helper.ImageHelper(path_or_image)

def is_valid_imgui_key(key: str) -> bool:
    """
    Return True if `key` corresponds to a member on imgui.Key.
    For digit keys "0"..."9", we test against the enum names "_0"..."_9".
    """
    if key in '0123456789':
        key = f'_{key}'
    try:
        return hasattr(imgui.Key, key)
    except Exception:
        return False

class JobInfo(typing.NamedTuple):
    action:     process.Action
    session:    str
    recording:  typing.Optional[str] = None