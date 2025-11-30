import pathlib
import numpy as np
import pandas as pd
import polars as pl

from glassesTools import data_types, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, pose as gt_pose, process_pool
from glassesTools.validation import Plane as val_Plane
from glassesTools.validation.config import get_validation_setup

from .. import config, episode, naming, plane, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('samples')
    progress_indicator.set_start_time_to_now()

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run gaze_offset on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    episodes_to_proc = process.get_specific_event_types(study_config, check_specific_fields=['gaze_offset_setup'])
    if not episodes_to_proc:
        raise ValueError('No episodes configured for gaze offset computation (need at least one episode with planes defined)')

    # get episodes for which to compute gaze offsets from targets
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {cs['name'] for cs in episodes_to_proc})[0]

    # we transform to map to plane for validate and trial episodes, set it up
    episodes_per_plane: dict[str, list[list[int]]] = {}
    targets_per_plane: dict[str, set[int]] = {}
    d_types_per_plane: dict[str, set[data_types.DataType]] = {}
    allow_d_fallback_per_plane: dict[str, bool] = {}
    viewing_distance_per_plane: dict[str, dict[str, float]] = {}
    for cs in episodes_to_proc:
        nm = cs['name']
        if nm not in viewing_distance_per_plane:
            viewing_distance_per_plane[nm] = {}
        for p in cs['gaze_offset_setup']['which_targets']:
            if p not in episodes_per_plane:
                episodes_per_plane[p] = []
                targets_per_plane[p] = set()
                d_types_per_plane[p] = set()
                allow_d_fallback_per_plane[p] = False
                viewing_distance_per_plane[nm][p] = {}
            episodes_per_plane[p].extend(episodes[cs['name']][1])
            targets_per_plane[p].update(cs['gaze_offset_setup']['which_targets'][p])
            if cs['gaze_offset_setup']['data_types'] is not None:
                d_types_per_plane[p].update(cs['gaze_offset_setup']['data_types'])
            allow_d_fallback_per_plane[p] |= cs['gaze_offset_setup']['allow_data_type_fallback']
            viewing_distance_per_plane[nm][p] = cs['gaze_offset_setup'].get('viewing_distance_mm', None)
    # sort episodes per plane by start frame, and sort viewing distance using the same order
    episodes_per_plane = {p:sorted(episodes_per_plane[p], key = lambda x: x[0]) for p in episodes_per_plane}
    targets_per_plane = {p:sorted(list(targets_per_plane[p])) for p in targets_per_plane}
    viewing_distance_per_plane = {nm: {p: viewing_distance_per_plane[nm][p] for p in episodes_per_plane} for nm in viewing_distance_per_plane}
    planes: dict[str,gt_plane.TargetPlane] = {}
    for p in episodes_per_plane:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        planes[p] = plane.get_plane_from_definition(p_def, config_dir/p)

    # load gaze data and poses
    plane_gazes = {p: gaze_worldref.read_dict_from_file(working_dir / f'{naming.world_gaze_prefix}{p}.tsv', episodes=episodes_per_plane[p], ts_column_suffixes=['VOR','']) for p in episodes_per_plane}
    poses = {p:gt_pose.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', episodes_per_plane[p]) for p in episodes_per_plane}

    # get target positions on the plane(s), in mm
    targets = {p: {t_id: np.append(planes[p].targets[t_id].center, 0.) for t_id in targets_per_plane[p]} for p in targets_per_plane}
    viewing_distance = {p: get_validation_setup(config_dir/p)['distance']*10. for p in targets_per_plane if isinstance(planes[p], val_Plane)}
    targets_for_homography = {p: {t_id: np.append(planes[p].targets[t_id].center, viewing_distance[p]) for t_id in targets_per_plane[p]} for p in targets_per_plane if isinstance(planes[p], val_Plane)}

    # prep progress indicator
    total = sum(len(plane_gazes[p][f]) for p in poses for f in poses[p] if f in plane_gazes[p])
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(step:=min(50,int(total/200)), step)

    # per plane, per target, compute gaze offsets from target
    for p in episodes_per_plane:
        df: pd.DataFrame|None = None
        for e in episodes_per_plane[p]:
            this_gaze = {k:v for (k,v) in plane_gazes[p].items() if k>=e[0] and k<=e[1]}
            if not this_gaze:
                raise RuntimeError(f'There is no gaze data on the glassesValidator surface for episode "{e}" on plane "{p}", cannot proceed. This may be because there was no gaze during this interval or because the plane was not detected.')

            # compute offsets
            # check what data quality types we should output. Go with good defaults
            # first see what we have available
            d_have = data_types.get_available_data_types(this_gaze)
            # then determine, based on what user requests, what we will output
            d_types = data_types.select_data_types_to_use(d_types_per_plane[p], d_have, allow_d_fallback_per_plane[p])

            frame_idxs, timestamps, offsets = data_types.calculate_gaze_angles_to_point(
                this_gaze,
                poses[p],
                targets[p],
                d_types,
                targets_for_homography[p] if p in viewing_distance else None,
                viewing_distance[p] if p in viewing_distance else None
                )
            # prepare output data frame
            new_rows = pd.DataFrame({'frame_idx': frame_idxs}, index=pd.Index(timestamps, name='timestamp'))
            if df is None:
                df = new_rows
            else:
                df = pd.concat([df, new_rows])

            for t in offsets:
                for d_type in offsets[t]:
                    col_names = [f'{x}_target_{t}_{d_type.name}' for x in ('offset', 'offset_x', 'offset_y')]
                    df.loc[timestamps, col_names] = offsets[t][d_type]
                    progress_indicator.update(n=len(frame_idxs))

        # store to file
        df = pl.from_pandas(df, include_index=True)
        df.write_csv(working_dir / f'{naming.gaze_offset_prefix}{p}.tsv', separator='\t', null_value='nan', float_precision=8)

    # update state
    session.update_action_states(working_dir, process.Action.COMPUTE_GAZE_OFFSETS, process_pool.State.Completed, study_config)