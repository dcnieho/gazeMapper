import pathlib

from glassesTools import gaze_overlay_video, naming as gt_naming, propagating_thread, timestamps
from glassesTools.gui.video_player import GUI


from .. import config, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, **study_settings):
    # if show_visualization, each frame is shown in a viewer as video is generated
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # if we need gui, we run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    if show_visualization:
        gui = GUI(use_thread = False)
        gui.add_window(working_dir.name)
        gui.set_show_controls(True)
        gui.set_show_play_percentage(True)
        gui.set_show_action_tooltip(True)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run gaze_overlay_video on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    # set up gaze overlay video maker and run it
    video_maker = gaze_overlay_video.VideoMaker(working_dir, in_video, video_ts, working_dir / gt_naming.gaze_data_fname)
    video_maker.attach_gui(gui)
    if gui is not None:
        gui.set_show_timeline(True, video_ts, window_id=gui.main_window_id)

    # update state: set to not run so that if we crash or cancel below the task is correctly marked as not run (video files are corrupt)
    session.update_action_states(working_dir, process.Action.MAKE_GAZE_OVERLAY_VIDEO, process.State.Not_Run, study_config)

    # now run
    video_maker.process_video()

    # update state
    session.update_action_states(working_dir, process.Action.MAKE_GAZE_OVERLAY_VIDEO, process.State.Completed, study_config)
