import pathlib
import numpy as np
import pandas as pd
import polars as pl

from glassesTools import data_types, gaze_worldref, naming as gt_naming, plane as gt_plane, pose as gt_pose, process_pool
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

    # get info about targets and other info needed to compute gaze offsets, per plane
    episodes_per_plane:         dict[str, dict[str, list[list[int]]]] = {}
    targets_per_plane:          dict[str, dict[str, list[int]]] = {}
    d_types_per_plane:          dict[str, dict[str, set[data_types.DataType]]] = {}
    allow_d_fallback_per_plane: dict[str, dict[str, bool]] = {}
    viewing_distance_per_plane: dict[str, dict[str, float]] = {}
    for cs in episodes_to_proc:
        nm = cs['name']
        if nm not in viewing_distance_per_plane:
            episodes_per_plane[nm]          = {}
            targets_per_plane[nm]           = {}
            d_types_per_plane[nm]           = {}
            allow_d_fallback_per_plane[nm]  = {}
            viewing_distance_per_plane[nm]  = {}
        for p in cs['gaze_offset_setup']:
            episodes_per_plane[nm][p] = sorted(list(episodes[cs['name']][1]))
            targets_per_plane[nm][p]  = sorted(list(cs['gaze_offset_setup'][p]['which_targets']))
            d_types_per_plane[nm][p]  = set()
            if cs['gaze_offset_setup'][p]['data_types'] is not None:
                d_types_per_plane[nm][p].update(cs['gaze_offset_setup'][p]['data_types'])
            allow_d_fallback_per_plane[nm][p] = cs['gaze_offset_setup'][p]['allow_data_type_fallback']
            viewing_distance_per_plane[nm][p] = cs['gaze_offset_setup'][p].get('viewing_distance_mm', None)

    # get information about the planes
    all_planes = {p for nm in episodes_per_plane for p in episodes_per_plane[nm]}
    planes: dict[str,gt_plane.TargetPlane] = {}
    for p in all_planes:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        planes[p] = plane.get_plane_from_definition(p_def, config_dir/p)
        # for validation planes, fall back to viewing distance from config if not specified in the gaze offset setup
        for nm in viewing_distance_per_plane:
            if p in viewing_distance_per_plane[nm] and viewing_distance_per_plane[nm][p] is None and isinstance(planes[p], val_Plane):
                viewing_distance_per_plane[nm][p] = get_validation_setup(config_dir/p)['distance']*10.

    # load gaze data and poses
    all_episodes_per_plane = {p: [] for p in all_planes}
    for nm in episodes_per_plane:
        for p in episodes_per_plane[nm]:
            all_episodes_per_plane[p].extend(episodes_per_plane[nm][p])
    all_episodes_per_plane = {p: sorted(all_episodes_per_plane[p]) for p in all_episodes_per_plane}
    plane_gazes = {p: gaze_worldref.read_dict_from_file(working_dir / f'{naming.world_gaze_prefix}{p}.tsv', episodes=all_episodes_per_plane[p], ts_column_suffixes=['VOR','']) for p in all_planes}
    poses = {p:gt_pose.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', all_episodes_per_plane[p]) for p in all_planes}
    head_gaze = pd.read_csv(working_dir/gt_naming.gaze_data_fname, delimiter='\t', index_col=False)


    # get first plane gaze
    p = list(plane_gazes.keys())[0]
    has_VOR = plane_gazes[p][list(plane_gazes[p].keys())[0]][0].timestamp_VOR is not None
    extra_suffix = '_VOR' if has_VOR else ''
    # for head_gaze, keep only timestamp and frame_idx columns (and VOR versions, if available)
    head_gaze_cols = ['timestamp','frame_idx']
    if 'timestamp_VOR' in head_gaze.columns:
        head_gaze_cols.extend(['timestamp_VOR','frame_idx_VOR'])
    head_gaze = head_gaze[head_gaze_cols].set_index('timestamp'+extra_suffix)

    # get target positions on the plane(s), in mm
    targets                 = {nm: {p: {t_id: np.append(planes[p].targets[t_id].center, 0.                               ) for t_id in targets_per_plane[nm][p]} for p in targets_per_plane[nm]} for nm in episodes_per_plane}
    targets_for_homography  = {nm: {p: {t_id: np.append(planes[p].targets[t_id].center, viewing_distance_per_plane[nm][p]) for t_id in targets_per_plane[nm][p]} for p in targets_per_plane[nm]} for nm in episodes_per_plane}

    # prep progress indicator
    total = sum(e[1]-e[0]+1 for nm in episodes_per_plane for p in episodes_per_plane[nm] for e in episodes_per_plane[nm][p])
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(step:=min(50,int(total/200)), step)

    # per plane, per target, compute gaze offsets from target
    for p in all_planes:
        df: pd.DataFrame|None = None
        for nm in episodes_per_plane:
            if not p in episodes_per_plane[nm]:
                continue
            for e in episodes_per_plane[nm][p]:
                this_gaze = {k:v for (k,v) in plane_gazes[p].items() if k>=e[0] and k<=e[1]}
                if not this_gaze:
                    raise RuntimeError(f'There is no gaze data on the plane for episode "{e}" on plane "{p}", cannot proceed. This may be because there was no gaze during this interval or because the plane was not detected.')

                # compute offsets
                # check what data quality types we should output. Go with good defaults
                # first see what we have available
                d_have = data_types.get_available_data_types(this_gaze)
                # then determine, based on what user requests, what we will output
                d_types = data_types.select_data_types_to_use(d_types_per_plane[nm][p], d_have, allow_d_fallback_per_plane[nm][p])

                frame_idxs, timestamps, offsets = data_types.calculate_gaze_angles_to_point(
                    this_gaze,
                    poses[p],
                    targets[nm][p],
                    d_types,
                    targets_for_homography[nm][p],
                    viewing_distance_per_plane[nm][p]
                    )
                # prepare output data frame
                new_rows = pd.DataFrame({'frame_idx'+extra_suffix: frame_idxs}, index=pd.Index(timestamps, name='timestamp'+extra_suffix))
                new_rows['episode'] = nm
                if df is None:
                    df = new_rows
                else:
                    df = pd.concat([df, new_rows])

                for t in offsets:
                    for d_type in offsets[t]:
                        col_names = [f'{x}_target_{t}_{d_type.name}' for x in ('offset', 'offset_x', 'offset_y')]
                        df.loc[timestamps, col_names] = offsets[t][d_type]
                        progress_indicator.update(n=e[1]-e[0]+1)

        # merge with original timestamps so that we have nan in the signal for missing gaze timestamps
        # select all coded intervals that are configured for this plane
        selector = np.zeros(len(head_gaze), dtype=bool)
        for nm in episodes_per_plane:
            if p in episodes_per_plane[nm]:
                selector |= (head_gaze['frame_idx'+extra_suffix]>=episodes_per_plane[nm][p][0][0]) & (head_gaze['frame_idx'+extra_suffix]<=episodes_per_plane[nm][p][-1][1])
        hg = head_gaze.loc[selector]
        # combine to add missing rows
        if has_VOR:
            # also add non-VOR timestamps and frame_idxs, plus need some special handling to preserve integer type
            frame_idx_VOR = df.pop('frame_idx_VOR').combine_first(hg.pop('frame_idx_VOR'))
            # merge in the original timestamps and frame indexes (and adds missing rows)
            df = df.merge(hg[['timestamp', 'frame_idx']], how='outer', left_index=True, right_index=True)
            # Combine back in the VOR column
            df['frame_idx_VOR'] = frame_idx_VOR
        else:
            df = df.merge(pd.DataFrame(index=hg.index), how='outer', left_index=True, right_index=True)

        # store to file
        df = pl.from_pandas(df, include_index=True)
        df.write_csv(working_dir / f'{naming.gaze_offset_prefix}{p}.tsv', separator='\t', null_value='nan', float_precision=8)

    # update state
    session.update_action_states(working_dir, process.Action.COMPUTE_GAZE_OFFSETS, process_pool.State.Completed, study_config)