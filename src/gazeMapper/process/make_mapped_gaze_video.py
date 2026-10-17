import shutil
import os
import pathlib
import math
import cv2
import numpy as np
import subprocess
import typing

from ffpyplayer.writer import MediaWriter

from glassesTools import annotation, aruco, drawing, gaze_headref, gaze_worldref, marker_assembly as gt_marker_assembly, naming as gt_naming, ocv, plane as gt_plane, pose, process_pool, propagating_thread, timestamps, utils, video_utils
from glassesTools.gui import video_player

from .. import config, episode, marker, naming, process, session, synchronization
from .detect_markers import _get_assembly_setup
from .run_sync_function import _get_sync_function

def _flatten_episodes(episodes: episode.EpisodeMap) -> dict[str, tuple[annotation.EventType, list[int]]]:
    return {name: (event_type, [idx for interval_ in intervals_ for idx in interval_]) for name, (event_type, intervals_) in episodes.items()}

def _make_color_triplet(color: tuple[float, float, float]) -> tuple[int, int, int]:
    return tuple(int(round(component * 255)) for component in color)

def _ensure_bgr_frame(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2 or (frame.ndim == 3 and frame.shape[2] == 1):
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    return frame

def _get_sync_settings(study_config: config.Study) -> tuple[bool, list[str], str]:
    return (
        bool(study_config.sync_ref_do_time_stretch),
        list(study_config.sync_ref_average_recordings or []),
        study_config.sync_ref_stretch_which or 'other',
    )

def _style_with_recording_color(style: drawing.CircleStyle | None, color: drawing.RgbColor | None) -> drawing.CircleStyle | None:
    if style is None or color is None:
        return style
    edge_color = color if style.edge_color is not None else None
    fill_color = color if style.fill_color is not None else None
    if edge_color is None and fill_color is None:
        fill_color = color
    return style._replace(edge_color=edge_color, fill_color=fill_color)

_style_fields = {
    gaze_worldref.Type.Scene_Video_Position: 'video_position',
    gaze_worldref.Type.World_3D_Point: 'world_position',
    gaze_worldref.Type.Left_Eye_Gaze_Vector: 'left_eye',
    gaze_worldref.Type.Right_Eye_Gaze_Vector: 'right_eye',
    gaze_worldref.Type.Average_Gaze_Vector: 'average',
}
def _mapped_gaze_styles_for_recording(styles: gaze_worldref.MappedGazeStyles, gaze_type: gaze_worldref.Type, color: drawing.RgbColor | None) -> gaze_worldref.MappedGazeStyles:
    style_field = _style_fields[gaze_type]
    style = _style_with_recording_color(getattr(styles, style_field), color)
    if style == getattr(styles, style_field):
        return styles
    return styles._replace(**{
        style_field: style,
    })

def _needs_live_estimator(rec: str, rec_type: session.RecordingType, study_config: config.Study, output_recs: set[str]) -> bool:
    # determine if we need an estimator for the current recording. It is only needed for recordings
    # that can affect a visible output: an output video, the sync reference, whole-video plane
    # processing, camera-position overlays from any recording, and gaze-dependent overlays from eye trackers.
    if rec in output_recs or rec == study_config.sync_ref_recording:
        return True
    if study_config.mapped_video_process_marker_assemblies_for_all_frames:
        return True
    # should the camera position of this recording be shown on any output video?
    camera_overlay_targets = set(study_config.mapped_video_show_camera_in_which or ())
    if bool((output_recs - {rec}) & camera_overlay_targets):
        return True
    # else, we only have left to check if something about gaze data is to be drawn on other videos. Always false if not an eye tracker recording
    if rec_type != session.RecordingType.Eye_Tracker:
        return False

    overlay_targets = set(study_config.mapped_video_show_gaze_on_plane_in_which or ())
    overlay_targets.update(study_config.mapped_video_show_gaze_vec_in_which or ())
    return bool((output_recs - {rec}) & overlay_targets)

def _get_active_episode_indices(frame_idx: int, episodes: episode.EpisodeMap) -> list[tuple[str, int]]:
    active: list[tuple[str, int]] = []
    for name in episodes:
        for idx, iv in enumerate(episodes[name][1]):
            if len(iv)==1:
                if frame_idx==iv[0]:
                    active.append((name, idx))
            elif iv[0] <= frame_idx <= iv[1]:
                active.append((name, idx))
    return active


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, show_visualization=False, progress_indicator: process_pool.JobProgress|None = None, **study_settings):
    # if show_visualization, the generated video(s) are shown as they are created in a viewer
    working_dir  = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    if show_visualization:
        # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
        gui = video_player.GUI(use_thread = False)
        gui.add_window(working_dir.name)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, progress_indicator), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, progress_indicator, **study_settings)

