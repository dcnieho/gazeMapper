import pathlib
import shutil
import copy
import numpy as np
import pandas as pd
import polars as pl
import cv2
from collections import defaultdict
import dataclasses
import csv
import warnings

from ETDQualitizer import vector_to_Fick

from glassesTools import annotation, data_types, gaze_headref, gaze_worldref, marker as gt_marker, naming as gt_naming, ocv, process_pool, transforms
from glassesTools.validation import export as val_export

from .. import config, episode, naming, marker_assembly, process, session, type_utils
from . import _pose_files


@dataclasses.dataclass
class OptionBase:
    available: bool
    do_it: bool
    sess: list[str]
    recs: list[str]

@dataclasses.dataclass
class PlaneGaze(OptionBase):
    include_unit_header_row: bool = True
    include_head_ref_gaze: bool = True
    include_head_ref_gaze_Fick_angles: bool = False
    include_plane_pose: bool = False
    include_2D: bool = True
    include_3D: bool = False
    # TODO: something about specific fields/data types
    plane_gaze_types: list[str] = dataclasses.field(default_factory=lambda: [])
    include_markers: bool = True
    markers_only_presence: bool = True
    markers_compress: bool = True
    planes: list[str] = dataclasses.field(default_factory=lambda: [])
    episodes: list[str] = dataclasses.field(default_factory=lambda: [])
@dataclasses.dataclass
class HeadPose(OptionBase):
    include_unit_header_row: bool = True
    include_Fick_angles: bool = True
    planes: list[str] = dataclasses.field(default_factory=lambda: [])
    episodes: list[str] = dataclasses.field(default_factory=lambda: [])
@dataclasses.dataclass
class GazeOffset(OptionBase):
    include_unit_header_row: bool = True
    planes: list[str] = dataclasses.field(default_factory=lambda: [])
    episodes: list[str] = dataclasses.field(default_factory=lambda: [])
@dataclasses.dataclass
class ValidationEpisode(OptionBase):
    df: pd.DataFrame
    d_types: dict[data_types.DataType,bool] = dataclasses.field(default_factory=lambda: {})
    targets: dict[int,bool] = dataclasses.field(default_factory=lambda: {})
    targets_avg: bool = False
    include_data_loss: bool = False
    include_unit_header_row: bool = True
@dataclasses.dataclass
class Validation:
    available: bool
    do_it: bool
    episodes: dict[str, ValidationEpisode] = dataclasses.field(default_factory=lambda: {})
@dataclasses.dataclass
class GazeOverlayVideo(OptionBase):
    recs: list[str] = dataclasses.field(default_factory=lambda: [])

@dataclasses.dataclass
class ExportConfig:
    plane_gaze: PlaneGaze = dataclasses.field(default_factory=lambda: PlaneGaze(False, False, [], []))
    head_pose: HeadPose = dataclasses.field(default_factory=lambda: HeadPose(False, False, [], []))
    gaze_offsets: GazeOffset = dataclasses.field(default_factory=lambda: GazeOffset(False, False, [], []))
    validation: Validation = dataclasses.field(default_factory=lambda: Validation(False, False))
    trial_data_quality: OptionBase = dataclasses.field(default_factory=lambda: OptionBase(False, False, [], []))
    eye_tracker_sync: OptionBase = dataclasses.field(default_factory=lambda: OptionBase(False, False, [], []))
    gaze_overlay_video: GazeOverlayVideo = dataclasses.field(default_factory=lambda: GazeOverlayVideo(False, False, [], []))
    mapped_gaze_video: OptionBase = dataclasses.field(default_factory=lambda: OptionBase(False, False, [], []))


