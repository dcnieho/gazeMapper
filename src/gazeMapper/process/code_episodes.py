import pathlib
import numpy as np

import cv2
from imgui_bundle import imgui
import threading

from ffpyplayer.player import MediaPlayer

import sys
isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import drawing, gaze_headref, gaze_worldref, ocv, plane, timestamps
from glassesTools.video_gui import GUI, generic_tooltip_drawer


from .. import  episode, session

# This script shows a video player that is used to indicate the interval(s)
# during which the poster should be found in the video and in later
# steps data quality computed. So this interval/these intervals would for
# instance be the exact interval during which the subject performs the
# validation task.
# This script can be run directly on recordings converted to the common format,
# but output from steps c_detectMarkers and d_gazeToPoster
# (which can be run before this script, they will just process the whole video)
# will also be shown if available.

_key_to_event_type_map = {
    'v': episode.Event.Validate,
    'c': episode.Event.Sync_Camera,
    'e': episode.Event.Sync_VOR,
    'm': episode.Event.Map,
}
_event_type_to_key_map = {v: k for k, v in _key_to_event_type_map.items()}

_key_tooltip = {
    "h": "Back 1 s, shift+H: back 10 s",
    "l": "Forward 1 s, shift+L: forward 10 s",
    "j": "Back 1 frame",
    "k": "Forward 1 frame",
    "p": "Pause or resume playback",
    _event_type_to_key_map[episode.Event.Validate]: "Mark frame as start or end of validation episode",
    _event_type_to_key_map[episode.Event.Sync_Camera]: "Mark frame as camera sync point",
    _event_type_to_key_map[episode.Event.Sync_VOR]: "Mark frame as start or end of VOR synchronization episode",
    _event_type_to_key_map[episode.Event.Map]: "Mark frame as start or end of analysis (gaze-to-world mapping) episode",
    "d": "Delete frame marking(s)",
    "q": "Quit",
    'n': 'Next'
}


