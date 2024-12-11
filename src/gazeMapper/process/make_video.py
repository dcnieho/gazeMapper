import shutil
import os
import pathlib
import math
import cv2
import numpy as np
import copy

from glassesTools import annotation, aruco, drawing, intervals, gaze_headref, gaze_worldref, naming as gt_naming, ocv, plane, propagating_thread, timestamps, transforms, utils
from glassesTools.gui.video_player import GUI

from .. import config, episode, marker, naming, process, session, synchronization
from .detect_markers import _get_plane_setup, _get_sync_function

from ffpyplayer.writer import MediaWriter
from ffpyplayer.pic import Image
import ffpyplayer.tools
from fractions import Fraction


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, show_visualization=False, **study_settings):
    # if show_visualization, the generated video(s) are shown as they are created in a viewer
    working_dir  = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    if show_visualization:
        # We run processing in a separate thread (GUI needs to be on the main thread for OSX, see https://github.com/pthom/hello_imgui/issues/33)
        gui = GUI(use_thread = False)
        gui.add_window(working_dir.name)

        proc_thread = propagating_thread.PropagatingThread(target=do_the_work, args=(working_dir, config_dir, gui), kwargs=study_settings, cleanup_fun=gui.stop)
        proc_thread.start()
        gui.start()
        proc_thread.join()
    else:
        do_the_work(working_dir, config_dir, None, **study_settings)