def _invert_plane_pose(rotation_vec: np.ndarray, translation_vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    plane_to_camera_rotation = cv2.Rodrigues(np.asarray(rotation_vec, dtype=float).reshape(3, 1))[0]
    camera_to_plane_rotation = plane_to_camera_rotation.T
    camera_to_plane_translation = -camera_to_plane_rotation @ np.asarray(translation_vec, dtype=float).reshape(3)
    return cv2.Rodrigues(camera_to_plane_rotation)[0].reshape(3), camera_to_plane_translation


export_config_doc = {
    'plane_gaze': type_utils.GUIDocInfo('Mapped gaze data', 'Export gaze mapped to planes, with optional gaze, head-pose, and marker data.', {
        'include_unit_header_row': type_utils.GUIDocInfo('Include units row', 'Add a second header row describing the units or meaning of each column.'),
        'include_head_ref_gaze': type_utils.GUIDocInfo('Include eye-tracker gaze fields', 'Append the available head-referenced gaze fields from the eye tracker\'s gaze file to each mapped-gaze export. These are eye-tracker measurements, not gaze positions mapped onto a plane and, depending on availability, include: 2D gaze position on the scene image (gaze_pos_vid), 3D/world gaze point (gaze_pos_3d), and left/right eye gaze direction vectors (gaze_dir_l/r) with their origins (gaze_ori_l/r). Also provided are timestamps and frame indices, including synchronized scene-camera timing when available.'),
        'include_head_ref_gaze_Fick_angles': type_utils.GUIDocInfo('Include eye-tracker gaze angles', 'Add azimuth and elevation angles computed from the 2D gaze position on the scene image, relative to straight ahead in the scene camera. Requires eye-tracker gaze fields to be included.'),
        'include_plane_pose': type_utils.GUIDocInfo('Include plane pose', 'Add each mapped plane\'s stored pose to this gaze export. The pose maps plane coordinates into the scene-camera coordinate system.'),
        'include_2D': type_utils.GUIDocInfo('Include 2D gaze positions', 'Include gaze positions in each plane\'s 2D coordinate system.'),
        'include_3D': type_utils.GUIDocInfo('Include 3D gaze positions', 'Include gaze positions in the scene-camera 3D coordinate system.'),
        'include_markers': type_utils.GUIDocInfo('Include individual markers', 'Add detected individual marker data to the export.'),
        'markers_only_presence': type_utils.GUIDocInfo('Export marker presence only', 'Applies only when Include individual markers is enabled. Export only whether each marker was detected, rather than its full pose data.'),
        'markers_compress': type_utils.GUIDocInfo('Combine marker presence into one column', 'Applies only when Include individual markers and Export marker presence only are enabled. Store detected marker IDs in one comma-separated column instead of as a separate presence column for each marker.'),
    }),
    'head_pose': type_utils.GUIDocInfo('Head pose relative to planes', 'Export the scene-camera (head) pose in each configured plane\'s coordinate system at available pose frames. This is the inverse of the stored plane-to-camera pose.', {
        'include_unit_header_row': type_utils.GUIDocInfo('Include units row', 'Add a second header row describing the units or meaning of each column.'),
        'include_Fick_angles': type_utils.GUIDocInfo('Include Fick angles', 'Include azimuth and elevation of the scene-camera forward direction, expressed in each plane\'s coordinate system.'),
    }),
    'gaze_offsets': type_utils.GUIDocInfo('Gaze offsets', 'Export computed gaze offsets for configured episodes and planes.', {
        'include_unit_header_row': type_utils.GUIDocInfo('Include units row', 'Add a second header row describing the units or meaning of each column.'),
    }),
    'validation': type_utils.GUIDocInfo('Validation data quality', 'Export data-quality metrics from validation episodes.', {
        'targets_avg': type_utils.GUIDocInfo('Average over selected targets', 'Average the selected targets\' metrics together.'),
        'include_data_loss': type_utils.GUIDocInfo('Include data loss', 'Include the data-loss metric in the export. This is unlikely what you want, as it is data loss only during the validation episode, not during your trials. See the Compute data quality action and the Trial data quality export section below (only available if you have run the Compute data quality action).'),
        'include_unit_header_row': type_utils.GUIDocInfo('Include units row', 'Add a second header row describing the units or meaning of each column.'),
    }),
    'trial_data_quality': type_utils.GUIDocInfo('Trial data quality', 'Export computed trial data-quality metrics.'),
    'eye_tracker_sync': type_utils.GUIDocInfo('Eye-tracker to scene-camera synchronization', 'Export the time offset used to align eye-tracker gaze timestamps with the scene-camera video. The TSV contains one row per coded synchronization interval, with session, recording, interval, and offset_t (seconds). This is the offset applied to the eye-tracker\'s gaze data timestamps before associating samples with scene-camera frames.'),
    'gaze_overlay_video': type_utils.GUIDocInfo('Gaze overlay videos', 'Export videos showing eye-tracker gaze overlaid on the scene video.'),
    'mapped_gaze_video': type_utils.GUIDocInfo('Mapped gaze videos', 'Export videos showing gaze mapped to detected planes.'),
}


def prep_export(project_dir: str|pathlib.Path, session_names: list[str], config_dir: str|pathlib.Path|None = None, **study_settings) -> tuple[ExportConfig, bool]:
    project_dir = pathlib.Path(project_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(project_dir)
    config_dir  = pathlib.Path(config_dir)

    # get settings and sessions for the study
    study_config = config.read_study_config_with_overrides(config_dir, **study_settings)
    sessions = session.get_sessions_from_project_directory(project_dir, study_config.session_def)

    # for the sessions, see what we have to export
    export_config = ExportConfig()
    plane_names = {definition.name for definition in study_config.marker_assemblies if isinstance(definition, marker_assembly.Definition_Plane_Aruco)}
    cs_plane_gaze = [cs for cs in process.get_specific_event_types(study_config, check_specific_fields=['marker_assemblies']) if any(name in plane_names for name in cs['marker_assemblies'])]
    cs_gaze_offset = process.get_specific_event_types(study_config, check_specific_fields=['gaze_offset_setup'])
    cs_validation = process.get_specific_event_types(study_config, annotation.EventType.Validate)
    cs_et_sync = process.get_specific_event_types(study_config, check_specific_fields=['sync_setup'])
    val_sess_recs: list[tuple[str,str]] = []
    got_any = False
    for s_name in session_names:
        ss = [s for s in sessions if s.name==s_name]
        if not ss:
            warnings.warn(f'Session {s_name} not found in project, skipping...', process_pool.ProcessingWarning)
            continue
        s = ss[0]
        got_any = True

        def _get_sess_recs_for_action(action: process.Action):
            sess,recs = zip(*[(s.name,r) for r in s.recordings if s.recordings[r].state[action]==process_pool.State.Completed])
            return list(sess), list(recs)

        # plane gaze
        for cs,a,out in zip((cs_plane_gaze, cs_gaze_offset, cs_et_sync, None), (process.Action.GAZE_TO_PLANE, process.Action.COMPUTE_GAZE_OFFSETS, process.Action.SYNC_ET_TO_CAM, process.Action.MAKE_GAZE_OVERLAY_VIDEO), (export_config.plane_gaze, export_config.gaze_offsets, export_config.eye_tracker_sync, export_config.gaze_overlay_video)):
            if (cs is None or cs) and any((s.recordings[r].state[a]==process_pool.State.Completed for r in s.recordings)):
                out.available = True
                out.do_it = True
                sess,recs = _get_sess_recs_for_action(a)
                out.sess.extend(sess)
                out.recs.extend(recs)

        # handle the others that needs special logic
        if cs_validation and any((s.recordings[r].state[process.Action.VALIDATE]==process_pool.State.Completed for r in s.recordings)):
            val_sess_recs.extend([(s.name, r) for r in s.recordings if s.recordings[r].state[process.Action.VALIDATE]==process_pool.State.Completed])
        if s.state[process.Action.MAKE_MAPPED_GAZE_VIDEO]==process_pool.State.Completed:
            export_config.mapped_gaze_video.available = True
            export_config.mapped_gaze_video.do_it = True
            export_config.mapped_gaze_video.sess.extend([s.name for _ in s.recordings])
            export_config.mapped_gaze_video.recs.extend([r for r in s.recordings])  # NB: even though this is done per session, we want to flag all recordings for export, as there is a video for each recording

        for r in s.recordings:
            if s.recordings[r].state[process.Action.COMPUTE_DATA_QUALITY]==process_pool.State.Completed:
                export_config.trial_data_quality.available = True
                export_config.trial_data_quality.do_it = True
                export_config.trial_data_quality.sess.append(s.name)
                export_config.trial_data_quality.recs.append(r)
            rec_dir = project_dir / s.name / r
            if any(_pose_files.get_preferred_plane_pose_file(rec_dir, p)[0].is_file() for p in plane_names):
                export_config.head_pose.available = True
                export_config.head_pose.do_it = True
                export_config.head_pose.sess.append(s.name)
                export_config.head_pose.recs.append(r)

    # if validation export is wanted, prep some of the data for it (data quality info from validation export, which is needed to decide what to include in the export, and also to be included in the export itself)
    if val_sess_recs:
        rec_dirs = [project_dir/s/r for s, r in val_sess_recs]
        for cs in cs_validation:
            nm = cs['name']
            pl = list(cs['marker_assemblies'])[0]
            dq_df, default_dq_type, dq_targets = val_export.collect_data_quality(rec_dirs, {pl:f'{naming.validation_prefix}{nm}_data_quality.tsv'}, col_for_parent='session')
            if dq_df is not None:
                export_config.validation.episodes[nm] = ValidationEpisode(True,True,[],[],dq_df)
                # determine which sessions and recordings we have for this plane
                pairs = (
                    dq_df.index
                    .to_frame(index=False)[['session', 'recording']]
                    .drop_duplicates()
                ).to_numpy().tolist()
                export_config.validation.episodes[nm].sess = [x[0] for x in pairs]
                export_config.validation.episodes[nm].recs = [x[1] for x in pairs]

                # prep config for validation export
                # data quality type
                type_idx = dq_df.index.names.index('type')
                export_config.validation.episodes[nm].d_types = {k:False for k in sorted(list(dq_df.index.levels[type_idx]), key=lambda dq: dq.value)}
                for dq in data_types.DataType:
                    if cs['validation_setup']['data_types'] is not None and dq in cs['validation_setup']['data_types'] and dq in export_config.validation.episodes[nm].d_types:
                        export_config.validation.episodes[nm].d_types[dq] = True
                if not any(export_config.validation.episodes[nm].d_types.values()):
                    export_config.validation.episodes[nm].d_types[default_dq_type] = True

                # targets
                export_config.validation.episodes[nm].targets     = {t:True for t in dq_targets}
                export_config.validation.episodes[nm].targets_avg = False

                # other settings
                export_config.validation.episodes[nm].include_data_loss = cs['validation_setup']['include_data_loss']
        if export_config.validation.episodes:
            export_config.validation.available = True
            export_config.validation.do_it = True

    return export_config, got_any

def run(working_dir: str|pathlib.Path, export_path: str|pathlib.Path, export_config: ExportConfig, config_dir: str|pathlib.Path|None = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    export_path = pathlib.Path(export_path)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir}, **study_settings)

    # now all the actual exports (NB: validation and et_sync are done once for a whole selection, so not handled here as this function is called per session)
    if export_config.plane_gaze.do_it:
        export_plane_gaze(export_path, working_dir, study_config, export_config.plane_gaze)

    if export_config.gaze_offsets.do_it:
        export_gaze_offsets(export_path, working_dir, study_config, export_config.gaze_offsets)

    if export_config.head_pose.do_it:
        export_head_pose(export_path, working_dir, study_config, export_config.head_pose)

    if export_config.gaze_overlay_video.do_it:
        export_gazeOverlay_video(export_path, working_dir, export_config.gaze_overlay_video)

    if export_config.mapped_gaze_video.do_it:
        export_mappedGaze_video(export_path, working_dir, export_config.mapped_gaze_video)

    # update state
    session.update_action_states(working_dir, process.Action.EXPORT_TRIALS, process_pool.State.Completed, study_config)


def export_plane_gaze(export_path: pathlib.Path, working_dir: pathlib.Path, study_config: config.Study, export_config: PlaneGaze):
    plane_definitions = {definition.name: definition for definition in study_config.marker_assemblies if isinstance(definition, marker_assembly.Definition_Plane_Aruco)}
    plane_names = set(plane_definitions)
    cs_plane_gaze = [cs for cs in study_config.coding_setup if any(name in plane_names for name in cs['marker_assemblies'])]
    if not cs_plane_gaze:
        raise ValueError('No events where gaze is mapped to a plane are configured for the study, nothing to process')

    planes = {v for cs in cs_plane_gaze for v in cs['marker_assemblies'] if v in plane_names}

    # per recording, read the relevant files and put them all together
    for r in export_config.recs:
        # check if files needed for export are present, else skip
        if not all((working_dir / r / f'{naming.world_gaze_prefix}{p}.tsv').is_file() for p in planes):
            warnings.warn(f'Not all plane gaze files found for recording {r} in session {working_dir.name}. Skipping...', process_pool.ProcessingWarning)
            continue
        # get trial coding
        # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
        episodes = episode.load_episodes_from_all_recordings(study_config, working_dir/r, {cs['name'] for cs in cs_plane_gaze})[0]
        if not any(episodes[e][1] for e in episodes):
            warnings.warn(f'No {annotation.tooltip_map[annotation.EventType.Trial]} events found in the coding file for recording {r} in session {working_dir.name}. Skipping...', process_pool.ProcessingWarning)
            continue

        # keep track of units or other descriptions for each column
        column_info = {'frame_ts': 'scene camera frame timestamp (ms)'}

        # get all plane-gaze data
        plane_gazes = {p:pd.read_csv(working_dir / r / f'{naming.world_gaze_prefix}{p}.tsv', sep='\t', dtype=defaultdict(lambda: float, **gaze_worldref.Gaze._non_float)) for p in planes}
        cols = set(c for df in plane_gazes.values() for c in df.columns)
        for c in cols:
            if c.startswith('gazePos'):
                column_info[c] = 'mm'
            elif c.startswith('gazeOri'):
                column_info[c] = 'mm'
            elif c == 'frame_idx':
                column_info[c] = 'frame number'
            elif c == 'frame_idx_VOR':
                column_info[c] = 'frame number (after gaze to camera sync)'
            elif c == 'frame_idx_ref':
                column_info[c] = 'frame number (in reference camera video)'
            elif c == 'timestamp':
                column_info[c] = 'ms'
            elif c == 'timestamp_VOR':
                column_info[c] = 'ms (timestamp after gaze to camera sync)'
            elif c == 'timestamp_ref':
                column_info[c] = 'ms (timestamp in clock of reference camera video)'

        # get head-referenced gaze
        if export_config.include_head_ref_gaze:
            head_ref_gaze = pd.read_csv(working_dir / r / f'{gt_naming.gaze_data_fname}', sep='\t', dtype=defaultdict(lambda: float, **gaze_headref.Gaze._non_float))
            cols = head_ref_gaze.columns
            for c in cols:
                if c.startswith('gaze_pos_vid'):
                    column_info[c] = 'pixels'
                elif c.startswith('gaze_pos_3d'):
                    column_info[c] = 'mm'
                elif c.startswith('gaze_ori'):
                    column_info[c] = 'mm'
                elif c.startswith('gaze_dir'):
                    column_info[c] = 'unit vector component'
            if export_config.include_head_ref_gaze_Fick_angles:
                # get w.r.t. straight ahead ([0, 0, 1] in camera coordinates)
                cam=ocv.CameraParams.read_from_file(working_dir / r / gt_naming.scene_camera_calibration_fname)
                # first unproject to get gaze vector from gaze position in pixels on the camera image
                gazew=transforms.unproject_points(head_ref_gaze[['gaze_pos_vid_x', 'gaze_pos_vid_y']].values, cam)
                # then get Fick angles from gaze vector (NB: positive angles are rightward and downward)
                head_ref_gaze['azimuth'], head_ref_gaze['elevation'] = vector_to_Fick(gazew[:,0], gazew[:,1], gazew[:,2])
                column_info.update({'azimuth': 'deg (Fick)', 'elevation': 'deg (Fick)'})

        # throw away unwanted columns
        if not export_config.include_3D:
            for p in plane_gazes:
                # throw away all columns starting with gazePosCam or gazeOriCam
                plane_gazes[p] = plane_gazes[p].drop(columns=[c for c in plane_gazes[p].columns if c.startswith('gazePosCam') or c.startswith('gazeOriCam')])
        if not export_config.include_2D:
            for p in plane_gazes:
                # throw away all columns starting with gazePosPlane2D
                plane_gazes[p] = plane_gazes[p].drop(columns=[c for c in plane_gazes[p].columns if c.startswith('gazePosPlane2D')])

        # rename putting plane name in there so that names are unique
        for p in plane_gazes:
            rename = {c:f'{c[:7]}_{p}_{c[7:]}' for c in plane_gazes[p].columns if c.startswith('gazePos') or c.startswith('gazeOri')}
            plane_gazes[p] = plane_gazes[p].rename(columns=rename)
            # update column info with new names
            for c in rename:
                if c in column_info:
                    column_info[rename[c]] = column_info[c]

        # if there are individual markers, load them so they can be added later
        # load
        if export_config.include_markers:
            markers = {m.id: gt_marker.read_dataframe_from_file(m.id, m.aruco_dict_id, working_dir/r) for m in study_config.individual_markers if gt_marker.get_file_name(m.id, m.aruco_dict_id, working_dir/r).is_file()}
            # recode to presence/absence if wanted
            if export_config.markers_only_presence:
                markers = gt_marker.code_for_presence(markers, allow_failed=True)
                # compress if wanted (turn presence into comma-separated list of present marker ids, and drop the individual columns)
                if export_config.markers_compress:
                    presence_cols = {i:f'marker_{i}_presence' for i in markers}
                    observed: dict[int, set[int]] = {}   # keep track of which markers observed for each frame
                    for i in markers:
                        # get frame indices where this marker is observed
                        frame_indices = markers[i].loc[markers[i][presence_cols[i]],['frame_idx']].values.flatten()
                        for fr in frame_indices:
                            if fr not in observed:
                                observed[fr] = set()
                            observed[fr].add(i)
                    # turn into comma-separated list of observed marker ids
                    markers = pd.DataFrame(
                        [
                            {"frame_idx": k, "markers": ",".join(map(str, sorted(v)))}
                            for k, v in sorted(observed.items())
                        ]
                    )
                    column_info['markers'] = 'observed marker ids (comma-separated)'
            else:
                # rename columns to unique names
                for i in markers:
                    markers[i] = markers[i].rename(columns={c:f'marker_{i}_{c}' for c in markers[i].columns if c not in ['frame_idx']})

        # if head pose is wanted, load it so it can be added later
        plane_poses: dict[str, pd.DataFrame] = {}
        if export_config.include_plane_pose:
            plane_poses = {pl: pd.read_csv(_pose_files.get_preferred_plane_pose_file(working_dir / r, pl)[0], sep='\t') for pl in planes}
            column_info.update({f: 'rotation-vector component (rad)' for f in ('pose_R_vec_x','pose_R_vec_y','pose_R_vec_z')})
            column_info.update({f: 'translation component (plane units)' for f in ('pose_T_vec_x','pose_T_vec_y','pose_T_vec_z')})
            for p in plane_gazes:
                # drop unwanted columns
                plane_poses[p] = plane_poses[p].drop(columns=[c for c in plane_poses[p].columns if c.startswith('homography') or c.endswith('N_points') or c.endswith('reprojection_error')])
                # rename columns to include plane name so that they are unique
                rename = {c:f'{c[:4]}_{p}_{c[5:]}' for c in plane_poses[p].columns if c.startswith('pose')}
                plane_poses[p] = plane_poses[p].rename(columns=rename)
                # update column info with new names
                for c in rename:
                    if c in column_info:
                        column_info[rename[c]] = column_info[c]
                        if c.startswith('pose_T_vec_'):
                            plane_unit = plane_definitions[p].unit or 'plane units'
                            column_info[rename[c]] = f'plane position in scene-camera coordinates ({plane_unit})'

        # for the rest, make a file per coding stream
        ori_plane_gazes = copy.deepcopy(plane_gazes)
        for cs in cs_plane_gaze:
            nm = cs['name']
            if not episodes[nm][1]:
                continue

            # now merge
            this_planes = list(cs['marker_assemblies'])
            plane_gazes = {p:ori_plane_gazes[p].copy() for p in this_planes}
            keys = list(plane_gazes.keys())
            if export_config.include_head_ref_gaze:
                # get all frame_idxs that occur in plane gazes
                frame_idxs = set()
                for p in this_planes:
                    frame_idxs.update(plane_gazes[p]['frame_idx'].values)
                # get head ref gaze for those frame_idxs only
                plane_gazes['__xxhead_refxx__'] = head_ref_gaze[head_ref_gaze['frame_idx'].isin(frame_idxs)].copy()
                keys.insert(0,'__xxhead_refxx__')
            for i in range(1,len(keys)):
                # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
                plane_gazes[keys[0]] = plane_gazes[keys[0]].merge(plane_gazes[keys[i]], how='outer')
            plane_gazes = plane_gazes[keys[0]]

            # merge in markers
            if export_config.include_markers:
                if export_config.markers_only_presence and export_config.markers_compress:
                    plane_gazes = plane_gazes.merge(markers, how='left', on='frame_idx')
                    # replace nan with empty string for frames where no markers are observed
                    plane_gazes['markers'] = plane_gazes['markers'].fillna('')
                else:
                    for i in markers:
                        # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
                        plane_gazes = plane_gazes.merge(markers[i], how='left', on='frame_idx')
                    # correct missing values in presence column to false
                    if export_config.markers_only_presence:
                        for i in markers:
                            plane_gazes[f'marker_{i}_presence'] = plane_gazes[f'marker_{i}_presence'].notnull()

            # merge in head pose
            if export_config.include_plane_pose:
                for pln in this_planes:
                    merge_cols = [c for c in ('timestamp','timestamp_VOR','frame_idx','frame_idx_VOR') if c in plane_gazes.columns and c in plane_poses[pln].columns]
                    if not merge_cols:
                        merge_cols = ['frame_idx']
                    plane_gazes = plane_gazes.merge(plane_poses[pln], how='left', on=merge_cols)

            # add scene and reference camera timestamp info, if present
            ts = pd.read_csv(working_dir / r / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'timestamp':'frame_ts'})
            plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx')
            to_move = 1
            if 'frame_idx_VOR' in plane_gazes.columns:
                ts = ts.rename(columns={'frame_idx':'frame_idx_VOR','frame_ts':'frame_ts_VOR'})
                plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_VOR')
                column_info['frame_ts_VOR'] = 'scene camera frame timestamp (ms) after gaze to camera sync'
                to_move += 1
            if 'frame_idx_ref' in plane_gazes.columns:
                ts = pd.read_csv(working_dir / study_config.sync_ref_recording / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'frame_idx':'frame_idx_ref','timestamp':'frame_ts_ref','timestamp_stretched':'frame_ts_ref_stretched'})
                plane_gazes = plane_gazes.merge(ts, how="left", on='frame_idx_ref')
                column_info['frame_ts_ref'] = 'reference camera frame timestamp (ms)'
                column_info['frame_ts_ref_stretched'] = 'reference camera frame timestamp (ms), stretched so that clocks run at same rate'
                to_move += len([c for c in ts.columns if c.startswith('frame_ts_')])
            # reorder to get ts columns in the right place
            cols= plane_gazes.columns.to_list()
            idx = max([cols.index(c) for c in cols if c.startswith('frame_idx')])+1
            cols= cols[:idx] + cols[-to_move:] + cols[idx:-to_move]
            plane_gazes = plane_gazes[cols]

            # add trial numbers
            idx = max([cols.index(c) for c in cols if c.startswith('frame_ts')])+1
            plane_gazes.insert(idx,'trial',np.int32(-1))
            for i,e in enumerate(episodes[nm][1]):
                sel = (plane_gazes['frame_idx'] >= e[0]) & (plane_gazes['frame_idx'] <= e[1])
                plane_gazes.loc[sel,'trial'] = i+1
            column_info['trial'] = 'trial number (-1 means not during trial)'

            # store
            # to add second header row with column information, turn column index into a multiindex
            if export_config.include_unit_header_row:
                headers = list(zip(*[(c, column_info.get(c, "")) for c in plane_gazes.columns]))
            else:
                headers = [[c for c in plane_gazes.columns]]
            # write into df (use polars as that library saves to file waaay faster). Open file manually so we can write header ourselves
            plane_gazes = pl.from_pandas(plane_gazes)
            with open(export_path / f'{naming.gaze_export_prefix}{working_dir.name}_{r}_{nm}.tsv', 'w', encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
                for h in headers:
                    w.writerow(h)
                plane_gazes.write_csv(f, separator='\t', null_value='nan', float_precision=8, include_header=False)

def export_head_pose(export_path: pathlib.Path, working_dir: pathlib.Path, study_config: config.Study, export_config: HeadPose):
    plane_definitions = {definition.name: definition for definition in study_config.marker_assemblies if isinstance(definition, marker_assembly.Definition_Plane_Aruco)}
    plane_names = sorted(plane_definitions)
    temporal_fields = ('timestamp', 'timestamp_VOR', 'frame_idx_VOR', 'timestamp_ori', 'frame_idx_ori')

    for rec_name in export_config.recs:
        rec_dir = working_dir / rec_name
        plane_frames = {}
        for plane_name in plane_names:
            pose_file, _ = _pose_files.get_preferred_plane_pose_file(rec_dir, plane_name)
            if pose_file.is_file():
                plane_frames[plane_name] = pd.read_csv(pose_file, sep='\t')
        if not plane_frames:
            warnings.warn(f'No plane pose files found for recording {rec_name} in session {working_dir.name}. Skipping head-pose export...', process_pool.ProcessingWarning)
            continue

        shared_temporal = [field for field in temporal_fields if all(field in df.columns for df in plane_frames.values())]
        merge_cols = ['frame_idx', *shared_temporal]
        per_plane_exports = {}
        column_info = {
            'frame_idx': 'frame number',
            'timestamp': 'ms',
            'timestamp_VOR': 'ms after gaze-to-camera synchronization',
            'frame_idx_VOR': 'frame number after gaze-to-camera synchronization',
            'timestamp_ori': 'original timestamp (ms)',
            'frame_idx_ori': 'original frame number',
        }

        for plane_name, pose_df in plane_frames.items():
            rotation_columns = ['pose_R_vec_x', 'pose_R_vec_y', 'pose_R_vec_z']
            translation_columns = ['pose_T_vec_x', 'pose_T_vec_y', 'pose_T_vec_z']
            missing_columns = [col for col in rotation_columns + translation_columns if col not in pose_df.columns]
            if missing_columns:
                warnings.warn(f'Plane pose file for plane {plane_name} in recording {rec_name} is missing pose columns {missing_columns}. Skipping this plane...', process_pool.ProcessingWarning)
                continue

            valid_pose = pose_df[rotation_columns + translation_columns].notna().all(axis=1).to_numpy()
            inverse_rotations = np.full((len(pose_df), 3), np.nan)
            inverse_translations = np.full((len(pose_df), 3), np.nan)
            for row_idx in np.flatnonzero(valid_pose):
                inverse_rotations[row_idx], inverse_translations[row_idx] = _invert_plane_pose(
                    pose_df.loc[pose_df.index[row_idx], rotation_columns].to_numpy(),
                    pose_df.loc[pose_df.index[row_idx], translation_columns].to_numpy(),
                )
            pose_df = pose_df.copy()
            pose_df[rotation_columns] = inverse_rotations
            pose_df[translation_columns] = inverse_translations
            if export_config.include_Fick_angles:
                pose_df['pose_azimuth'] = np.nan
                pose_df['pose_elevation'] = np.nan
                for row_idx in np.flatnonzero(valid_pose):
                    inverse_rotation = cv2.Rodrigues(inverse_rotations[row_idx])[0]
                    forward_in_plane = inverse_rotation @ np.array([0., 0., 1.])
                    azimuth, elevation = vector_to_Fick(*forward_in_plane)
                    pose_df.loc[pose_df.index[row_idx], ['pose_azimuth', 'pose_elevation']] = [azimuth, elevation]

            payload_columns = [
                col for col in pose_df.columns
                if col not in merge_cols
                and not col.startswith('homography_')
                and col not in ('pose_N_points', 'pose_reprojection_error')
            ]
            rename = {}
            for col in payload_columns:
                if col.startswith('pose_'):
                    rename[col] = f'pose_{plane_name}_{col[5:]}'
                    suffix = col[5:]
                    if suffix.startswith('R_vec_'):
                        column_info[rename[col]] = 'camera-to-plane rotation-vector component (rad)'
                    elif suffix.startswith('T_vec_'):
                        plane_unit = plane_definitions[plane_name].unit or 'plane units'
                        column_info[rename[col]] = f'scene-camera position in plane coordinates ({plane_unit})'
                    elif suffix == 'azimuth' or suffix == 'elevation':
                        column_info[rename[col]] = 'deg (Fick)'
                elif col in temporal_fields and col not in shared_temporal:
                    rename[col] = f'{col}_{plane_name}'
                    column_info[rename[col]] = column_info.get(col, '')
            plane_df = pose_df[merge_cols + payload_columns].rename(columns=rename)
            per_plane_exports[plane_name] = plane_df

        if not per_plane_exports:
            continue
        plane_export_tables = list(per_plane_exports.values())
        head_pose = plane_export_tables[0]
        for plane_df in plane_export_tables[1:]:
            head_pose = head_pose.merge(plane_df, how='outer', on=merge_cols)
        head_pose = head_pose.sort_values('frame_idx').reset_index(drop=True)

        if export_config.include_unit_header_row:
            headers = list(zip(*[(col, column_info.get(col, '')) for col in head_pose.columns]))
        else:
            headers = [[col for col in head_pose.columns]]

        head_pose = pl.from_pandas(head_pose)
        output_file = export_path / f'{naming.head_pose_export_prefix}{working_dir.name}_{rec_name}.tsv'
        with open(output_file, 'w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
            for header in headers:
                writer.writerow(header)
            head_pose.write_csv(output, separator='\t', null_value='nan', float_precision=8, include_header=False)


def export_gaze_offsets(export_path: pathlib.Path, working_dir: pathlib.Path, study_config: config.Study, export_config: GazeOffset):
    episodes_to_proc = process.get_specific_event_types(study_config, check_specific_fields=['gaze_offset_setup'])
    if not episodes_to_proc:
        raise ValueError('No episodes configured for gaze offset computation (need at least one episode with planes defined)')
    if export_config.episodes:
        episodes_to_proc = [cs for cs in episodes_to_proc if cs['name'] in export_config.episodes]
        if not episodes_to_proc:
            raise ValueError('None of the selected gaze offset episodes are configured for the study')

    # data is stored per recording, per plane, with all episodes for a given plane in the same file
    # reorganize to get one file per participant per episode, with all planes for that episode in the same file
    episode_planes = {
        cs['name']: [p for p in cs['gaze_offset_setup'] if not export_config.planes or p in export_config.planes]
        for cs in episodes_to_proc
    }
    episodes_to_proc = [cs for cs in episodes_to_proc if episode_planes[cs['name']]]
    if not episodes_to_proc:
        raise ValueError('None of the selected gaze offset planes are configured for the selected episodes')
    all_planes = {p for pl in episode_planes.values() for p in pl}

    # per recording, read the relevant files and put them all together
    for r in export_config.recs:
        # check if files needed for export are present, else skip
        if not all((working_dir / r / f'{naming.gaze_offset_prefix}{p}.tsv').is_file() for p in all_planes):
            warnings.warn(f'Not all gaze offset files found for recording {r} in session {working_dir.name}. Skipping...', process_pool.ProcessingWarning)
            continue
        # get episode coding
        episodes = episode.load_episodes_from_all_recordings(study_config, working_dir/r, {cs['name'] for cs in episodes_to_proc})[0]
        if not any(episodes[e][1] for e in episodes):
            warnings.warn(f'No coding for any of the events with gaze offset setup was found in the coding file for recording {r} in session {working_dir.name}. Skipping...', process_pool.ProcessingWarning)
            continue

        # get gaze offset data per plane
        gaze_offsets = {p:pd.read_csv(working_dir / r / f'{naming.gaze_offset_prefix}{p}.tsv', sep='\t') for p in all_planes}

        # rename putting plane name in there so that names are unique
        for p in gaze_offsets:
            cols = {}
            for c in gaze_offsets[p].columns:
                if c.startswith('offset_'):
                    # find position of '_target' in the column name, we want to insert plane name before that
                    pos = c.find('_target')
                    cols[c] = f'{c[:pos]}_{p}{c[pos:]}'
            gaze_offsets[p] = gaze_offsets[p].rename(columns=cols)

        # rest per coding stream
        ori_gaze_offsets = copy.deepcopy(gaze_offsets)
        for cs in episodes_to_proc:
            nm = cs['name']
            if not episodes[nm][1]:
                continue

            if not cs['which_recordings'] or r in cs['which_recordings']:
                subset_var = 'frame_idx'
            else:
                subset_var = 'frame_idx_ref'

            # now merge
            cs_planes = episode_planes[nm]
            gaze_offsets = {}
            for p in cs_planes:
                this_gaze_offsets = ori_gaze_offsets[p][ori_gaze_offsets[p]['episode']==nm].copy().drop(columns=['episode'])
                if not this_gaze_offsets.empty:
                    gaze_offsets[p] = this_gaze_offsets
            if not gaze_offsets:
                continue
            plane_keys = list(gaze_offsets.keys())
            for i in range(1,len(plane_keys)):
                # NB: by not providing on, merge is done on intersection of columns (so that is all timestamp and frame_idx columns, which is what we want)
                gaze_offsets[plane_keys[0]] = gaze_offsets[plane_keys[0]].merge(gaze_offsets[plane_keys[i]], how='outer')
            gaze_offsets = gaze_offsets[plane_keys[0]]

            # drop columns that are all nan (this can just be offset columns for targets that were not configured for this episode, so is safe to call over whole dataframe)
            gaze_offsets = gaze_offsets.dropna(axis=1, how='all')

            # add scene and reference camera timestamp info, if present
            ts = pd.read_csv(working_dir / r / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'timestamp':'frame_ts'})
            gaze_offsets = gaze_offsets.merge(ts, how="left", on='frame_idx')
            to_move = 1
            if 'frame_idx_VOR' in gaze_offsets.columns:
                ts = ts.rename(columns={'frame_idx':'frame_idx_VOR','frame_ts':'frame_ts_VOR'})
                gaze_offsets = gaze_offsets.merge(ts, how="left", on='frame_idx_VOR')
                to_move += 1
            if 'frame_idx_ref' in gaze_offsets.columns:
                ts = pd.read_csv(working_dir / study_config.sync_ref_recording / gt_naming.frame_timestamps_fname,sep='\t').rename(columns={'frame_idx':'frame_idx_ref','timestamp':'frame_ts_ref','timestamp_stretched':'frame_ts_ref_stretched'})
                gaze_offsets = gaze_offsets.merge(ts, how="left", on='frame_idx_ref')
                to_move += len([c for c in ts.columns if c.startswith('frame_ts_')])
            # reorder to get ts columns in the right place
            cols= gaze_offsets.columns.to_list()
            idx = max([cols.index(c) for c in cols if c.startswith('frame_idx')])+1
            cols= cols[:idx] + cols[-to_move:] + cols[idx:-to_move]
            gaze_offsets = gaze_offsets[cols]

            # add trial numbers
            idx = max([cols.index(c) for c in cols if c.startswith('frame_ts')])+1
            gaze_offsets.insert(idx,'trial',np.int32(-1))
            for i,e in enumerate(episodes[nm][1]):
                sel = (gaze_offsets[subset_var] >= e[0]) & (gaze_offsets[subset_var] <= e[1])
                gaze_offsets.loc[sel,'trial'] = i+1

            # store
            if export_config.include_unit_header_row:
                column_info = {}
                for c in gaze_offsets.columns:
                    if c.startswith('offset'):
                        column_info[c] = 'deg'
                    elif c == 'frame_idx':
                        column_info[c] = 'frame number'
                    elif c == 'frame_idx_VOR':
                        column_info[c] = 'frame number (after gaze to camera sync)'
                    elif c == 'frame_idx_ref':
                        column_info[c] = 'frame number (in reference camera video)'
                    elif c == 'timestamp':
                        column_info[c] = 'ms'
                    elif c == 'timestamp_VOR':
                        column_info[c] = 'ms (timestamp after gaze to camera sync)'
                    elif c == 'timestamp_ref':
                        column_info[c] = 'ms (timestamp in clock of reference camera video)'
                    elif c == 'frame_ts':
                        column_info[c] = 'scene camera frame timestamp (ms)'
                    elif c == 'frame_ts_VOR':
                        column_info[c] = 'scene camera frame timestamp (ms) after gaze to camera sync'
                    elif c == 'frame_ts_ref':
                        column_info[c] = 'reference camera frame timestamp (ms)'
                    elif c == 'frame_ts_ref_stretched':
                        column_info[c] = 'reference camera frame timestamp (ms), stretched so that clocks run at same rate'
                    elif c == 'trial':
                        column_info[c] = 'trial number (-1 means not during trial)'

                headers = list(zip(*[(c, column_info.get(c, "")) for c in gaze_offsets.columns]))
            else:
                headers = [[c for c in gaze_offsets.columns]]

            # write into df (use polars as that library saves to file waaay faster). Open file manually so we can write header ourselves
            gaze_offsets = pl.from_pandas(gaze_offsets)
            with open(export_path / f'{naming.offset_export_prefix}{working_dir.name}_{r}_{nm}.tsv', 'w', encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
                for h in headers:
                    w.writerow(h)
                gaze_offsets.write_csv(f, separator='\t', null_value='nan', float_precision=8, include_header=False)

def export_gazeOverlay_video(export_path: pathlib.Path, working_dir: pathlib.Path, export_config: GazeOverlayVideo):
    for r in export_config.recs:
        inFile = working_dir/r/gt_naming.gaze_overlay_video_file
        if not inFile.is_file():
            continue
        shutil.copy2(inFile, export_path / f'gazeOverlay_{working_dir.name}_{r}.mp4')

def export_mappedGaze_video(export_path: pathlib.Path, working_dir: pathlib.Path, export_config: OptionBase):
    for r in export_config.recs:
        inFile = working_dir/r/naming.mapped_gaze_video
        if not inFile.is_file():
            continue
        shutil.copy2(inFile, export_path / f'mappedGaze_{working_dir.name}_{r}.mp4')

def export_validation(export_path: pathlib.Path, val_export_config: Validation):
    for nm in val_export_config.episodes:
        dq_types = [dq for dq in val_export_config.episodes[nm].d_types if val_export_config.episodes[nm].d_types[dq]]
        targets  = [t for t in val_export_config.episodes[nm].targets if val_export_config.episodes[nm].targets[t]]
        val_export.summarize_and_store_data_quality(val_export_config.episodes[nm].df, export_path/f'data_quality_{nm}.tsv', dq_types, targets, val_export_config.episodes[nm].targets_avg, val_export_config.episodes[nm].include_data_loss)

def export_trial_data_quality(project_dir: str|pathlib.Path, sess_recs: list[tuple[str, str]], output_dir: str|pathlib.Path):
    project_dir = pathlib.Path(project_dir)
    output_dir = pathlib.Path(output_dir)
    data_frames = []
    for sess, rec in sess_recs:
        data_file = project_dir / sess / rec / naming.data_quality_fname
        if not data_file.is_file():
            warnings.warn(f'No computed trial data quality file found for recording {rec} in session {sess}; skipping.', process_pool.ProcessingWarning)
            continue
        df = pd.read_csv(data_file, delimiter='\t')
        df.insert(0, 'recording', rec)
        df.insert(0, 'session', sess)
        data_frames.append(df)
    if not data_frames:
        return
    pd.concat(data_frames, ignore_index=True).to_csv(output_dir / naming.data_quality_export_fname, index=False, sep='\t', na_rep='nan', float_format='%.6f')

def export_et_sync(project_dir: str|pathlib.Path, sess_recs: list[tuple[str,str|None]], output_dir: str|pathlib.Path):
    project_dir = pathlib.Path(project_dir)
    output_dir = pathlib.Path(output_dir)
    rec_dirs = [project_dir/s/r if r is not None else project_dir/s for s,r in sess_recs]
    sync_files = [(pathlib.Path(rec)/naming.VOR_sync_file,{'recording': rec.name, 'session': rec.parent.name}) for rec in rec_dirs]
    sync_files = [f for f in sync_files if f[0].is_file()]
    # get all sync files
    df = pd.concat((pd.read_csv(sync[0], delimiter='\t').assign(**sync[1]) for sync in sync_files), ignore_index=True)
    if df.empty:
        return
    df = df.set_index(['session','recording','interval'])
    # store
    output_file = output_dir / 'et_sync.tsv'
    df.to_csv(output_file, mode='w', header=True, sep='\t', na_rep='nan', float_format="%.6f")
