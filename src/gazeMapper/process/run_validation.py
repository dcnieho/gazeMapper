import pathlib
import numpy as np

from glassesTools import annotation, fixation_classification, naming as gt_naming, process_pool, validation
from glassesTools.validation import assign_intervals, compute_offsets

from .. import config, episode, naming, plane, process, session


stopAllProcessing = False
def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None=None, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
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
    val_events = process.get_specific_event_types(study_config, annotation.EventType.Validate)
    if not val_events:
        raise ValueError('No validation events are configured for the study, nothing to process')

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run run_validation on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get interval(s) coded to be analyzed, if any
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / naming.coding_file), [cs['name'] for cs in val_events])
    if not any(episodes[e] for e in episodes):
        raise RuntimeError(f'There are no validation episodes coded for session "{working_dir.parent.name}", recording "{working_dir.name}", nothing to process')

    # prep progress indicator
    total = 2*len(episodes) + sum(len(episodes[e]) for e in episodes)*3
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(int(total/200), int(total/200))
    progress_indicator.update(n=0)  # ensure a complete hover text appears before first processing step is finished

    # per plane, run the glassesValidator steps
    for e in episodes:
        # find corresponding coding config
        cs = [cs for cs in val_events if cs['name']==e][0]
        if len(cs['planes'])!=1:
            raise ValueError(f'Validation event "{e}" should be coded for exactly one glassesValidator plane, found {len(cs["planes"])}')
        p = list(cs['planes'])[0]
        plane_def = [pl for pl in study_config.planes if pl.name==p][0]
        if plane_def.type!=plane.Type.GlassesValidator:
            raise ValueError(f'Plane {p} is not a {plane.Type.GlassesValidator.value} plane, cannot be used for validation')
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
                                                    episodes[e],
                                                    working_dir,
                                                    I2MC_settings_override=cs['validation_setup']['I2MC_settings'],
                                                    filename_stem=f'{naming.validation_prefix}{e}_fixations',
                                                    plot_limits=plot_limits)
        progress_indicator.update()

        # assign intervals
        for idx,_ in enumerate(episodes[e]):
            if validation_plane.is_dynamic():
                selected_intervals, other_intervals = \
                    assign_intervals.dynamic_markers(marker_observations_per_target,
                                                    markers_per_target,
                                                    working_dir/gt_naming.frame_timestamps_fname,
                                                    episodes[e][idx],
                                                    cs['validation_setup']['dynamic_skip_first_duration'],
                                                    cs['validation_setup']['dynamic_max_gap_duration'],
                                                    cs['validation_setup']['dynamic_min_duration'])
            else:
                fix_file = working_dir / f'{naming.validation_prefix}{e}_fixations_interval_{idx+1:02d}.tsv'
                selected_intervals, other_intervals = \
                    assign_intervals.distance(targets,
                                            fix_file,
                                            do_global_shift=cs['validation_setup']['do_global_shift'],
                                            max_dist_fac=cs['validation_setup']['max_dist_fac'])
            progress_indicator.update()

            # plot output
            assign_intervals.plot(selected_intervals,
                                other_intervals,
                                targets,
                                working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                episodes[e][idx],
                                working_dir,
                                filename_stem=f'{naming.validation_prefix}{e}_fixation_assignment',
                                iteration=idx,
                                background_image=background_image,
                                plot_limits=plot_limits)
            progress_indicator.update()

            # store output to file
            assign_intervals.to_tsv(selected_intervals,
                                    working_dir,
                                    filename_stem=f'{naming.validation_prefix}{e}_fixation_assignment',
                                    iteration=idx)
            progress_indicator.update()

        compute_offsets.compute(working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                working_dir/f'{naming.plane_pose_prefix}{p}.tsv',
                                working_dir/f'{naming.validation_prefix}{e}_fixation_assignment.tsv',
                                episodes[e],
                                targets,
                                validation_plane.config['distance']*10.,    # cm -> mm
                                working_dir,
                                filename=f'{naming.validation_prefix}{e}_data_quality.tsv',
                                d_types=cs['validation_setup']['data_types'],
                                allow_data_type_fallback=cs['validation_setup']['allow_data_type_fallback'],
                                include_data_loss=cs['validation_setup']['include_data_loss'])
        progress_indicator.update()

    # update state
    session.update_action_states(working_dir, process.Action.VALIDATE, process_pool.State.Completed, study_config)