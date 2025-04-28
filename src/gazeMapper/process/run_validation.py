import pathlib
import numpy as np

from glassesTools import annotation, fixation_classification, naming as gt_naming, process_pool, validation
from glassesTools.validation import assign_intervals, compute_offsets

from .. import config, episode, naming, plane, process, session


stopAllProcessing = False
def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path=None, progress_indicator: process_pool.JobProgress=None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('steps')
    progress_indicator.set_start_time_to_now()

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

    # prep progress indicator
    total = len(planes)*(2+len(episodes)*3)
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(int(total/200), int(total/200))
    progress_indicator.update(n=0)  # ensure a complete hover text appears before first processing step is finished

    # per plane, run the glassesValidator steps
    for p in planes:
        plane_def = [pl for pl in study_config.planes if pl.name==p][0]
        if plane_def.type!=plane.Type.GlassesValidator:
            raise ValueError(f'Plane {p} is not a glassesValidator plane, cannot be used for validation')
        validation_plane = plane.get_plane_from_definition(plane_def, config_dir/p)

        plot_limits = [[validation_plane.bbox[0]-validation_plane.marker_size, validation_plane.bbox[2]+validation_plane.marker_size],
                       [validation_plane.bbox[1]-validation_plane.marker_size, validation_plane.bbox[3]+validation_plane.marker_size]]
        background_image = (validation_plane.get_ref_image(as_RGB=True),
                            np.array([validation_plane.bbox[x] for x in (0,2,3,1)]))
        targets = {t_id: np.append(validation_plane.targets[t_id].center, 0.) for t_id in validation_plane.targets}   # get centers of targets

        # find intervals
        if validation_plane.is_dynamic():
            marker_observations_per_target, markers_per_target = validation.dynamic.get_marker_observations(validation_plane, working_dir)
        else:
            fixation_classification.from_plane_gaze(working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                                    episodes,
                                                    working_dir,
                                                    I2MC_settings_override=study_config.validate_I2MC_settings,
                                                    filename_stem=f'{naming.validation_prefix}{p}_fixations',
                                                    plot_limits=plot_limits)
        progress_indicator.update()

        # assign intervals
        for idx,_ in enumerate(episodes):
            if validation_plane.is_dynamic():
                selected_intervals, other_intervals = \
                    assign_intervals.dynamic_markers(marker_observations_per_target,
                                                     markers_per_target,
                                                     working_dir/gt_naming.frame_timestamps_fname,
                                                     episodes[idx],
                                                     study_config.validate_dynamic_skip_first_duration,
                                                     study_config.validate_dynamic_max_gap_duration,
                                                     study_config.validate_dynamic_min_duration)
            else:
                fix_file = working_dir / f'{naming.validation_prefix}{p}_fixations_interval_{idx+1:02d}.tsv'
                selected_intervals, other_intervals = \
                    assign_intervals.distance(targets,
                                              fix_file,
                                              do_global_shift=study_config.validate_do_global_shift,
                                              max_dist_fac=study_config.validate_max_dist_fac)
            progress_indicator.update()

            # plot output
            assign_intervals.plot(selected_intervals,
                                  other_intervals,
                                  targets,
                                  working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                  episodes[idx],
                                  working_dir,
                                  filename_stem=f'{naming.validation_prefix}{p}_fixation_assignment',
                                  iteration=idx,
                                  background_image=background_image,
                                  plot_limits=plot_limits)
            progress_indicator.update()

            # store output to file
            assign_intervals.to_tsv(selected_intervals,
                                    working_dir,
                                    filename_stem=f'{naming.validation_prefix}{p}_fixation_assignment',
                                    iteration=idx)
            progress_indicator.update()

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
        progress_indicator.update()

    # update state
    session.update_action_states(working_dir, process.Action.VALIDATE, process_pool.State.Completed, study_config)