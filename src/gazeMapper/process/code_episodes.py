import pathlib
import numpy as np
import cv2
import copy
import typing

from ffpyplayer.player import MediaPlayer

import sys

isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, drawing, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, pose as gt_pose, process_pool, propagating_thread, timestamps
from glassesTools.camera_recording import Type as CameraRecordingType
from glassesTools.gui.video_player import GUI


from .. import config, episode, naming, plane, process, session, synchronization

# This script shows a video player that is used to indicate the interval(s)
# during which the poster should be found in the video and in later
# steps data quality computed. So this interval/these intervals would for
# instance be the exact interval during which the subject performs the
# validation task.
# This script can be run directly on recordings converted to the common format,
# but output from the detectMarkers and gazeToPoster actions (if available)
# will also be shown.

def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, **study_settings):
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
    planes = {v for cs in study_config.coding_setup for v in cs['planes']}
    if rec_def.type==session.RecordingType.Camera:
        has_gaze, has_plane_gaze = False, False
        # unless head-attached and this is the reference recording, no Sync_ET_Data or Validate events for camera recordings, remove
        if not (study_config.sync_ref_recording==rec_def.name and rec_def.camera_recording_type==CameraRecordingType.Head_attached):
            study_config.coding_setup = [cs for cs in study_config.coding_setup if cs['event_type'] not in (annotation.EventType.Sync_ET_Data, annotation.EventType.Validate)]
    elif rec_def.type==session.RecordingType.Eye_Tracker:
        # Read gaze data
        has_gaze = True
        gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR',''])[0]

        # Read gaze on poster data, if available
        plane_files = [working_dir/f'{naming.world_gaze_prefix}{p}.tsv' for p in planes]
        plane_gazes = {p:gaze_worldref.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
        has_plane_gaze = not not plane_gazes

        if has_plane_gaze:
            planes_setup: dict[str, gt_plane.Plane] = {}
            for p in planes:
                p_def = [pl for pl in study_config.planes if pl.name==p][0]
                planes_setup[p] = plane.get_plane_from_definition(p_def, config_dir/p)
    else:
        raise ValueError(f'recording type "{rec_def.type}" is not understood')

    # Read plane poses, if available
    plane_files = [working_dir/f'{naming.plane_pose_prefix}{p}.tsv' for p in planes]
    poses = {p:gt_pose.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
    has_plane_pose = not not poses

    # get camera calibration info
    cam_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

    # get previous interval coding, if available
    episodes, episodes_to_code = episode.load_episodes_from_all_recordings(study_config, working_dir, error_if_unwanted_found=False)
    episodes = annotation.flatten_annotation_dict(episodes)
    episodes_original = copy.deepcopy(episodes)

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
    gui.set_allow_annotate(episodes_to_code,
                           {cs['name']:cs['hotkey'] for cs in study_config.coding_setup if cs['hotkey'] is not None},
                           {cs['name']:cs['description'] for cs in study_config.coding_setup if cs['description'] is not None})
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
                best = None if not this_plane_gazes else sorted(this_plane_gazes.keys(), key=lambda d: this_plane_gazes[d] if this_plane_gazes[d]<=study_config.mapped_video_gaze_to_plane_margin else np.inf)
                # check if gaze is not too far outside all planes, draw
                if best is not None:
                    p = best[0]
                    plane_gazes[p][frame_idx][0].draw_on_world_video(frame, cam_params, sub_pixel_fac, None if not p in poses or not frame_idx in poses[p] else poses[p][frame_idx])

            if frame is not None:
                gui.update_image(frame, pts, frame_idx, window_id=gui.main_window_id)

        if gui.is_running() and not has_requested_focus:
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

    # early exit if nothing has changed
    if episodes==episodes_original:
        if session.get_action_states(working_dir, True)[process.Action.CODE_EPISODES]==process_pool.State.Completed:
            return
        session.update_action_states(working_dir, process.Action.CODE_EPISODES, process_pool.State.Completed, study_config, unchanged=True)
        return

    # store coded intervals to file
    to_remove = [nm for nm in episodes if nm not in episodes_to_code]
    for nm in to_remove:
        episodes.pop(nm)
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), working_dir / naming.coding_file)

    # update state
    session.update_action_states(working_dir, process.Action.CODE_EPISODES, process_pool.State.Completed, study_config)