import pathlib

from glassesTools import annotation, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, pose as gt_pose, process_pool, propagating_thread
from glassesTools.gui import worldgaze as worldgaze_gui
from glassesTools.gui.video_player import GUI

from .. import config, episode, naming, plane, process, session


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, show_visualization=False, show_planes=True, show_only_intervals=True, progress_indicator: process_pool.JobProgress|None=None, **study_settings):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected planes and projected gaze
    # if show_poster, gaze in space of each plane is also drawn in a separate windows
    # if show_only_intervals, only the coded mapping episodes (if available) are shown in the viewer while the rest of the scene video is skipped past
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

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, show_planes, show_only_intervals, progress_indicator), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, False, False, progress_indicator, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI|None, show_planes: bool, show_only_intervals: bool, progress_indicator: process_pool.JobProgress|None, **study_settings):
    # progress indicator
    if progress_indicator is None:
        progress_indicator = process_pool.JobProgress(printer=lambda x: print(x))
    progress_indicator.set_unit('samples')
    progress_indicator.set_start_time_to_now()

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run gaze_to_plane on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get episodes for which to transform gaze (episodes should have a plane and apply to this recording)
    episodes_to_proc = [cs for cs in study_config.coding_setup if cs.get('planes') and (cs['which_recordings'] is None or working_dir.name in cs['which_recordings'])]
    if not episodes_to_proc:
        raise RuntimeError(f'There are no episodes with planes configured for session "{working_dir.parent.name}", recording "{working_dir.name}", nothing to process')
    episodes = episode.load_episodes_from_all_recordings(study_config, working_dir, {cs['name'] for cs in episodes_to_proc})[0]

    # get planes we should process
    mapping_setup: dict[str, list[list[int]]] = {}
    for cs in episodes_to_proc:
        for p in cs['planes']:
            if p not in mapping_setup:
                mapping_setup[p] = []
            mapping_setup[p].extend(episodes[cs['name']][1])
    mapping_setup = {p:sorted(mapping_setup[p], key = lambda x: x[0]) for p in mapping_setup}

    planes: dict[str,gt_plane.Plane] = {}
    for p in mapping_setup:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        planes[p] = plane.get_plane_from_definition(p_def, config_dir/p)

    # load gaze data and poses
    processing_intervals = [e for p in mapping_setup for e in mapping_setup[p]] # NB: doesn't need to be sorted
    should_load_part = not gui or show_only_intervals
    head_gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, processing_intervals if should_load_part else None, ts_column_suffixes=['VOR', ''])[0]
    poses = {p:gt_pose.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', mapping_setup[p] if should_load_part else None) for p in mapping_setup}

    # prep progress indicator
    total = sum(len(head_gazes[f]) for p in poses for f in poses[p] if f in head_gazes)
    progress_indicator.set_total(total)
    progress_indicator.set_intervals(step:=min(50,int(total/200)), step)
    # get camera calibration info
    camera_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

    # transform gaze to plane(s)
    plane_gazes: dict[str, dict[int,list[gaze_worldref.Gaze]]] = {}
    for p in planes:
        plane_gazes[p] = gaze_worldref.from_head(poses[p], head_gazes, camera_params, progress_indicator.update)
        gaze_worldref.write_dict_to_file(plane_gazes[p], working_dir/f'{naming.world_gaze_prefix}{p}.tsv', skip_missing=True)

    # update state
    session.update_action_states(working_dir, process.Action.GAZE_TO_PLANE, process_pool.State.Completed, study_config)

    # done if no visualization wanted
    if gui is None:
        return

    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    worldgaze_gui.show_visualization(
        in_video, working_dir / gt_naming.frame_timestamps_fname, working_dir / gt_naming.scene_camera_calibration_fname,
        planes, poses, head_gazes, plane_gazes,
        {n:episodes[n] for cs in episodes_to_proc if (n:=cs['name']) in episodes},
        gui, show_planes, show_only_intervals, 8
    )