stopAllProcessing = False
def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path, rec_type: session.RecordingType):
    # if show_poster, also draw poster with gaze overlaid on it (if available)
    working_dir = pathlib.Path(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print('processing: {}'.format(working_dir.name))

    # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    gui = GUI(use_thread = False)

    gui.set_interesting_keys(list(_key_tooltip.keys()))
    main_win_id = gui.add_window(working_dir.name)

    proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, main_win_id, rec_type))
    proc_thread.start()
    gui.start()
    proc_thread.join()
    return stopAllProcessing


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, main_win_id: int, rec_type: session.RecordingType):
    global stopAllProcessing

    # get info about recording
    _, in_video = session.read_recording_info(working_dir, rec_type)
    if rec_type==session.RecordingType.Camera:
        hasGaze, hasPosterGaze, hasPosterPose = False, False, False
    elif rec_type==session.RecordingType.EyeTracker:
        # Read gaze data
        hasGaze = True
        gazes = gaze_headref.read_dict_from_file(working_dir / 'gazeData.tsv')[0]

        # Read gaze on poster data, if available
        hasPosterGaze = False
        if (working_dir / 'gazePosterPos.tsv').is_file():
            try:
                gazesPoster = gaze_worldref.read_dict_from_file(working_dir / 'gazePosterPos.tsv')
                hasPosterGaze = True
            except:
                # ignore when file can't be read or is empty
                pass

        # Read pose of poster, if available
        hasPosterPose = False
        if (working_dir / 'posterPose.tsv').is_file():
            try:
                poses = plane.read_dict_from_file(working_dir / 'posterPose.tsv')
                hasPosterPose = True
            except:
                # ignore when file can't be read or is empty
                pass
    else:
        raise ValueError(f'recording type "{rec_type}" is not understood')

    # get camera calibration info
    cameraParams= ocv.CameraParams.readFromFile(working_dir / "calibration.xml")
    hasCamCal   = cameraParams.has_intrinsics()

    # get previous interval coding, if available
    coding_file = working_dir / 'coding.tsv'
    if coding_file.is_file():
        episodes = episode.list_to_marker_dict(episode.read_list_from_file(coding_file))
        # flatten
        for e in episodes:
            episodes[e] = [i for iv in episodes[e] for i in iv]
    else:
        episodes = episode.get_empty_marker_dict()
    gui.register_draw_callback('status',lambda: my_tooltip(episodes, _key_tooltip))

    # set up video playback
    # 1. timestamp info for relating audio to video frames
    t2i = timestamps.Timestamp2Index( working_dir / 'frameTimestamps.tsv' )
    i2t = timestamps.Idx2Timestamp( working_dir / 'frameTimestamps.tsv' )
    # 2. mediaplayer for the actual video playback, with sound if available
    ff_opts = {'volume': 1., 'sync': 'audio', 'framedrop': True}
    player = MediaPlayer(str(in_video), ff_opts=ff_opts)

    # show
    subPixelFac = 8   # for sub-pixel positioning
    armLength = 20    # mm
    stopAllProcessing = False
    hasRequestedFocus = not isMacOS # False only if on Mac OS, else True since its a no-op
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
            frame_idx = t2i.find(pts*1000)  # pts is in seconds, our frame timestamps are in ms

            # if we have poster pose, draw poster origin on video
            if hasPosterPose and frame_idx in poses and hasCamCal:
                drawing.openCVFrameAxis(frame, cameraParams.camera_mtx, cameraParams.distort_coeffs, poses[frame_idx].pose_R_vec, poses[frame_idx].pose_T_vec, armLength, 3, subPixelFac)

            # if have gaze for this frame, draw it
            # NB: usually have multiple gaze samples for a video frame, draw one
            if hasGaze:
                if frame_idx in gazes:
                    gazes[frame_idx][0].draw(frame, cameraParams, subPixelFac)

            # if have gaze in world info, draw it too (also only first)
            if hasPosterGaze and frame_idx in gazesPoster:
                if hasCamCal:
                    gazesPoster[frame_idx][0].drawOnWorldVideo(frame, cameraParams, subPixelFac)

            if frame is not None:
                gui.update_image(frame, pts, frame_idx, window_id = main_win_id)

        if not hasRequestedFocus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            hasRequestedFocus = True

        keys = gui.get_key_presses()
        # seek: don't ask me why, but relative seeking works best for backward,
        # and seeking to absolute pts best for forward seeking.
        if 'j' in keys:
            step = (i2t.get(frame_idx)-i2t.get(max(0,frame_idx-1)))/1000
            player.seek(-step)                              # back one frame
        if 'k' in keys:
            nextTs = i2t.get(frame_idx+1)
            if nextTs != -1.:
                step = (nextTs-i2t.get(max(0,frame_idx)))/1000
                player.seek(pts+step, relative=False)       # forward one frame
        if 'h' in keys or 'H' in keys:
            step = 1 if 'h' in keys else 10
            player.seek(-step)                              # back one or ten seconds
        if 'l' in keys or 'L' in keys:
            step = 1 if 'l' in keys else 10
            player.seek(pts+step, relative=False)           # forward one or ten seconds

        if 'p' in keys:
            player.toggle_pause()
            if not player.get_pause():
                player.seek(0)  # needed to get frames rolling in again, apparently, after seeking occurred while paused

        code_keys = [x for x in keys if x in _key_to_event_type_map.keys()]
        for code in code_keys:
            # determine which event
            event = _key_to_event_type_map[code]
            if not frame_idx in episodes[event]:
                episodes[event].append(frame_idx)
                episodes[event].sort()
        if 'd' in keys:
            for e in episodes:
                if frame_idx in episodes[e]:
                    episodes[e].remove(frame_idx)

        if 'q' in keys:
            # quit fully
            stopAllProcessing = True
            break
        if 'n' in keys:
            # goto next
            break

        closed, = gui.get_state()
        if closed:
            stopAllProcessing = True
            break

    player.close_player()
    gui.stop()

    # store coded intervals to file
    episode.write_list_to_file(episode.marker_dict_to_list(episodes), coding_file)

    return stopAllProcessing

def markers_to_string(episodes: dict[episode.Event,list[int]]):
    descs: list[str] = []
    for e in episodes:
        parts: list[str] = []
        if episode.type_map[e]==episode.Type.Point:
            parts = [str(x) for x in episodes[e]]
        else:
            for m in range(0,len(episodes[e])-1,2):     # -1 to make sure we don't try incomplete intervals
                parts.append('{} -- {}'.format(*episodes[e][m:m+2]))
            if len(episodes[e])%2:                      # open interval
                parts.append('{} -- xx'.format(episodes[e][-1]))
        marks = ', '.join(parts)
        if marks:
            descs.append(f'{e.value}: {marks}')

    return ', '.join(descs)

def my_tooltip(episodes: dict[episode.Event,list[int]], key_info_dict: dict[str,str]):
    imgui.same_line()
    imgui.text(markers_to_string(episodes))
    generic_tooltip_drawer(key_info_dict)