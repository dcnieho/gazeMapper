import pathlib
import threading

from glassesTools import aruco, marker, plane as gt_plane, timestamps
from glassesTools.video_gui import GUI, generic_tooltip_drawer, qns_tooltip


from . import naming
from .. import config, episode, plane, session, synchronization


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
    episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir / 'coding.tsv'))

    # trial episodes are gotten from the reference recording if there is one and this is not the reference recording
    if study_config.sync_ref_recording and rec_def.name!=study_config.sync_ref_recording:
        assert episode.Event.Trial not in episodes or not episodes[episode.Event.Trial], f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}) and should not be coded for this recording ({rec_def.name})'
        ref_episodes = episode.list_to_marker_dict(episode.read_list_from_file(working_dir.parent / study_config.sync_ref_recording / 'coding.tsv'))
        assert episode.Event.Trial in ref_episodes, f'Trial episodes are gotten from the reference recording ({study_config.sync_ref_recording}), but the coding file for this reference recording doesn\'t contain any ({episode.Event.Trial.value}) episodes'
        # get sync and timestamp info we need to transform reference frames indices to frame indices of this recording
        sync = synchronization.get_sync_for_recs(working_dir.parent, study_config.sync_ref_recording, rec_def.name)
        video_ts_ref = timestamps.VideoTimestamps(working_dir.parent / study_config.sync_ref_recording / 'frameTimestamps.tsv')
        video_ts     = timestamps.VideoTimestamps(working_dir / 'frameTimestamps.tsv')
        off = -sync.loc[(rec_def.name,0),'mean_off']*1000.   # s -> ms, negate because value is sync this_rec->ref, we need the opposite
        frame_ts_ref = [[video_ts_ref.get_timestamp(i) for i in ifs] for ifs in ref_episodes[episode.Event.Trial]]
        frame_idx    = [[video_ts.find_frame(i+off) for i in ts] for ts in frame_ts_ref]
        episodes[episode.Event.Trial] = [[ifs[0]-10, ifs[1]+10] for ifs in frame_idx]   # arbitrarily expand by ten frames on each edge, so we've likely got the frame we need

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

    stopAllProcessing, poses, individual_markers = \
        aruco.run_pose_estimation(in_video, working_dir / "frameTimestamps.tsv", working_dir / "calibration.xml",   # input video
                                  # output
                                  working_dir,
                                  # intervals to process
                                  analyze_frames,
                                  # detector and pose estimator setup
                                  planes_setup, config.get_marker_dict_from_list(study_config.individual_markers),
                                  # visualization setup
                                  gui, 8, show_rejected_markers)

    for p in poses:
        gt_plane.write_list_to_file(poses[p], working_dir/f'{naming.plane_pose_prefix}{p}.tsv', skip_failed=True)
    for i in individual_markers:
        marker.write_list_to_file(individual_markers[i], working_dir/f'{naming.marker_pose_prefix}{i}.tsv', skip_failed=True)

    return stopAllProcessing