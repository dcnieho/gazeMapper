import pathlib
import numpy as np
import pandas as pd
import polars as pl
from collections import defaultdict

from glassesTools import gaze_worldref, marker as gt_marker

from .. import config, episode, marker, naming, session


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, output3D = False, output2D = True, only_code_marker_presence = True):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get info about the study it is a part of
    study_config = config.Study.load_from_json(config_dir)
    assert episode.Event.Trial in study_config.planes_per_episode, 'No planes are specified for mapping gaze to during trials, nothing to export'
    planes = study_config.planes_per_episode[episode.Event.Trial]

    # get session info
    session_info = session.Session.load_from_json(working_dir)
    recs    = [r for r in session_info.recordings if session_info.recordings[r].defition.type==session.RecordingType.EyeTracker]

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
        assert episode.Event.Trial in episodes and episodes[episode.Event.Trial], f'No {episode.Event.Trial.value} episodes found in the coding file, nothing to export'
        episodes = episodes[episode.Event.Trial]

        # get all gaze data
        plane_gazes = {p:pd.read_csv(working_dir / r / f'{naming.world_gaze_prefix}{p}.tsv',sep='\t', dtype=defaultdict(lambda: float, **gaze_worldref.Gaze._non_float)) for p in planes}

        # throw away unwanted columns
        if not output3D:
            for p in plane_gazes:
                # throw away all columns starting with gazePosCam or gazeOriCam
                plane_gazes[p] = plane_gazes[p].drop(columns=[c for c in plane_gazes[p].columns if c.startswith('gazePosCam') or c.startswith('gazeOriCam')])
        if not output2D:
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
        m_files = {m.id: working_dir / r / f'{naming.marker_pose_prefix}{m.id}.tsv' for m in study_config.individual_markers}
        markers = {m: pd.read_csv(m_files[m],sep='\t', dtype=defaultdict(lambda: float, **gt_marker.Pose._non_float)) for m in m_files if m_files[m].is_file()}
        # recode to presence/absence if wanted
        if only_code_marker_presence:
            markers = marker.code_marker_for_presence(markers)
        else:
            # rename columns to unique names
            for i in markers:
                markers[i] = markers[i].rename(columns={c:f'marker_{i}_{c}' for c in markers[i].columns if c not in ['frame_idx']})

        # merge
        for i in markers:
            # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
            plane_gazes = plane_gazes.merge(markers[i], how='left', on='frame_idx')
        # correct missing values in presence column to false
        if only_code_marker_presence:
            for i in markers:
                plane_gazes[f'marker_{i}_presence'] = plane_gazes[f'marker_{i}_presence'].notnull()

        # add scene and reference camera timestamp info, if present
        ts = pd.read_csv(working_dir / r / 'frameTimestamps.tsv',sep='\t').rename(columns={'timestamp':'frame_ts'})
        plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx')
        to_move = 1
        if 'frame_idx_VOR' in plane_gazes.columns:
            ts = ts.rename(columns={'frame_idx':'frame_idx_VOR','frame_ts':'frame_ts_VOR'})
            plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_VOR')
            to_move += 1
        if 'frame_idx_ref' in plane_gazes.columns:
            ts = pd.read_csv(working_dir / study_config.sync_ref_recording / 'frameTimestamps.tsv',sep='\t').rename(columns={'frame_idx':'frame_idx_ref','timestamp':'frame_ts_ref'}).drop(columns=['timestamp_stretched'],errors='ignore')
            plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_ref')
            to_move += 1
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
        plane_gazes.write_csv(working_dir / f'{naming.plane_pose_prefix}{r}.tsv', separator='\t', null_value='nan', float_precision=8)