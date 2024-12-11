import pathlib
import numpy as np
import cv2
from imgui_bundle import imgui

from ffpyplayer.player import MediaPlayer

import sys
isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, drawing, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, propagating_thread, timestamps, transforms
from glassesTools.gui.video_player import GUI


from .. import config, episode, naming, plane, process, session, synchronization

# This script shows a video player that is used to indicate the interval(s)
# during which the poster should be found in the video and in later
# steps data quality computed. So this interval/these intervals would for
# instance be the exact interval during which the subject performs the
# validation task.
# This script can be run directly on recordings converted to the common format,
# but output from steps c_detectMarkers and d_gazeToPoster
# (which can be run before this script, they will just process the whole video)
# will also be shown if available.

_event_type_to_key_map = {
    annotation.Event.Validate       : imgui.Key.v,
    annotation.Event.Sync_Camera    : imgui.Key.c,
    annotation.Event.Sync_ET_Data   : imgui.Key.e,
    annotation.Event.Trial          : imgui.Key.t,
}

def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
    # if show_poster, also draw poster with gaze overlaid on it (if available)
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    gui = GUI(use_thread = False)
    gui.add_window(f'{working_dir.parent.name}, {working_dir.name}')

    proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui), kwargs=study_settings, cleanup_fun=gui.stop)
    proc_thread.start()
    gui.start()
    proc_thread.join()


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def  = study_config.session_def.get_recording_def(working_dir.name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    if rec_def.type==session.RecordingType.Camera:
        has_gaze, has_plane_gaze, has_plane_pose = False, False, False
        # no episode.Event.Sync_ET_Data for camera recordings, remove
        if annotation.Event.Sync_ET_Data in study_config.episodes_to_code:
            study_config.episodes_to_code.remove(annotation.Event.Sync_ET_Data)
        # no episode.Event.Validate for camera recordings, remove
        if annotation.Event.Validate in study_config.episodes_to_code:
            study_config.episodes_to_code.remove(annotation.Event.Validate)
    elif rec_def.type==session.RecordingType.Eye_Tracker:
        # Read gaze data
        has_gaze = True
        gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR',''])[0]

        planes: set[str] = set()
        for e in [annotation.Event.Validate, annotation.Event.Trial]:
            if e in study_config.planes_per_episode:
                planes.update(study_config.planes_per_episode[e])
        if study_config.get_cam_movement_for_et_sync_method=='plane' and annotation.Event.Sync_ET_Data in study_config.planes_per_episode:
            planes.update(study_config.planes_per_episode[annotation.Event.Sync_ET_Data])

        # Read gaze on poster data, if available
        plane_files = [working_dir/f'{naming.world_gaze_prefix}{p}.tsv' for p in planes]
        plane_gazes = {p:gaze_worldref.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
        has_plane_gaze = not not plane_gazes

        if has_plane_gaze:
            planes_setup: dict[str, dict[str]] = {}
            for p in planes:
                p_def = [pl for pl in study_config.planes if pl.name==p][0]
                planes_setup[p] = plane.get_plane_from_definition(p_def, config_dir/p)

        # Read plane poses, if available
        plane_files = [working_dir/f'{naming.plane_pose_prefix}{p}.tsv' for p in planes]
        poses = {p:gt_plane.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
        has_plane_pose = not not poses
    else:
        raise ValueError(f'recording type "{rec_def.type}" is not understood')

    # get camera calibration info
    cam_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

    # get previous interval coding, if available
    coding_file = working_dir / naming.coding_file
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file), study_config.episodes_to_code)
    else:
        episodes = episode.get_empty_marker_dict(study_config.episodes_to_code)
    episodes_to_code = {e for e in episodes}
    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        # any trial coding there is should be discarded
        episodes.pop(annotation.Event.Trial, None)
        # mark trial as not codable
        if annotation.Event.Trial in episodes_to_code:
            episodes_to_code.remove(annotation.Event.Trial)
        # if there is trial coding for the reference recording, get them and show them (read only)
        if annotation.Event.Trial in study_config.episodes_to_code:
            all_recs = [r.name for r in study_config.session_def.recordings if r.name!=study_config.sync_ref_recording]
            episodes[annotation.Event.Trial] = synchronization.get_episode_frame_indices_from_ref(working_dir, annotation.Event.Trial, rec_def.name, study_config.sync_ref_recording, all_recs, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, study_config.sync_ref_stretch_which, missing_ref_coding_ok=True)
            # if nothing found, remove again so we don't have a useless empty track
            if not episodes[annotation.Event.Trial]:
                episodes.pop(annotation.Event.Trial, None)
    episodes = annotation.flatten_annotation_dict(episodes) # NB: also ensures ordering is consistent

    # set up video playback
    # 1. timestamp info for relating audio to video frames
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)
    # 2. mediaplayer for the actual video playback, with sound if available
    ff_opts = {'volume': 1., 'sync': 'audio', 'framedrop': True}
    player = MediaPlayer(str(in_video), ff_opts=ff_opts)
    gui.set_playing(True)

    # set up annotation GUI
    gui.set_allow_pause(True)
    gui.set_allow_seek(True)
    gui.set_allow_timeline_zoom(True)
    gui.set_show_controls(True, gui.main_window_id)
    gui.set_allow_annotate(episodes_to_code, {e:_event_type_to_key_map[e] for e in episodes})
    gui.set_show_timeline(True, video_ts, episodes, gui.main_window_id)
    gui.set_show_annotation_label(True, gui.main_window_id)
    gui.set_show_action_tooltip(True, gui.main_window_id)

    # show
    sub_pixel_fac = 8   # for sub-pixel positioning
    should_exit = False
    has_requested_focus = not isMacOS # False only if on Mac OS, else True since its a no-op
    while True:
        frame, val = player.get_frame(force_refresh=True)
        if val == 'eof':
            player.toggle_pause()
        if frame is not None:
            image, pts = frame
            width, height = image.get_size()
            frame = cv2.cvtColor(np.asarray(image.to_memoryview()[0]).reshape((height,width,3)), cv2.COLOR_RGB2BGR)
            del image

        if frame is not None:
            # the audio is my shepherd and nothing shall I lack :-)
            frame_idx = video_ts.find_frame(pts*1000)  # pts is in seconds, our frame timestamps are in ms

            # if we have plane pose, draw plane origin on video
            if has_plane_pose:
                for p in planes:
                    if p in poses and frame_idx in poses[p]:
                        a = poses[p][frame_idx].get_origin_on_image(cam_params)
                        drawing.openCVCircle(frame, a, 3, (0,255,0), -1, sub_pixel_fac)
                        drawing.openCVLine(frame, (a[0],a[1]-10), (a[0],a[1]+10), (0,255,0), 1, sub_pixel_fac)
                        drawing.openCVLine(frame, (a[0]-10,a[1]), (a[0]+10,a[1]), (0,255,0), 1, sub_pixel_fac)

            # if have gaze for this frame, draw it
            # NB: usually have multiple gaze samples for a video frame, draw one
            if has_gaze:
                if frame_idx in gazes:
                    gazes[frame_idx][0].draw(frame, cam_params, sub_pixel_fac)

            # if have gaze in world info, draw it too (also only first sample)
            if has_plane_gaze:
                # first collect for which plane to draw it
                this_plane_gazes: dict[str, gaze_worldref.Gaze] = {}
                for p in planes:
                    if p in plane_gazes and frame_idx in plane_gazes[p]:
                        this_plane_gazes[p] = gaze_worldref.distance_from_plane(plane_gazes[p][frame_idx][0], planes_setup[p])
                # get best gaze (closest to a plane)
                best = None if not this_plane_gazes else sorted(this_plane_gazes.keys(), key=lambda d: this_plane_gazes[d] if this_plane_gazes[d]<=study_config.video_gaze_to_plane_margin else np.inf)
                # check if gaze is not too far outside all planes, draw
                if best is not None:
                    p = best[0]
                    plane_gazes[p][frame_idx][0].draw_on_world_video(frame, cam_params, sub_pixel_fac, None if not p in poses or not frame_idx in poses[p] else poses[p][frame_idx])

            if frame is not None:
                gui.update_image(frame, pts, frame_idx, window_id=gui.main_window_id)

        if not has_requested_focus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            has_requested_focus = True

        requests = gui.get_requests()
        for r,p in requests:
            match r:
                case 'toggle_pause':
                    player.toggle_pause()
                    if not player.get_pause():
                        player.seek(0)  # needed to get frames rolling in again, apparently, after seeking occurred while paused
                    gui.set_playing(not player.get_pause())
                case 'seek':
                    player.seek(p, relative=False)
                case 'delta_frame':
                    new_ts = video_ts.get_timestamp(frame_idx+p)
                    if new_ts != -1.:
                        step = (new_ts-video_ts.get_timestamp(max(0,frame_idx)))/1000
                        player.seek(pts+step, relative=False)
                case 'delta_time':
                    player.seek(pts+p, relative=False)
                case 'add_coding':
                    event,frame_idx = p
                    if frame_idx not in episodes[event]:
                        episodes[event].append(frame_idx)
                        episodes[event].sort()
                        gui.notify_annotations_changed()
                case 'delete_coding':
                    event,frame_idxs = p
                    episodes[event] = [i for i in episodes[event] if i not in frame_idxs]
                    gui.notify_annotations_changed()
                case 'exit':
                    should_exit = True
                    break
        if should_exit:
            break

    player.close_player()
    gui.stop()

    # store coded intervals to file
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        # any (read only) trial coding there is should not be written to file
        episodes.pop(annotation.Event.Trial, None)
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.CODE_EPISODES, process.State.Completed, study_config)