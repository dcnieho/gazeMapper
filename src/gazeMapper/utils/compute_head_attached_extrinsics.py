# If you set up a head-attached recording to replace the scene camera of an eye tracker recording, gazeMapper
# needs not only this camera's intrinsics but also its extrinsics (the transformation from the head-attached
# camera to the eye tracker's scene camera reference frame). This utility computes these extrinsics based on
# the existing extrinsics of the eye tracker scene camera (if any) and the head-attached camera's poses relative
# to the eye tracker scene camera.)
# Since gazeMapper checks that extrinsics are available for a head-attached camera, it will error out if you
# have not determined them yet. You can therefore initialize them to identity transforms (no translation and
# identity rotation matrix) and then:
# 1. run the Detect Markers action to get poses of both the eye tracker and the scene camera w.r.t. some plane,
#    so that that:
# 2. You can run this utility to compute the correct extrinsics.
# 3. Manually edit the camera calibration file to set the to set the extrinsics to the computed values.
import pathlib
import numpy as np
import cv2
from scipy.spatial.transform import Rotation as R

from gazeMapper import config, episode, naming, session, synchronization

from glassesTools import naming as gt_naming, ocv, pose as gt_pose, timestamps
from glassesTools.camera_recording import Type as CameraRecordingType

session_dir = pathlib.Path(r'C:\dat\projects\SEL\station 1\camera_clip\v2\cameraclip\gazeMapper project\rec_full')
et_rec_name = 'et'
ha_rec_name = 'clip'
plane_name = 'val'


def compute_relative_pose(R_A, t_A, R_B, t_B):
    R_AB = R_B @ R_A.T
    t_AB = t_B - R_AB @ t_A
    return R_AB, t_AB

def rotation_to_quaternion(R_mat):
    return R.from_matrix(R_mat).as_quat()

def quaternion_to_rotation(q):
    return R.from_quat(q).as_matrix()

def average_quaternions(quaternions):
    q_mean = np.mean(quaternions, axis=0)
    q_mean /= np.linalg.norm(q_mean)
    return q_mean

def pose_distance(R1, t1, R2, t2):
    rot_diff = R.from_matrix(R1.T @ R2)
    angle = rot_diff.magnitude()
    trans_dist = np.linalg.norm(t1 - t2)
    return angle, trans_dist

def compute_adaptive_thresholds(R_list, t_list):
    angles = []
    dists = []
    n = len(R_list)
    for i in range(n):
        for j in range(i+1, n):
            a, d = pose_distance(R_list[i], t_list[i], R_list[j], t_list[j])
            angles.append(a)
            dists.append(d)
    # Median + MAD
    angle_median = np.median(angles)
    angle_mad = np.median(np.abs(angles - angle_median))
    dist_median = np.median(dists)
    dist_mad = np.median(np.abs(dists - dist_median))
    rot_thresh = angle_median + 2 * angle_mad
    trans_thresh = dist_median + 2 * dist_mad
    return rot_thresh, trans_thresh

def ransac_pose_filter(R_list, t_list, max_iter=100):
    rot_thresh, trans_thresh = compute_adaptive_thresholds(R_list, t_list)
    print(f"Adaptive thresholds — Rotation: {rot_thresh:.4f} rad, Translation: {trans_thresh:.4f} mm")

    n = len(R_list)
    best_inliers = []
    for _ in range(max_iter):
        idx = np.random.randint(0, n)
        R_ref, t_ref = R_list[idx], t_list[idx]
        inliers = []
        for i in range(n):
            angle, dist = pose_distance(R_ref, t_ref, R_list[i], t_list[i])
            if angle < rot_thresh and dist < trans_thresh:
                inliers.append(i)
        if len(inliers) > len(best_inliers):
            best_inliers = inliers
    return best_inliers

def average_relative_poses(R_As, t_As, R_Bs, t_Bs):
    R_list, t_list = [], []
    for R_A, t_A, R_B, t_B in zip(R_As, t_As, R_Bs, t_Bs):
        R_AB, t_AB = compute_relative_pose(R_A, t_A, R_B, t_B)
        R_list.append(R_AB)
        t_list.append(t_AB)

    inliers = ransac_pose_filter(R_list, t_list)

    quats = np.array([rotation_to_quaternion(R_list[i]) for i in inliers])
    q_avg = average_quaternions(quats)
    R_avg = quaternion_to_rotation(q_avg)
    t_avg = np.mean(np.stack([t_list[i] for i in inliers], axis=0), axis=0)

    return R_avg, t_avg

# get study config
config_dir = config.guess_config_dir(session_dir)
study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: session_dir})

# get info about session
session_info = session.Session.from_definition(study_config.session_def, session_dir)

