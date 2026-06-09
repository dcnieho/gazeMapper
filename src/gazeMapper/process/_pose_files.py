import pathlib

from glassesTools import pose as gt_pose

from .. import naming


def get_preferred_plane_pose_file(working_dir: str|pathlib.Path, plane_name: str) -> tuple[pathlib.Path, bool]:
    working_dir = pathlib.Path(working_dir)
    interpolated = working_dir / f'{naming.plane_pose_interpolated_prefix}{plane_name}.tsv'
    if interpolated.is_file():
        return interpolated, True
    return working_dir / f'{naming.plane_pose_prefix}{plane_name}.tsv', False


def read_preferred_plane_pose(working_dir: str|pathlib.Path, plane_name: str, episodes: list[list[int]]|None=None):
    pose_file, is_interpolated = get_preferred_plane_pose_file(working_dir, plane_name)
    if is_interpolated:
        return gt_pose.read_list_dict_from_file(pose_file, episodes, ts_column_suffixes=['VOR',''])
    return gt_pose.read_dict_from_file(pose_file, episodes, ts_column_suffixes=['VOR',''])