def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: video_player.GUI|None, progress_indicator: process_pool.JobProgress|None, **study_settings):
    has_gui = gui is not None
    sub_pixel_fac = 8   # for anti-aliased drawing

    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('frames')
    progress_indicator.set_start_time_to_now()

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir}, **study_settings)
    if not study_config.mapped_video_make_which:
        raise ValueError(f'There are no videos to be made (mapped_video_make_which is not defined or null in the study setup)')
    coding_events = [(cs['name'], cs['event_type']) for cs in config.get_coding_setup_with_auto_sync_offsets(study_config.coding_setup)]
    empty_episodes = episode.get_empty_marker_dict(coding_events)
    event_colors = {name: _make_color_triplet(color) for (name, _), color in zip(coding_events, utils.get_colors(len(coding_events), 0.45, 0.65))}
    do_time_stretch, average_recordings, stretch_which = _get_sync_settings(study_config)

    # get session info
    session_info = session.Session.from_definition(study_config.session_def, working_dir)

    # load info for all recordings in the recording session and setup wanted output videos
    episodes        : dict[str, episode.EpisodeMap]                     = {}
    episodes_as_ref : dict[str, episode.EpisodeMap]                     = {}
    episodes_seq_nrs: dict[str, dict[str, list[str]]]                   = {}
    episode_source_refs: dict[str, episode.EpisodeSourceRefs]           = {}
    imported_episodes: dict[str, episode.EpisodeImportedMap]            = {}
    episode_colors  : dict[str, dict[str, tuple[int, int, int]]]        = {}
    gazes_head      : dict[str, dict[int, list[gaze_headref.Gaze]]]     = {}
    in_videos       : dict[str, pathlib.Path]                           = {}
    camera_params   : dict[str, ocv.CameraParams]                       = {}
    videos_ts       : dict[str, timestamps.VideoTimestamps]             = {}
    pose_estimators : dict[str, pose.Estimator]                         = {}
    vid_info        : dict[str, tuple[int, int, float]]                 = {}
    assemblies      : dict[str, gt_marker_assembly.MarkerAssembly]      = {}
    planes          : dict[str, gt_plane.Plane]                         = {}
    recs = set(session_info.recordings)
    sync = None
    ref_frame_idxs: dict[str, list[int]] = {}
    for rec in recs:
        rec_def = session_info.recordings[rec].definition
        rec_working_dir = working_dir / rec

        # get coded interval(s), if any
        episodes[rec], _, episode_source_refs[rec], imported_episodes[rec] = episode.load_episodes_from_all_recordings_with_info(study_config, rec_working_dir, error_if_unwanted_found=False, missing_other_coding_ok=True)
        episodes[rec] = episode.copy_episode_map(empty_episodes) | episodes[rec]
        episode_source_refs[rec] = episode.copy_episode_source_refs(episode_source_refs[rec])
        for name in episodes[rec]:
            if name not in episode_source_refs[rec]:
                episode_source_refs[rec][name] = []
        episode_colors[rec] = {name: event_colors[name] for name in episodes[rec]}
        episodes_seq_nrs[rec] = {name: [str(i+1) if src is None else f'{i+1} ({src})' for src, i in episode_source_refs[rec][name]] for name in episodes[rec]}

        # Read gaze data
        if rec_def.type==session.RecordingType.Eye_Tracker:
            # NB: we want to use synced gaze data for these videos, if available
            gazes_head[rec] = gaze_headref.read_dict_from_file(rec_working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['ref', 'VOR', ''])[0]
            # check we have timestamps synced to ref, if relevant
            if study_config.sync_ref_recording and rec!=study_config.sync_ref_recording:
                if gazes_head[rec][next(iter(gazes_head[rec]))][0].timestamp_ref is None:
                    raise ValueError(f'This study has a reference recording ({study_config.sync_ref_recording}) to synchronize the recordings to, but the gaze data for this recording ({rec}) has not been synchronized. Run sync_to_ref before running this.')

        # get camera calibration info
        camera_params[rec] = ocv.CameraParams.read_from_file(rec_working_dir / gt_naming.scene_camera_calibration_fname)

        # get frame timestamps
        videos_ts[rec] = timestamps.VideoTimestamps(rec_working_dir / gt_naming.frame_timestamps_fname)

    # get frame sync info, and recording's episodes expressed in the reference video's frame indices
    if study_config.sync_ref_recording:
        ref_rec = study_config.sync_ref_recording
        sync = synchronization.get_sync_for_recs(working_dir, [r for r in recs if r!=ref_rec], ref_rec, do_time_stretch, average_recordings)
        if sync is None:
            raise RuntimeError(f'Cannot make mapped gaze videos for session "{working_dir.name}": missing camera synchronization with reference recording "{ref_rec}".')

        if study_config.mapped_video_process_annotations_for_all_recordings:
            # show Sync_ET_Data and Validate annotations from all eligible recordings on each video's timeline
            events = process.get_specific_event_types(study_config, [annotation.EventType.Sync_ET_Data, annotation.EventType.Validate])
            for rec in recs:
                for e in events:
                    nm = e['name']
                    for source_rec, imported_eps in imported_episodes[rec].get(nm, {}).items():
                        for i, ep in enumerate(imported_eps):
                            if (source_rec, i) in episode_source_refs[rec][nm]:
                                continue
                            episodes[rec][nm][1].append(ep.copy())
                            episodes_seq_nrs[rec][nm].append(f'{i+1} ({source_rec})')
                            episode_source_refs[rec][nm].append((source_rec, i))

        sync_recs = sync.index.get_level_values('recording').unique()
        episodes_as_ref[ref_rec] = episode.copy_episode_map(episodes[ref_rec])
        for r in sync_recs:
            # for each frame in the reference video, get the corresponding frame in this recording
            ref_frame_idxs[r] = synchronization.reference_frames_to_video(r, sync, videos_ts[ref_rec].indices, videos_ts[r].timestamps, videos_ts[ref_rec].timestamps, do_time_stretch, stretch_which)
            ref_frame_idxs[r] = synchronization.smooth_video_frames_indices(ref_frame_idxs[r])
            episodes_as_ref[r] = synchronization.video_episode_dict_to_reference(r, sync, episodes[r], videos_ts[r].timestamps, videos_ts[ref_rec].timestamps, do_time_stretch, stretch_which)


        # fix episodes with start or end points outside the reference video
        for r in sync_recs:
            for e in episodes_as_ref[r]:
                new_iv = []
                for i, iv in reversed(list(enumerate(episodes_as_ref[r][e][1]))):
                    if iv[0]==-1 and (len(iv)==1 or iv[1]==-1):
                        # not during reference video, so its irrelevant. Just remove
                        del episodes[r][e][1][i]
                        del episodes_seq_nrs[r][e][i]
                        del episode_source_refs[r][e][i]
                        continue
                    if iv[0]==-1:
                        iv[0] = 0
                    if len(iv)>1 and iv[1]==-1:
                        iv[1] = videos_ts[ref_rec].indices[-1]
                    new_iv.append(iv)
                episodes_as_ref[r][e] = (episodes_as_ref[r][e][0], new_iv[::-1])
    else:
        episodes_as_ref = {r: episode.copy_episode_map(episodes[r]) for r in episodes}

    # flatten the episodes for each recording, that's what the GUI and movie annotator want
    episodes_as_ref_flat = {r: _flatten_episodes(episodes_as_ref[r]) for r in episodes_as_ref}

    if study_config.sync_ref_recording and sync is not None:
        # check that all camera sync point frames of a recording are in the reference recordings sync frames (a recording may miss some, but the ones it has must be equal)
        sync_events = [
            cs for cs in config.get_coding_setup_with_auto_sync_offsets(study_config.coding_setup)
            if cs['event_type'] == annotation.EventType.Sync_Camera_Offset
            or (cs['event_type'] == annotation.EventType.Sync_Camera and cs['auto_code'] is not None)
        ]
        for r in sync.index.get_level_values('recording').unique():
            for cs in sync_events:
                nm = cs['name']
                ref_sync_points = episodes_as_ref_flat[study_config.sync_ref_recording][nm][1]
                rec_sync_points = episodes_as_ref_flat[r][nm][1]
                # NB: allow one frame leeway to allow for small offsets due to conversion, or cameras not running completely in sync
                if not all([abs(i_ref-i_rec)<=1 for i_ref,i_rec in zip(ref_sync_points,rec_sync_points)]):
                    raise RuntimeError(f'Camera sync points found for recording {r} ({episodes_as_ref_flat[r][nm]}) that do not occur among the reference recordings sync points ({study_config.sync_ref_recording}, {episodes_as_ref_flat[study_config.sync_ref_recording][nm]}). That means the sync logic must have failed')

    output_recs = set(study_config.mapped_video_make_which)
    mapped_gaze_styles = {
        rec: _mapped_gaze_styles_for_recording(
            study_config.mapped_video_gaze_styles,
            study_config.mapped_video_which_gaze_type_on_plane,
            study_config.mapped_video_recording_colors[rec],
        )
        for rec in output_recs
    }
    estimator_recs = {
        rec for rec in recs
        if _needs_live_estimator(rec, session_info.recordings[rec].definition.type, study_config, output_recs)
    }

    # build pose estimator
    for rec in session_info.recordings:
        if rec not in estimator_recs:
            continue
        rec_def = session_info.recordings[rec].definition
        in_videos[rec] = session.read_recording_info(working_dir / rec, rec_def.type)[1]
        pose_estimators[rec] = pose.Estimator(in_videos[rec], videos_ts[rec], camera_params[rec])
        pose_estimators[rec].set_allow_early_exit(False)    # make sure we run through the whole video
        # first, register all ArUco marker assemblies and individual markers with ArUco manager, which
        # will then wrap their detection and register them with the pose estimator
        recording_config = config.apply_study_config_overrides(study_config,
            {config.OverrideLevel.Recording: working_dir / rec}, rec_def.type, **study_settings)
        aruco_manager = aruco.Manager()
        assemblies_setup, analyze_frames, this_assemblies = _get_assembly_setup(recording_config, config_dir, episodes[rec])
        for a in assemblies_setup:
            assemblies[a] = this_assemblies[a]
            aruco_manager.add_assembly(a, assemblies_setup[a], None if study_config.mapped_video_process_marker_assemblies_for_all_frames else analyze_frames[a])
            if isinstance(this_assemblies[a], gt_plane.Plane):
                planes[a] = typing.cast(gt_plane.Plane, this_assemblies[a])
            if assemblies_setup[a].is_dynamic:
                if assemblies_setup[a].dynamic_marker_setup is None:
                    raise ValueError(f'Dynamic marker setup is missing for assembly "{a}"')
                for marker_id in assemblies_setup[a].dynamic_marker_ids:
                    aruco_manager.add_individual_marker(marker_id, assemblies_setup[a].dynamic_marker_setup, None if study_config.mapped_video_process_marker_assemblies_for_all_frames else analyze_frames[a])
        for m in (markers:=marker.get_setup_for_markers(recording_config.individual_markers, recording_config.individual_marker_settings)):
            aruco_manager.add_individual_marker(m, markers[m])
        aruco_manager.consolidate_setup(study_config.allow_duplicated_markers)
        aruco_manager.set_visualization_styles(study_config.mapped_video_marker_styles, study_config.mapped_video_recovered_assembly_marker_color, study_config.mapped_video_rejected_marker_color)
        aruco_manager.register_with_estimator(pose_estimators[rec])
        # other setup of estimator
        sync_target_functions, function_frames  = _get_sync_function(recording_config, rec_def, episodes[rec])
        if sync_target_functions:
            for sfe in sync_target_functions:
                pose_estimators[rec].register_extra_processing_fun(f'sync_{sfe}', function_frames[sfe], *sync_target_functions[sfe])
            pose_estimators[rec].show_extra_processing_output = study_config.mapped_video_show_sync_func_output

        if rec in study_config.mapped_video_make_which:
            pose_estimators[rec].set_visualize_on_frame(True)
            # set visualization properties
            pose_estimators[rec].sub_pixel_fac                      = sub_pixel_fac
            pose_estimators[rec].plane_axis_arm_length              = study_config.mapped_video_assembly_axis_arm_length
            pose_estimators[rec].individual_marker_axis_arm_length  = study_config.mapped_video_individual_marker_axis_arm_length

        if rec in study_config.mapped_video_make_which or rec==study_config.sync_ref_recording:
            # get video file info
            vid_info[rec] = pose_estimators[rec].get_video_info()
            # override fps with frame timestamp info
            if videos_ts[rec].has_stretched:
                vid_info[rec] = (*vid_info[rec][:2], 1000/videos_ts[rec].get_IFI(timestamps.Type.Stretched))
            else:
                vid_info[rec] = (*vid_info[rec][:2], 1000/videos_ts[rec].get_IFI(timestamps.Type.Normal))

    video_sets: list[tuple[str, set[str], set[str]]] = []
    if study_config.sync_ref_recording:
        video_sets.append((study_config.sync_ref_recording,{r for r in study_config.mapped_video_make_which if r!=study_config.sync_ref_recording}, estimator_recs))
    else:
        video_sets.extend((r, set(), {r}) for r in study_config.mapped_video_make_which)

    gui_window_ids: dict[str, int] = {}
    # per set of videos
    for vs_idx, (lead_vid, other_vids, proc_vids) in enumerate(video_sets):
        vid_writer          : dict[str, MediaWriter]                        = {}
        frame               : dict[str, np.ndarray | None]                  = {}
        frame_idx           : dict[str, int | None]                         = {}
        frame_ts            : dict[str, float | None]                       = {}
        frame_info          : dict[str, dict[str, typing.Any] | None]       = {}
        apose               : dict[str, dict[str, pose.Pose] | None]        = {}

        all_vids    = set([lead_vid]) | other_vids
        # videos to be written out may not be equal to all_vids, since user can configure
        # for which recordings they want to make a video
        write_vids  = {v for v in all_vids if v in study_config.mapped_video_make_which}

        if has_gui:
            gui_obj = typing.cast(video_player.GUI, gui)
            # clean up any previous windows (except main window, this will have to be renamed only)
            for v in gui_window_ids:
                if gui_window_ids[v]!=gui_obj.main_window_id:
                    gui_obj.delete_window(gui_window_ids[v])
            gui_window_ids.clear()
            main_vid = lead_vid if lead_vid in write_vids else next(iter(write_vids))
            for v in write_vids:
                # if we have a gui, set it up for this recording
                if v==main_vid:
                    gui_window_ids[v] = gui_obj.main_window_id
                    gui_obj.set_window_title(f'{working_dir.name}: {v}', gui_obj.main_window_id)
                else:
                    gui_window_ids[v] = gui_obj.add_window(f'{working_dir.name}: {v}')
                gui_obj.set_screenshot_filename_prefix(f'{working_dir.name}_{v}', gui_window_ids[v])
                gui_obj.set_detachable(True)
                gui_obj.set_show_timeline(True, videos_ts[lead_vid], episodes_as_ref_flat[v], gui_window_ids[v])
                gui_obj.set_frame_size(vid_info[v][:2], gui_window_ids[v])
                gui_obj.set_show_controls(True, gui_window_ids[v])
                gui_obj.set_timecode_position('r', gui_window_ids[v])
                gui_obj.set_show_play_percentage(True, gui_window_ids[v])
                gui_obj.set_show_action_tooltip(True, gui_window_ids[v])
                gui_obj.set_button_props_for_action(video_player.Action.Quit, 'Stop', tooltip='Interrupt (cut short) the video generation')

        # prep progress indicator
        progress_indicator.set_total(total:=videos_ts[lead_vid].get_last()[0])
        progress_indicator.set_intervals(step:=min(20,int(total/200)), step)

        # open output video files
        for v in write_vids:
            vid_writer[v] = video_utils.create_video_writer(working_dir / v / naming.mapped_gaze_video, study_config.mapped_video_pixel_format, vid_info[v][0], vid_info[v][1], vid_info[lead_vid][2])

        # update state: set to not run so that if we crash or cancel below the task is correctly marked as not run (video files are corrupt)
        session.update_action_states(working_dir, process.Action.MAKE_MAPPED_GAZE_VIDEO, process_pool.State.Not_Run, study_config)

        # now make the video
        def n_digit(value):
            return int(math.log10(value))+1
        def n_digit_timestamp(value_ms: float):
            return n_digit(value_ms/1000)+4    # ms->s, add decimal point and three decimals
        timestamp_width = {lead_vid: n_digit_timestamp(videos_ts[lead_vid].get_last()[1])}
        timestamp_width |= {v: n_digit_timestamp(videos_ts[v].get_timestamp(max(ref_frame_idxs[v]))) for v in other_vids}
        frame_idx_width = {lead_vid: n_digit(videos_ts[lead_vid].get_last()[0])}
        frame_idx_width |= {v: n_digit(max(ref_frame_idxs[v])) for v in other_vids}
        should_exit = False
        while True:
            if should_exit:
                break
            status, apose[lead_vid], _, _, (frame[lead_vid], frame_idx[lead_vid], frame_ts[lead_vid], frame_info[lead_vid]) = \
                pose_estimators[lead_vid].process_one_frame()
            # TODO: if there is a discontinuity, fill in the missing frames so audio stays in sync
            # check if we're done
            if status==pose.Status.Finished:
                break
            # NB: no need to handle pose.Status.Skip, since we didn't provide the pose estimator with any analysis intervals (we want to process the whole video)
            lead_frame_idx = typing.cast(int, frame_idx[lead_vid])
            lead_frame_ts = typing.cast(float, frame_ts[lead_vid])

            for v in proc_vids-set([lead_vid]):
                # find corresponding frame
                fr_idx_this = ref_frame_idxs[v][lead_frame_idx]

                if fr_idx_this==-1:
                    _, apose[v], _, _, (frame[v], frame_idx[v], frame_ts[v], frame_info[v]) = \
                        None, None, None, None, (None, None, None, None)
                else:
                    # read it
                    _, apose[v], _, _, (frame[v], frame_idx[v], frame_ts[v], frame_info[v]) = \
                        pose_estimators[v].process_one_frame(fr_idx_this)

            # prepare frame for annotation and writing
            for v in write_vids:
                if frame[v] is None:
                    # we don't have a valid frame, use a fully black frame
                    frame[v] = np.zeros((vid_info[v][1],vid_info[v][0],3), np.uint8)   # black image
                else:
                    # ensure the frame is in BGR format (it may be grayscale if the original video was grayscale), so that annotations can be drawn on it in color
                    frame[v] = _ensure_bgr_frame(frame[v])
            ROI_offsets = {
                v: (frame_info[v]['offset_x'], frame_info[v]['offset_y'])
                if frame_info[v] is not None and 'offset_x' in frame_info[v] and 'offset_y' in frame_info[v]
                else (0,0)
                for v in write_vids
            }

            # draw gaze on the video
            for v in proc_vids:
                if study_config.mapped_video_recording_colors is None or v not in study_config.mapped_video_recording_colors:
                    continue
                original_scene_video_style = study_config.mapped_video_gaze_styles.original_scene_video
                if original_scene_video_style is not None:
                    original_scene_video_style = original_scene_video_style._replace(edge_color=original_scene_video_style.edge_color or tuple(study_config.mapped_video_recording_colors[v]))
                poses_this = apose[v]
                # draw gaze associated with this recording. As wanted, drawn on this but also other videos
                if v in gazes_head and lead_frame_idx in gazes_head[v]:
                    for g in gazes_head[v][lead_frame_idx]:
                        if v in write_vids:
                            g.draw(frame[v], sub_pixel_fac=sub_pixel_fac, styles=gaze_headref.OverlayGazeStyles(original_scene_video_style, None))

                        # check if we need gaze on plane for drawing on any of the videos
                        plane_gaze_on_this_video = v in write_vids and study_config.mapped_video_show_gaze_on_plane_in_which is not None and v in study_config.mapped_video_show_gaze_on_plane_in_which
                        plane_gaze_or_pose_on_other_video = bool(
                            (write_vids - {v}) & set(study_config.mapped_video_show_gaze_on_plane_in_which or ())
                            or (write_vids - {v}) & set(study_config.mapped_video_show_gaze_vec_in_which or ())
                        )
                        if not poses_this or not (plane_gaze_on_this_video or plane_gaze_or_pose_on_other_video):
                            continue

                        # collect gaze on all planes for which pose or homography is available
                        plane_gazes: dict[str, tuple[float,float,gaze_worldref.Gaze]] = {}
                        for pl in poses_this:
                            if pl not in planes:
                                continue
                            this_pose = typing.cast(pose.PlanePose, poses_this[pl])
                            if this_pose.pose_successful() or this_pose.homography_successful():
                                # turn into position on plane
                                plane_gaze = gaze_worldref.from_head(this_pose, g, camera_params[v])
                                plane_gazes[pl] = (gaze_worldref.distance_from_plane(plane_gaze, planes[pl]), this_pose.pose_reprojection_error if this_pose.pose_successful() else np.nan, plane_gaze)

                        # find the plane to which gaze is closest
                        best = None if not plane_gazes else sorted(plane_gazes.keys(), key=lambda d: (sum(plane_gazes[d][0:2])/2 if not np.isnan(plane_gazes[d][1]) else plane_gazes[d][0]) if plane_gazes[d][0]<=study_config.mapped_video_gaze_to_plane_margin else math.inf)
                        # check if gaze is not too far outside all planes
                        if best is None:
                            continue

                        # draw on current video
                        if study_config.mapped_video_show_gaze_on_plane_in_which is not None and v in study_config.mapped_video_show_gaze_on_plane_in_which:
                            plane_gazes[best[0]][2].draw_on_world_video(frame[v], camera_params[v], ROI_offsets[v], sub_pixel_fac, poses_this[best[0]], styles=mapped_gaze_styles[v])

                        # also draw on other recordings, if so configured
                        # depending on configuration also includes gaze vector with origin at the camera
                        for vo in write_vids-set([v]):
                            poses_other = apose[vo]
                            if poses_other is None:
                                continue
                            matched_plane = next((pl for pl in best if pl in planes and pl in poses_other and (poses_other[pl].pose_successful() or poses_other[pl].homography_successful())), None)
                            if matched_plane is None:
                                continue
                            # draw gaze point, and gaze vector between camera position and gaze point on the other video,
                            # as configured and as possible (camera position and gaze vector require pose, not only homography)
                            draw_gaze_on_other_video(frame[vo],
                                                     ROI_offsets[vo],
                                                     poses_this[matched_plane], poses_other[matched_plane],
                                                     plane_gazes[matched_plane][2],
                                                     camera_params[vo],
                                                     study_config.mapped_video_recording_colors[v],
                                                     study_config.mapped_video_which_gaze_type_on_plane,
                                                     study_config.mapped_video_which_gaze_type_on_plane_allow_fallback,
                                                     study_config.mapped_video_show_gaze_on_plane_in_which is not None and vo in study_config.mapped_video_show_gaze_on_plane_in_which,
                                                     study_config.mapped_video_show_gaze_vec_in_which is not None and vo in study_config.mapped_video_show_gaze_vec_in_which,
                                                     sub_pixel_fac,
                                                     study_config.mapped_video_gaze_styles)

                # Draw camera position on other videos, if so configured
                if not poses_this or study_config.mapped_video_show_camera_in_which is None:
                    continue
                for vo in (write_vids - {v}) & set(study_config.mapped_video_show_camera_in_which):
                    poses_other = apose[vo]
                    if poses_other is None:
                        continue
                    matched_assembly = next((a for a in poses_this if a in assemblies and a in poses_other and poses_this[a].pose_successful() and poses_other[a].pose_successful()), None)
                    if matched_assembly is None:
                        continue
                    draw_camera_on_other_video(frame[vo], ROI_offsets[vo], poses_this[matched_assembly], poses_other[matched_assembly], camera_params[vo], study_config.mapped_video_recording_colors[v], sub_pixel_fac)


            # print info on frame and submit to to be encoded
            for v in write_vids:
                out_frame = typing.cast(np.ndarray, frame[v])
                # timecode and frame number
                if v==lead_vid and videos_ts[lead_vid].has_stretched:
                    # for reference video, if we have stretched timestamps, print those too
                    texts = [f'{lead_frame_ts/1000.:{timestamp_width[lead_vid]}.3f} ({videos_ts[lead_vid].get_timestamp(lead_frame_idx, timestamps.Type.Stretched)/1000.:{timestamp_width[lead_vid]}.3f})']
                else:
                    texts = [f'{lead_frame_ts/1000.:{timestamp_width[lead_vid]}.3f}']
                texts[0] += f' [{lead_frame_idx:{frame_idx_width[lead_vid]}d}]'
                frame_colors: list[tuple[int, int, int]] = [(0,0,0)]
                if v in other_vids:
                    if frame_ts[v] is None:
                        texts.append('no frame')
                    else:
                        texts.append(f'{frame_ts[v]/1000.:{timestamp_width[v]}.3f} [{frame_idx[v]:{frame_idx_width[v]}d}]')
                    frame_colors.append((128,128,128))
                # events, if any
                for e, idx in _get_active_episode_indices(lead_frame_idx, episodes_as_ref[v]):
                    texts.append(f'{e} {episodes_seq_nrs[v][e][idx]}')
                    frame_colors.append(episode_colors[v][e][::-1])
                # now print them all
                text_sizes: list[tuple[int,int]]= []
                baselines : list[int]           = []
                for t in texts:
                    t, b = cv2.getTextSize(t,cv2.FONT_HERSHEY_PLAIN,1.4,1)
                    text_sizes.append((t[0], t[1]))
                    baselines.append(b)
                max_height = max(text_sizes, key=lambda x: x[1])[1]
                x_end = 0
                margin = 4
                for t,f,ts,b in zip(texts,frame_colors,text_sizes,baselines):
                    x_advance = ts[0]+margin
                    cv2.rectangle(out_frame,(x_end,out_frame.shape[0]),(x_end+x_advance,out_frame.shape[0]-max_height-b-margin//2), f, -1)
                    cv2.putText(out_frame, t, (x_end+margin, out_frame.shape[0]-int(margin*1.5)), cv2.FONT_HERSHEY_PLAIN, 1.4, (0,255,255), 1)
                    x_end += x_advance

                # submit frame to be encoded
                video_utils.write_video_frame(vid_writer[v], out_frame, lead_frame_idx/vid_info[lead_vid][2])
            progress_indicator.update()

            # update gui, if any
            if has_gui:
                gui_obj = typing.cast(video_player.GUI, gui)
                for v in write_vids:
                    gui_obj.update_image(frame[v], lead_frame_ts/1000., lead_frame_idx, window_id=gui_window_ids[v])

                requests = gui_obj.get_requests()
                for r,_ in requests:
                    if r=='exit':   # only requests we need to handle
                        should_exit = True
                        break
                    if r=='close':
                        has_gui = False
                        gui_obj.stop()

        # done with this set of videos
        # clean up as needed (close GUI when nothing to show anymore)
        if has_gui and vs_idx==len(video_sets)-1:
            gui.stop()
        # close videos
        for v in write_vids:
            vid_writer[v].close()

        # if ffmpeg is on path, add audio to scene and optionally marker-assembly video
        if shutil.which('ffmpeg') is not None:
            for v in write_vids:
                rec_working_dir = working_dir / v
                file = rec_working_dir / naming.mapped_gaze_video

                # check if source file has audio
                command = ['ffprobe',
                    '-loglevel', 'error',
                    '-select_streams', 'a',
                    '-show_entries', 'stream=codec_type',
                    '-of', 'csv=p=0',
                    f'{in_videos[v]}']
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = proc.communicate()
                if err or out.decode().strip()!='audio':
                    # file does not have audio, nothing to do, skip
                    continue
                # move file to temp name
                tempName = file.parent / (file.stem + '_temp' + file.suffix)
                shutil.move(file, tempName)

                # add audio
                if v==lead_vid:
                    cmd_str = ' '.join(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'"{tempName}"', '-i', f'"{in_videos[v]}"', '-vcodec', 'copy', '-acodec', 'copy', '-map', '0:v:0', '-map', '1:a:0?', '-shortest', f'"{file}"'])
                else:
                    inputs = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'"{tempName}"', '-i', f'"{in_videos[v]}"']

                    first_frame = ref_frame_idxs[v][0]
                    if first_frame==-1:
                        # video starts later, we need to delay audio when copying
                        frame_off = int(np.argmax(np.array(ref_frame_idxs[v])>-1))
                        t_off = videos_ts[lead_vid].get_timestamp(frame_off, timestamps.Type.Stretched if videos_ts[lead_vid].has_stretched else timestamps.Type.Normal)
                        filt = f'[1:a]adelay=delays={t_off:.9f}:all=1[a];[a]apad[audio];'
                    else:
                        t_off = videos_ts[v].get_timestamp(first_frame)
                        filt = f'[1:a]atrim=start={t_off/1000},asetpts=PTS-STARTPTS[a];[a]apad[audio];'
                    cmd_str = ' '.join(inputs + ['-filter_complex', f'"{filt}"', '-map', '0:v', '-map', '[audio]', '-c:v', 'copy', '-shortest', f'"{file}"'])
                os.system(cmd_str)

                # clean up
                if file.exists():
                    tempName.unlink(missing_ok=True)
                else:
                    # something failed. Put file without audio back under output name
                    shutil.move(tempName, file)

    # update state
    session.update_action_states(working_dir, process.Action.MAKE_MAPPED_GAZE_VIDEO, process_pool.State.Completed, study_config)

