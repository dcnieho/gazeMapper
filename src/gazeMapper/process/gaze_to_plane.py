import pathlib
import threading

from glassesTools import gaze_headref, gaze_worldref, ocv, plane
from glassesTools.video_gui import GUI, generic_tooltip_drawer, qns_tooltip


from . import naming
from .. import config, episode, session


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
        gui.add_window(working_dir.name)

        proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, show_planes, show_only_intervals))
        proc_thread.start()
        gui.start()
        proc_thread.join()
        return stopAllProcessing
    else:
        return do_the_work(working_dir, config_dir, None, False, False)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, show_planes: bool, show_only_intervals: bool):
    global stopAllProcessing

    # get info about the study the recording is a part of
    study_config = config.Study.load_from_json(config_dir)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    assert rec_def.type==session.RecordingType.EyeTracker, f'You can only run gaze_to_plane on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording'
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]

    # we want to map to plane for validate and map episodes
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / 'coding.tsv'))
    mapping_setup: dict[str, list[str]] = {}
    for e in [episode.Event.Validate, episode.Event.Map]:
        if e in study_config.planes_per_interval:
            for p in study_config.planes_per_interval[e]:
                if p not in mapping_setup:
                    mapping_setup[p] = []
                mapping_setup[p].extend(episodes[e])
    mapping_setup = {p:sorted(mapping_setup[p], key = lambda x: x[0]) for p in mapping_setup}

    # load gaze data and poses
    gazes = gaze_headref.read_dict_from_file(working_dir / 'gazeData.tsv', [e for p in mapping_setup for e in mapping_setup[p]])[0]
    poses = {p:plane.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', mapping_setup[p]) for p in mapping_setup}

    # get camera calibration info
    cameraParams = ocv.CameraParams.readFromFile(working_dir / "calibration.xml")
    cameraParams.has_intrinsics()

    # transform gaze to plane(s)
    for p in mapping_setup:
        plane_gazes = gaze_worldref.gazes_head_to_world(poses[p], gazes, cameraParams)
        gaze_worldref.write_dict_to_file(plane_gazes, working_dir/f'{naming.world_gaze_prefix}{p}.tsv', skip_missing=True)

    # done if no visualization wanted
    if gui is None:
        return False

    return stopAllProcessing