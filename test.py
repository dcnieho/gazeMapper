import pathlib

from glassesTools import eyetracker, importing, marker

from src.gazeMapper import camera_recording, config, episode, session, plane
from src.gazeMapper.process.code_episodes import process as do_coding
from src.gazeMapper.process.detect_markers import process as detect_markers
from src.gazeMapper.process.sync_VOR import process as sync_VOR
from src.gazeMapper.process.sync_to_ref import process as sync_to_ref


base = pathlib.Path(r'C:\dat\projects\Roy Japanese Lego\pilot 3\data\J00')
proj = pathlib.Path(r'C:\dat\projects\gazeMapper\test_proj')


# # create session
# sess_def = session.SessionDefinition.load_from_json(proj/'config'/'session_def.json')
# sess = session.Session(sess_def,'N24')
# sess.create_working_directory(proj)
# # import recordings
# #rec_info = importing.get_recording_info(base/'J13A'/'20231019T081115Z', eyetracker.EyeTracker.Tobii_Glasses_3)
# rec_info = importing.get_recording_info(base/'N24A'/'2023-11-30_10-10-47-d1aa2982', eyetracker.EyeTracker.Pupil_Invisible)
# rec = sess.import_and_add_recording('et1',rec_info[0],False)

# #rec_info = importing.get_recording_info(base/'J13D'/'20231019T081031Z', eyetracker.EyeTracker.Tobii_Glasses_3)
# rec_info = importing.get_recording_info(base/'N24D'/'2023-11-30_10-10-31-cc2e58e9', eyetracker.EyeTracker.Pupil_Invisible)
# rec = sess.import_and_add_recording('et2',rec_info[0],False)

# #rec_info = camera_recording.Recording('topview','2023-10-19_17-14-59.mp4', base)
# rec_info = camera_recording.Recording('topview','2023-11-30_10-13-09.mov', base)
# rec = sess.import_and_add_recording('cam',rec_info,False)

# sess.store_as_json()


# sd = session.SessionDefinition.load_from_json(proj/'config'/'session_def.json')
# st = config.Study.load_from_json(proj/'config')

# sess = session.Session.from_definition(st.session_def,proj/'test1')
# sess.has_all_recordings()

# sess2 = session.Session.load_from_json(proj/'test1')

# pl1 = plane.get_plane_from_definition(st.planes[0], proj/'config'/st.planes[0].name)
# pl2 = plane.get_plane_from_path(proj/'config'/st.planes[1].name)

sessions = session.get_sessions_from_directory(proj)

# do_coding(sessions[0].recordings['et1'].info.working_directory,session.RecordingType.EyeTracker)
# do_coding(sessions[0].recordings['et2'].info.working_directory,session.RecordingType.EyeTracker)
# do_coding(sessions[0].recordings['cam'].info.working_directory,session.RecordingType.Camera)

# for s in sessions:
#     detect_markers(s.recordings['cam'].info.working_directory,proj/'config',session.RecordingType.Camera)
#     detect_markers(s.recordings['et1'].info.working_directory,proj/'config',session.RecordingType.EyeTracker)
#     detect_markers(s.recordings['et2'].info.working_directory,proj/'config',session.RecordingType.EyeTracker)

for s in sessions:
    sync_VOR(s.recordings['et1'].info.working_directory,proj/'config')
    sync_VOR(s.recordings['et2'].info.working_directory,proj/'config')
    sync_to_ref(s.working_directory,proj/'config',do_time_stretch=True)