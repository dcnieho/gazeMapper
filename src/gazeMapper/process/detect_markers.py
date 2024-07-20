import pathlib
import threading
import pandas as pd

from glassesTools import annotation, aruco, marker as gt_marker, plane as gt_plane
from glassesTools.video_gui import GUI, generic_tooltip_drawer, qns_tooltip


from .. import config, episode, marker, naming, plane, session, synchronization


stopAllProcessing = False
def process(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, show_rejected_markers=False):
    # if show_visualization, each frame is shown in a viewer, overlaid with info about detected markers and planes
    # if show_rejected_markers, rejected ArUco marker candidates are also shown in the viewer. Possibly useful for debug
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

        proc_thread = threading.Thread(target=do_the_work, args=(working_dir, config_dir, gui, show_rejected_markers))
        proc_thread.start()
        gui.start()
        proc_thread.join()
        return stopAllProcessing
    else:
        return do_the_work(working_dir, config_dir, None, False)


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, show_rejected_markers: bool):
    global stopAllProcessing

    # get info about the study the recording is a part of
    study_config = config.Study.load_from_json(config_dir)

    # get info about recording
    rec_def = study_config.session_def.get_recording_def(working_dir.name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]

    # get interval(s) coded to be analyzed, if any
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / naming.coding_file), study_config.episodes_to_code)

    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        assert annotation.Event.Trial not in episodes or not episodes[annotation.Event.Trial], f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}) and should not be coded for this recording ({rec_def.name})'
        episodes[annotation.Event.Trial] = synchronization.get_episode_frame_indices_from_ref(working_dir, annotation.Event.Trial, study_config.sync_ref_recording, rec_def.name)

    extra_processing = None
    if rec_def.type==session.RecordingType.Camera:
        # no annotation.Event.Sync_ET_Data for camera recordings, remove
        if annotation.Event.Sync_ET_Data in study_config.planes_per_episode:
            study_config.planes_per_episode.pop(annotation.Event.Sync_ET_Data)
    elif rec_def.type==session.RecordingType.EyeTracker:
        match study_config.get_cam_movement_for_et_sync_method:
            case '':
                pass # nothing to do
            case 'plane':
                assert annotation.Event.Sync_ET_Data in study_config.planes_per_episode, f'The method for synchronizing eye tracker data to the scene camera (get_cam_movement_for_et_sync_method) is set to "plane" but no plane is configured for {annotation.Event.Sync_ET_Data.name} in the planes_per_episode config'
                # NB: no extra_funcs to run
            case 'function':
                import importlib
                to_load = study_config.get_cam_movement_for_et_sync_function['module_or_file']
                if (to_load_path:=pathlib.Path(to_load)).is_file():
                    import sys
                    module_name = to_load_path.stem
                    spec = importlib.util.spec_from_file_location(module_name, to_load_path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                else:
                    module = importlib.import_module(study_config.get_cam_movement_for_et_sync_function['module_or_file'])
                func = getattr(module,study_config.get_cam_movement_for_et_sync_function['function'])
                extra_processing = {'sync_func': (func, episodes[annotation.Event.Sync_ET_Data], study_config.get_cam_movement_for_et_sync_function['parameters'])}
            case _:
                raise ValueError(f'study config get_cam_movement_for_et_sync_method={study_config.get_cam_movement_for_et_sync_method} not understood')

    # process the above into a dict of plane definitions and a dict with frame number intervals for which to use each
    planes = {v for k in study_config.planes_per_episode for v in study_config.planes_per_episode[k]}
    planes_setup = {}
    analyze_frames = {}
    for p in planes:
        p_def = [pl for pl in study_config.planes if pl.name==p][0]
        pl = plane.get_plane_from_definition(p_def, config_dir/p)
        planes_setup[p] = {'plane': pl, 'aruco_dict': p_def.aruco_dict, 'aruco_params': {'markerBorderBits': p_def.marker_border_bits}, 'min_num_markers': p_def.min_num_markers}
        # determine for which frames this plane should be used
        anal_episodes = [k for k in study_config.planes_per_episode if p in study_config.planes_per_episode[k]]
        all_episodes = [ep for k in anal_episodes for ep in episodes[k]]
        analyze_frames[p] = sorted(all_episodes, key = lambda x: x[1])

    # if there is some form of automatic coding configured, then we'll need to process the whole video for each recording in a session
    if study_config.auto_code_sync_points or study_config.auto_code_trials_episodes:
        analyze_frames = {p:None for p in analyze_frames}

    stopAllProcessing, poses, individual_markers, extra_processing_output = \
        aruco.run_pose_estimation(in_video, working_dir / "frameTimestamps.tsv", working_dir / "calibration.xml",   # input video
                                  # output
                                  working_dir,
                                  # intervals to process
                                  analyze_frames,
                                  # detector and pose estimator setup
                                  planes_setup, marker.get_marker_dict_from_list(study_config.individual_markers),
                                  # other functions to run
                                  extra_processing,
                                  # visualization setup
                                  gui, 8, show_rejected_markers)

    for p in poses:
        gt_plane.write_list_to_file(poses[p], working_dir/f'{naming.plane_pose_prefix}{p}.tsv', skip_failed=True)
    for i in individual_markers:
        gt_marker.write_list_to_file(individual_markers[i], working_dir/f'{naming.marker_pose_prefix}{i}.tsv', skip_failed=True)
    if extra_processing:
        df = pd.DataFrame(extra_processing_output['sync_func'],columns=['frame_idx','target_x','target_y'])
        df.to_csv(working_dir/naming.target_sync_file, sep='\t', index=False, na_rep='nan', float_format="%.8f")

    return stopAllProcessing