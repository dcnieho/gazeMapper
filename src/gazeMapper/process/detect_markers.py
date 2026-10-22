import pathlib
import cv2
import numpy as np
import warnings
import typing

from glassesTools import annotation, aruco, drawing, marker as gt_marker, marker_assembly as gt_marker_assembly, naming as gt_naming, pose, process_pool, propagating_thread, ocv, timestamps
from glassesTools.camera_recording import Type as CameraRecordingType
from glassesTools.gui.video_player import GUI

from .. import config, episode, marker, naming, marker_assembly, process, session, synchronization


def _without_id_label_background(styles: drawing.MarkerStyles) -> drawing.MarkerStyles:
    return styles._replace(**{
        category: style._replace(id_style=style.id_style._replace(background_opacity=0))
        for category, style in styles._asdict().items()
    })

def _transform_pose_vectors(rotation_vec: np.ndarray, translation: np.ndarray, camera_params: ocv.CameraParams) -> tuple[np.ndarray, np.ndarray]:
    if (not camera_params.has_extrinsics() or camera_params.rotation_vec is None or camera_params.position is None):
        raise ValueError('The replacement camera calibration does not include extrinsics.')
    if rotation_vec is None or translation is None:
        raise ValueError('Cannot transform an incomplete pose.')
    rotation = cv2.Rodrigues(np.ascontiguousarray(camera_params.rotation_vec, dtype=np.float64))[0]
    pose_rotation = cv2.Rodrigues(rotation_vec)[0]
    scene_rotation = rotation @ pose_rotation
    scene_translation = (rotation @ translation).flatten() + camera_params.position
    return cv2.Rodrigues(scene_rotation)[0].flatten(), scene_translation

_T = typing.TypeVar('_T', bound=pose.Pose)
def _transform_pose_to_scene_camera(pose_estimate: _T, camera_params: ocv.CameraParams, frame_idx: int) -> _T:
    if not pose_estimate.pose_successful() or pose_estimate.pose_R_vec is None or pose_estimate.pose_T_vec is None:
        return type(pose_estimate)(frame_idx=frame_idx)
    scene_rotation, scene_translation = _transform_pose_vectors(pose_estimate.pose_R_vec, pose_estimate.pose_T_vec, camera_params)
    return type(pose_estimate)(frame_idx=frame_idx, pose_R_vec=scene_rotation, pose_T_vec=scene_translation, pose_N_points=pose_estimate.pose_N_points, pose_reprojection_error=pose_estimate.pose_reprojection_error)

