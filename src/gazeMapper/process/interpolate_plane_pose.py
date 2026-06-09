import pathlib

from glassesTools import gaze_headref, naming as gt_naming, pose as gt_pose, process_pool, timestamps

from .. import config, naming, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('samples')
    progress_indicator.set_start_time_to_now()

    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run interpolate_plane_pose on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')
    if not process.config_has_plane_pose_interpolation(study_config, working_dir.name):
        raise ValueError(f'Plane pose interpolation is not enabled for recording "{working_dir.name}"')

    pose_files = sorted(
        f for f in working_dir.glob(f'{naming.plane_pose_prefix}*.tsv')
        if not f.name.startswith(naming.plane_pose_interpolated_prefix)
    )
    if not pose_files:
        raise FileNotFoundError(f'No plane pose files found in "{working_dir}". Run Detect Markers first.')

    gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR',''])[0]
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    total = sum(len(samples) for samples in gazes.values()) * len(pose_files)
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(min(50, int(total/200)), min(50, int(total/200)))

    for pose_file in pose_files:
        plane_name = pose_file.stem[len(naming.plane_pose_prefix):]
        poses = gt_pose.read_dict_from_file(pose_file)
        sampled_poses = gt_pose.interpolate_plane_poses_to_gaze_samples(
            poses,
            gazes,
            video_ts,
            max_missing_frames=study_config.interpolate_plane_pose_max_missing_frames,
            progress_updater=progress_indicator.update
        )

        out_file = working_dir / f'{naming.plane_pose_interpolated_prefix}{plane_name}.tsv'
        if sampled_poses:
            gt_pose.write_list_to_file(sampled_poses, out_file, skip_failed=True)
        else:
            out_file.unlink(missing_ok=True)

    session.update_action_states(working_dir, process.Action.INTERPOLATE_PLANE_POSE, process_pool.State.Completed, study_config)
