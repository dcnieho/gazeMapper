import pathlib
import numpy as np
import pandas as pd
import cv2
import copy

from ffpyplayer.player import MediaPlayer

import sys

isMacOS = sys.platform.startswith("darwin")
if isMacOS:
    import AppKit

from glassesTools import annotation, drawing, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane as gt_plane, pose as gt_pose, process_pool, propagating_thread, timestamps, validation
from glassesTools.camera_recording import Type as CameraRecordingType
from glassesTools.gui.video_player import GUI
from glassesTools.validation import assign_intervals


from .. import config, episode, naming, plane, process, session

# This script shows a video player that is used to indicate the interval(s)
# during which the poster should be found in the video and in later
# steps data quality computed. So this interval/these intervals would for
# instance be the exact interval during which the subject performs the
# validation task.
# This script can be run directly on recordings converted to the common format,
# but output from the detectMarkers and gazeToPoster actions (if available)
# will also be shown.

def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path|None = None, val_coding_event: str|None = None, **study_settings):
    # if show_poster, also draw poster with gaze overlaid on it (if available)
    working_dir = pathlib.Path(working_dir)
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)

    print(f'processing: {working_dir.parent.name}/{working_dir.name}')

    # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
    gui = GUI(use_thread = False)
    gui.add_window(f'{working_dir.parent.name}, {working_dir.name}')

    proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui, val_coding_event), kwargs=study_settings, cleanup_fun=gui.stop)
    proc_thread.start()
    gui.start()
    proc_thread.join()