def draw_gaze_on_other_video(frame_other, ROI_offset, pose_this: pose.Pose, pose_other: pose.PlanePose, plane_gaze: gaze_worldref.Gaze, camera_params_other, recording_color: drawing.RgbColor, which_gaze_on_plane, which_gaze_on_plane_allow_fallback, do_draw_gaze, do_draw_gaze_vec, sub_pixel_fac, styles: gaze_worldref.MappedGazeStyles|None=None):
    if not do_draw_gaze and not do_draw_gaze_vec:
        # nothing to do
        return

    # determine the gaze point to draw
    gaze_point_plane = plane_gaze.get_gaze_point(which_gaze_on_plane)
    gaze_point_plane_type = which_gaze_on_plane
    if gaze_point_plane is None:
        if which_gaze_on_plane_allow_fallback:
            gaze_point_plane = plane_gaze.get_gaze_point(gaze_worldref.Type.Scene_Video_Position)
            gaze_point_plane_type = gaze_worldref.Type.Scene_Video_Position
        else:
            raise RuntimeError(f'Gaze of type {which_gaze_on_plane.value} was requested, but is not available. Select a different gaze type or set allow_fallback to True.')
    if gaze_point_plane is None:
        return

    # get style in which to draw the gaze point
    styles = styles or gaze_worldref.DEFAULT_MAPPED_GAZE_STYLES
    style  = _style_with_recording_color(getattr(styles, _style_fields[gaze_point_plane_type]), recording_color)

    if not pose_this.pose_successful() or not pose_other.pose_successful():
        if not do_draw_gaze or style is None:
            return
        gaze_pos_other = pose_other.get_plane_point_on_image(gaze_point_plane, camera_params_other, ROI_offset)
        drawing.draw_circle(frame_other, gaze_pos_other, style, sub_pixel_fac)
        # can only do gaze position on plane with homography, so, exit
        return

    point_3d = np.append(gaze_point_plane, 0.).reshape(1, 3)
    gaze_pos_other = None
    # check if gaze position in camera frame is ok. If gaze point is behind this camera,
    # it won't be visible and projecting it anyway yields a nonsensical result
    if pose_other.world_frame_to_cam(point_3d)[2] > 0:
        gaze_pos_other = pose_other.get_plane_point_on_image(gaze_point_plane, camera_params_other, ROI_offset)
        if do_draw_gaze and style is not None:
            drawing.draw_circle(frame_other, gaze_pos_other, style, sub_pixel_fac)

    # also draw position of this camera on the other video, and possibly gaze vector
    if do_draw_gaze_vec and gaze_pos_other is not None:
        cam_pos_other = _camera_position_in_other_camera_image(ROI_offset, pose_this, pose_other, camera_params_other)
        if cam_pos_other is None:
            return
        # draw line connecting the camera and the gaze point
        drawing.openCVLine(frame_other, gaze_pos_other, cam_pos_other, (*recording_color[::-1],), 5, sub_pixel_fac)

def _camera_position_in_other_camera_image(ROI_offset, pose_this: pose.Pose, pose_other: pose.Pose, camera_params_other):
    if not pose_this.pose_successful() or not pose_other.pose_successful():
        return None
    # take point 0,0,0 in this camera's space (i.e. camera position) and transform to the plane's world space
    cam_pos_world_this = pose_this.cam_frame_to_world(np.zeros((1,3)))
    if pose_other.world_frame_to_cam(cam_pos_world_this)[2] <= 0:
        # other video's camera is behind this camera, won't be visible
        # and projecting it anyway yields a nonsensical result
        return None
    # determine where that camera position is in the other video
    return pose_other.world_frame_to_camera_image(cam_pos_world_this, camera_params_other, ROI_offset)

def draw_camera_on_other_video(frame_other, ROI_offset, pose_this: pose.Pose, pose_other: pose.Pose, camera_params_other, clr: drawing.RgbColor, sub_pixel_fac):
    cam_pos_other = _camera_position_in_other_camera_image(ROI_offset, pose_this, pose_other, camera_params_other)
    if cam_pos_other is None:
        return
    drawing.openCVCircle(frame_other, cam_pos_other, 3, (*clr[::-1],), 1, sub_pixel_fac)