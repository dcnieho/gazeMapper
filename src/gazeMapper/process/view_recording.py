import pathlib
import sys

import cv2
import numpy as np
from ffpyplayer.player import MediaPlayer

from glassesTools import gaze_headref, naming as gt_naming, ocv, propagating_thread, timestamps
from glassesTools.gui.video_player import GUI

from .. import config, session


isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit


def run(working_dir: str | pathlib.Path, config_dir: str | pathlib.Path | None = None, **study_settings):
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir = pathlib.Path(config_dir)

    gui = GUI(use_thread=False)
    gui.add_window(f'{working_dir.parent.name}, {working_dir.name}')
    gui.set_screenshot_filename_prefix(f'{working_dir.parent.name}_{working_dir.name}')

    proc_thread = propagating_thread.PropagatingThread(
        target=do_the_work,
        args=(working_dir, config_dir, gui),
        kwargs=study_settings,
        cleanup_fun=gui.stop,
    )
    proc_thread.start()
    gui.start()
    proc_thread.join()


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    study_config = config.read_study_config_with_overrides(
        config_dir,
        {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir},
        **study_settings,
    )
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    if not in_video.is_file():
        raise FileNotFoundError(f'Input video file "{in_video}" for recording "{rec_def.name}" not found')

    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)
    frame_info_file = in_video.parent / (in_video.stem + gt_naming.frame_info_suffix)
    frame_info = ocv.FrameInfoHandler(frame_info_file)

    gazes = {}
    cam_params = None
    if rec_def.type == session.RecordingType.Eye_Tracker:
        gazes = gaze_headref.read_dict_from_file(
            working_dir / gt_naming.gaze_data_fname,
            ts_column_suffixes=['VOR', ''],
        )[0]
        cam_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)
    elif rec_def.type != session.RecordingType.Camera:
        raise ValueError(f'recording type "{rec_def.type}" is not understood')

    player = MediaPlayer(str(in_video), ff_opts={'volume': 1., 'sync': 'audio', 'framedrop': True})
    gui.set_playing(True)
    gui.set_allow_pause(True)
    gui.set_allow_seek(True)
    gui.set_allow_timeline_zoom(True)
    gui.set_show_controls(True, gui.main_window_id)
    gui.set_show_timeline(True, video_ts, {}, gui.main_window_id)
    gui.set_show_annotation_label(False, gui.main_window_id)
    gui.set_show_action_tooltip(False, gui.main_window_id)

    should_exit = False
    has_requested_focus = not isMacOS
    try:
        while True:
            frame, val = player.get_frame(force_refresh=True)
            if val == 'eof':
                player.toggle_pause()
            if frame is not None:
                image, pts = frame
                width, height = image.get_size()
                frame = cv2.cvtColor(np.asarray(image.to_memoryview()[0]).reshape((height, width, 3)), cv2.COLOR_RGB2BGR)
                del image

                frame_idx = video_ts.find_frame(pts * 1000)
                roi_offset = [0., 0.]
                if frame_info.data is not None:
                    info = frame_info.get_frame_info(frame_idx)
                    if 'offset_x' in info and 'offset_y' in info:
                        roi_offset = [info['offset_x'], info['offset_y']]
                if cam_params is not None and frame_idx in gazes:
                    gazes[frame_idx][0].draw(frame, cam_params, 8, ROI_offset=roi_offset)
                gui.update_image(frame, pts, frame_idx, window_id=gui.main_window_id)

            if gui.is_running() and not has_requested_focus:
                AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
                has_requested_focus = True

            requests = gui.get_requests()
            for request, parameter in requests:
                if request == 'toggle_pause':
                    player.toggle_pause()
                    if not player.get_pause():
                        player.seek(0)
                    gui.set_playing(not player.get_pause())
                elif request == 'seek':
                    player.seek(parameter, relative=False)
                elif request == 'delta_frame' and frame is not None:
                    new_ts = video_ts.get_timestamp(frame_idx + parameter)
                    if new_ts != -1.:
                        step = (new_ts - video_ts.get_timestamp(max(0, frame_idx))) / 1000
                        player.seek(pts + step, relative=False)
                elif request == 'delta_time' and frame is not None:
                    player.seek(pts + parameter, relative=False)
                elif request == 'exit':
                    should_exit = True
                    break
            if should_exit:
                break
    finally:
        player.close_player()
        gui.stop()