def _transform_marker_pose_to_scene_camera(pose_estimate: gt_marker.Pose, camera_params: ocv.CameraParams, frame_idx: int) -> gt_marker.Pose:
    if not pose_estimate.pose_successful() or pose_estimate.R_vec is None or pose_estimate.T_vec is None:
        return gt_marker.Pose(frame_idx=frame_idx)
    scene_rotation, scene_translation = _transform_pose_vectors(pose_estimate.R_vec, pose_estimate.T_vec, camera_params)
    return gt_marker.Pose(frame_idx=frame_idx, R_vec=scene_rotation, T_vec=scene_translation)


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None=None, show_visualization=False, visualization_show_rejected_markers=False, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected markers and assemblies
    # if show_rejected_markers, rejected ArUco marker candidates are also shown in the viewer. Possibly useful for debug
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # if we need gui, we run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    if show_visualization:
        gui = GUI(use_thread = False)
        gui.add_window(working_dir.name)
        gui.set_screenshot_filename_prefix(f'{working_dir.parent.name}_{working_dir.name}')
        gui.set_interruptible(False)
        gui.set_detachable(True, on_detach=lambda: process_pool.release_current_job_resource(('gui',)))
        gui.set_show_controls(True)
        gui.set_show_play_percentage(True)
        gui.set_show_action_tooltip(True)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, visualization_show_rejected_markers, progress_indicator), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, False, progress_indicator, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI|None, visualization_show_rejected_markers: bool, progress_indicator: process_pool.JobProgress|None, **study_settings):
    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('frames')
    progress_indicator.set_start_time_to_now()

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_name = working_dir.name
    rec_def = study_config.session_def.get_recording_def(rec_name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]

    # check whether this recording's pose is replaced by that of a head-attached camera. If so, we only have to run any camera sync detections here
    is_replaced_recording = study_config.head_attached_recordings_replace_et_scene is not None and any(r.associated_recording==rec_name for r in study_config.session_def.recordings if r.name in study_config.head_attached_recordings_replace_et_scene)
    if is_replaced_recording:
        sync_events = process.get_specific_event_types(study_config, annotation.EventType.Sync_Camera, ['auto_code'])
        if not sync_events:
            raise RuntimeError(f'Detect Markers should not be run for recording "{rec_name}" as its pose is replaced by that of a head-attached camera and there are no camera sync events to process')

        sync_markers = {m for e in sync_events for m in e['auto_code'].get('markers', [])}
        individual_markers_to_process = [m for m in study_config.individual_markers if (m.id,m.aruco_dict_id) in sync_markers]
        assemblies_setup, _, episodes = {}, {}, {}
    else:
        # get coded interval(s) with assemblies, if any. Just to show in GUI, if attached
        wanted_episodes = {cs['name'] for cs in study_config.coding_setup if cs.get('marker_assemblies',[])}
        episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, wanted_episodes, missing_other_coding_ok=True)[0]

        assemblies_setup, _, _ = _get_assembly_setup(study_config, config_dir)  # we don't need to know for which frames to run the detection, we always process all frames

        if not assemblies_setup and not study_config.individual_markers:
            raise RuntimeError(f'No marker assemblies (e.g., planes) or individual markers are configured for detection for the "{rec_name}" recording (a {rec_def.type.value} recording), cannot run Detect Markers')
        individual_markers_to_process = study_config.individual_markers

    # set up pose estimator
    estimator = pose.Estimator(in_video, working_dir / gt_naming.frame_timestamps_fname, working_dir / gt_naming.scene_camera_calibration_fname)
    # first, register all ArUco assemblies and individual markers with ArUco manager, which
    # will then wrap their detection and register them with the pose estimator
    aruco_manager = aruco.Manager()
    for a in assemblies_setup:
        setup = assemblies_setup[a]
        aruco_manager.add_assembly(a, setup)
        if setup.is_dynamic:
            if setup.dynamic_marker_setup is None:
                raise ValueError(f'Dynamic marker setup is missing for assembly "{a}"')
            for marker_id in setup.dynamic_marker_ids:
                aruco_manager.add_individual_marker(marker_id, setup.dynamic_marker_setup)
    for m in (markers:=marker.get_setup_for_markers(individual_markers_to_process, study_config.individual_marker_settings)):
        aruco_manager.add_individual_marker(m, markers[m])
    aruco_manager.consolidate_setup(study_config.allow_duplicated_markers)
    aruco_manager.register_with_estimator(estimator)
    estimator.attach_gui(gui)
    if gui is not None:
        gui.set_show_timeline(True, timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname), annotation.flatten_annotation_dict(episodes), window_id=gui.main_window_id)
        styles = _without_id_label_background(study_config.mapped_video_marker_styles)
        recovered_color = study_config.mapped_video_recovered_assembly_marker_color or drawing.RgbColor(0, 255, 255)
        rejected_color = None
        if visualization_show_rejected_markers:
            rejected_color = study_config.mapped_video_rejected_marker_color or drawing.RgbColor(255, 0, 0)
        aruco_manager.set_visualization_styles(styles, recovered_color, rejected_color)

    # prep progress indicator
    progress_indicator.set_total(total:=estimator.video_ts.get_last()[0])
    progress_indicator.set_intervals(step:=min(20,int(total/200)), step)
    estimator.set_progress_updater(progress_indicator.update)

    poses, individual_markers, _ = estimator.process_video()

    assembly_definitions = {definition.name: definition for definition in study_config.marker_assemblies}
    for a in poses:
        if isinstance(assembly_definitions[a], marker_assembly.Definition_MarkerAssembly_3D):
            pose.write_list_to_file(poses[a], working_dir/f'{naming.pose_prefix}{a}.tsv', skip_failed=True)
        else:
            pose.write_plane_list_to_file(poses[a], working_dir/f'{naming.plane_pose_prefix}{a}.tsv', skip_failed=True)
    for m in individual_markers:
        gt_marker.write_list_to_file(individual_markers[m], gt_marker.get_file_name(m.m_id, m.aruco_dict_id, working_dir), skip_failed=False)

    # if this is a head-attached recording, further check whether its pose is used to replace pose for another camera
    # if so, write a pose file for that camera also
    is_head_attached_camera_recording = rec_def.type==session.RecordingType.Camera and rec_def.camera_recording_type==CameraRecordingType.Head_attached
    if is_head_attached_camera_recording and study_config.head_attached_recordings_replace_et_scene is not None and rec_name in study_config.head_attached_recordings_replace_et_scene:
        to_replace = rec_def.associated_recording
        if to_replace is None:
            raise ValueError(f'Head-attached recording "{rec_name}" has no associated eye-tracker recording.')
        # we need to have camera sync for the head-attached camera recording to be able to replace the eye tracker scene camera poses
        sync = synchronization.get_sync_for_recs(working_dir.parent, [to_replace], rec_name, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, missing_ref_coding_ok=True)
        if sync is not None:
            # get eye tracker scene camera frame indices we have poses for
            videos_ts = {r:timestamps.VideoTimestamps(working_dir.parent / r / gt_naming.frame_timestamps_fname) for r in [to_replace, rec_name]}
            to_sync_frame_idxs = synchronization.reference_frames_to_video(to_replace, sync, videos_ts[rec_name].indices,
                                                                           videos_ts[to_replace].timestamps, videos_ts[rec_name].timestamps,
                                                                           study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
            frame_map = {frep:fr for fr,frep in zip(videos_ts[rec_name].indices,to_sync_frame_idxs) if frep!=-1}
            # turn poses into a dict for easy access
            poses = {pl:{p.frame_idx:p for p in poses[pl]} for pl in poses}
            # use camera extrinsics to make new poses for the eye tracker scene camera
            cam_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)
            # now, per assembly, transform head-attached camera poses into eye tracker scene camera poses and store
            for pl in poses:
                # get poses
                is_3d_assembly = isinstance(assembly_definitions[pl], marker_assembly.Definition_MarkerAssembly_3D)
                replaced_poses: list[pose.Pose] = []
                for frep in frame_map:
                    if frame_map[frep] in poses[pl]:
                        ha_pose = poses[pl][frame_map[frep]]
                        replaced_poses.append(_transform_pose_to_scene_camera(ha_pose, cam_params, frep))
                # store to file
                if is_3d_assembly:
                    pose.write_list_to_file(replaced_poses, working_dir.parent/to_replace/f'{naming.pose_prefix}{pl}.tsv', skip_failed=True)
                else:
                    pose.write_plane_list_to_file(replaced_poses, working_dir.parent/to_replace/f'{naming.plane_pose_prefix}{pl}.tsv', skip_failed=True)
            for marker_id, marker_poses in individual_markers.items():
                poses_by_frame = {marker_pose.frame_idx: marker_pose for marker_pose in marker_poses}
                replaced_marker_poses = [
                    _transform_marker_pose_to_scene_camera(poses_by_frame[frame_map[frame_idx]], cam_params, frame_idx)
                    for frame_idx in frame_map
                    if frame_map[frame_idx] in poses_by_frame
                ]
                gt_marker.write_list_to_file(
                    replaced_marker_poses,
                    gt_marker.get_file_name(marker_id.m_id, marker_id.aruco_dict_id, working_dir.parent/to_replace),
                    skip_failed=False,
                )
        else:
            warnings.warn(f'Cannot replace eye tracker scene camera poses with head-attached camera poses: missing camera synchronization between head-attached camera recording "{rec_name}" and eye tracker scene camera recording "{to_replace}". Run Auto Coding if you have this set up, or manually code at least one sync point for both recordings, and then run the Detect Markers action again.', process_pool.ProcessingWarning)


    # update state
    session.update_action_states(working_dir, process.Action.DETECT_MARKERS, process_pool.State.Completed, study_config)


