import pathlib
import threading
import cv2
import numpy as np

from glassesTools import drawing, gaze_headref, gaze_worldref, intervals, ocv, plane as gt_plane, timestamps
from glassesTools.video_gui import GUI, generic_tooltip_drawer, qns_tooltip


from . import naming
from .. import config, episode, plane, session


stopAllProcessing = False
def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, show_planes=True, show_only_intervals=True):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected planes and projected gaze
    # if show_poster, gaze in space od each plane is also drawn in a separate windows
    # if show_only_intervals, only the coded mapping episodes (if available) are shown in the viewer while the rest of the scene video is skipped past
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # if we need gui, we run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    if show_visualization:
        gui = GUI(use_thread = False)
        gui.set_interesting_keys('qns')
        gui.register_draw_callback('status',lambda: generic_tooltip_drawer(qns_tooltip()))
        frame_win_id = gui.add_window(working_dir.name)

        proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, frame_win_id, show_planes, show_only_intervals))
        proc_thread.start()
        gui.start()
        proc_thread.join()
        return stopAllProcessing
    else:
        return do_the_work(working_dir, config_dir, None, None, False, False)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, frame_win_id: int, show_planes: bool, show_only_intervals: bool):
    global stopAllProcessing

    # get info about the study the recording is a part of
    study_config = config.Study.load_from_json(config_dir)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    assert rec_def.type==session.RecordingType.EyeTracker, f'You can only run gaze_to_plane on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording'

    # we want to map to plane for validate and map episodes
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / 'coding.tsv'))
    mapping_setup: dict[str, list[list[int]]] = {}
    for e in [episode.Event.Validate, episode.Event.Map]:
        if e in study_config.planes_per_interval:
            for p in study_config.planes_per_interval[e]:
                if p not in mapping_setup:
                    mapping_setup[p] = []
                mapping_setup[p].extend(episodes[e])
    mapping_setup = {p:sorted(mapping_setup[p], key = lambda x: x[0]) for p in mapping_setup}

    planes: dict[str,plane.Plane] = {}
    for p in mapping_setup:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        planes[p] = plane.get_plane_from_definition(p_def, config_dir/p)

    # load gaze data and poses
    processing_intervals = [e for p in mapping_setup for e in mapping_setup[p]] # NB: doesn't need to be sorted
    gazes,maxFrameIdx = gaze_headref.read_dict_from_file(working_dir / 'gazeData.tsv', processing_intervals if not gui or show_only_intervals else None)
    poses = {p:gt_plane.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', mapping_setup[p]) for p in mapping_setup}

    # get camera calibration info
    cameraParams = ocv.CameraParams.readFromFile(working_dir / "calibration.xml")
    cameraParams.has_intrinsics()

    # transform gaze to plane(s)
    plane_gazes: dict[str, dict[int,list[gaze_worldref.Gaze]]] = {}
    for p in planes:
        plane_gazes[p] = gaze_worldref.gazes_head_to_world(poses[p], gazes, cameraParams)
        gaze_worldref.write_dict_to_file(plane_gazes[p], working_dir/f'{naming.world_gaze_prefix}{p}.tsv', skip_missing=True)

    # done if no visualization wanted
    if gui is None:
        return False


    # prep visualizations
    # open video
    in_video        = session.read_recording_info(working_dir, rec_def.type)[1]
    cap             = ocv.CV2VideoReader(in_video, timestamps.VideoTimestamps(working_dir / 'frameTimestamps.tsv').timestamps)
    width           = cap.get_prop(cv2.CAP_PROP_FRAME_WIDTH)
    height          = cap.get_prop(cv2.CAP_PROP_FRAME_HEIGHT)

    # add windows for planes, if wanted
    if show_planes:
        plane_win_id = {p: gui.add_window(p) for p in planes}

    subPixelFac = 8   # for sub-pixel positioning
    stopAllProcessing = False
    for frame_idx in range(maxFrameIdx+1):
        done, frame, frame_idx, frame_ts = cap.read_frame(report_gap=True)
        if done or intervals.beyond_last_interval(frame_idx, mapping_setup):
            break

        keys = gui.get_key_presses()
        if 'q' in keys:
            # quit fully
            stopAllProcessing = True
            break
        if 'n' in keys:
            # goto next
            break

        # check we're in a current interval, else skip processing
        # NB: have to spool through like this, setting specific frame to read
        # with cap.get(cv2.CAP_PROP_POS_FRAMES) doesn't seem to work reliably
        # for VFR video files
        if show_only_intervals and not intervals.is_in_interval(frame_idx, mapping_setup):
            # no need to show this frame
            continue

        refImg: dict[str: np.ndarray]
        if show_planes:
            refImg = {p: planes[p].get_ref_image(400) for p in planes}

        if frame_idx in gazes:
            for gaze_head in gazes[frame_idx]:
                # draw gaze point on scene video
                gaze_head.draw(frame, cameraParams, subPixelFac)

                # draw plane gazes on video and plane
                for p in planes:
                    if frame_idx in plane_gazes[p]:
                        for gaze_world in plane_gazes[p][frame_idx]:
                            gaze_world.drawOnWorldVideo(frame, cameraParams, subPixelFac)
                            if show_planes:
                                gaze_world.drawOnPlane(refImg[p], planes[p], subPixelFac)

        if show_planes:
            for p in planes:
                gui.update_image(refImg[p], frame_ts/1000., frame_idx, window_id = plane_win_id[p])

        # if we have poster pose, draw poster origin on video
        for p in planes:
            if frame_idx in poses[p]:
                a = poses[p][frame_idx].getOriginOnImage(cameraParams)
                drawing.openCVCircle(frame, a, 3, (0,255,0), -1, subPixelFac)
                drawing.openCVLine(frame, (a[0],0), (a[0],height), (0,255,0), 1, subPixelFac)
                drawing.openCVLine(frame, (0,a[1]), (width,a[1]) , (0,255,0), 1, subPixelFac)

        # keys is populated above
        if 's' in keys:
            # screenshot
            cv2.imwrite(working_dir / f'project_frame_{frame_idx}.png', frame)

        gui.update_image(frame, frame_ts/1000., frame_idx, window_id = frame_win_id)
        closed, = gui.get_state()
        if closed:
            stopAllProcessing = True
            break

    gui.stop()

    return stopAllProcessing