# check plane is defined
if not any(plane.name==plane_name for plane in study_config.planes):
    raise ValueError(f'Plane "{plane_name}" is not defined in the study config, cannot proceed')
# check recordings are defined and are indeed a scene camera and head-attached recording
for r in [et_rec_name, ha_rec_name]:
    rec_def = study_config.session_def.get_recording_def(r)
    if rec_def is None:
        raise ValueError(f'Recording "{r}" is not defined in the session definition, cannot proceed')
    if r not in session_info.recordings:
        raise ValueError(f'Recording "{r}" is not part of the session found at {str(session_dir)}. Load the recording into the session first. Cannot proceed')
    if r==et_rec_name and rec_def.type!=session.RecordingType.Eye_Tracker:
        raise ValueError(f'Recording "{r}" is not an eye tracker recording, cannot proceed')
    if r==ha_rec_name and (rec_def.type!=session.RecordingType.Camera or rec_def.camera_recording_type!=CameraRecordingType.Head_attached):
        raise ValueError(f'Recording "{r}" is not a head-attached camera recording, cannot proceed')

# load plane pose for both recordings
poses = {r:gt_pose.read_dict_from_file(session_dir/r/f'{naming.plane_pose_prefix}{plane_name}.tsv') for r in [et_rec_name, ha_rec_name]}
# load camera calibrations for both recordings
camera_calibs = {r:ocv.CameraParams.read_from_file(session_dir/r/gt_naming.scene_camera_calibration_fname) for r in [et_rec_name, ha_rec_name]}

# get episodes involving the plane
codings = {cs['name']:cs for cs in study_config.coding_setup if plane_name in cs.get('planes',[])}
ref_episodes = episode.list_to_marker_dict(episode.read_list_from_file(session_dir/study_config.sync_ref_recording/naming.coding_file), list(codings.keys()))
videos_ts = {r:timestamps.VideoTimestamps(session_dir / r / gt_naming.frame_timestamps_fname) for r in [et_rec_name, ha_rec_name]}
to_sync = ha_rec_name if et_rec_name==study_config.sync_ref_recording else et_rec_name

# for each frame in the reference recording, get corresponding frame in the to_sync recording
sync = synchronization.get_sync_for_recs(session_dir, [to_sync], study_config.sync_ref_recording, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings)
to_sync_frame_idxs = synchronization.reference_frames_to_video(to_sync, sync, videos_ts[study_config.sync_ref_recording].indices,
                                                               videos_ts[to_sync].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                               study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)

# collect matching poses
frame_idxs = {r:[] for r in [et_rec_name, ha_rec_name]}
for c in ref_episodes:
    for e in ref_episodes[c]:
        frame_idxs[study_config.sync_ref_recording].extend(range(e[0], e[1]+1))
        # get corresponding frames in to_sync recording
        to_sync_frames = [to_sync_frame_idxs[f_idx] for f_idx in range(e[0], e[1]+1) if f_idx<len(to_sync_frame_idxs)]
        # check that these are in the expected range (just a sanity check)
        expected_range = synchronization.reference_frames_to_video(to_sync, sync, e,
                                                                   videos_ts[to_sync].timestamps, videos_ts[study_config.sync_ref_recording].timestamps,
                                                                   study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)
        if min(to_sync_frames)<expected_range[0] or max(to_sync_frames)>expected_range[1]:
            raise ValueError('Computed frame indices for the to-sync recording are outside the expected range, cannot proceed')
        frame_idxs[to_sync].extend(to_sync_frames)

matched_poses: list[tuple[gt_pose.Pose, gt_pose.Pose]] = []
for f_idx_ref, f_idx_sync in zip(frame_idxs[ha_rec_name], frame_idxs[et_rec_name]):
    if f_idx_ref in poses[ha_rec_name] and f_idx_sync in poses[et_rec_name]:
        matched_poses.append((poses[ha_rec_name][f_idx_ref], poses[et_rec_name][f_idx_sync]))

# compute relative pose of head-attached camera w.r.t. eye tracker scene camera. Takes average over all matched poses
R_As = [cv2.Rodrigues(p[0].pose_R_vec)[0] for p in matched_poses]
t_As = [p[0].pose_T_vec for p in matched_poses]
R_Bs = [cv2.Rodrigues(p[1].pose_R_vec)[0] for p in matched_poses]
t_Bs = [p[1].pose_T_vec for p in matched_poses]
R_AB_avg, t_AB_avg = average_relative_poses(R_As, t_As, R_Bs, t_Bs)

print("Average Relative Rotation:\n", R_AB_avg)
print("Average Relative Translation:\n", t_AB_avg)