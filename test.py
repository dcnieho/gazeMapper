import pathlib

from glassesTools import eyetracker, importing, marker

from gazeMapper import camera_recording, config, episode, session, plane
from gazeMapper.process.code_episodes import run as do_coding
from gazeMapper.process.detect_markers import run as detect_markers
from gazeMapper.process.sync_et_to_cam import run as sync_et_to_cam
from gazeMapper.process.sync_to_ref import run as sync_to_ref
from gazeMapper.process.gaze_to_plane import run as gaze_to_plane
from gazeMapper.process.export_trials import run as export_trials
from gazeMapper.process.run_validation import run as run_validation
from gazeMapper.process.auto_code_sync_points import run as auto_code_sync_points
from gazeMapper.process.auto_code_trials import run as auto_code_trials
from gazeMapper.process.make_video import run as make_video


which = 3
match which:
    case 1:
        base1 = pathlib.Path(r'C:\dat\projects\Roy Japanese Lego\pilot 3\data\J13')
        base2 = pathlib.Path(r'C:\dat\projects\Roy Japanese Lego\pilot 3\data\N24')
        proj = pathlib.Path(r'C:\dat\projects\gazeMapper\projs\roy japan')
        et_recs = ['et1','et2']
        cam_recs= ['cam']

    case 2:
        base = pathlib.Path(r'C:\dat\projects\a_finished\2023 roy lego\analysis\data\pair12\Markers')
        proj = pathlib.Path(r'C:\dat\projects\gazeMapper\projs\roy_markers')
        et_recs = ['et1','et2']
        cam_recs= ['cam']

    case 3:
        base = pathlib.Path(r'C:\dat\projects\Margot gazeMapper\pilot 4\data')
        proj = pathlib.Path(r'C:\dat\projects\gazeMapper\projs\margot_2_et')
        et_recs = ['et_student','et_teacher']
        cam_recs= []


# # create session
# sess_def = session.SessionDefinition.load_from_json(proj/'config'/'session_def.json')

# # import
# match which:
#     case 1:
#         sess1 = session.Session(sess_def,'J13')
#         sess1.create_working_directory(proj)
#         rec_info = importing.get_recording_info(base1/'J13A'/'20231019T081115Z', eyetracker.EyeTracker.Tobii_Glasses_3)
#         rec = sess1.add_recording_and_import('et1',rec_info[0],False)
#         rec_info = importing.get_recording_info(base1/'J13D'/'20231019T081031Z', eyetracker.EyeTracker.Tobii_Glasses_3)
#         rec = sess1.add_recording_and_import('et2',rec_info[0],False)
#         rec_info = camera_recording.Recording('topview','2023-10-19_17-14-59.mp4', base1)
#         rec = sess1.add_recording_and_import('cam',rec_info,False, cam_cal_file=r"C:\dat\projects\Roy Japanese Lego\pilot 3\analysis\data\brio_calibration_J.xml")
#         sess1.store_as_json()

#         sess2 = session.Session(sess_def,'N24')
#         sess2.create_working_directory(proj)
#         rec_info = importing.get_recording_info(base2/'N24A'/'2023-11-30_10-10-47-d1aa2982', eyetracker.EyeTracker.Pupil_Invisible)
#         rec = sess2.add_recording_and_import('et1',rec_info[0],False)
#         rec_info = importing.get_recording_info(base2/'N24D'/'2023-11-30_10-10-31-cc2e58e9', eyetracker.EyeTracker.Pupil_Invisible)
#         rec = sess2.add_recording_and_import('et2',rec_info[0],False)
#         rec_info = camera_recording.Recording('topview','2023-11-30_10-13-09.mov', base2)
#         rec = sess2.add_recording_and_import('cam',rec_info,False, cam_cal_file=r"C:\dat\projects\Roy Japanese Lego\pilot 3\analysis\data\brio_calibration_N.xml")
#         sess2.store_as_json()

#     case 2:
#         sess = session.Session(sess_def,'pair12')
#         sess.create_working_directory(proj)
#         # import recordings
#         rec_info = importing.get_recording_info(base/'PI1_scherm'/'2022-07-01_14-04-15-0bdecc9e', eyetracker.EyeTracker.Pupil_Invisible)
#         rec = sess.add_recording_and_import('et1',rec_info[0],False)

#         rec_info = importing.get_recording_info(base/'PI2_deur'/'2022-07-01_14-04-01-a251b241', eyetracker.EyeTracker.Pupil_Invisible)
#         rec = sess.add_recording_and_import('et2',rec_info[0],False)

#         rec_info = camera_recording.Recording('topview','topview_2022-07-01_14-05-10.mov', base)
#         rec = sess.add_recording_and_import('cam',rec_info,False, cam_cal_file=r"C:\dat\projects\a_finished\2023 roy lego\analysis\data\brio_calibration.xml")
#         sess.store_as_json()

#     case 3:
#         recs = {'N01A':'2024-04-17_11-29-00-c263c366',
#                 'N01D':'2024-04-17_11-29-39-f7e2e6f0',
#                 'N02A':'2024-04-17_13-36-23-a1cf026b',
#                 'N02D':'2024-04-17_13-37-02-6b4e4eec'}
#         for r in ['N01','N02']:
#             sess = session.Session(sess_def,r)
#             sess.create_working_directory(proj)

#             rec_info = importing.get_recording_info(base/r/(r+'A')/recs[r+'A'], eyetracker.EyeTracker.Pupil_Neon)
#             rec = sess.add_recording_and_import('et_teacher',rec_info[0],False)

#             rec_info = importing.get_recording_info(base/r/(r+'D')/recs[r+'D'], eyetracker.EyeTracker.Pupil_Neon)
#             rec = sess.add_recording_and_import('et_student',rec_info[0],False)
#             sess.store_as_json()




sessions = session.get_sessions_from_project_directory(proj)
sessions = [sessions[0]]
study_config = config.Study.load_from_json(config.guess_config_dir(proj))

# for s in sessions:
#     for r in et_recs+cam_recs:
#         do_coding(s.recordings[r].info.working_directory)

# for s in sessions:
#     for r in et_recs+cam_recs:
#         detect_markers(s.recordings[r].info.working_directory)

# for s in sessions:
#     for r in et_recs:
#         sync_et_to_cam(s.recordings[r].info.working_directory)
#     sync_to_ref(s.working_directory)

# for s in sessions:
#     for r in et_recs:
#         gaze_to_plane(s.recordings[r].info.working_directory)

# for s in sessions:
#     export_trials(s.working_directory)

# for s in sessions:
#     for r in et_recs:
#         run_validation(s.recordings[r].info.working_directory)

# for s in sessions:
#     if study_config.sync_ref_recording:
#         auto_code_trials(s.recordings[study_config.sync_ref_recording].info.working_directory)

# for s in sessions:
#     for r in et_recs+cam_recs:
#         auto_code_sync_points(s.recordings[r].info.working_directory)

for s in sessions:
    make_video(s.working_directory, show_visualization=True)