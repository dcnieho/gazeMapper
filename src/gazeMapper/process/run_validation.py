import pathlib
import numpy as np

from glassesTools import annotation, fixation_classification
from glassesTools.validation import config as val_config, assign_fixations, compute_offsets


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

        validation_plane = val_config.plane.ValidationPlane(validator_config_dir)

        plot_limits = [[validation_plane.bbox[0]-validation_plane.marker_size, validation_plane.bbox[2]+validation_plane.marker_size],
                       [validation_plane.bbox[1]-validation_plane.marker_size, validation_plane.bbox[3]+validation_plane.marker_size]]
        fixation_classification.from_plane_gaze(working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                                episodes,
                                                working_dir,
                                                I2MC_settings_override=study_config.validate_I2MC_settings,
                                                filename_stem=f'{naming.validation_prefix}{p}_fixations',
                                                plot_limits=plot_limits)

        targets = {t_id: np.append(validation_plane.targets[t_id].center, 0.) for t_id in validation_plane.targets}   # get centers of targets
        for idx,_ in enumerate(episodes):
            fix_file = working_dir / f'{naming.validation_prefix}{p}_fixations_interval_{idx+1:02d}.tsv'
            assign_fixations.distance(targets,
                                      fix_file,
                                      working_dir,
                                      do_global_shift=study_config.validate_do_global_shift,
                                      max_dist_fac=study_config.validate_max_dist_fac,
                                      filename_stem=f'{naming.validation_prefix}{p}_fixation_assignment',
                                      iteration=idx,
                                      background_image=(validation_plane.get_ref_image(as_RGB=True),
                                                        np.array([validation_plane.bbox[x] for x in (0,2,3,1)])),
                                      plot_limits=plot_limits)

        compute_offsets.compute(working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                working_dir/f'{naming.plane_pose_prefix}{p}.tsv',
                                working_dir/f'{naming.validation_prefix}{p}_fixation_assignment.tsv',
                                episodes,
                                targets,
                                validation_plane.config['distance']*10.,
                                working_dir,
                                filename=f'{naming.validation_prefix}{p}_data_quality.tsv',
                                dq_types=study_config.validate_dq_types,
                                allow_dq_fallback=study_config.validate_allow_dq_fallback,
                                include_data_loss=study_config.validate_include_data_loss)

    # update state
    session.update_action_states(working_dir, process.Action.VALIDATE, process.State.Completed, study_config)