import pathlib
import typing
import numpy as np
import cv2

from . import image_helper
from ... import config, process, session


def is_project_folder(path: str | pathlib.Path):
    path = pathlib.Path(path)
    if not path.is_dir():
        return False
    # a project directory should contain a 'config'
    # folder and inside that are 'study_def.json' and 'session_def.json' file
    return (path/'config').is_dir() and \
        (path/'config'/config.Study.default_json_file_name).is_file() and \
        (path/'config'/session.SessionDefinition.default_json_file_name).is_file()

def init_project_folder(path: str | pathlib.Path):
    path = pathlib.Path(path)
    if not path.is_dir():
        raise ValueError(f'The provided path is not a folder. Provided path: {path}')
    config_dir = path/'config'
    if not config_dir.is_dir():
        config_dir.mkdir()
    # make empty 'study_def.json' file
    study_config = config.Study.get_empty(path)
    study_config.store_as_json(config_dir)


def get_aruco_marker_image(sz: int, m_id: int, dictionary_id: int, marker_border_bits: int):
    marker_image = np.zeros((sz, sz), dtype=np.uint8)
    marker_image = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(dictionary_id), m_id, sz, marker_image, marker_border_bits)
    return image_helper.ImageHelper(marker_image)

def load_image_with_helper(path: pathlib.Path):
    return image_helper.ImageHelper(cv2.cvtColor(cv2.imread(path, cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB))


class JobInfo(typing.NamedTuple):
    action:     process.Action
    session:    str
    recording:  typing.Optional[str] = None