def do_the_work(working_dir: pathlib.Path, config_dir: pathlib.Path, gui: GUI, **study_settings):
    has_gui = gui is not None
    sub_pixel_fac = 8   # for anti-aliased drawing

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir}, **study_settings)
    if not study_config.video_make_which:
        raise ValueError(f'There are no videos to be made (make_video_which is not defined or null in the study setup)')

    # get session info
    session_info = session.Session.from_definition(study_config.session_def, working_dir)

    # load info for all recordings in the recording session and setup wanted output videos
    episodes        : dict[str, dict[annotation.Event, list[list[int]]]]            = {}
    episodes_as_ref : dict[str, dict[annotation.Event, list[list[int]]]]            = {}
    episodes_seq_nrs: dict[str, dict[annotation.Event, list[str]]]                  = {}
    episode_colors  : dict[str, dict[annotation.Event, tuple[float, float, float]]] = {}
    gazes_head      : dict[str, dict[int, list[gaze_headref.Gaze]]]                 = {}
    in_videos       : dict[str, pathlib.Path]                                       = {}
    camera_params   : dict[str, ocv.CameraParams]                                   = {}
    videos_ts       : dict[str, timestamps.VideoTimestamps]                         = {}
    pose_estimators : dict[str, aruco.PoseEstimator]                                = {}
    vid_info        : dict[str, tuple[int, int, float]]                             = {}
    plane_names     = {p for k in study_config.planes_per_episode for p in study_config.planes_per_episode[k]}
    planes          : dict[str, plane.Plane]                                        = {}
    all_poses       : dict[str, dict[str, dict[int, plane.Pose]]]                   = {}
    recs = {r for r in session_info.recordings}
    for rec in recs:
        rec_def = session_info.recordings[rec].definition
        rec_working_dir = working_dir / rec

        # get interval(s) coded to be analyzed, if any
        episodes[rec] = episode.list_to_marker_dict(episode.read_list_from_file(rec_working_dir / naming.coding_file), study_config.episodes_to_code)
        colors = [tuple(round(cc*255) for cc in c) for c in utils.get_colors(len(episodes[rec]), 0.45, 0.65)]
        episode_colors[rec] = {k:c for k,c in zip(episodes[rec], colors)}
        episodes_seq_nrs[rec] = {e: [str(x) for x in range(1,len(episodes[rec][e])+1)] for e in episodes[rec]}

        # Read gaze data
        if rec_def.type==session.RecordingType.Eye_Tracker:
            # NB: we want to use synced gaze data for these videos, if available
            gazes_head[rec]     = gaze_headref.read_dict_from_file(rec_working_dir / gt_naming.gaze_data_fname, ts_column_suffixes=['ref', 'VOR', ''])[0]
            # check we have timestamps synced to ref, if relevant
            if study_config.sync_ref_recording and rec!=study_config.sync_ref_recording:
                if gazes_head[rec][next(iter(gazes_head[rec]))][0].timestamp_ref is None:
                    raise ValueError(f'This study has a reference recording ({study_config.sync_ref_recording}) to synchronize the recordings to, but the gaze data for this recording ({rec}) has not been synchronized. Run sync_to_ref before running this.')

        # get camera calibration info
        camera_params[rec]      = ocv.CameraParams.read_from_file(rec_working_dir / gt_naming.scene_camera_calibration_fname)

        # get frame timestamps
        videos_ts[rec] = timestamps.VideoTimestamps(rec_working_dir / gt_naming.frame_timestamps_fname)

    # get frame sync info, and recording's episodes expressed in the reference video's frame indices
    if study_config.sync_ref_recording:
        sync = synchronization.get_sync_for_recs(working_dir, [r for r in recs if r!=study_config.sync_ref_recording], study_config.sync_ref_recording, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings)
        ref_frame_idxs: dict[str, list[int]] = {}
        episodes_as_ref[study_config.sync_ref_recording] = copy.deepcopy(episodes[study_config.sync_ref_recording])
        for r in sync.index.get_level_values('recording').unique():
            # for each frame in the reference video, get the corresponding frame in this recording
            ref_frame_idxs[r] = synchronization.reference_frames_to_video(r, sync, videos_ts[study_config.sync_ref_recording].indices,
                                                                              videos_ts[r].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                              study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
            ref_frame_idxs[r] = synchronization.smooth_video_frames_indices(ref_frame_idxs[r])
            # make sure episodes has a trial annotation, which comes from the reference recording
            episodes[r][annotation.Event.Trial] = synchronization.reference_frames_to_video(r, sync, episodes[study_config.sync_ref_recording][annotation.Event.Trial],
                                                                                            videos_ts[r].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                                            study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
            episodes_seq_nrs[r][annotation.Event.Trial] = episodes_seq_nrs[study_config.sync_ref_recording][annotation.Event.Trial]
            episode_colors[r] = {k:c for k,c in zip(episodes[r], colors)}
            # also get this recording's coded events in the reference's frames idxs
            episodes_as_ref[r] = {e: synchronization.video_frames_to_reference(r, sync, episodes[r][e],
                                                                        videos_ts[r].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                        study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
                           for e in episodes[r]}

        if study_config.video_process_annotations_for_all_recordings:
            # go through all planes for all episodes and apply them to all recordings
            # not annotation.Event.Trial as that already comes from only the reference recording
            inserted = False
            evts = [annotation.Event.Sync_ET_Data, annotation.Event.Validate]
            for rec in recs:
                for e in evts:
                    if e in episodes[rec]:
                        for r in recs-set([rec]):
                            if r==study_config.sync_ref_recording:
                                inp = episodes_as_ref[rec][e].copy()
                                eps = inp
                            else:
                                inp = [[max(0,ep[0]), min(ep[1],videos_ts[study_config.sync_ref_recording].indices[-1])] if not all([x==-1 for x in ep]) else ep for ep in episodes_as_ref[rec][e]]
                                eps = synchronization.reference_frames_to_video(r, sync, inp,
                                                                                videos_ts[r].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                                study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
                            # insert, but skip if:
                            # 1. ref episode or resulting are equal to [-1 -1]
                            # 2. episode is already in the set (one frame leeway for round trip errors)
                            # 3. episode comes from another recording (has lbl with a name in brackets), we don't want those to propagate (test this with one frame leeway to allow for round trip errors)
                            for i,ep in reversed(list(enumerate(eps))):
                                if all([x==-1 for x in inp[i]]) or \
                                   all([x==-1 for x in ep]) or \
                                   any([all([abs(y-z)<=1 for y,z in zip(x,ep)]) for x in episodes[r][e]]) or \
                                   '(' in episodes_seq_nrs[rec][e][i]:
                                    continue
                                episodes[r][e].append(ep)
                                episodes_as_ref[r][e].append(inp[i])
                                episodes_seq_nrs[r][e].append(f'{episodes_seq_nrs[rec][e][i]} ({rec})')
                                inserted = True
            if inserted:
                # if any new items added, have to resort the list (though actually it turns out all the below logic doesn't require the lists to be sorted, lets do it anyway as thats not a guarantee)
                for rec in recs:
                    for e in evts:
                        if e not in episodes[rec] or not episodes[rec][e]:
                            continue
                        episodes[rec][e], episodes_as_ref[rec][e], episodes_seq_nrs[rec][e] = \
                            [list(ivs) for ivs in zip(*[(x,y,z) for x,y,z in sorted(zip(episodes[rec][e], episodes_as_ref[rec][e], episodes_seq_nrs[rec][e]), key=lambda x: x[0])])]


        # fix episodes with start or end points outside the reference video
        for r in sync.index.get_level_values('recording').unique():
            for e in episodes_as_ref[r]:
                new_iv = []
                for i,iv in reversed(list(enumerate(episodes_as_ref[r][e]))):
                    if iv[0]==-1 and (len(iv)==1 or iv[1]==-1):
                        # not during reference video, so its irrelevant. Just remove
                        del episodes[r][e][i]
                        del episodes_seq_nrs[r][e][i]
                        continue
                    if iv[0]==-1:
                        iv[0] = 0
                    if len(iv)>1 and iv[1]==-1:
                        iv[1] = videos_ts[study_config.sync_ref_recording].indices[-1]
                    new_iv.append(iv)
                episodes_as_ref[r][e] = new_iv[::-1]
    else:
        # just an alias
        episodes_as_ref = episodes

    # flatten the episodes for each recording, that's what the GUI and movie annotator want
    episodes_as_ref_flat = {r:{e:[i for iv in episodes_as_ref[r][e] for i in iv] for e in episodes_as_ref[r]} for r in episodes_as_ref}

    if study_config.sync_ref_recording:
        # check that all camera sync point frames of a recording are in the reference recordings sync frames (a recording may miss some, but the ones it has must be equal)
        for r in sync.index.get_level_values('recording').unique():
            ref_sync_points = episodes_as_ref_flat[study_config.sync_ref_recording][annotation.Event.Sync_Camera]
            rec_sync_points = episodes_as_ref_flat[r][annotation.Event.Sync_Camera]
            # NB: allow one frame leeway to allow for small offsets due to conversion, or cameras not running completely in sync
            if not all([abs(i_ref-i_rec)<=1 for i_ref,i_rec in zip(ref_sync_points,rec_sync_points)]):
                raise RuntimeError(f'Camera sync points found for recording {r} ({episodes_as_ref_flat[r][annotation.Event.Sync_Camera]}) that do not occur among the reference recordings sync points ({study_config.sync_ref_recording}, {episodes_as_ref_flat[study_config.sync_ref_recording][annotation.Event.Sync_Camera]}). That means the sync logic must have failed')
        # load plane poses
        if not (study_config.video_process_planes_for_all_frames or study_config.video_process_individual_markers_for_all_frames or study_config.video_show_detected_markers or study_config.video_show_rejected_markers):
            to_load = [r for r in recs if r not in study_config.video_make_which]
            for r in to_load:
                all_poses[r] = {}
                for p in plane_names:
                    all_poses[r][p] = plane.read_dict_from_file(working_dir/r/f'{naming.plane_pose_prefix}{p}.tsv')

    # build pose estimator
    for rec in recs:
        if rec not in study_config.video_make_which and not (study_config.video_process_planes_for_all_frames or study_config.video_process_individual_markers_for_all_frames):
            continue
        in_videos[rec] = session.get_video_path(session_info.recordings[rec].info)     # get video file to process
        pose_estimators[rec] = aruco.PoseEstimator(in_videos[rec], videos_ts[rec], camera_params[rec])
        pose_estimators[rec].set_allow_early_exit(False)    # make sure we run through the whole video
        planes_setup, analyze_frames = _get_plane_setup(study_config, config_dir, episodes[rec], want_analyze_frames=True)
        for p in planes_setup:
            planes[p] = planes_setup[p]['plane']
            pose_estimators[rec].add_plane(p, planes_setup[p], None if study_config.video_process_planes_for_all_frames else analyze_frames[p])
        for i in (markers:=marker.get_marker_dict_from_list(study_config.individual_markers)):
            pose_estimators[rec].add_individual_marker(i, markers[i])
        sync_target_function = _get_sync_function(study_config, session_info.recordings[rec].definition, None if annotation.Event.Sync_ET_Data not in episodes[rec] else episodes[rec][annotation.Event.Sync_ET_Data])
        if sync_target_function is not None:
            pose_estimators[rec].register_extra_processing_fun('sync', *sync_target_function)
        if study_config.sync_ref_recording and rec!=study_config.sync_ref_recording:
            pose_estimators[rec].set_do_report_frames(False)

        if rec in study_config.video_make_which:
            pose_estimators[rec].set_visualize_on_frame(True)
            pose_estimators[rec].sub_pixel_fac                      = sub_pixel_fac
            pose_estimators[rec].show_detected_markers              = study_config.video_show_detected_markers
            pose_estimators[rec].show_plane_axes                    = study_config.video_show_plane_axes
            pose_estimators[rec].proc_individual_markers_all_frames = study_config.video_process_individual_markers_for_all_frames
            pose_estimators[rec].show_individual_marker_axes        = study_config.video_show_individual_marker_axes
            pose_estimators[rec].show_sync_func_output              = study_config.video_show_sync_func_output
            pose_estimators[rec].show_unexpected_markers            = study_config.video_show_unexpected_markers
            pose_estimators[rec].show_rejected_markers              = study_config.video_show_rejected_markers

        if rec in study_config.video_make_which or rec==study_config.sync_ref_recording:
            # get video file info
            vid_info[rec] = pose_estimators[rec].get_video_info()
            # override fps with frame timestamp info
            if videos_ts[rec].has_stretched:
                vid_info[rec] = (*vid_info[rec][:2], 1000/videos_ts[rec].get_IFI(timestamps.Type.Stretched))
            else:
                vid_info[rec] = (*vid_info[rec][:2], 1000/videos_ts[rec].get_IFI(timestamps.Type.Normal))

    video_sets: list[tuple[str, set[str], set[str]]] = []
    if study_config.sync_ref_recording:
        video_sets.append((study_config.sync_ref_recording,{r for r in study_config.video_make_which if r!=study_config.sync_ref_recording}, recs))
    else:
        video_sets.extend([(r,set(),recs) for r in study_config.video_make_which])

    # per set of videos
    should_exit = False
    for lead_vid, other_vids, proc_vids in video_sets:
        if should_exit:
            break

        vid_writer          : dict[str, MediaWriter]                = {}
        frame               : dict[str, np.ndarray]                 = {}
        frame_idx           : dict[str, int]                        = {}
        frame_ts            : dict[str, float]                      = {}
        pose                : dict[str, dict[str, plane.Pose]]      = {}
        gui_window_ids      : dict[str, int]                        = {}

        all_vids    = set([lead_vid]) | other_vids
        # videos to be written out may not be equal to all_vids. This can occur if we
        # have a study_config.sync_ref_recording, but config is not to make a video for it
        write_vids  = {v for v in all_vids if v in study_config.video_make_which}

        if has_gui:
            # clean up any previous windows (except main window, this will have to be renamed only)
            for v in gui_window_ids:
                if gui_window_ids[v]!=gui.main_window_id:
                    gui.delete_window(v)
            gui_window_ids.clear()
            for v in all_vids:
                # if we have a gui, set it up for this recording
                if v==lead_vid:
                    gui_window_ids[v] = gui.main_window_id
                    gui.set_window_title(f'{working_dir.name}: {v}', gui.main_window_id)
                else:
                    gui_window_ids[v] = gui.add_window(f'{working_dir.name}: {v}')
                gui.set_show_timeline(True, videos_ts[lead_vid], episodes_as_ref_flat[v], gui_window_ids[v])
                gui.set_frame_size(vid_info[v], gui_window_ids[v])
                gui.set_show_controls(True, gui_window_ids[v])
                gui.set_timecode_position('r', gui_window_ids[v])
                gui.set_show_play_percentage(True, gui_window_ids[v])
                gui.set_show_action_tooltip(True, gui_window_ids[v])

        # open output video files
        for v in write_vids:
            # get which pixel format
            codec    = ffpyplayer.tools.get_format_codec(fmt=pathlib.Path(naming.process_video).suffix[1:])
            pix_fmt  = ffpyplayer.tools.get_best_pix_fmt('bgr24',ffpyplayer.tools.get_supported_pixfmts(codec))
            fpsFrac  = Fraction(vid_info[lead_vid][2]).limit_denominator(10000).as_integer_ratio()
            # scene video
            out_opts = {'pix_fmt_in':'bgr24', 'pix_fmt_out':pix_fmt, 'width_in':vid_info[v][0], 'height_in':vid_info[v][1], 'frame_rate':fpsFrac}
            vid_writer[v] = MediaWriter(str(working_dir / v / naming.process_video), [out_opts], overwrite=True)

        # update state: set to not run so that if we crash or cancel below the task is correctly marked as not run (video files are corrupt)
        session.update_action_states(working_dir, process.Action.MAKE_VIDEO, process.State.Not_Run, study_config)

        # now make the video
        def n_digit(value):
            return int(math.log10(value))+1
        def n_digit_timestamp(value_ms: float):
            return n_digit(value_ms/1000)+4    # ms->s, add decimal point and three decimals
        timestamp_width = {lead_vid: n_digit_timestamp(videos_ts[lead_vid].get_last()[1])}
        timestamp_width |= {v: n_digit_timestamp(videos_ts[v].get_timestamp(max(ref_frame_idxs[v]))) for v in other_vids}
        frame_idx_width = {lead_vid: n_digit(videos_ts[lead_vid].get_last()[0])}
        frame_idx_width |= {v: n_digit(max(ref_frame_idxs[v])) for v in other_vids}
        while True:
            status, pose[lead_vid], _, _, (frame[lead_vid], frame_idx[lead_vid], frame_ts[lead_vid]) = \
                pose_estimators[lead_vid].process_one_frame()
            # TODO: if there is a discontinuity, fill in the missing frames so audio stays in sync
            # check if we're done
            if status==aruco.Status.Finished:
                break
            # NB: no need to handle aruco.Status.Skip, since we didn't provide the pose estimator with any analysis intervals (we want to process the whole video)

            for v in proc_vids-set([lead_vid]):
                # find corresponding frame
                fr_idx_this = ref_frame_idxs[v][frame_idx[lead_vid]]

                if v in all_poses:
                    pose[v] = {p: ps for p in plane_names if (ps:=all_poses[v][p].get(fr_idx_this, None)) is not None}
                    continue
                if fr_idx_this==-1:
                    _, pose[v], _, _, (frame[v], frame_idx[v], frame_ts[v]) = \
                        None, None, None, None, (None, None, None)
                else:
                    # read it
                    _, pose[v], _, _, (frame[v], frame_idx[v], frame_ts[v]) = \
                        pose_estimators[v].process_one_frame(fr_idx_this)

            for v in write_vids:
                if frame[v] is None:
                    # we don't have a valid frame, use a fully black frame
                    frame[v] = np.zeros((vid_info[v][1],vid_info[v][0],3), np.uint8)   # black image

            # draw gaze on the video
            for v in proc_vids:
                if v in gazes_head and frame_idx[lead_vid] in gazes_head[v]:
                    clr = study_config.video_recording_colors[v][::-1]  # RGB -> BGR
                    for g in gazes_head[v][frame_idx[lead_vid]]:
                        if v in all_vids:
                            g.draw(frame[v], sub_pixel_fac=sub_pixel_fac, clr=clr, draw_3d_gaze_point=False)

                        # check if we need gaze on plane for drawing on any of the videos
                        plane_gaze_on_this_video = not not study_config.video_show_gaze_on_plane_in_which and v in study_config.video_show_gaze_on_plane_in_which
                        plane_gaze_or_pose_on_other_video = (not not study_config.video_show_gaze_on_plane_in_which and any((vo!=v for vo in study_config.video_show_gaze_on_plane_in_which))) or (not not study_config.video_show_gaze_vec_in_which and any((vo!=v for vo in study_config.video_show_gaze_vec_in_which))) or (not not study_config.video_show_camera_in_which and any((vo!=v for vo in study_config.video_show_camera_in_which)))
                        if not pose[v] or not (plane_gaze_on_this_video or plane_gaze_or_pose_on_other_video):
                            continue

                        # collect gaze on all planes for which pose or homography is available
                        plane_gazes: dict[str, tuple[float,float,gaze_worldref.Gaze]] = {}
                        for pl in pose[v]:
                            if pl in pose[v] and (pose[v][pl].pose_successful() or pose[v][pl].homography_successful()):
                                # turn into position on board
                                plane_gaze = gaze_worldref.from_head(pose[v][pl], g, camera_params[v])
                                plane_gazes[pl] = (gaze_worldref.distance_from_plane(plane_gaze, planes[pl]), pose[v][pl].pose_reprojection_error if pose[v][pl].pose_successful() else np.nan, plane_gaze)

                        # find the plane to which gaze is closest
                        best = None if not plane_gazes else sorted(plane_gazes.keys(), key=lambda d: (sum(plane_gazes[d][0:2])/2 if not np.isnan(plane_gazes[d][1]) else plane_gazes[d][0]) if plane_gazes[d][0]<=study_config.video_gaze_to_plane_margin else math.inf)
                        # check if gaze is not too far outside all planes
                        if best is None:
                            continue

                        # draw on current video
                        if v in study_config.video_show_gaze_on_plane_in_which:
                            plane_gazes[best[0]][2].draw_on_world_video(frame[v], camera_params[v], sub_pixel_fac, pose[v][best[0]], study_config.video_projected_vidPos_color, study_config.video_projected_world_pos_color, study_config.video_projected_left_ray_color, study_config.video_projected_right_ray_color, study_config.video_projected_average_ray_color)

                        # also draw on other recordings, if so configured
                        # depending on configuration also includes camera and gaze vector between the two
                        for vo in write_vids-set([v]):
                            if pose[vo] is None:
                                continue
                            for pl in best:
                                if pl in pose[vo] and (pose[vo][pl].pose_successful() or pose[vo][pl].homography_successful()):
                                    break
                            if pl not in pose[vo] or not (pose[vo][pl].pose_successful() or pose[vo][pl].homography_successful()):
                                continue
                            # draw gaze point, camera position, and gaze vector between them on the other video, as configured
                            # and as possible (camera position and gaze vector require pose, not only homography)
                            draw_gaze_on_other_video(frame[vo],
                                                     pose[v][pl], pose[vo][pl],
                                                     plane_gazes[pl][2],
                                                     camera_params[vo], clr,
                                                     study_config.video_which_gaze_type_on_plane,
                                                     study_config.video_which_gaze_type_on_plane_allow_fallback,
                                                     vo in study_config.video_show_gaze_on_plane_in_which,
                                                     vo in study_config.video_show_gaze_vec_in_which,
                                                     vo in study_config.video_show_camera_in_which,
                                                     sub_pixel_fac)


            # print info on frame
            for v in write_vids:
                # timecode and frame number
                if v==lead_vid and videos_ts[lead_vid].has_stretched:
                    # for reference video, if we have stretched timestamps, print those too
                    texts = [f'{frame_ts[lead_vid]/1000.:{timestamp_width[lead_vid]}.3f} ({videos_ts[lead_vid].get_timestamp(frame_idx[lead_vid], timestamps.Type.Stretched)/1000.:{timestamp_width[lead_vid]}.3f})']
                else:
                    texts = [f'{frame_ts[lead_vid]/1000.:{timestamp_width[lead_vid]}.3f}']
                texts[0] += f' [{frame_idx[lead_vid]:{frame_idx_width[lead_vid]}d}]'
                frame_colors = [(0,0,0)]
                if v in other_vids:
                    if frame_ts[v] is None:
                        texts.append('no frame')
                    else:
                        texts.append(f'{frame_ts[v]/1000.:{timestamp_width[v]}.3f} [{frame_idx[v]:{frame_idx_width[v]}d}]')
                    frame_colors.append((128,128,128))
                # events, if any
                event, ivals = intervals.which_interval(frame_idx[lead_vid], episodes_as_ref[v])
                for e,iv in zip(event,ivals):
                    idx = episodes_as_ref[v][e].index(iv)
                    texts.append(f'{e.value} {episodes_seq_nrs[v][e][idx]}')
                    frame_colors.append(episode_colors[v][e][::-1])
                # now print them all
                text_sizes: list[tuple[int,int]]= []
                baselines : list[int]           = []
                for t in texts:
                    t, b = cv2.getTextSize(t,cv2.FONT_HERSHEY_PLAIN,2,2)
                    text_sizes.append(t)
                    baselines.append(b)
                max_height = max(text_sizes, key=lambda x: x[1])[1]
                x_end = 0
                margin = 5
                for t,f,ts,b in zip(texts,frame_colors,text_sizes,baselines):
                    x_advance = ts[0]+margin
                    cv2.rectangle(frame[v],(x_end,frame[v].shape[0]),(x_end+x_advance,frame[v].shape[0]-max_height-b-margin), f, -1)
                    cv2.putText(frame[v], (t), (x_end+margin, frame[v].shape[0]-margin), cv2.FONT_HERSHEY_PLAIN, 2, (0,255,255), 2)
                    x_end += x_advance

            # submit frame to be encoded
            for v in write_vids:
                img = Image(plane_buffers=[frame[v].flatten().tobytes()], pix_fmt='bgr24', size=(frame[v].shape[1], frame[v].shape[0]))
                vid_writer[v].write_frame(img=img, pts=frame_idx[lead_vid]/vid_info[lead_vid][2])

            # update gui, if any
            if has_gui:
                for v in write_vids:
                    gui.update_image(frame[v], frame_ts[lead_vid]/1000., frame_idx[lead_vid], window_id=gui_window_ids[v])

                requests = gui.get_requests()
                for r,_ in requests:
                    if r=='exit':   # only request we need to handle
                        should_exit = True
                        break

        # done with this set of videos
        for v in write_vids:
            vid_writer[v].close()

        # if ffmpeg is on path, add audio to scene and optionally board video
        if shutil.which('ffmpeg') is not None:
            for v in write_vids:
                rec_working_dir = working_dir / v

                file = rec_working_dir / naming.process_video

                # move file to temp name
                tempName = file.parent / (file.stem + '_temp' + file.suffix)
                shutil.move(file, tempName)

                # add audio
                if v==lead_vid:
                    cmd_str = ' '.join(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'"{tempName}"', '-i', f'"{in_videos[v]}"', '-vcodec', 'copy', '-acodec', 'copy', '-map', '0:v:0', '-map', '1:a:0?', '-shortest', f'"{file}"'])
                else:
                    inputs = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'"{tempName}"', '-i', f'"{in_videos[v]}"']

                    first_frame = ref_frame_idxs[v][0]
                    if first_frame==-1:
                        # video starts later, we need to delay audio when copying
                        frame_off = np.argmax(np.array(ref_frame_idxs[v])>-1)
                        t_off = videos_ts[lead_vid].get_timestamp(frame_off, timestamps.Type.Stretched if videos_ts[lead_vid].has_stretched else timestamps.Type.Normal)
                        filt = f'[1:a]adelay=delays={t_off:.9f}:all=1[a];[a]apad[audio];'
                    else:
                        t_off = videos_ts[v].get_timestamp(first_frame)
                        filt = f'[1:a]atrim=start={t_off/1000},asetpts=PTS-STARTPTS[a];[a]apad[audio];'
                    cmd_str = ' '.join(inputs + ['-filter_complex', f'"{filt}"', '-map', '0:v', '-map', '[audio]', '-c:v', 'copy', '-shortest', f'"{file}"'])
                os.system(cmd_str)

                # clean up
                if file.exists():
                    tempName.unlink(missing_ok=True)
                else:
                    # something failed. Put file without audio back under output name
                    shutil.move(tempName, file)

    # done with all videos, clean up
    if has_gui:
        gui.stop()

    # update state
    session.update_action_states(working_dir, process.Action.MAKE_VIDEO, process.State.Completed, study_config)

def draw_gaze_on_other_video(frame_other, pose_this: plane.Pose, pose_other: plane.Pose, plane_gaze: gaze_worldref.Gaze, camera_params_other, clr, which_gaze_on_plane, which_gaze_on_plane_allow_fallback, do_draw_gaze, do_draw_gaze_vec, do_draw_camera, sub_pixel_fac):
    if not do_draw_gaze and not do_draw_gaze_vec and not do_draw_camera:
        # nothing to do
        return

    gaze_point_plane = plane_gaze.get_gaze_point(which_gaze_on_plane)
    if gaze_point_plane is None:
        if which_gaze_on_plane_allow_fallback:
            gaze_point_plane = plane_gaze.get_gaze_point(gaze_worldref.Type.Scene_Video_Position)
        else:
            raise RuntimeError(f'Gaze of type {which_gaze_on_plane.value} was requested, but is not available. Select a different gaze type or set allow_fallback to True.')

    if not pose_this.pose_successful() or not pose_other.pose_successful():
        if not do_draw_gaze:
            return
        # use homography
        gaze_pos_other = pose_other.plane_to_cam_homography(gaze_point_plane, camera_params_other)
        drawing.openCVCircle(frame_other, gaze_pos_other, 8, clr, 2, sub_pixel_fac)
        # can only do gaze position on plane with homography, so, exit
        return

    gaze_point_plane = np.append(gaze_point_plane,0.).reshape(1,3)
    # check if gaze position in camera frame is ok. If gaze point is behind this camera,
    # it won't be visible and projecting it anyway yields a nonsensical result
    if gaze_ok := pose_other.world_frame_to_cam(gaze_point_plane)[2]>0:
        # project from plane to camera
        gaze_pos_other = pose_other.plane_to_cam_pose(gaze_point_plane, camera_params_other)
        # draw on the other video
        if do_draw_gaze:
            drawing.openCVCircle(frame_other, gaze_pos_other, 8, clr, 2, sub_pixel_fac)

    # also draw position of this camera on the other video, and possibly gaze vector
    if do_draw_camera or do_draw_gaze_vec:
        # take point 0,0,0 in this camera's space (i.e. camera position) and transform to the plane's world space
        cam_pos_world_this = pose_this.cam_frame_to_world((0.,0.,0.))
        if pose_other.world_frame_to_cam(cam_pos_world_this)[2]<=0:
            # other video's camera is behind this camera, won't be visible
            # and projecting it anyway yields a nonsensical result
            return
        # draw on the other video
        cam_pos_other = pose_other.plane_to_cam_pose(cam_pos_world_this, camera_params_other)
        if do_draw_camera:
            drawing.openCVCircle(frame_other, cam_pos_other, 3, clr, 1, sub_pixel_fac)
        # and draw line connecting the camera and the gaze point
        if gaze_ok and do_draw_gaze_vec:
            drawing.openCVLine(frame_other, gaze_pos_other, cam_pos_other, clr, 5, sub_pixel_fac)