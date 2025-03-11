import pathlib
import shutil
import numpy as np
import pandas as pd
import polars as pl
from collections import defaultdict

from glassesTools import annotation, gaze_worldref, naming as gt_naming

from .. import config, episode, marker, naming, process, session


def run(working_dir: str|pathlib.Path, export_path: str|pathlib.Path, to_export: list[str], config_dir: str|pathlib.Path = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    export_path = pathlib.Path(export_path)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir}, **study_settings)
    # get session info
    session_info = session.Session.from_definition(study_config.session_def, working_dir)
    all_recs    = [r for r in session_info.recordings]
    et_recs     = [r for r in session_info.recordings if session_info.recordings[r].definition.type==session.RecordingType.Eye_Tracker]

    if 'planeGaze' in to_export:
        export_plane_gaze(export_path, working_dir, study_config, et_recs)

    if 'gaze_overlay_video' in to_export:
        export_gazeOverlay_video(export_path, working_dir, et_recs)

    if 'mapped_gaze_video' in to_export:
        export_mappedGaze_video(export_path, working_dir, all_recs)

    # update state
    session.update_action_states(working_dir, process.Action.EXPORT_TRIALS, process.State.Completed, study_config)


def export_plane_gaze(export_path: pathlib.Path, working_dir: pathlib.Path, study_config: config.Study, recs: list[str]):
    if annotation.Event.Trial not in study_config.planes_per_episode:
        raise ValueError('No planes are specified for mapping gaze to during trials, no gaze-on-plane data to export')

    planes = list(study_config.planes_per_episode[annotation.Event.Trial])

    # per recording, read the relevant files and put them all together
    for r in recs:
        # get trial coding
        # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
        if study_config.sync_ref_recording and r!=study_config.sync_ref_recording:
            episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / study_config.sync_ref_recording / naming.coding_file), study_config.episodes_to_code)
            subset_var = 'frame_idx_ref'
        else:
            episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / r / naming.coding_file), study_config.episodes_to_code)
            subset_var = 'frame_idx'
        if annotation.Event.Trial not in episodes or not episodes[annotation.Event.Trial]:
            raise RuntimeError(f'No {annotation.Event.Trial.value} episodes found in the coding file, nothing to export')
        episodes = episodes[annotation.Event.Trial]

        # get all gaze data
        plane_gazes = {p:pd.read_csv(working_dir / r / f'{naming.world_gaze_prefix}{p}.tsv',sep='\t', dtype=defaultdict(lambda: float, **gaze_worldref.Gaze._non_float)) for p in planes}

        # throw away unwanted columns
        if not study_config.export_output3D:
            for p in plane_gazes:
                # throw away all columns starting with gazePosCam or gazeOriCam
                plane_gazes[p] = plane_gazes[p].drop(columns=[c for c in plane_gazes[p].columns if c.startswith('gazePosCam') or c.startswith('gazeOriCam')])
        if not study_config.export_output2D:
            for p in plane_gazes:
                # throw away all columns starting with gazePosPlane2D
                plane_gazes[p] = plane_gazes[p].drop(columns=[c for c in plane_gazes[p].columns if c.startswith('gazePosPlane2D')])

        # rename putting plane name in there so that names are unique
        for p in plane_gazes:
            plane_gazes[p] = plane_gazes[p].rename(columns={c:f'{c[:7]}_{p}_{c[7:]}' for c in plane_gazes[p].columns if c.startswith('gazePos') or c.startswith('gazeOri')})

        # now merge
        for i in range(1,len(planes)):
            # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
            plane_gazes[planes[0]] = plane_gazes[planes[0]].merge(plane_gazes[planes[i]], how='outer')
        plane_gazes = plane_gazes[planes[0]]

        # if there are individual markers, add them
        # load
        markers = {m.id: marker.load_file(m.id, working_dir / r) for m in study_config.individual_markers}
        # recode to presence/absence if wanted
        if study_config.export_only_code_marker_presence:
            markers = marker.code_marker_for_presence(markers, allow_failed=True)
        else:
            # rename columns to unique names
            for i in markers:
                markers[i] = markers[i].rename(columns={c:f'marker_{i}_{c}' for c in markers[i].columns if c not in ['frame_idx']})

        # merge
        for i in markers:
            # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
            plane_gazes = plane_gazes.merge(markers[i], how='left', on='frame_idx')
        # correct missing values in presence column to false
        if study_config.export_only_code_marker_presence:
            for i in markers:
                plane_gazes[f'marker_{i}_presence'] = plane_gazes[f'marker_{i}_presence'].notnull()

        # add scene and reference camera timestamp info, if present
        ts = pd.read_csv(working_dir / r / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'timestamp':'frame_ts'})
        plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx')
        to_move = 1
        if 'frame_idx_VOR' in plane_gazes.columns:
            ts = ts.rename(columns={'frame_idx':'frame_idx_VOR','frame_ts':'frame_ts_VOR'})
            plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_VOR')
            to_move += 1
        if 'frame_idx_ref' in plane_gazes.columns:
            ts = pd.read_csv(working_dir / study_config.sync_ref_recording / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'frame_idx':'frame_idx_ref','timestamp':'frame_ts_ref','timestamp_stretched':'frame_ts_ref_stretched'})
            plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_ref')
            to_move += len([c for c in ts.columns if c.startswith('frame_ts_')])
        # reorder to get ts columns in the right place
        cols= plane_gazes.columns.to_list()
        idx = max([cols.index(c) for c in cols if c.startswith('frame_idx')])+1
        cols= cols[:idx] + cols[-to_move:] + cols[idx:-to_move]
        plane_gazes = plane_gazes[cols]

        # add trial numbers
        idx = max([cols.index(c) for c in cols if c.startswith('frame_ts')])+1
        plane_gazes.insert(idx,'trial',np.int32(-1))
        for i,e in enumerate(episodes):
            sel = (plane_gazes[subset_var] >= e[0]) & (plane_gazes[subset_var] <= e[1])
            plane_gazes.loc[sel,'trial'] = i+1

        # store
        # write into df (use polars as that library saves to file waaay faster)
        plane_gazes = pl.from_pandas(plane_gazes)
        plane_gazes.write_csv(export_path / f'{naming.gaze_export_name}_{working_dir.name}_{r}.tsv', separator='\t', null_value='nan', float_precision=8)

def export_gazeOverlay_video(export_path: pathlib.Path, working_dir: pathlib.Path, recs: list[str]):
    for r in recs:
        inFile = working_dir/r/gt_naming.gaze_overlay_video_file
        if not inFile.is_file():
            continue
        shutil.copy2(inFile, export_path / f'gazeOverlay_{working_dir.name}_{r}.mp4')

def export_mappedGaze_video(export_path: pathlib.Path, working_dir: pathlib.Path, recs: list[str]):
    for r in recs:
        inFile = working_dir/r/naming.mapped_gaze_video
        if not inFile.is_file():
            continue
        shutil.copy2(inFile, export_path / f'mappedGaze_{working_dir.name}_{r}.mp4')