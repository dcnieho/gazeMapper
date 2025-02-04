import pathlib

from glassesTools import annotation, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, propagating_thread
from glassesTools.gui import worldgaze as worldgaze_gui
from glassesTools.gui.video_player import GUI


from .. import config, episode, naming, plane, process, session, synchronization


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, show_planes=True, show_only_intervals=True, **study_settings):
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

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, show_planes, show_only_intervals), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, False, False, **study_settings)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, show_planes: bool, show_only_intervals: bool, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    if rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'You can only run gaze_to_plane on eye tracker recordings, not on a {str(rec_def.type).split(".")[1]} recording')

    # get episodes for which to transform gaze
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / naming.coding_file), study_config.episodes_to_code)
    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        if annotation.Event.Trial in episodes and episodes[annotation.Event.Trial]:
            raise ValueError(f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}) and should not be coded for this recording ({rec_def.name})')
        if annotation.Event.Trial in study_config.episodes_to_code:
            all_recs = [r.name for r in study_config.session_def.recordings]
            episodes[annotation.Event.Trial] = synchronization.get_episode_frame_indices_from_ref(working_dir, annotation.Event.Trial, rec_def.name, study_config.sync_ref_recording, all_recs, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings, study_config.sync_ref_stretch_which)

    # we transform to map to plane for validate and trial episodes, set it up
    episodes_to_proc = [annotation.Event.Validate, annotation.Event.Trial]
    if annotation.Event.Sync_ET_Data in study_config.episodes_to_code and study_config.get_cam_movement_for_et_sync_method=='plane':
        episodes_to_proc.append(annotation.Event.Sync_ET_Data)
    mapping_setup: dict[str, list[list[int]]] = {}
    for e in episodes_to_proc:
        if e in study_config.planes_per_episode:
            for p in study_config.planes_per_episode[e]:
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
    should_load_part = not gui or show_only_intervals
    head_gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, processing_intervals if should_load_part else None, ts_column_suffixes=['VOR', ''])[0]
    poses = {p:gt_plane.read_dict_from_file(working_dir/f'{naming.plane_pose_prefix}{p}.tsv', mapping_setup[p] if should_load_part else None) for p in mapping_setup}

    # get camera calibration info
    camera_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

    # transform gaze to plane(s)
    plane_gazes: dict[str, dict[int,list[gaze_worldref.Gaze]]] = {}
    for p in planes:
        plane_gazes[p] = gaze_worldref.from_head(poses[p], head_gazes, camera_params)
        gaze_worldref.write_dict_to_file(plane_gazes[p], working_dir/f'{naming.world_gaze_prefix}{p}.tsv', skip_missing=True)

    # update state
    session.update_action_states(working_dir, process.Action.GAZE_TO_PLANE, process.State.Completed, study_config)

    # done if no visualization wanted
    if gui is None:
        return

    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    worldgaze_gui.show_visualization(
        in_video, working_dir / gt_naming.frame_timestamps_fname, working_dir / gt_naming.scene_camera_calibration_fname,
        planes, poses, head_gazes, plane_gazes,
        {e:episodes[e] for e in episodes_to_proc if e in episodes},
        gui, show_planes, show_only_intervals, 8
    )