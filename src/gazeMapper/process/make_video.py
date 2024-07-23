import shutil
import os
import pathlib

import cv2
import numpy as np
import threading
from typing import Any, Callable

from glassesTools import annotation, aruco, gaze_headref, ocv, plane, timestamps, transforms
from glassesTools.video_gui import GUI

from .. import config, episode, marker, naming, session, synchronization
from .detect_markers import _get_plane_setup, _get_sync_function

from ffpyplayer.writer import MediaWriter
from ffpyplayer.pic import Image
import ffpyplayer.tools
from fractions import Fraction


def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_rejected_markers=False, show_visualization=False):
    # if show_visualization, the generated video is shown as it is created in a viewer
    working_dir  = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    if show_visualization:
        # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
        gui = GUI(use_thread = False)
        main_win_id = gui.add_window(working_dir.name)
        gui.set_show_controls(True)
        gui.set_show_play_percentage(True)

        proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, main_win_id, show_rejected_markers))
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, None, show_rejected_markers)

def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, main_win_id: int, show_rejected_markers: bool):
    has_gui = gui is not None
    sub_pixel_fac = 8   # for anti-aliased drawing

    # get info about the study the recording is a part of
    study_config = config.Study.load_from_json(config_dir)
    assert not not study_config.make_video_which, f'There are no videos to be made (make_video_which is not defined or null in the study setup)'

    # get session info
    session_info = session.Session.load_from_json(working_dir)

    # load info for all recordings in the recording session and setup wanted output videos
    episodes        : dict[str, dict[annotation.Event, list[list[int]]]]    = {}
    gazes_head      : dict[str, dict[int, list[gaze_headref.Gaze]]]         = {}
    in_videos       : dict[str, pathlib.Path]                               = {}
    camera_params   : dict[str, ocv.CameraParams]                           = {}
    videos_ts       : dict[str, timestamps.VideoTimestamps]                 = {}
    pose_estimators : dict[str, aruco.PoseEstimator]                        = {}
    gui_window_ids  : dict[str, int]                                        = {}
    vid_info        : dict[str, tuple[int, int, float]]                     = {}
    recs = [r for r in session_info.recordings]
    for rec in recs:
        rec_def = session_info.recordings[rec].defition
        rec_working_dir = working_dir / rec

        # get interval(s) coded to be analyzed, if any
        episodes[rec] = episode.list_to_marker_dict(episode.read_list_from_file(rec_working_dir / naming.coding_file), study_config.episodes_to_code)

        # get other setup
        sync_target_function    = _get_sync_function(study_config, rec_def, None if annotation.Event.Sync_ET_Data not in episodes[rec] else episodes[rec][annotation.Event.Sync_ET_Data])
        planes_setup, _         = _get_plane_setup(study_config, config_dir)

        # Read gaze data
        ts_column_suffixes = []
        if study_config.sync_ref_recording and rec==study_config.sync_ref_recording:
            ts_column_suffixes = ['ref', 'VOR', ''] # we want to use synced gaze data for these videos, if available
        gazes_head[rec]         = gaze_headref.read_dict_from_file(rec_working_dir / 'gazeData.tsv', ts_column_suffixes=ts_column_suffixes)[0]

        # get camera calibration info
        camera_params[rec]      = ocv.CameraParams.read_from_file(rec_working_dir / "calibration.xml")

        # build pose estimator
        in_videos[rec] = session.get_video_path(session_info.recordings[rec].info)     # get video file to process
        videos_ts[rec] = timestamps.VideoTimestamps(rec_working_dir / "frameTimestamps.tsv")
        pose_estimators[rec] = aruco.PoseEstimator(in_videos[rec], videos_ts[rec], camera_params[rec])
        for p in planes_setup:
            pose_estimators[rec].add_plane(p, planes_setup[p])
        for i in (markers:=marker.get_marker_dict_from_list(study_config.individual_markers)):
            pose_estimators[rec].add_individual_marker(i, markers[i])
        if sync_target_function is not None:
            pose_estimators[rec].register_extra_processing_fun('sync', *sync_target_function)
        if study_config.sync_ref_recording and rec!=study_config.sync_ref_recording:
            pose_estimators[rec].set_do_report_frames(False)

        if rec in study_config.make_video_which:
            pose_estimators[rec].set_visualize_on_frame(True, show_rejected_markers)

            # get video file info
            vid_info[rec] = pose_estimators[rec].get_video_info()

            # if we have a gui, set it up
            if has_gui:
                if (study_config.sync_ref_recording and rec==study_config.sync_ref_recording) or (not study_config.sync_ref_recording and len(vid_writer)==1):
                    gui_window_ids[rec] = main_win_id
                    gui.set_show_timeline(True, videos_ts[rec], episodes[rec], main_win_id)
                else:
                    gui_window_ids[rec] = gui.add_window(rec)
                gui.set_frame_size(vid_info[rec], gui_window_ids[rec])

    # get frame sync info
    if study_config.sync_ref_recording:
        sync = synchronization.get_sync_for_recs(working_dir, recs, study_config.sync_ref_recording, study_config.do_time_stretch, study_config.sync_average_recordings)
        ref_frame_idxs: dict[str, list[int]] = {}
        for r in sync.index.get_level_values('recording').unique():
            ref_frame_idxs[r] = synchronization.get_frame_idxs_from_reference(r, sync, videos_ts[study_config.sync_ref_recording].indices,
                                                                              videos_ts[r].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                              study_config.do_time_stretch, study_config.stretch_which)

    video_sets: list[tuple[str, list[str]]] = []
    if study_config.sync_ref_recording:
        video_sets.append((study_config.sync_ref_recording,[r for r in study_config.make_video_which if r!=study_config.sync_ref_recording]))
    else:
        video_sets.extend([(r,[]) for r in study_config.make_video_which])

    # per set of videos
    should_exit = False
    for lead_vid, other_vids in video_sets:
        if should_exit:
            break

        vid_writer          : dict[str, MediaWriter]                = {}
        frame               : dict[str, np.ndarray]                 = {}
        frame_idx           : dict[str, int]                        = {}
        frame_ts            : dict[str, float]                      = {}
        pose                : dict[str, dict[str, plane.Pose]]      = {}
        sync_target_signal  : dict[str, dict[str, list[int, Any]]]  = {}

        all_vids = [lead_vid] + other_vids

        # open output video files
        for v in all_vids:
            # get which pixel format
            codec    = ffpyplayer.tools.get_format_codec(fmt=pathlib.Path(naming.process_video).suffix[1:])
            pix_fmt  = ffpyplayer.tools.get_best_pix_fmt('bgr24',ffpyplayer.tools.get_supported_pixfmts(codec))
            fpsFrac  = Fraction(vid_info[lead_vid][2]).limit_denominator(10000).as_integer_ratio()
            # scene video
            out_opts = {'pix_fmt_in':'bgr24', 'pix_fmt_out':pix_fmt, 'width_in':vid_info[v][0], 'height_in':vid_info[v][1], 'frame_rate':fpsFrac}
            vid_writer[v] = MediaWriter(str(working_dir / v / naming.process_video), [out_opts], overwrite=True)

        # now make the video
        while True:
            status, pose[lead_vid], _, sync_target_signal[lead_vid], (frame[lead_vid], frame_idx[lead_vid], frame_ts[lead_vid]) = \
                pose_estimators[lead_vid].process_one_frame()
            # TODO: if there is a discontinuity, fill in the missing frames so audio stays in sync
            # check if we're done
            if status==aruco.Status.Finished or frame_idx[lead_vid]>1600:
                break
            # NB: no need to handle aruco.Status.Skip, since we didn't provide the pose estimator with any analysis intervals (we want to process the whole video)

            for v in other_vids:
                # find corresponding frame
                fr_idx_this = ref_frame_idxs[v][frame_idx[lead_vid]]
                if fr_idx_this==-1:
                    _, pose[v], _, sync_target_signal[v], (frame[v], frame_idx[v], frame_ts[v]) = \
                        None, None, None, None, (None, None, None)
                else:
                    # read it
                    _, pose[v], _, sync_target_signal[v], (frame[v], frame_idx[v], frame_ts[v]) = \
                        pose_estimators[v].process_one_frame()

            for v in all_vids:
                if frame[v] is None:
                    # we don't have a valid frame, use a fully black frame
                    frame[v] = np.zeros((vid_info[v][1],vid_info[v][0],3), np.uint8)   # black image

            for v in all_vids:
                img = Image(plane_buffers=[frame[v].flatten().tobytes()], pix_fmt='bgr24', size=(frame[v].shape[1], frame[v].shape[0]))
                vid_writer[v].write_frame(img=img, pts=frame_idx[lead_vid]/vid_info[lead_vid][2])

        # done
        for v in all_vids:
            vid_writer[v].close()

        # if ffmpeg is on path, add audio to scene and optionally board video
        if shutil.which('ffmpeg') is not None:
            for v in all_vids:
                rec_working_dir = working_dir / v

                file = rec_working_dir / naming.process_video

                # move file to temp name
                tempName = file.parent / (file.stem + '_temp' + file.suffix)
                shutil.move(file, tempName)

                # add audio
                if v==lead_vid:
                    cmd_str = ' '.join(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'"{tempName}"', '-i', f'"{in_videos[v]}"', '-vcodec', 'copy', '-acodec', 'copy', '-map', '0:v:0', '-map', '1:a:0?', f'"{file}"'])
                else:
                    pass # TODO
                os.system(cmd_str)

                # clean up
                if file.exists():
                    tempName.unlink(missing_ok=True)
                else:
                    # something failed. Put file without audio back under output name
                    shutil.move(tempName, file)