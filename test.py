import pathlib

from glassesTools import eyetracker, importing

from src.gazeMapper import camera_recording, episode, session
from src.gazeMapper.process.code_episodes import process as do_coding


base = pathlib.Path(r'C:\dat\projects\Roy Japanese Lego\pilot 3\data\J00')
proj = pathlib.Path(r'C:\dat\projects\gazeMapper\test_proj')

# sess_def = session.SessionDefinition.load_from_json(proj/'config'/'session.json')

# sess_def.store_as_json(proj/'session_test.json')


# # create session
# sess = session.Session(sess_def,'test1')
# sess.create_working_directory(proj)
# # import recordings
# rec_info = importing.get_recording_info(base/'J00A'/'20230518T012901Z', eyetracker.EyeTracker.Tobii_Glasses_3)
# rec = sess.import_and_add_recording('test_et1',rec_info[0])

# rec_info = importing.get_recording_info(base/'J00D'/'20230518T012502Z', eyetracker.EyeTracker.Tobii_Glasses_3)
# rec = sess.import_and_add_recording('test_et2',rec_info[0])

# rec_info = camera_recording.Recording('topview_2023-05-18_10-32-48','topview_2023-05-18_10-32-48.mp4', base)
# rec = sess.import_and_add_recording('test_cam',rec_info)

# sess.store_as_json()

sessions = session.get_sessions_from_directory(proj)


#do_coding(sessions[0].recordings['test_et1'].info.working_directory,'None',session.RecordingType.EyeTracker)
do_coding(sessions[0].recordings['test_cam'].info.working_directory,'None',session.RecordingType.Camera)