def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, val_coding_event: str|None = None, **study_settings):
    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir.parent, config.OverrideLevel.Recording: working_dir}, **study_settings)

    # get timestamp info. Needed for some interval coding, and for relating audio to video frames
    video_ts = timestamps.VideoTimestamps(working_dir / gt_naming.frame_timestamps_fname)

    # get previous interval coding, if available
    if val_coding_event is None:
        episodes, episodes_to_code = episode.load_episodes_from_all_recordings(study_config, working_dir, error_if_unwanted_found=False, missing_other_coding_ok=True)
        hotkeys = {cs['name']:cs['hotkey'] for cs in study_config.coding_setup if cs['hotkey'] is not None}
        descriptions = {cs['name']:cs['description'] for cs in study_config.coding_setup if cs['description'] is not None}
        plane_points = {0: np.zeros((1,2))}
    else:
        # code target occurrences on a validation plane
        evts = [cs for cs in study_config.coding_setup if cs['name']==val_coding_event]
        if not evts:
            raise ValueError(f'Coding of validation targets for the "{val_coding_event}" was selected, but this event is unknown, can\'t continue')
        cs = evts[0]
        # get coded episodes, if available:
        e = episode.load_episodes_from_all_recordings(study_config, working_dir, error_if_unwanted_found=False, missing_other_coding_ok=True)[0]
        # get targets to code
        val_p_def = [pl for pl in study_config.planes if pl.name in cs['planes']][0]    # NB: only one plane for a validation interval
        pl    = plane.get_plane_from_definition(val_p_def, config_dir/val_p_def.name)
        targets = pl.get_target_IDs()
        plane_points = {t:pl.targets[t].center for t in pl.targets}
        def _get_target_name(t: int) -> str:
            return f'Target {t}'
        used_keys = {k.lower() for k in gui.get_shortcut_keys(include_unused=True)}
        possible_hotkeys = [c for a in range(ord('a'), ord('z') + 1) if (c:=chr(a)) not in used_keys]
        hotkeys = {_get_target_name(t):f'_{t}' if t<=9 else possible_hotkeys[t-10] for t in targets}
        episodes_to_code = set(hotkeys.keys())
        episodes = {}
        descriptions = {}
        # get coding
        fname = working_dir/f'{naming.validation_prefix}{val_coding_event}_fixation_assignment_override.tsv'
        if not fname.exists():
            fname = working_dir/f'{naming.validation_prefix}{cs["name"]}_fixation_assignment.tsv'
        if fname.exists():
            coding = pd.read_csv(fname, delimiter='\t', dtype={'marker_interval':int},index_col=['target'])
            coding['start_frame'] = [video_ts.find_frame(t) for t in coding['start_timestamp'].to_numpy()]
            coding['end_frame'] = [video_ts.find_frame(t) for t in coding['end_timestamp'].to_numpy()]
            for t in targets:
                if (cnt:=sum(coding.index==t)) > 1:
                    values = coding.loc[t, ['start_frame', 'end_frame']].to_dict(orient='records')
                elif cnt==1:
                    values = [coding.loc[t, ['start_frame', 'end_frame']].to_dict()]
                else:
                    values = []
                episodes[_get_target_name(t)] = (annotation.EventType.Target, [list(v.values()) for v in values])
        else:
            for t in targets:
                episodes[_get_target_name(t)] = (annotation.EventType.Target, [])
            if val_p_def.is_dynamic and val_coding_event in e:
                # dynamic plane but no coding yet, try and prepopulate the coding based on ArUco marker detections (c.f. run_validation)
                marker_observations_per_target, markers_per_target = validation.dynamic.get_marker_observations(pl, working_dir, val_coding_event, missing_ok=True)
                for idx,_ in enumerate(e[val_coding_event][1]):
                    selected_intervals, _ = assign_intervals.dynamic_markers(marker_observations_per_target,
                                                markers_per_target,
                                                working_dir/gt_naming.frame_timestamps_fname,
                                                e[val_coding_event][1][idx],
                                                cs['validation_setup']['dynamic_skip_first_duration'],
                                                cs['validation_setup']['dynamic_max_gap_duration'],
                                                cs['validation_setup']['dynamic_min_duration'],
                                                val_coding_event,
                                                allow_missing=True)
                    for t in targets:
                        target_name = _get_target_name(t)
                        if t in selected_intervals.index:
                            episodes[target_name][1].append([video_ts.find_frame(t) for t in selected_intervals.loc[t, ['startT', 'endT']].to_list()])
        # add validation episodes (read only), for reference
        if val_coding_event in e:
            episodes[val_coding_event] = e[val_coding_event]
            if cs['description'] is not None:
                descriptions[val_coding_event] = cs['description']

    episodes = annotation.flatten_annotation_dict(episodes)
    episodes_original = copy.deepcopy(episodes)


    # get info about recording
    rec_def  = study_config.session_def.get_recording_def(working_dir.name)
    in_video = session.read_recording_info(working_dir, rec_def.type)[1]
    if not in_video.is_file():
        raise FileNotFoundError(f'Input video file "{in_video}" for recording "{rec_def.name}" not found')
    planes = {v for cs in study_config.coding_setup for v in cs['planes']}
    if rec_def.type==session.RecordingType.Camera:
        has_gaze, has_plane_gaze = False, False
        # unless head-attached and this is the reference recording, no Sync_ET_Data or Validate events for camera recordings, remove
        if not (study_config.sync_ref_recording==rec_def.name and rec_def.camera_recording_type==CameraRecordingType.Head_attached):
            study_config.coding_setup = [cs for cs in study_config.coding_setup if cs['event_type'] not in (annotation.EventType.Sync_ET_Data, annotation.EventType.Validate)]
    elif rec_def.type==session.RecordingType.Eye_Tracker:
        # Read gaze data
        has_gaze = True
        gazes = gaze_headref.read_dict_from_file(working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['VOR',''])[0]

        # Read gaze on poster data, if available
        plane_files = [working_dir/f'{naming.world_gaze_prefix}{p}.tsv' for p in planes]
        plane_gazes = {p:gaze_worldref.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
        has_plane_gaze = not not plane_gazes

        if has_plane_gaze:
            planes_setup: dict[str, gt_plane.Plane] = {}
            for p in planes:
                p_def = [pl for pl in study_config.planes if pl.name==p][0]
                planes_setup[p] = plane.get_plane_from_definition(p_def, config_dir/p)
    else:
        raise ValueError(f'recording type "{rec_def.type}" is not understood')

    # Read plane poses, if available
    plane_files = [working_dir/f'{naming.plane_pose_prefix}{p}.tsv' for p in planes]
    poses = {p:gt_pose.read_dict_from_file(f) for p,f in zip(planes,plane_files) if f.is_file()}
    has_plane_pose = not not poses

    # get camera calibration info
    cam_params = ocv.CameraParams.read_from_file(working_dir / gt_naming.scene_camera_calibration_fname)

    # set up video playback: mediaplayer for the actual video playback, with sound if available
    ff_opts = {'volume': 1., 'sync': 'audio', 'framedrop': True}
    player = MediaPlayer(str(in_video), ff_opts=ff_opts)
    gui.set_playing(True)

    # set up annotation GUI
    gui.set_allow_pause(True)
    gui.set_allow_seek(True)
    gui.set_allow_timeline_zoom(True)
    gui.set_show_controls(True, gui.main_window_id)
    gui.set_allow_annotate(episodes_to_code,
                           hotkeys,
                           descriptions)
    gui.set_show_timeline(True, video_ts, episodes, gui.main_window_id)
    gui.set_show_annotation_label(True, gui.main_window_id)
    gui.set_show_action_tooltip(True, gui.main_window_id)

    # show
    sub_pixel_fac = 8   # for sub-pixel positioning
    should_exit = False
    has_requested_focus = not isMacOS # False only if on Mac OS, else True since its a no-op
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
            frame_idx = video_ts.find_frame(pts*1000)  # pts is in seconds, our frame timestamps are in ms

            # if we have plane pose, draw plane origin or targets on video
            if has_plane_pose:
                for p in planes:
                    if val_coding_event and p!=val_p_def.name:
                        continue
                    if p in poses and frame_idx in poses[p]:
                        for pp in plane_points:
                            a = poses[p][frame_idx].get_plane_point_on_image(plane_points[pp], cam_params)
                            drawing.openCVCircle(frame, a, 3, (0,255,0), -1, sub_pixel_fac)
                            drawing.openCVLine(frame, (a[0],a[1]-10), (a[0],a[1]+10), (0,255,0), 1, sub_pixel_fac)
                            drawing.openCVLine(frame, (a[0]-10,a[1]), (a[0]+10,a[1]), (0,255,0), 1, sub_pixel_fac)
                            if len(plane_points)>1:
                                cv2.putText(frame, str(pp), tuple(a.astype(np.intc)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,0,0), 2, lineType=cv2.LINE_AA)

            # if have gaze for this frame, draw it
            # NB: usually have multiple gaze samples for a video frame, draw one
            if has_gaze:
                if frame_idx in gazes:
                    gazes[frame_idx][0].draw(frame, cam_params, sub_pixel_fac)

            # if have gaze in world info, draw it too (also only first sample)
            if has_plane_gaze:
                # first collect for which plane to draw it
                this_plane_gazes: dict[str, gaze_worldref.Gaze] = {}
                for p in planes:
                    if p in plane_gazes and frame_idx in plane_gazes[p]:
                        this_plane_gazes[p] = gaze_worldref.distance_from_plane(plane_gazes[p][frame_idx][0], planes_setup[p])
                # get best gaze (closest to a plane)
                best = None if not this_plane_gazes else sorted(this_plane_gazes.keys(), key=lambda d: this_plane_gazes[d] if this_plane_gazes[d]<=study_config.mapped_video_gaze_to_plane_margin else np.inf)
                # check if gaze is not too far outside all planes, draw
                if best is not None:
                    p = best[0]
                    plane_gazes[p][frame_idx][0].draw_on_world_video(frame, cam_params, sub_pixel_fac, None if not p in poses or not frame_idx in poses[p] else poses[p][frame_idx])

            if frame is not None:
                gui.update_image(frame, pts, frame_idx, window_id=gui.main_window_id)

        if gui.is_running() and not has_requested_focus:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(1)
            has_requested_focus = True

        requests = gui.get_requests()
        for r,p in requests:
            match r:
                case 'toggle_pause':
                    player.toggle_pause()
                    if not player.get_pause():
                        player.seek(0)  # needed to get frames rolling in again, apparently, after seeking occurred while paused
                    gui.set_playing(not player.get_pause())
                case 'seek':
                    player.seek(p, relative=False)
                case 'delta_frame':
                    new_ts = video_ts.get_timestamp(frame_idx+p)
                    if new_ts != -1.:
                        step = (new_ts-video_ts.get_timestamp(max(0,frame_idx)))/1000
                        player.seek(pts+step, relative=False)
                case 'delta_time':
                    player.seek(pts+p, relative=False)
                case 'add_coding':
                    event,frame_idx = p
                    if frame_idx not in episodes[event][1]:
                        episodes[event][1].append(frame_idx)
                        episodes[event][1].sort()
                        gui.notify_annotations_changed()
                case 'delete_coding':
                    event,frame_idxs = p
                    for f in frame_idxs:
                        if f in episodes[event][1]:
                            episodes[event][1].remove(f)
                    gui.notify_annotations_changed()
                case 'exit':
                    should_exit = True
                    break
        if should_exit:
            break

    player.close_player()
    gui.stop()

    # early exit if nothing has changed
    if episodes==episodes_original:
        if val_coding_event is None:
            if session.get_action_states(working_dir, True)[process.Action.CODE_EPISODES]==process_pool.State.Completed:
                return
            session.update_action_states(working_dir, process.Action.CODE_EPISODES, process_pool.State.Completed, study_config, unchanged=True)
        return

    # store coded intervals to file
    to_remove = [nm for nm in episodes if nm not in episodes_to_code]
    for nm in to_remove:
        episodes.pop(nm)
    if val_coding_event is None:
        episode.write_list_to_file(episode.marker_dict_to_list(episodes), working_dir/naming.coding_file)
    else:
        # write only validation coding
        fname = working_dir/f'{naming.validation_prefix}{val_coding_event}_fixation_assignment_override.tsv'
        records = []
        episodes = annotation.unflatten_annotation_dict(episodes)
        for t in episodes:
            for interval in episodes[t][1]:
                records.append({'target': int(t.removeprefix('Target ')),    # keep just the numeric target ID
                                'start_timestamp':video_ts.get_timestamp(interval[0]),
                                'end_timestamp'  :video_ts.get_timestamp(interval[1])})
        coding = pd.DataFrame.from_records(records, columns=['target','start_timestamp','end_timestamp'])
        coding = coding.sort_values(by=['start_timestamp']).reset_index(drop=True)
        # attempt to set marker_intervals. Can be wrong if user didn't code all target intervals
        # each interval should have all targets once, so either after all targets seen, or when a target repeats, increment
        current_interval = 1
        seen_targets = []
        marker_intervals = []
        for idx,row in coding.iterrows():
            if row['target'] not in seen_targets:
                seen_targets.append(row['target'])
                marker_intervals.append(current_interval)
            else:
                current_interval += 1
                seen_targets = [row['target']]
                marker_intervals.append(current_interval)
            if len(seen_targets)==len(targets):
                current_interval += 1
                seen_targets = []
        coding.insert(1, 'marker_interval', marker_intervals)
        coding.to_csv(fname, sep='\t', index=False)

    # update state
    if val_coding_event is None:
        session.update_action_states(working_dir, process.Action.CODE_EPISODES, process_pool.State.Completed, study_config)