def _get_assembly_setup(study_config: config.Study,
                     config_dir: pathlib.Path,
                     episodes: dict[str, tuple[annotation.EventType, list[list[int]]]]|None = None) -> tuple[dict[str, aruco.AssemblySetup], dict[str, tuple[annotation.EventType, list[int]|list[list[int]]]|None], dict[str, gt_marker_assembly.MarkerAssembly]]:
    # process the above into a dict of marker assembly definitions and a dict with frame number intervals for which to use each
    codings = [e for e in episodes] if episodes else [cs['name'] for cs in study_config.coding_setup if cs.get('marker_assemblies',[])]
    coding_setups = [cs for cs in study_config.coding_setup if cs['name'] in codings]
    assembly_names = {v for cs in coding_setups for v in cs['marker_assemblies']}
    assemblies: dict[str, gt_marker_assembly.MarkerAssembly] = {}
    assemblies_setup: dict[str, aruco.AssemblySetup] = {}
    analyze_frames: dict[str, tuple[annotation.EventType, list[list[int]]]|None] = {}
    for a in assembly_names:
        a_def = [asbl for asbl in study_config.marker_assemblies if asbl.name==a][0]
        assemblies[a] = marker_assembly.get_marker_assembly_from_definition(a_def, config_dir/a)
        assemblies_setup[a] = assemblies[a].get_assembly_setup()
        if episodes:
            # determine for which frames this assembly should be used
            anal_events  = {cs['name']: cs['event_type'] for cs in study_config.coding_setup if a in cs['marker_assemblies']}
            all_episodes = [ep for k in anal_events if k in episodes for ep in episodes[k][1] if ep]  # filter out empty
            analyze_frames[a] = (list(anal_events.values())[0],sorted(all_episodes, key = lambda x: x[1]))
        else:
            analyze_frames[a] = None

    return assemblies_setup, analyze_frames, assemblies