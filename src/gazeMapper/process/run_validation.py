import pathlib
import numpy as np
import pandas as pd
from collections import defaultdict

from glassesTools import annotation, fixation_classification, marker as gt_marker, naming as gt_naming, process_pool
from glassesTools.validation import assign_intervals, compute_offsets

from .. import config, episode, marker, naming, plane, process, session


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
        validation_plane = plane.get_plane_from_definition(plane_def, config_dir/p)

        plot_limits = [[validation_plane.bbox[0]-validation_plane.marker_size, validation_plane.bbox[2]+validation_plane.marker_size],
                       [validation_plane.bbox[1]-validation_plane.marker_size, validation_plane.bbox[3]+validation_plane.marker_size]]
        background_image = (validation_plane.get_ref_image(as_RGB=True),
                            np.array([validation_plane.bbox[x] for x in (0,2,3,1)]))
        targets = {t_id: np.append(validation_plane.targets[t_id].center, 0.) for t_id in validation_plane.targets}   # get centers of targets

        # find intervals
        if validation_plane.is_dynamic():
            # organize markers
            markers_per_target: dict[int,list[gt_marker.MarkerID]] = defaultdict(list)
            for m in validation_plane.dynamic_markers:
                t = validation_plane.dynamic_markers[m][0]
                markers_per_target[t].append(gt_marker.MarkerID(m, validation_plane.aruco_dict_id))
            markers_per_target = dict(markers_per_target)   # get rid of defaultdict now its no longer needed so we get normal indexing
            all_marker_ids = [m for ms in markers_per_target for m in markers_per_target[ms]]
            # for each target, check at least one of the marker files exists
            for t in markers_per_target:
                missing = [not marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir).is_file() for m in markers_per_target[t]]
                if all(missing):
                    file_missing = [marker.get_file_name(m.m_id, m.aruco_dict_id, None) for m in markers_per_target[t]]
                    missing_str  = '\n- '.join(file_missing)
                    raise FileNotFoundError(f'None of the marker files for target {t} were found:\n- {missing_str}')
                # remove missing from list of markers to load
                if any(missing):
                    for i,m in enumerate(missing):
                        if not m:
                            continue
                        all_marker_ids.remove(markers_per_target[t][i])
            # load all markers and recode so we just have a boolean indicating when markers are present
            marker_observations = {m: marker.load_file(m.m_id, m.aruco_dict_id, working_dir) for m in all_marker_ids}
            marker_observations = {m: gt_marker.code_for_presence(marker_observations[m], allow_failed=True) for m in marker_observations if not marker_observations[m].empty}
            # marker presence signal only contains marker detections (True). We need to fill the gaps in between detections with False (not detected) so we have a continuous signal without gaps
            marker_observations = {m: gt_marker.expand_detection(marker_observations[m], fill_value=False) for m in marker_observations}
            # also need frame timestamps as intervals are expressed in time, not as frame indices. Add a timestamp column based on scene video timestamps
            timestamps  = pd.read_csv(working_dir/gt_naming.frame_timestamps_fname, delimiter='\t', index_col='frame_idx')
            ts_col      = 'timestamp_stretched' if 'timestamp_stretched' in timestamps else 'timestamp'
            for m in marker_observations:
                marker_observations[m]['timestamp'] = timestamps.loc[marker_observations[m]['frame_idx'],ts_col].reset_index(drop=True)
        else:
            fixation_classification.from_plane_gaze(working_dir/f'{naming.world_gaze_prefix}{p}.tsv',
                                                    episodes,
                                                    working_dir,
                                                    I2MC_settings_override=study_config.validate_I2MC_settings,
                                                    filename_stem=f'{naming.validation_prefix}{p}_fixations',
                                                    plot_limits=plot_limits)

        # assign intervals
        for idx,_ in enumerate(episodes):
            if validation_plane.is_dynamic():
                selected_intervals, other_intervals = \
                    assign_intervals.dynamic_markers(markers_per_target,
                                                     marker_observations,
                                                     episodes[idx],
                                                     study_config.validate_dynamic_skip_first_duration,
                                                     study_config.validate_dynamic_max_gap_duration)
            else:
                fix_file = working_dir / f'{naming.validation_prefix}{p}_fixations_interval_{idx+1:02d}.tsv'
                selected_intervals, other_intervals = \
                    assign_intervals.distance(targets,
                                              fix_file,
                                              do_global_shift=study_config.validate_do_global_shift,
                                              max_dist_fac=study_config.validate_max_dist_fac)
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
            # store output to file
            assign_intervals.to_tsv(selected_intervals,
                                    working_dir,
                                    filename_stem=f'{naming.validation_prefix}{p}_fixation_assignment',
                                    iteration=idx)

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
    session.update_action_states(working_dir, process.Action.VALIDATE, process_pool.State.Completed, study_config)