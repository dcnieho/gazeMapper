import pathlib

from glassesValidator import process as gv_process, utils as gv_utils


from . import naming
from .. import config, episode, plane, session


stopAllProcessing = False
def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None,
            do_global_shift=True, max_dist_fac=.5,
            dq_types: list[gv_process.DataQualityType]=None, allow_dq_fallback=False, include_data_loss=False):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    # get info about the study the recording is a part of
    study_config = config.Study.load_from_json(config_dir)
    assert episode.Event.Validate in study_config.planes_per_episode, 'No planes to use for validation are specified for the study, nothing to process'
    planes = study_config.planes_per_episode[episode.Event.Validate]

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    assert rec_def.type==session.RecordingType.EyeTracker, f'You can only run run_validation on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording'

    # get interval(s) coded to be analyzed, if any
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / 'coding.tsv'))[episode.Event.Validate]

    # per plane, run the glassesValidator steps
    for p in planes:
        plane_def = [pl for pl in study_config.planes if pl.name==p][0]
        assert plane_def.type==plane.Type.GlassesValidator, f'Plane {p} is not a glassesValidator plane, cannot be used for validation'
        validator_config_dir = None # use glassesValidator built-in/default
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
        gv_process.determine_fixation_intervals(working_dir, validator_config_dir, do_global_shift, max_dist_fac,
                                                marker_interval_file_name=marker_interval_file_name,
                                                world_gaze_file_name=f'{naming.world_gaze_prefix}{p}.tsv',
                                                fixation_detection_file_name_prefix=f'{naming.validation_prefix}{p}_targetSelection_I2MC_',
                                                output_analysis_interval_file_name=output_analysis_interval_file_name)

        gv_process.calculate_data_quality(working_dir, dq_types, allow_dq_fallback, include_data_loss,
                                          analysis_interval_file_name=output_analysis_interval_file_name,
                                          gaze_offset_file_name=output_gaze_offset_file_name,
                                          output_data_quality_file_name=f'{naming.validation_prefix}{p}_data_quality.tsv')