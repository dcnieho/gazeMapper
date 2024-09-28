import pathlib

from glassesValidator import process as gv_process, utils as gv_utils
from glassesTools import annotation


from .. import config, episode, naming, plane, process, session


stopAllProcessing = False
def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)
    if annotation.Event.Validate not in study_config.planes_per_episode:
        raise ValueError('No planes to use for validation are specified for the study, nothing to process')
    planes = list(study_config.planes_per_episode[annotation.Event.Validate])

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run run_validation on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get interval(s) coded to be analyzed, if any
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / naming.coding_file))[annotation.Event.Validate]

    # per plane, run the glassesValidator steps
    for p in planes:
        plane_def = [pl for pl in study_config.planes if pl.name==p][0]
        if plane_def.type!=plane.Type.GlassesValidator:
            raise ValueError(f'Plane {p} is not a glassesValidator plane, cannot be used for validation')
        validator_config_dir = None # None -> use glassesValidator built-in/default
        if not plane_def.use_default:
            validator_config_dir = config_dir/p

        # write marker intervals file as we'll need it
        marker_interval_file_name = f'{naming.validation_prefix}{p}_analysis_intervals.tsv'
        gv_utils.writeMarkerIntervalsFile(working_dir/marker_interval_file_name, episodes)
        # call the glassesValidator processing steps
        output_gaze_offset_file_name = f'{naming.validation_prefix}{p}_gaze_target_offsets.tsv'
        gv_process.compute_offsets_to_targets(working_dir, validator_config_dir,
                                              marker_interval_file_name=marker_interval_file_name,
                                              pose_file_name=f'{naming.plane_pose_prefix}{p}.tsv',
                                              world_gaze_file_name=f'{naming.world_gaze_prefix}{p}.tsv',
                                              output_gaze_offset_file_name=output_gaze_offset_file_name)

        output_analysis_interval_file_name = f'{naming.validation_prefix}{p}_fixation_intervals.tsv'
        gv_process.determine_fixation_intervals(working_dir, validator_config_dir, study_config.validate_do_global_shift, study_config.validate_max_dist_fac,
                                                study_config.validate_I2MC_settings,
                                                marker_interval_file_name=marker_interval_file_name,
                                                world_gaze_file_name=f'{naming.world_gaze_prefix}{p}.tsv',
                                                fixation_detection_file_name_prefix=f'{naming.validation_prefix}{p}_targetSelection_I2MC_',
                                                output_analysis_interval_file_name=output_analysis_interval_file_name)

        gv_process.calculate_data_quality(working_dir, study_config.validate_dq_types, study_config.validate_allow_dq_fallback, study_config.validate_include_data_loss,
                                          analysis_interval_file_name=output_analysis_interval_file_name,
                                          gaze_offset_file_name=output_gaze_offset_file_name,
                                          output_data_quality_file_name=f'{naming.validation_prefix}{p}_data_quality.tsv')

    # update state
    session.update_action_states(working_dir, process.Action.RUN_VALIDATION, process.State.Completed, study_config)