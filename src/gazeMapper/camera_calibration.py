import dataclasses
import pathlib

import numpy as np

from glassesTools import ocv

from . import type_utils


@dataclasses.dataclass(frozen=True)
class Inspection:
    path: pathlib.Path | None
    parameters: ocv.CameraParams | None
    level: type_utils.ProblemLevel
    messages: tuple[str, ...]

    @property
    def label(self) -> str:
        match self.level:
            case type_utils.ProblemLevel.Error:
                return 'Invalid camera calibration'
            case type_utils.ProblemLevel.Warning:
                return 'Camera calibration missing'
            case _:
                return 'Camera calibration valid'

    @property
    def diagnostic(self) -> str:
        return self.label + (': ' + '\n'.join(self.messages) if self.messages else '')

    @property
    def extra_info(self) -> str | None:
        return f'Calibration file: {self.path}' if self.path is not None else None

    @property
    def details(self) -> str:
        lines = [self.diagnostic]
        if self.path is not None and self.level == type_utils.ProblemLevel.Error and self.path.is_file():
            lines.append(self.extra_info)
        return '\n'.join(lines)


def inspect(path: str | pathlib.Path | None, require_extrinsics: bool = False) -> Inspection:
    if path is None:
        return Inspection(None, None, type_utils.ProblemLevel.Warning, ('No calibration file is configured.',))

    path = pathlib.Path(path)
    if not path.is_file():
        return Inspection(path, None, type_utils.ProblemLevel.Warning, ('Calibration file is missing.',))

    try:
        parameters = ocv.CameraParams.read_from_file(path)
    except Exception as exc:
        return Inspection(path, None, type_utils.ProblemLevel.Error, (f'Could not read calibration file: {exc}',))

    problems: list[str] = []
    if parameters.resolution is None:
        problems.append('Resolution is required.')
    else:
        resolution = np.asarray(parameters.resolution)
        if resolution.size != 2 or not np.isfinite(resolution).all() or np.any(resolution <= 0):
            problems.append('Resolution must contain two positive finite dimensions.')

    camera_matrix = parameters.camera_mtx
    distortion = parameters.distort_coeffs
    has_colmap = parameters.has_colmap_camera()
    has_opencv_data = camera_matrix is not None or distortion is not None
    if has_opencv_data:
        if camera_matrix is None or distortion is None:
            problems.append('OpenCV intrinsics require both cameraMatrix and distCoeff.')
        else:
            camera_matrix = np.asarray(camera_matrix)
            distortion = np.asarray(distortion)
            if camera_matrix.shape != (3, 3) or not np.isfinite(camera_matrix).all():
                problems.append('cameraMatrix must be a finite 3x3 matrix.')
            elif camera_matrix[0, 0] <= 0 or camera_matrix[1, 1] <= 0:
                problems.append('cameraMatrix focal lengths must be positive.')
            if distortion.size == 0 or not np.isfinite(distortion).all():
                problems.append('distCoeff must contain finite distortion coefficients.')
    if not has_colmap and not has_opencv_data:
        problems.append('No usable camera intrinsics were found.')

    rotation = parameters.rotation_vec
    position = parameters.position
    has_any_extrinsics = rotation is not None or position is not None
    if has_any_extrinsics:
        if rotation is None or position is None:
            problems.append('Extrinsics require both rotation and position.')
        else:
            rotation = np.asarray(rotation)
            position = np.asarray(position)
            if rotation.size != 3 or not np.isfinite(rotation).all():
                problems.append('rotation must contain three finite values.')
            if position.size != 3 or not np.isfinite(position).all():
                problems.append('position must contain three finite values.')
    elif require_extrinsics:
        problems.append(
            "The provided calibration file does not include camera extrinsics "
            "(transformation between this head-attached camera and the scene camera "
            "of the associated eye tracker recording), but these are required because "
            "the recording is configured to replace the eye tracker recording's scene "
            "camera (see the head_attached_recordings_replace_et_scene setting)."
        )

    level = type_utils.ProblemLevel.Error if problems else type_utils.ProblemLevel.Information
    return Inspection(path, parameters, level, tuple(problems))