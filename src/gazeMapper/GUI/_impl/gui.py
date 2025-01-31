import concurrent.futures
import pathlib
import asyncio
import concurrent
import sys
import platform
import webbrowser
from typing import Callable
import copy
import threading
import time

import imgui_bundle
from imgui_bundle import imgui, immapp, imgui_md, hello_imgui, glfw_utils, icons_fontawesome_6 as ifa6
import glfw
import OpenGL
import OpenGL.GL as gl

import glassesTools
from glassesTools import annotation, gui as gt_gui, naming as gt_naming, plane as gt_plane, platform as gt_platform
import glassesValidator

from ... import config, marker, plane, process, project_watcher, session, type_utils, version
from .. import async_thread
from . import callbacks, colors, image_helper, process_pool, session_lister, settings_editor, utils


class GUI:
    def __init__(self):
        self.popup_stack = []
        self.running     = False
        settings_editor.set_gui_instance(self)

        self.project_dir: pathlib.Path = None
        self.study_config: config.Study = None

        self.sessions: dict[str, session.Session]                       = {}
        self.session_config_overrides: dict[str, config.StudyOverride]  = {}
        self._sessions_lock: threading.Lock                             = threading.Lock()
        self._selected_sessions: dict[str, bool]                        = {}
        self._session_lister = session_lister.List(self.sessions, self._sessions_lock, self._selected_sessions, info_callback=self._open_session_detail, draw_action_status_callback=self._session_action_status, item_context_callback=self._session_context_menu)

        self.recording_config_overrides: dict[str, dict[str, config.StudyOverride]]   = {}
        self._recording_listers  : dict[str, gt_gui.recording_table.RecordingTable]   = {}
        self._selected_recordings: dict[str, dict[str, bool]]                         = {}

        self._possible_value_getters: dict[str] = {}

        self.need_setup_recordings  = True
        self.need_setup_plane       = True
        self.need_setup_episode     = True
        self.can_accept_sessions    = False
        self._session_actions: set[process.Action] = set()

        self.config_watcher             : concurrent.futures.Future = None
        self.config_watcher_stop_event  : asyncio.Event             = None

        self.process_pool   = process_pool.ProcessPool()
        self.job_scheduler  = process_pool.JobScheduler[utils.JobInfo](self.process_pool, self._check_job_valid)

        self._window_list                   : list[hello_imgui.DockableWindow]  = []
        self._to_dock                                                           = []
        self._to_focus                                                          = None
        self._after_window_update_callback  : Callable[[],None]                 = None
        self._need_set_window_title                                             = False
        self._main_dock_node_id                                                 = None

        self._sessions_pane         : hello_imgui.DockableWindow = None
        self._project_settings_pane : hello_imgui.DockableWindow = None
        self._action_list_pane      : hello_imgui.DockableWindow = None
        self._show_demo_window                                   = False

        self._icon_font: imgui.ImFont   = None
        self._big_font: imgui.ImFont    = None

        self._problems_cache        : type_utils.ProblemDict                            = {}
        self._marker_preview_cache  : dict[tuple[int,int,int], image_helper.ImageHelper]= {}
        self._plane_preview_cache   : dict[str               , image_helper.ImageHelper]= {}

        # Show errors in threads
        def asyncexcepthook(future: asyncio.Future):
            try:
                exc = future.exception()
            except concurrent.futures.CancelledError:
                return
            if not exc:
                return
            tb = gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
            if isinstance(exc, asyncio.TimeoutError):
                gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", gt_gui.msg_box.MsgBox.warn, more=tb)
                return
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", gt_gui.msg_box.MsgBox.error)
        async_thread.done_callback = asyncexcepthook

    def _load_fonts(self):
        def selected_glyphs_to_ranges(glyph_list: list[str]) -> list[tuple[int, int]]:
            return [tuple([ord(x),ord(x)]) for x in glyph_list]

        # load the default font. Do it manually as we want to use Roboto as the default font
        normal_size = 16.
        hello_imgui.load_font("fonts/Roboto/Roboto-Regular.ttf", normal_size)
        hello_imgui.load_font("fonts/Font_Awesome_6_Free-Solid-900.otf", normal_size, hello_imgui.FontLoadingParams(merge_to_last_font=True, glyph_ranges=[(ifa6.ICON_MIN_FA, ifa6.ICON_MAX_FA)]))

        # big font
        big_size = 28.
        self._big_font = hello_imgui.load_font("fonts/Roboto/Roboto-Regular.ttf", big_size)

        # load large icons for message box
        msg_box_size = 69.
        large_icons_params = hello_imgui.FontLoadingParams()
        large_icons_params.glyph_ranges = selected_glyphs_to_ranges([ifa6.ICON_FA_CIRCLE_QUESTION, ifa6.ICON_FA_CIRCLE_INFO, ifa6.ICON_FA_TRIANGLE_EXCLAMATION])
        self._icon_font = gt_gui.msg_box.icon_font = \
            hello_imgui.load_font("fonts/Font_Awesome_6_Free-Solid-900.otf", msg_box_size, large_icons_params)

    def _setup_glfw(self):
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_drop_callback(win, self._drop_callback)

    def _drop_callback(self, _: glfw._GLFWwindow, items: list[str]):
        paths = [pathlib.Path(item) for item in items]
        if self.popup_stack and isinstance(picker := self.popup_stack[-1], gt_gui.file_picker.FilePicker):
            picker.set_dir(paths)
        else:
            if self.project_dir is not None:
                # import recordings
                callbacks.add_recordings(self, paths, [s for s in self._selected_sessions if self._selected_sessions[s] and not self.sessions[s].has_all_recordings()])
            else:
                # load project
                if len(paths)!=1 or not (path := paths[0]).is_dir():
                    gt_gui.utils.push_popup(gt_gui.msg_box.msgbox, "Project opening error", "Only a single project directory should be drag-dropped on the glassesValidator GUI.", gt_gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in paths])))
                else:
                    callbacks.try_load_project(self, path, 'loading')

    def run(self):
        # Hello ImGui params (they hold the settings as well as the Gui callbacks)
        runner_params = hello_imgui.RunnerParams()

        runner_params.app_window_params.window_title = self._get_window_title()

        runner_params.app_window_params.window_geometry.size = (1400, 700)
        runner_params.app_window_params.restore_previous_geometry = True
        runner_params.callbacks.post_init_add_platform_backend_callbacks = self._setup_glfw
        runner_params.callbacks.load_additional_fonts = self._load_fonts
        runner_params.callbacks.pre_new_frame = self._update_windows
        runner_params.callbacks.before_exit = self._exiting

        # Status bar, idle throttling
        runner_params.imgui_window_params.show_status_bar = False
        runner_params.imgui_window_params.show_status_fps = False
        runner_params.fps_idling.enable_idling = False

        # Menu bar
        runner_params.imgui_window_params.show_menu_bar = True
        runner_params.imgui_window_params.menu_app_title = "File"
        runner_params.callbacks.show_app_menu_items = self._show_app_menu_items
        runner_params.callbacks.show_menus = self._show_menu_gui

        # dockspace
        # First, tell HelloImGui that we want full screen dock space (this will create "MainDockSpace")
        runner_params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
        )
        runner_params.imgui_window_params.enable_viewports = True
        # Always start with this layout, do not persist changes made by the user
        runner_params.docking_params.layout_condition = hello_imgui.DockingLayoutCondition.application_start
        # we use docking throughout this app just for the tab bar
        # set some flags so that users can't undock or see the menu arrow to hide the tab bar
        runner_params.docking_params.main_dock_space_node_flags = imgui.DockNodeFlags_.no_undocking | imgui.internal.DockNodeFlagsPrivate_.no_docking | imgui.internal.DockNodeFlagsPrivate_.no_window_menu_button

        # define first windows
        self._sessions_pane = self._make_main_space_window("Sessions", self._sessions_pane_drawer)
        self._project_settings_pane = self._make_main_space_window("Project settings", self._project_settings_pane_drawer, is_visible=False)
        self._action_list_pane = self._make_main_space_window("Processing queue", self._action_list_pane_drawer, is_visible=False)
        # transmit them to HelloImGui
        runner_params.docking_params.dockable_windows = [
            self._sessions_pane,
            self._project_settings_pane,
            self._action_list_pane
        ]

        addons_params = immapp.AddOnsParams()
        addons_params.with_markdown = True
        immapp.run(runner_params, addons_params)

    def _exiting(self):
        self.close_project()
        self.running = False

    def _make_main_space_window(self, name: str, gui_func: Callable[[],None], can_be_closed=False, is_visible=True):
        main_space_view = hello_imgui.DockableWindow()
        main_space_view.label = name
        main_space_view.dock_space_name = "MainDockSpace"
        main_space_view.gui_function = gui_func
        main_space_view.can_be_closed = can_be_closed
        main_space_view.is_visible = is_visible
        main_space_view.remember_is_visible = False
        return main_space_view

    def _update_windows(self):
        if not self.running:
            # apply theme
            hello_imgui.apply_theme(hello_imgui.ImGuiTheme_.darcula_darker)
            # fix up the style: fully opaque window backgrounds
            window_bg = imgui.get_style().color_(imgui.Col_.window_bg)
            window_bg.w = 1
        self.running = True
        if self._need_set_window_title:
            self._set_window_title()

        # update windows to be shown
        old_window_labels = {w.label for w in hello_imgui.get_runner_params().docking_params.dockable_windows}
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []
        else:
            # check if any session detail windows were closed. Those should be removed from the list
            hello_imgui.get_runner_params().docking_params.dockable_windows = \
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.is_visible or w.label in ['Project settings', 'Processing queue']]
        current_windows = {w.label for w in hello_imgui.get_runner_params().docking_params.dockable_windows}
        # some cleanup may be needed for some of the closed windows
        if (removed:=old_window_labels-current_windows):
            for r in removed:
                if r.endswith('##session_view'):
                    sess_name = r.removesuffix('##session_view')
                    # cleanup
                    self.session_config_overrides.pop(sess_name)
                    self.recording_config_overrides.pop(sess_name)
                    self._selected_recordings.pop(sess_name)
                    self._recording_listers.pop(sess_name)

        # we also handle docking requests here
        if self._to_dock and self._main_dock_node_id:
            for w in self._to_dock:
                imgui.internal.dock_builder_dock_window(w, self._main_dock_node_id)
            self._to_dock = []

        # handle focus requests, which apparently need to be delayed
        # one frame for them to work also in case its a new window
        if self._to_focus is not None:
            if isinstance(self._to_focus,str):
                self._to_focus = [self._to_focus,1]
            if self._to_focus[1]>0:
                self._to_focus[1] -= 1
            else:
                for w in hello_imgui.get_runner_params().docking_params.dockable_windows:
                    if w.label==self._to_focus[0]:
                        w.focus_window_at_next_frame = True
                self._to_focus = None

        # if any callback set, call it once then remove
        if self._after_window_update_callback:
            self._after_window_update_callback()
            self._after_window_update_callback = None

    def _show_app_menu_items(self):
        disabled = not self.project_dir
        if disabled:
            imgui.begin_disabled()
        if imgui.menu_item(ifa6.ICON_FA_CIRCLE_XMARK+" Close project", "", False)[0]:
            self.close_project()
        if disabled:
            imgui.end_disabled()
        if imgui.menu_item(ifa6.ICON_FA_FOLDER_OPEN+" Open project folder", "", False)[0]:
            callbacks.open_folder(self.project_dir)

    def _show_menu_gui(self):
        # this is always called, so we handle popups and other state here
        self._check_project_setup_state()
        gt_gui.utils.handle_popup_stack(self.popup_stack)
        self._update_jobs_and_process_pool()
        # also handle showing of debug windows
        if self._show_demo_window:
            self._show_demo_window = imgui.show_demo_window(self._show_demo_window)

        # now actual menu
        if imgui.begin_menu("Help"):
            if imgui.menu_item("About", "", False)[0]:
                gt_gui.utils.push_popup(self, self._about_popup_drawer)
            self._show_demo_window = imgui.menu_item("Debug window", "", self._show_demo_window)[1]
            imgui.end_menu()

    def _get_window_title(self):
        title = "gazeMapper"
        if self.project_dir is not None:
            title += f' ({self.project_dir.name})'
        return title

    def _set_window_title(self):
        new_title = self._get_window_title()
        # this is just for show, doesn't trigger an update. But lets keep them in sync
        hello_imgui.get_runner_params().app_window_params.window_title = new_title
        # actually update window title
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_window_title(win, new_title)
        self._need_set_window_title = False

    def _config_change_callback(self, change_path: str, change_type: str):
        change_path = pathlib.Path(change_path)
        # NB watcher filter is configured such that all adds and deletes are folder and all modifies are files of interest
        if change_type=='modified':
            # file: deal with status changes
            def status_file_reloader(loader: Callable[[bool], None]):
                n_try=0
                while n_try<3:
                    try:
                        n_try+=1
                        loader(False)
                    except:
                        time.sleep(.1)  # file possibly not fully written yet
                    else:
                        break
                # NB: reapplying pending and running state (not stored in file) is done on next frame as part of _update_jobs_and_process_pool()
            change_path = change_path.relative_to(self.project_dir)
            match len(change_path.parents):
                case 2:
                    # session-level states
                    sess = change_path.parent.name
                    with self._sessions_lock:
                        if sess not in self.sessions:
                            # some other folder apparently
                            return
                        status_file_reloader(self.sessions[sess].load_action_states)
                case 3:
                    # recording-level states
                    sess = change_path.parent.parent.name
                    rec = change_path.parent.name
                    with self._sessions_lock:
                        if sess not in self.sessions:
                            # some other folder apparently
                            return
                        if rec not in self.sessions[sess].recordings:
                            # some other folder apparently
                            return
                        status_file_reloader(self.sessions[sess].recordings[rec].load_action_states)
                case _:
                    pass    # ignore, not of interest
        else:
            # folder
            change_path = change_path.relative_to(self.project_dir)
            match len(change_path.parents):
                case 1:
                    # added or deleted session
                    with self._sessions_lock:
                        if change_type=='deleted':
                            if (sess:=change_path.name) in self.sessions:
                                self.sessions.pop(sess)
                                self._selected_sessions.pop(sess)
                                self._window_list = [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.label!=f'{sess}##session_view']
                        else:
                            # get new session
                            try:
                                sess = session.get_session_from_directory(self.project_dir/change_path, self.study_config.session_def)
                            except:
                                pass
                            else:
                                self.sessions |= {sess.name: sess}
                                self._selected_sessions |= {sess.name: False}
                case 2:
                    # added or deleted recording
                    sess = change_path.parent.name
                    with self._sessions_lock:
                        if sess not in self.sessions:
                            # some other folder apparently
                            return
                        rec = change_path.name
                        if not self.sessions[sess].definition.is_known_recording(rec):
                            # some other folder apparently
                            return
                        if change_type=='deleted':
                            self.sessions[sess].recordings.pop(rec, None)
                            if sess in self._selected_recordings:
                                self._selected_recordings[sess].pop(rec, None)
                        elif rec not in self.sessions[sess].recordings: # don't replace if already present
                            self.sessions[sess].add_existing_recording(rec)
                            if sess in self._selected_recordings:
                                self._selected_recordings[sess] |= {rec: False}
                case _:
                    pass    # ignore, not of interest

    def launch_task(self, sess: str, recording: str|None, action: process.Action, **kwargs):
        # NB: this is run under lock, so sess and recording are valid
        job = utils.JobInfo(action, sess, recording)
        if job in self._get_pending_running_job_list():
            # already scheduled, nothing to do
            return
        if action==process.Action.IMPORT:
            # NB: if import fails, remove directory, which removes recording from GUI (automatically thanks to watcher)
            func = self.sessions[sess].import_recording
            args = (recording,)
        else:
            func = process.action_to_func(action)
            if recording:
                working_dir = self.sessions[sess].recordings[recording].info.working_directory
            else:
                working_dir = self.sessions[sess].working_directory
            args = (working_dir,)
        # check if task needs a GUI, if so make sure only one needing a GUI can run at the same time, and that these
        # tasks are prioritized so we're not stuck waiting for a GUI task while some other task completes
        exclusive_id = 1 if (action.needs_GUI or kwargs.get('show_visualization',False)) else None
        priority = 1 if exclusive_id is not None else None

        # add to scheduler
        payload = process_pool.JobPayload(func, args, kwargs)
        self.job_scheduler.add_job(job, payload, self._action_done_callback, exclusive_id=exclusive_id, priority=priority)

    def _update_jobs_and_process_pool(self):
        with self._sessions_lock:
            # tick job scheduler (under lock as checking job validity needs session lock)
            self.job_scheduler.update()

            # set pending and running states since these are not stored in the states file
            for job_id in self.job_scheduler.jobs:
                job_state = self.job_scheduler.jobs[job_id].get_state()
                if job_state in [process.State.Pending, process.State.Running]:
                    self._update_job_states_impl(self.job_scheduler.jobs[job_id].user_data, job_state)

        # if there are no jobs left, clean up process pool
        self.process_pool.cleanup_if_no_jobs()

    def _check_job_valid(self, job: utils.JobInfo) -> bool:
        sess = self.sessions.get(job.session,None)
        if sess is None:
            return False
        if job.recording and job.recording not in sess.recordings:
            return False
        return True

    def _update_job_states_impl(self, job: utils.JobInfo, job_state: process.State):
        sess = self.sessions.get(job.session,None)
        if sess is None:
            return
        if job.recording and job.recording not in sess.recordings:
            return
        if job.recording:
            rec = sess.recordings.get(job.recording,None)
            if rec is None:
                return
            rec.state[job.action] = job_state
        else:
            sess.state[job.action] = job_state

    def _action_done_callback(self, future: process_pool.ProcessFuture, job_id: int, job: utils.JobInfo, state: process.State):
        # if process failed, notify error
        session_level = job.recording is None
        if state==process.State.Failed:
            exc = future.exception()    # should not throw exception since CancelledError is already encoded in state and future is done
            tb = gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
            lbl = f'session "{job.session}"'
            if not session_level:
                lbl += f', recording "{job.recording}"'
            lbl += f' (work item {job_id}, action {job.action.displayable_name})'
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", f"Something went wrong in a worker process for {lbl}:\n\n{tb}", gt_gui.msg_box.MsgBox.error)
            self.job_scheduler.jobs[job_id].error = tb

        # clean up, if needed, when a task failed or was canceled
        if job.action==process.Action.IMPORT and state in [process.State.Canceled, process.State.Failed]:
            # remove working directory if this was an import task
            async_thread.run(callbacks.remove_recording_working_dir(self.project_dir, job.session, job.recording))
            # also update GUI state, might be needed if there was no working directory yet for instance
            if sess:=self.sessions.get(job.session,None):
                sess.recordings.pop(job.recording,None)
            return
        # get final task state when completed, load from file. Need to do this because change listener may fire before task
        # completes, and its output is then overwritten in _update_jobs_and_process_pool()
        sess = self.sessions.get(job.session,None)
        if sess is None:
            return
        if job.recording:
            rec = sess.recordings.get(job.recording,None)
            if rec is None:
                return
            if job.action==process.Action.IMPORT:
                # the import call has created a working directory for the recording, and may have updated the info in other
                # ways (e.g. filled in recording length that wasn't known from metadata). Read from file and update what we
                # hold in memory. NB: must be loaded from file as recording update is run in a different process
                rec_info = sess.load_recording_info(job.recording)
                sess.update_recording_info(job.recording, rec_info)
            rec.load_action_states(False)
        else:
            sess.load_action_states(False)

    def load_project(self, path: pathlib.Path):
        self.project_dir = path
        try:
            config_dir = config.guess_config_dir(self.project_dir)
            self.study_config = config.Study.load_from_json(config_dir, strict_check=False)
            self._reload_sessions()
            self.process_pool.set_num_workers(self.study_config.gui_num_workers)
        except Exception as e:
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Project loading error", f"Failed to load the project at {self.project_dir}:\n{e}\n\n{gt_gui.utils.get_traceback(e)}", gt_gui.msg_box.MsgBox.error)
            self.close_project()
            return

        self.config_watcher_stop_event = asyncio.Event()
        self.config_watcher = async_thread.run(project_watcher.watch_and_report_changes(self.project_dir, self._config_change_callback, self.config_watcher_stop_event, watch_filter=project_watcher.ProjectFilter(('.gazeMapper',), True, {config_dir}, True, True)))

        def _get_known_recordings(filter_ref=False, dev_types:list[session.RecordingType]|None=None) -> set[str]:
            recs = {r.name for r in self.study_config.session_def.recordings}
            if filter_ref and self.study_config.sync_ref_recording:
                recs = {r for r in recs if r!=self.study_config.sync_ref_recording}
            if dev_types:
                recs = {r for r in recs if self.study_config.session_def.get_recording_def(r).type in dev_types}
            return recs
        def _get_known_recordings_no_ref() -> set[str]:
            return _get_known_recordings(filter_ref=True)
        def _get_known_recordings_only_eye_tracker() -> set[str]:
            return _get_known_recordings(dev_types=[session.RecordingType.Eye_Tracker])
        def _get_known_individual_markers() -> set[str]:
            return {m.id for m in self.study_config.individual_markers}
        def _get_known_planes() -> set[str]:
            return {p.name for p in self.study_config.planes}
        def _get_episodes_to_code_for_planes() -> set[annotation.Event]:
            return {e for e in self.study_config.episodes_to_code if e!=annotation.Event.Sync_Camera}
        self._possible_value_getters = {
            'video_make_which': _get_known_recordings,
            'video_recording_colors': _get_known_recordings_only_eye_tracker,
            'video_show_gaze_on_plane_in_which': _get_known_recordings,
            'video_show_camera_in_which': _get_known_recordings,
            'video_show_gaze_vec_in_which': _get_known_recordings,
            'sync_ref_recording': _get_known_recordings,
            'sync_ref_average_recordings': _get_known_recordings_no_ref,
            'planes_per_episode': [_get_episodes_to_code_for_planes, _get_known_planes],
            'auto_code_sync_points': {'markers': _get_known_individual_markers},
            'auto_code_trial_episodes': {'start_markers': _get_known_individual_markers, 'end_markers': _get_known_individual_markers}
        }

        self._need_set_window_title = True
        self._project_settings_pane.is_visible = True
        self._action_list_pane.is_visible = True
        # trigger update so visibility change is honored
        self._window_list = [self._sessions_pane, self._project_settings_pane, self._action_list_pane]
        self._to_focus = self._sessions_pane.label  # ensure sessions pane remains focused

    def _reload_sessions(self):
        sessions = session.get_sessions_from_project_directory(self.project_dir, self.study_config.session_def)
        with self._sessions_lock:
            self.sessions.clear()
            self.sessions |= {s.name:s for s in sessions}
            selected = self._selected_sessions.copy()
            self._selected_sessions.clear()
            self._selected_sessions |= {k:(selected[k] if k in selected else False) for k in self.sessions}
        self._update_shown_actions_for_config()

    def _check_project_setup_state(self):
        if self.study_config is not None:
            self._problems_cache = self.study_config.field_problems()
        # need to have:
        # 1. at least one recording defined in the session;
        # 2. one plane set up
        # 3. one episode to code
        # 4. one plane linked to one episode
        self.need_setup_recordings = not self.study_config or not self.study_config.session_def.recordings or 'session_def' in self._problems_cache
        self.need_setup_plane = not self.study_config or not self.study_config.planes or any((not p.has_complete_setup() for p in self.study_config.planes))
        self.need_setup_episode = not self.study_config or not self.study_config.episodes_to_code or not self.study_config.planes_per_episode or any((x in self._problems_cache for x in ['episodes_to_code', 'planes_per_episode']))

        self.can_accept_sessions = \
            not self._problems_cache and \
            not self.need_setup_recordings and \
            not self.need_setup_plane and \
            not self.need_setup_episode

    def _session_lister_set_actions_to_show(self, lister: session_lister.List):
        if self.study_config is None:
            lister.set_actions_to_show(set())
            return

        self._session_actions = process.get_actions_for_config(self.study_config, exclude_session_level=False)
        lister.set_actions_to_show(self._session_actions)

    def _recording_lister_set_actions_to_show(self, lister: gt_gui.recording_table.RecordingTable, sess: str):
        cfg = self.session_config_overrides[sess].apply(self.study_config, strict_check=False)
        actions = process.get_actions_for_config(cfg, exclude_session_level=True)
        def _draw_status(action: process.Action, item: session.Recording):
            if process.is_action_possible_for_recording(item.definition.name, item.definition.type, action, cfg):
                session_lister.draw_process_state(item.state[action])
            else:
                imgui.text('-')
                if action==process.Action.AUTO_CODE_TRIALS and cfg.sync_ref_recording and item.definition.name!=cfg.sync_ref_recording:
                    msg = f'Not applicable to a recording that is not the sync_ref_recording'
                else:
                    msg = f'Not applicable to a {item.definition.type.value} recording'
                gt_gui.utils.draw_hover_text(msg,'')
        def _get_sort_value(action: process.Action, iid: int):
            item = self.sessions[sess].recordings[iid]
            if process.is_action_possible_for_recording(item.definition.name, item.definition.type, action, cfg):
                return item.state[action]
            else:
                return 999

        # build set of column, trigger column rebuild
        columns = [
            gt_gui.recording_table.ColumnSpec(1,ifa6.ICON_FA_SIGNATURE+" Recording name",imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide, lambda rec: imgui.text(rec.definition.name), lambda iid: iid, "Recording name")
        ]+[
            gt_gui.recording_table.ColumnSpec(2+c, a.displayable_name, imgui.TableColumnFlags_.angled_header, lambda rec, a=a: _draw_status(a, rec), lambda iid, a=a: _get_sort_value(a, iid)) for c,a in enumerate(actions)
        ]
        lister.build_columns(columns)

    def _update_shown_actions_for_config(self):
        self._session_lister_set_actions_to_show(self._session_lister)
        for sess in self._recording_listers:
            self._recording_lister_set_actions_to_show(self._recording_listers[sess], sess)

    def close_project(self):
        self._project_settings_pane.is_visible = False
        self._action_list_pane.is_visible = False
        # trigger update so visibility change is honored, also delete other windows in the process
        self._window_list = [self._sessions_pane, self._project_settings_pane, self._action_list_pane]

        # stop watching for config changes
        if self.config_watcher_stop_event is not None:
            self.config_watcher_stop_event.set()
        if self.config_watcher is not None:
            self.config_watcher.result()
        self.config_watcher = None
        self.config_watcher_stop_event = None

        # defer rest of unloading until windows deleted, as some of these variables will be accessed during this draw loop
        self._after_window_update_callback = self._finish_unload_project

    def _finish_unload_project(self):
        self.job_scheduler.clear()
        self.project_dir = None
        self._possible_value_getters = {}
        self.study_config = None
        self.sessions.clear()
        self._selected_sessions.clear()
        self._need_set_window_title = True
        self.recording_config_overrides.clear()
        self._recording_listers.clear()
        self._selected_recordings.clear()
        self._plane_preview_cache.clear()
        self._marker_preview_cache.clear()


    def _sessions_pane_drawer(self):
        if not self._main_dock_node_id:
            # this window is docked to the right dock node, if we don't
            # have it yet, query id of this dock node as we'll need it for later
            # windows
            self._main_dock_node_id = imgui.get_window_dock_id()
        if not self.project_dir:
            self._unopened_interface_drawer()
            return
        elif not self.can_accept_sessions:
            imgui.text_colored(colors.error, "This study's set up is incomplete or has a problem.")
            imgui.align_text_to_frame_padding()
            imgui.text_colored(colors.error, 'Finish the setup in the')
            imgui.same_line()
            if imgui.button('Project settings##button'):
                self._to_focus = self._project_settings_pane.label
            imgui.same_line()
            imgui.text_colored(colors.error, 'tab before you can import and process recording sessions.')
            return

        if imgui.button('+ new session'):
            callbacks.new_session_button(self)
        imgui.same_line()
        if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import eye tracker recordings'):
            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_et_recordings', sessions=[s for s in self._selected_sessions if self._selected_sessions[s] and self.sessions[s].missing_recordings(session.RecordingType.Eye_Tracker)]))
        if any((r.type==session.RecordingType.Camera for r in self.study_config.session_def.recordings)):
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import camera recordings'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_cam_recordings', sessions=[s for s in self._selected_sessions if self._selected_sessions[s] and self.sessions[s].missing_recordings(session.RecordingType.Camera)]))
        self._session_lister.draw()

    def _unopened_interface_drawer(self):
        avail      = imgui.get_content_region_avail()
        but_width  = 200*hello_imgui.dpi_window_size_factor()
        but_height = 100*hello_imgui.dpi_window_size_factor()

        but_x = (avail.x - 2*but_width - 10*imgui.get_style().item_spacing.x) / 2
        but_y = (avail.y - but_height) / 2

        imgui.push_font(self._big_font)
        text = "Drag and drop a gazeMapper project folder or use the below buttons"
        size = imgui.calc_text_size(text)
        imgui.set_cursor_pos(((avail.x-size.x)/2, (but_y-size.y)/2))
        imgui.text(text)
        imgui.pop_font()

        imgui.set_cursor_pos((but_x, but_y))
        if imgui.button(ifa6.ICON_FA_FOLDER_PLUS+" New project", size=(but_width, but_height)):
            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='creating'))
        imgui.same_line(spacing=10*imgui.get_style().item_spacing.x)
        if imgui.button(ifa6.ICON_FA_FOLDER_OPEN+" Open project", size=(but_width, but_height)):
            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='loading'))

    def _project_settings_pane_drawer(self):
        def _indicate_needs_attention():
            imgui.push_style_color(imgui.Col_.button,         colors.error_dark)
            imgui.push_style_color(imgui.Col_.button_hovered, colors.error)
            imgui.push_style_color(imgui.Col_.button_active,  colors.error_bright)
        # options handled in separate panes
        new_win_params: tuple[str,Callable[[],None]] = None
        if self.need_setup_recordings:
            _indicate_needs_attention()
        if imgui.button("Edit session definition"):
            new_win_params = ('Session definition', self._session_definition_pane_drawer)
        if self.need_setup_recordings:
            imgui.pop_style_color(3)
        imgui.same_line()

        if self.need_setup_plane:
            _indicate_needs_attention()
        if imgui.button("Edit planes"):
            new_win_params = ('Plane editor', self._plane_editor_pane_drawer)
        if self.need_setup_plane:
            imgui.pop_style_color(3)
        imgui.same_line()

        if self.need_setup_episode:
            _indicate_needs_attention()
        if imgui.button("Episode setup"):
            new_win_params = ('Episode setup', self._episode_setup_pane_drawer)
        if self.need_setup_episode:
            imgui.pop_style_color(3)
        imgui.same_line()

        if imgui.button("Edit individual markers"):
            new_win_params = ('Individual marker editor', self._individual_marker_setup_pane_drawer)
        if new_win_params is not None:
            if not any((w.label==new_win_params[0] for w in hello_imgui.get_runner_params().docking_params.dockable_windows)):
                new_win = self._make_main_space_window(*new_win_params, can_be_closed=True)
                if not self._window_list:
                    self._window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
                self._window_list.append(new_win)
                self._to_dock.append(new_win_params[0])
            self._to_focus = new_win_params[0]

        # rest of settings handled here in a settings tree
        if any((k not in ['session_def', 'episodes_to_code', 'planes_per_episode'] for k in self._problems_cache)):
            imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved')

        fields = [k for k in config.study_parameter_types.keys() if k in config.study_defaults]
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, None, self._problems_cache, config.study_parameter_doc)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", gt_gui.msg_box.MsgBox.error)
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()
                self._update_shown_actions_for_config()
                self.process_pool.set_num_workers(self.study_config.gui_num_workers)

    def _action_list_pane_drawer(self):
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), ['gui_num_workers'], config.study_parameter_types, config.study_defaults, self._possible_value_getters, None, self._problems_cache, config.study_parameter_doc)
        if changed:
            self.study_config = new_config
            self.study_config.store_as_json()
            self.process_pool.set_num_workers(self.study_config.gui_num_workers)

        if not self.job_scheduler.jobs:
            imgui.text('No actions have been enqueued or performed')
            return

        # gather all file actions
        jobs = self.job_scheduler.jobs.copy()
        job_ids = sorted(jobs.keys())
        job_states = {job_id:jobs[job_id].get_state() for job_id in job_ids}
        if any((job_states[i] in [process.State.Pending, process.State.Running] for i in job_states)):
            if imgui.button(ifa6.ICON_FA_HAND+' Cancel all'):
                self.job_scheduler.cancel_all_jobs()

        table_flags = (
                imgui.TableFlags_.scroll_x |
                imgui.TableFlags_.scroll_y |
                imgui.TableFlags_.hideable |
                imgui.TableFlags_.sortable |
                imgui.TableFlags_.sort_multi |
                imgui.TableFlags_.reorderable |
                imgui.TableFlags_.sizing_fixed_fit |
                imgui.TableFlags_.no_host_extend_y
            )
        if imgui.begin_table(f"##processing_queue",columns=5,flags=table_flags):
            imgui.table_setup_column("ID", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 0
            imgui.table_setup_column("Status", imgui.TableColumnFlags_.no_hide)  # 1
            imgui.table_setup_column("Session", imgui.TableColumnFlags_.no_hide)  # 2
            imgui.table_setup_column("Recording", imgui.TableColumnFlags_.no_hide)  # 3
            imgui.table_setup_column("Action", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_hide)  # 4
            imgui.table_setup_scroll_freeze(0, 1)  # Sticky column headers

            # Headers
            imgui.table_headers_row()

            # sort
            sort_specs = imgui.table_get_sort_specs()
            sort_specs = [sort_specs.get_specs(i) for i in range(sort_specs.specs_count)]
            for sort_spec in reversed(sort_specs):
                match sort_spec.column_index:
                    case 0:     # job ID
                        key = lambda idx: idx
                    case 1:     # status
                        key = lambda idx: jobs[idx].get_state()
                    case 2:     # session
                        key = lambda idx: jobs[idx].user_data.session
                    case 3:     # recording
                        key = lambda idx: (jobs[idx].user_data.recording is None, jobs[idx].user_data.recording)
                    case 4:     # action
                        key = lambda idx: jobs[idx].user_data.action

                job_ids.sort(key=key, reverse=sort_spec.get_sort_direction()==imgui.SortDirection.descending)

            # render actions
            for job_id in job_ids:
                imgui.table_next_row()

                for ci in range(5):
                    if not (imgui.table_get_column_flags(ci) & imgui.TableColumnFlags_.is_enabled):
                        continue
                    imgui.table_set_column_index(ci)

                    match ci:
                        case 0:
                            # ID
                            imgui.text(f'{job_id}')
                        case 1:
                            # Status
                            session_lister.draw_process_state((job_state:=jobs[job_id].get_state()))
                            if jobs[job_id].error:
                                gt_gui.utils.draw_hover_text(jobs[job_id].error, text='')
                                imgui.same_line()
                                if imgui.small_button(ifa6.ICON_FA_COPY+f'##{job_id}_copy_error'):
                                    imgui.set_clipboard_text(jobs[job_id].error)
                                gt_gui.utils.draw_hover_text('Copy error to clipboard', text='')
                            if job_state in [process.State.Pending, process.State.Running]:
                                imgui.same_line()
                                if imgui.small_button(ifa6.ICON_FA_HAND+f' Cancel##{job_id}'):
                                    self.job_scheduler.cancel_job(job_id)
                        case 2:
                            # Session
                            imgui.text(jobs[job_id].user_data.session)
                        case 3:
                            # Recording
                            if jobs[job_id].user_data.recording:
                                imgui.text(jobs[job_id].user_data.recording)
                        case 4:
                            # action
                            imgui.text(jobs[job_id].user_data.action.displayable_name)

            imgui.end_table()


    def _session_definition_pane_drawer(self):
        if not self.study_config.session_def.recordings:
            imgui.text_colored(colors.error,'*At minimum one recording should be defined')
        if 'session_def' in self._problems_cache:
            imgui.text_colored(colors.error,f"*{self._problems_cache['session_def']}")
        table_is_started = imgui.begin_table(f"##session_def_list", 3)
        if not table_is_started:
            return
        imgui.table_setup_column("Recording", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Type", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Camera calibration", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        config_path = config.guess_config_dir(self.study_config.working_directory)
        for r in self.study_config.session_def.recordings:
            imgui.table_next_row()
            imgui.table_next_column()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f'##{r.name}'):
                callbacks.delete_recording_definition(self.study_config, r)
                self._reload_sessions()
            imgui.same_line()
            imgui.text(r.name)
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.text(r.type.value)
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            cal_path = r.get_default_cal_file(config_path)
            if cal_path is None:
                imgui.text('Default camera calibration not set')
                gt_gui.utils.draw_hover_text('This is not a problem for most eye trackers, as the recording contains the calibration', '')
            else:
                imgui.text('Default camera calibration set')
                imgui.same_line()
                if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete default calibration##{r.name}'):
                    r.remove_default_cal_file(config_path)
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_DOWNLOAD+f' select calibration xml##cal_{r.name}'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='set_default_cam_cal', rec_def=r, rec_def_path=config_path))
        imgui.end_table()
        if imgui.button('+ new recording'):
            new_rec_name = ''
            new_rec_type: session.RecordingType = None
            def _valid_rec_name():
                nonlocal new_rec_name
                return new_rec_name and not any((r.name==new_rec_name for r in self.study_config.session_def.recordings))
            def _add_rec_popup():
                nonlocal new_rec_name
                nonlocal new_rec_type
                imgui.dummy((30*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_rec_info",2):
                    imgui.table_setup_column("##new_rec_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_rec_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_rec_name()
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Recording name")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_rec_name = imgui.input_text("##new_rec_name",new_rec_name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = new_rec_type is None
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Recording type")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    r_idx = session.recording_types.index(new_rec_type) if new_rec_type is not None else -1
                    _,r_idx = imgui.combo("##rec_type_selector", r_idx, [r.value for r in session.RecordingType])
                    new_rec_type = None if r_idx==-1 else session.recording_types[r_idx]
                    imgui.end_table()
                return 0 if imgui.is_key_released(imgui.Key.enter) else None

            buttons = {
                ifa6.ICON_FA_CHECK+" Create recording": (lambda: (callbacks.make_recording_definition(self.study_config, new_rec_type, new_rec_name), self._reload_sessions()), lambda: not _valid_rec_name() or new_rec_type is None),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add recording", _add_rec_popup, buttons = buttons, outside=False))

    def _plane_editor_pane_drawer(self):
        if not self.study_config.planes:
            imgui.text_colored(colors.error,'*At minimum one plane should be defined')
        for i,p in enumerate(self.study_config.planes):
            problem_fields = p.field_problems()
            fixed_fields   = p.fixed_fields()
            extra = ''
            lbl = f'{p.name} ({p.type.value})'
            if problem_fields:
                extra = '*'
                imgui.push_style_color(imgui.Col_.text, colors.error)
            if imgui.tree_node_ex(f'{extra}{lbl}###{lbl}', imgui.TreeNodeFlags_.framed):
                plane_dir = config.guess_config_dir(self.study_config.working_directory)/p.name
                if problem_fields:
                    imgui.pop_style_color()
                if p.type==plane.Type.Plane_2D and imgui.button(ifa6.ICON_FA_BARCODE+f' deploy ArUco markers'):
                    gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='deploy_aruco', ArUco_dict=p.aruco_dict, markerBorderBits=p.marker_border_bits))
                changed, _, new_p, _ = settings_editor.draw_dict_editor(copy.deepcopy(p), type(p), 0, list(plane.definition_parameter_types[p.type].keys()), plane.definition_parameter_types[p.type], plane.definition_defaults[p.type], problems=problem_fields, fixed=fixed_fields, documentation=plane.definition_parameter_doc)
                if changed:
                    # persist changed config
                    new_p.store_as_json(plane_dir)
                    if new_p.type==plane.Type.GlassesValidator and not new_p.use_default:
                        callbacks.glasses_validator_plane_check_config(self.study_config, new_p)
                    # recreate plane so any settings changes (e.g. applied defaults) are reflected in the gui
                    self.study_config.planes[i] = plane.Definition.load_from_json(plane_dir)
                    self._plane_preview_cache.pop(p.name, None)
                if imgui.button(ifa6.ICON_FA_FOLDER_OPEN+' open plane configuration folder'):
                    callbacks.open_folder(plane_dir)
                imgui.same_line()
                if imgui.button(ifa6.ICON_FA_IMAGE+' generate reference image'):
                    p_dir = config.guess_config_dir(self.study_config.working_directory) / p.name
                    plane.get_plane_from_definition(p, p_dir)   # constructing the plane triggers generation of the reference image
                    self._plane_preview_cache[p.name] = utils.load_image_with_helper(p_dir/gt_plane.Plane.default_ref_image_name)
                if p.name in self._plane_preview_cache and imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip|imgui.HoveredFlags_.delay_normal):
                    imgui.begin_tooltip()
                    self._plane_preview_cache[p.name].render(largest=400*hello_imgui.dpi_window_size_factor())
                    imgui.end_tooltip()
                imgui.same_line()
                if imgui.button(ifa6.ICON_FA_TRASH_CAN+' delete plane'):
                    callbacks.delete_plane(self.study_config, p)
                imgui.tree_pop()
            elif problem_fields:
                imgui.pop_style_color()
        if imgui.button('+ new plane'):
            new_plane_name = ''
            new_plane_type: plane.Type = None
            def _valid_plane_name():
                nonlocal new_plane_name
                return new_plane_name and not any((p.name==new_plane_name for p in self.study_config.planes))
            def _add_plane_popup():
                nonlocal new_plane_name
                nonlocal new_plane_type
                imgui.dummy((30*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_plane_info",2):
                    imgui.table_setup_column("##new_plane_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_plane_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_plane_name()
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Plane name")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_plane_name = imgui.input_text("##new_plane_name",new_plane_name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = new_plane_type is None
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Plane type")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    p_idx = plane.types.index(new_plane_type) if new_plane_type is not None else -1
                    _,p_idx = imgui.combo("##plane_type_selector", p_idx, [p.value for p in plane.types])
                    new_plane_type = None if p_idx==-1 else plane.types[p_idx]
                    imgui.end_table()
                return 0 if imgui.is_key_released(imgui.Key.enter) else None

            buttons = {
                ifa6.ICON_FA_CHECK+" Create plane": (lambda: callbacks.make_plane(self.study_config, new_plane_type, new_plane_name), lambda: not _valid_plane_name() or new_plane_type is None),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add plane", _add_plane_popup, buttons = buttons, outside=False))

    def _episode_setup_pane_drawer(self):
        if not self.study_config.planes:
            imgui.align_text_to_frame_padding()
            imgui.text_colored(colors.error,'*At minimum one plane should be defined.')
            imgui.same_line()
            tab_lbl = 'Plane editor'
            if imgui.button('Edit planes'):
                if not any((w.label==tab_lbl for w in hello_imgui.get_runner_params().docking_params.dockable_windows)):
                    new_win = self._make_main_space_window(tab_lbl, self._plane_editor_pane_drawer, can_be_closed=True)
                    if not self._window_list:
                        self._window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
                    self._window_list.append(new_win)
                    self._to_dock.append(tab_lbl)
                self._to_focus = tab_lbl
            imgui.same_line()
            imgui.text_colored(colors.error,'to set this up.')
        if any((x in self._problems_cache for x in ['episodes_to_code', 'planes_per_episode'])):
            imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved')

        # episodes to be coded
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), ['episodes_to_code', 'planes_per_episode'], config.study_parameter_types, {}, self._possible_value_getters, None, self._problems_cache, config.study_parameter_doc)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", gt_gui.msg_box.MsgBox.error)
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()
                self._update_shown_actions_for_config()

    def _individual_marker_setup_pane_drawer(self):
        table_is_started = imgui.begin_table(f"##markers_def_list", 4)
        if not table_is_started:
            return
        imgui.table_setup_column("Marker ID", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Size", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("ArUco dictionary", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Marker border bits", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        changed = False
        for m in self.study_config.individual_markers:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.selectable(str(m.id), False)
            if imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip|imgui.HoveredFlags_.delay_normal):
                imgui.begin_tooltip()
                key = m.id,m.aruco_dict,m.marker_border_bits
                sz = int(200*hello_imgui.dpi_window_size_factor())
                if key not in self._marker_preview_cache:
                    self._marker_preview_cache[key] = utils.get_aruco_marker_image(sz, *key)
                self._marker_preview_cache[key].render(width=sz, height=sz)
                imgui.end_tooltip()
            imgui.table_next_column()
            imgui.set_next_item_width(imgui.calc_text_size('xxxxx.xxxxxx').x+2*imgui.get_style().frame_padding.x)
            new_val = settings_editor.draw_value(f'size_{m.id}', m.size, marker.marker_parameter_types['size'], False, marker.marker_defaults.get('size',None), None, False, {}, False)[0]
            if (this_changed:=m.size!=new_val):
                m.size = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'aruco_dict_{m.id}', m.aruco_dict, marker.marker_parameter_types['aruco_dict'], False, marker.marker_defaults.get('aruco_dict',None), None, False, {}, False)[0]
            if (this_changed:=m.aruco_dict!=new_val):
                m.aruco_dict = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'marker_border_bits_{m.id}', m.marker_border_bits, marker.marker_parameter_types['marker_border_bits'], False, marker.marker_defaults.get('marker_border_bits',None), None, False, {}, False)[0]
            if (this_changed:=m.marker_border_bits!=new_val):
                m.marker_border_bits = new_val
                changed |= this_changed
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete marker##{m.id}'):
                callbacks.delete_individual_marker(self.study_config, m)
        if changed:
            self.study_config.store_as_json()
            self._update_shown_actions_for_config()
        imgui.end_table()
        if imgui.button('+ new individual marker'):
            new_mark_id = -1
            new_mark_size = -1.
            def _valid_mark_id():
                nonlocal new_mark_id
                return new_mark_id>=0 and not any((m.id==new_mark_id for m in self.study_config.individual_markers))
            def _add_rec_popup():
                nonlocal new_mark_id
                nonlocal new_mark_size
                imgui.dummy((30*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_mark_info",2):
                    imgui.table_setup_column("##new_mark_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_mark_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_mark_id()
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Marker ID")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_mark_id = imgui.input_int("##new_mark_id",new_mark_id)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = new_mark_size<=0.
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Marker size")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    _,new_mark_size = imgui.input_float("##new_mark_size",new_mark_size)
                    imgui.end_table()
                return 0 if imgui.is_key_released(imgui.Key.enter) else None

            buttons = {
                ifa6.ICON_FA_CHECK+" Create marker": (lambda: callbacks.make_individual_marker(self.study_config, new_mark_id, new_mark_size), lambda: not _valid_mark_id() or new_mark_size<=0.),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add marker", _add_rec_popup, buttons = buttons, outside=False))

    def _get_pending_running_job_list(self) -> dict[utils.JobInfo, int]:
        active_jobs: dict[utils.JobInfo, int] = {}
        for job_id in self.job_scheduler.jobs:
            if self.job_scheduler.jobs[job_id].get_state() in [process.State.Pending, process.State.Running]:
                active_jobs[self.job_scheduler.jobs[job_id].user_data] = job_id
        return active_jobs

    def _session_action_status(self, item: session.Session, action: process.Action):
        if not item.has_all_recordings():
            imgui.text_colored(colors.error, '-')
        else:
            if process.is_session_level_action(action):
                session_lister.draw_process_state(item.state[action])
            else:
                cfg = self.session_config_overrides[item.name].apply(self.study_config, strict_check=False) if item.name in self.session_config_overrides else self.study_config
                states = {r:item.recordings[r].state[action] for r in item.recordings if process.is_action_possible_for_recording(r, item.definition.get_recording_def(r).type, action, cfg)}
                not_completed = [r for r in states if states[r]!=process.State.Completed]
                if any(st:=[s for r in states if (s:=states[r]) in [process.State.Pending, process.State.Running]]):
                    # progress marker
                    session_lister.draw_process_state(process.State.Running if process.State.Running in st else process.State.Pending, have_hover_popup=False)
                else:
                    n_rec = len(states)
                    clr = colors.error if not_completed else colors.ok
                    imgui.text_colored(clr, f'{n_rec-len(not_completed)}/{n_rec}')
                if not_completed:
                    rec_strs = [f'{r} ({states[r].displayable_name})' for r in not_completed]
                    glassesTools.gui.utils.draw_hover_text('not completed for recordings:\n'+'\n'.join(rec_strs),'')
        if imgui.begin_popup_context_item(f"##{item.name}_{action}_context"):
            self._session_context_menu(item.name)
            imgui.end_popup()

    def _session_context_menu(self, session_name: str) -> bool:
        # ignore input session name, get selected sessions
        sess = [self.sessions[s] for s in self.sessions if self._selected_sessions.get(s,False)]
        actions: dict[str, dict[process.Action, bool|list[str]]] = {}
        actions_running: dict[str, dict[process.Action, bool|list[str]]] = {}
        for s in sess:
            cfg = self.session_config_overrides[s.name].apply(self.study_config, strict_check=False) if s.name in self.session_config_overrides else self.study_config
            actions[s.name] = process.get_possible_actions(s.state, {r:s.recordings[r].state for r in s.recordings}, {a for a in process.Action if a!=process.Action.IMPORT}, cfg)
            actions[s.name], actions_running[s.name] = self._filter_session_context_menu_actions(s.name, None, actions[s.name])
        # draw actions that can be run
        all_actions = {a for s in actions for a in actions[s]}
        all_actions |= {a for r in actions_running for a in actions_running[r]}
        all_actions = [a for a in process.Action if a in all_actions]   # ensure order
        for a in all_actions:
            running = [r for r in actions_running if a in actions_running[r]]
            if running:
                if process.is_session_level_action(a):
                    running = [(s,None) for s in actions_running if a in actions_running[s]]
                    hover_text = f'Cancel running {a.displayable_name} for session(s):\n- '+'\n- '.join([s for s in actions_running if a in actions_running[s]])
                else:
                    running = [(s,r) for s in actions_running if a in actions_running[s] for r in actions_running[s][a]]
                    hover_text = f'Cancel running {a.displayable_name} for session(s):\n- '+'\n- '.join([f'{s}, recording(s):\n  - '+'\n  - '.join(actions_running[s][a]) for s in actions_running if a in actions_running[s]])
                hover_text = hover_text.replace('running Run','Run')    # deal with task called "Run Validation"
                icon = ifa6.ICON_FA_HAND
            else:
                if process.is_session_level_action(a):
                    to_run = [(s,None) for s in actions if a in actions[s] and actions[s][a]]
                    hover_text = f'Run {a.displayable_name} for session(s):\n- '+'\n- '.join([s for s in actions if a in actions[s] and actions[s][a]])
                    status = max([self.sessions[s].state[a] for s in actions])
                else:
                    to_run = [(s,r) for s in actions if a in actions[s] for r in actions[s][a]]
                    hover_text = f'Run {a.displayable_name} for session(s):\n- '+'\n- '.join([f'{s}, recording(s):\n  - '+'\n  - '.join(actions[s][a]) for s in actions if a in actions[s] and actions[s][a]])
                    status = max([self.sessions[s].recordings[r].state[a] for s in actions if a in actions[s] for r in actions[s][a]])
                hover_text = hover_text.replace('Run Run','Run')    # deal with task called "Run Validation"
                if a.has_options:
                    hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                icon = ifa6.ICON_FA_PLAY if status<process.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
            if imgui.selectable(icon+f" {a.displayable_name}", False)[0]:
                if running:
                    for s,r in running:
                        if r is None:
                            job_id = actions_running[s][a]
                        else:
                            job_id = actions_running[s][a][r]
                        self.job_scheduler.cancel_job(job_id)
                else:
                    if a==process.Action.EXPORT_TRIALS:
                        # for export, need to select destination folder
                        # and what to export
                        gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='export', sessions=[s for s,_ in to_run]))
                    else:
                        for s,r in to_run:
                            if a.has_options and imgui.get_io().key_shift:
                                callbacks.show_action_options(self, s, r, a)
                            else:
                                self.launch_task(s, r, a)
            gt_gui.utils.draw_hover_text(hover_text, '')
        # draw working folder interactions
        changed = False
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open session folder", False)[0]:
            for s in sess:
                callbacks.open_folder(s.working_directory)
        if imgui.selectable(ifa6.ICON_FA_TRASH_CAN + " Delete session", False)[0]:
            for s in sess:
                callbacks.remove_folder(s.working_directory)
            changed = True
        return changed
    def _recording_context_menu(self, session_name: str, rec_name: str) -> bool:
        # ignore input recording name, get selected sessions
        sess = self.sessions[session_name]
        recs = [r for r in sess.recordings if self._selected_recordings[session_name].get(r,False)]
        actions: dict[str, dict[process.Action, bool|list[str]]] = {}
        actions_running: dict[str, dict[process.Action, bool|list[str]]] = {}
        for r in recs:
            cfg = self.session_config_overrides[session_name].apply(self.study_config, strict_check=False) if session_name in self.session_config_overrides else self.study_config
            actions[r] = process.get_possible_actions(sess.state, {r:sess.recordings[r].state}, {a for a in process.Action if a!=process.Action.IMPORT and not process.is_session_level_action(a)}, cfg)
            actions[r], actions_running[r] = self._filter_session_context_menu_actions(sess.name, r, actions[r])
        # draw actions that can be run
        all_actions = {a for r in actions for a in actions[r]}
        all_actions |= {a for r in actions_running for a in actions_running[r]}
        all_actions = [a for a in process.Action if a in all_actions]   # ensure order
        for a in all_actions:
            to_run = [r for r in actions if a in actions[r]]
            running= [r for r in actions_running if a in actions_running[r]]
            if running:
                hover_text = f'Cancel running {a.displayable_name} for recordings:\n- '+'\n- '.join(running)
                hover_text = hover_text.replace('running Run','Run')    # deal with task called "Run Validation"
                icon = ifa6.ICON_FA_HAND
            else:
                hover_text = f'Run {a.displayable_name} for recordings:\n- '+'\n- '.join(to_run)
                hover_text = hover_text.replace('Run Run','Run')        # deal with task called "Run Validation"
                status = max([sess.recordings[r].state[a] for r in to_run])
                if a.has_options:
                    hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                icon = ifa6.ICON_FA_PLAY if status<process.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
            if imgui.selectable(icon+f" {a.displayable_name}##{session_name}", False)[0]:
                if running:
                    for r in running:
                        self.job_scheduler.cancel_job(actions_running[r][a])
                else:
                    for r in to_run:
                        if a.has_options and imgui.get_io().key_shift:
                            callbacks.show_action_options(self, session_name, r, a)
                        else:
                            self.launch_task(session_name, r, a)
            gt_gui.utils.draw_hover_text(hover_text, '')
        # draw source/working folder interactions
        source_directories = [sd for r in recs if (sd:=sess.recordings[r].info.source_directory).is_dir()]
        if source_directories and imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open source folder", False)[0]:
            for s in source_directories:
                callbacks.open_folder(s)
        if len(recs)==1 and imgui.begin_menu('Camera calibration'):
            working_directory = sess.recordings[recs[0]].info.working_directory
            has_cam_cal_file = (working_directory/gt_naming.scene_camera_calibration_fname).is_file()
            if not has_cam_cal_file:
                imgui.text_colored(colors.error,'No camera calibration!')
            else:
                if imgui.selectable(ifa6.ICON_FA_TRASH_CAN+' Delete calibration file', False)[0]:
                    callbacks.delete_cam_cal(working_directory)
            if imgui.selectable(ifa6.ICON_FA_DOWNLOAD+' Set calibration XML', False)[0]:
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='set_cam_cal', working_directory=working_directory))
            imgui.end_menu()
        changed = False
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open working folder", False)[0]:
            for r in recs:
                callbacks.open_folder(sess.recordings[r].info.working_directory)
        if imgui.selectable(ifa6.ICON_FA_TRASH_CAN + " Delete recording", False)[0]:
            for r in recs:
                callbacks.remove_folder(sess.recordings[r].info.working_directory)
            changed = True
        return changed
    def _filter_session_context_menu_actions(self, session_name: str, rec_name: str|None, actions: dict[process.Action,bool|list[str]]) -> tuple[dict[process.Action,bool|list[str]], dict[process.Action,int|dict[str,int]]]:
        # filter out running and pending tasks
        if not actions:
            return {}, {}

        # first get list of scheduled actions for this session/recordings
        active_jobs = self._get_pending_running_job_list()
        actions_running : dict[process.Action,int|dict[str,int]] = {}
        for a in process.Action:
            if process.is_session_level_action(a):
                if (j:=utils.JobInfo(a, session_name)) in active_jobs:
                    actions_running[a]  = active_jobs[j]
            else:
                if rec_name:
                    if (j:=utils.JobInfo(a, session_name, rec_name)) in active_jobs:
                        actions_running[a] = active_jobs[j]
                else:
                    # check each recording
                    recs = {r.name:active_jobs[j] for r in self.study_config.session_def.recordings if (j:=utils.JobInfo(a, session_name, r.name)) in active_jobs}
                    if recs:
                        actions_running[a] = recs

        # filter out running actions from possible actions
        actions_possible: dict[process.Action,bool|list[str]] = {}
        for a in actions:
            if process.is_session_level_action(a):
                if a not in actions_running:
                    actions_possible[a] = actions[a]
            else:
                if rec_name:
                    if a not in actions_running:
                        actions_possible[a] = actions[a]
                else:
                    # check each recording
                    if a not in actions_running:
                        actions_possible[a] = actions[a]
                    else:
                        recs = [r for r in actions[a] if r not in actions_running[a]]
                        if recs:
                            actions_possible[a] = recs
        return actions_possible, actions_running

    def _open_session_detail(self, sess: session.Session):
        win_name = f'{sess.name}##session_view'
        if win := hello_imgui.get_runner_params().docking_params.dockable_window_of_name(win_name):
            win.focus_window_at_next_frame = True
        else:
            window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
            window_list.append(
                self._make_main_space_window(win_name, lambda: self._session_detail_GUI(sess), can_be_closed=True)
            )
            self._window_list = window_list
            self._to_dock = [win_name]
            self._to_focus= win_name
            self._selected_recordings[sess.name] = {k:False for k in sess.recordings}
            self._recording_listers[sess.name] = gt_gui.recording_table.RecordingTable(sess.recordings, self._sessions_lock, self._selected_recordings[sess.name], None, lambda r: r.info, item_context_callback=lambda rec_name: self._recording_context_menu(sess.name, rec_name))
            self._recording_listers[sess.name].dont_show_empty = True
            self.session_config_overrides[sess.name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)
            self.recording_config_overrides[sess.name] = {}
            for r in sess.recordings:
                self.recording_config_overrides[sess.name][r] = config.load_or_create_override(config.OverrideLevel.Recording, sess.recordings[r].info.working_directory, sess.recordings[r].definition.type)
            self._recording_lister_set_actions_to_show(self._recording_listers[sess.name], sess.name)

    def _session_detail_GUI(self, sess: session.Session):
        missing_recs = sess.missing_recordings()
        if missing_recs:
            imgui.text_colored(colors.error,'*The following recordings are missing for this session:\n'+'\n'.join(missing_recs))
        show_import_et = any((sess.definition.get_recording_def(r).type==session.RecordingType.Eye_Tracker for r in missing_recs))
        show_import_cam = any((sess.definition.get_recording_def(r).type==session.RecordingType.Camera for r in missing_recs))
        if imgui.button(ifa6.ICON_FA_FOLDER_OPEN + " Open working folder"):
            callbacks.open_folder(sess.working_directory)
        if show_import_et:
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import eye tracker recordings'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_et_recordings', sessions=[sess.name]))
        if show_import_cam:
            if show_import_et:
                imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import camera recordings'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_cam_recordings', sessions=[sess.name]))
        session_level_actions = [a for a in self._session_actions if process.is_session_level_action(a)]
        effective_config = self.session_config_overrides[sess.name].apply(self.study_config, strict_check=False)
        possible_actions = process.get_possible_actions(sess.state, {r:sess.recordings[r].state for r in sess.recordings}, set(session_level_actions), effective_config)
        menu_actions,menu_actions_running = self._filter_session_context_menu_actions(sess.name, None, possible_actions)
        if session_level_actions and imgui.begin_table(f'##{sess.name}_session_level', 2, imgui.TableFlags_.sizing_fixed_fit):
            for a in session_level_actions:
                imgui.table_next_column()
                session_lister.draw_process_state(sess.state[a])
                imgui.table_next_column()
                imgui.selectable(a.displayable_name, False, imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap)
                if (a in menu_actions or a in menu_actions_running) and imgui.begin_popup_context_item(f"##{sess.name}_{a}_context"):
                    if a in menu_actions_running:
                        hover_text = f'Cancel running {a.displayable_name} for session: {sess.name}'
                        icon = ifa6.ICON_FA_HAND
                    else:
                        hover_text = f'Run {a.displayable_name} for session: {sess.name}'
                        if a.has_options:
                            hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                        status = self.sessions[sess.name].state[a]
                        icon = ifa6.ICON_FA_PLAY if status<process.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
                    if imgui.selectable(icon+f" {a.displayable_name}##{sess.name}", False)[0]:
                        if a in menu_actions_running:
                            self.job_scheduler.cancel_job(menu_actions_running[a])
                        else:
                            if a==process.Action.EXPORT_TRIALS:
                                # for export, need to select destination folder
                                # and what to export
                                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='export', sessions=[sess.name]))
                            else:
                                if a.has_options and imgui.get_io().key_shift:
                                    callbacks.show_action_options(self, sess.name, None, a)
                                else:
                                    self.launch_task(sess.name, None, a)
                    gt_gui.utils.draw_hover_text(hover_text, '')
                    imgui.end_popup()
            imgui.end_table()
        self._recording_listers[sess.name].draw(limit_outer_size=True)
        sess_changed = False
        if imgui.tree_node_ex('Setting overrides for this session',imgui.TreeNodeFlags_.framed):
            fields = config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Session)[0]
            sess_changed, new_config = settings_editor.draw(effective_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, self.study_config, effective_config.field_problems(), config.study_parameter_doc)
            if sess_changed:
                try:
                    new_config.check_valid(strict_check=False)
                    self.session_config_overrides[sess.name] = config.StudyOverride.from_study_diff(new_config, self.study_config, config.OverrideLevel.Session)
                except Exception as e:
                    # do not persist invalid config, inform user of problem
                    gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for session {sess.name}:\n{e}", gt_gui.msg_box.MsgBox.error)
                else:
                    # persist changed config
                    self.session_config_overrides[sess.name].store_as_json(sess.working_directory)
            imgui.tree_pop()
        if len(self.study_config.session_def.recordings)>1:
            # if more than one recording per session, show recording-level settings overrides
            # don't show if only one recording per session, would be redundant
            for r in sess.recordings:
                if imgui.tree_node_ex(f'Setting overrides for {r} recording',imgui.TreeNodeFlags_.framed):
                    fields = config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Recording, sess.recordings[r].definition.type)[0]
                    effective_config_for_session = self.session_config_overrides[sess.name].apply(self.study_config, strict_check=False)
                    effective_config = self.recording_config_overrides[sess.name][r].apply(effective_config_for_session, strict_check=False)
                    changed, new_config = settings_editor.draw(effective_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, effective_config_for_session, effective_config.field_problems(), config.study_parameter_doc)
                    if changed or sess_changed: # NB: also need to update file when parent has changed
                        try:
                            new_config.check_valid(strict_check=False)
                            self.recording_config_overrides[sess.name][r] = config.StudyOverride.from_study_diff(new_config, effective_config_for_session, config.OverrideLevel.Recording, sess.recordings[r].definition.type)
                        except Exception as e:
                            # do not persist invalid config, inform user of problem
                            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for recording {r} in session {sess.name}:\n{e}", gt_gui.msg_box.MsgBox.error)
                        else:
                            # persist changed config
                            self.recording_config_overrides[sess.name][r].store_as_json(sess.recordings[r].info.working_directory)
                    imgui.tree_pop()

    def _about_popup_drawer(self):
        def popup_content():
            _60 = 60*hello_imgui.dpi_window_size_factor()
            _200 = 200*hello_imgui.dpi_window_size_factor()
            width = 530*hello_imgui.dpi_window_size_factor()
            imgui.begin_group()
            imgui.dummy((_60, _200))
            imgui.same_line()
            imgui.dummy((_200, _200))
            imgui.same_line()
            imgui.begin_group()
            imgui.push_text_wrap_pos(width - imgui.get_style().frame_padding.x)
            imgui.push_font(self._big_font)
            imgui.text("gazeMapper")
            imgui.pop_font()
            imgui.text(f"Version {version.__version__}")
            imgui.text("Made by Diederick C. Niehorster")
            imgui.text("")
            imgui_md.render(f"[glassesTools {glassesTools.version.__version__}](https://github.com/dcnieho/glassesTools)")
            imgui_md.render(f"[glassesValidator {glassesValidator.version.__version__}](https://github.com/dcnieho/glassesValidator)")
            imgui.text(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
            imgui.text(f"OpenGL {'.'.join(str(gl.glGetInteger(num)) for num in (gl.GL_MAJOR_VERSION, gl.GL_MINOR_VERSION))}, PyOpenGL {OpenGL.__version__}")
            imgui.text(f"GLFW {'.'.join(str(num) for num in glfw.get_version())}, pyGLFW {glfw.__version__}")
            imgui_md.render(f"ImGui {imgui.get_version()}, [imgui_bundle {imgui_bundle.__version__}](https://github.com/pthom/imgui_bundle)")
            match gt_platform.os:
                case gt_platform.Os.Linux:
                    imgui.text(f"{platform.system()} {platform.release()}")
                case gt_platform.Os.Windows:
                    rel = 11 if sys.getwindowsversion().build>22000 else platform.release()
                    imgui.text(f"{platform.system()} {rel} {platform.win32_edition()} ({platform.version()})")
                case gt_platform.Os.MacOS:
                    imgui.text(f"{platform.system()} {platform.release()}")
            imgui.pop_text_wrap_pos()
            imgui.end_group()
            imgui.same_line()
            imgui.dummy((width-imgui.get_cursor_pos_x(), _200))
            imgui.end_group()
            imgui.spacing()
            btn_tot_width = (width - 2*imgui.get_style().item_spacing.x)
            if imgui.button("PyPI", size=(btn_tot_width/6, 0)):
                webbrowser.open("https://pypi.org/project/gazeMapper/")
            imgui.same_line()
            imgui.begin_disabled()
            if imgui.button("Paper", size=(btn_tot_width/6, 0)):
                webbrowser.open("https://doi.org/10.3758/xxx")
            imgui.end_disabled()
            imgui.same_line()
            if imgui.button("GitHub repo", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://github.com/dcnieho/gazeMapper")
            imgui.same_line()
            if imgui.button("Researcher homepage", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://scholar.google.se/citations?user=uRUYoVgAAAAJ&hl=en")

            imgui.spacing()
            imgui.spacing()
            imgui.push_text_wrap_pos(width - imgui.get_style().frame_padding.x)
            imgui.text("This software is licensed under the MIT license and is provided to you for free. Furthermore, due to "
                       "its license, it is also free as in freedom: you are free to use, study, modify and share this software "
                       "in whatever way you wish as long as you keep the same license.")
            imgui.spacing()
            imgui.spacing()
            imgui.text("If you find bugs or have some feedback, please do let me know on GitHub (using issues or pull requests).")
            imgui.spacing()
            imgui.spacing()
            imgui.dummy((0, 10*hello_imgui.dpi_window_size_factor()))
            imgui.push_font(self._big_font)
            size = imgui.calc_text_size("Reference")
            imgui.set_cursor_pos_x((width - size.x + imgui.get_style().scrollbar_size) / 2)
            imgui.text("Reference")
            imgui.pop_font()
            imgui.spacing()
            reference         = r"Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. & Hooge, I.T.C. (submitted). gazeMapper: A tool for automated world-based analysis of gaze data from one or multiple wearable eye trackers. Manuscript submitted for publication, 2024"
            reference_bibtex  = r"""@article{niehorster2025gazeMapper,
    Author = {Niehorster, Diederick C. and
              Hessels, Roy S. and
              Nystr{\"o}m, Marcus and
              Benjamins, Jeroen S. and
              Hooge, Ignace T. C.},
    Journal = {},
    Number = {},
    Title = {{gazeMapper}: A tool for automated world-based analysis of gaze data from one or multiple wearable eye trackers},
    Year = {},
    note = {Manuscript submitted for publication, 2024}
}
"""
            imgui.text(reference)
            if imgui.begin_popup_context_item(f"##reference_context_gazeMapper"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            gt_gui.utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")
            imgui.spacing()
            imgui.spacing()
            imgui.spacing()
            imgui_md.render(f"This tool makes use of [glassesValidator](https://github.com/dcnieho/glassesValidator) ([paper](https://doi.org/10.3758/s13428-023-02105-5)), please also reference:")
            imgui.spacing()
            reference         = r"Niehorster, D.C., Hessels, R.S., Benjamins, J.S., Nyström, M. & Hooge, I.T.C. (2023). GlassesValidator: Data quality tool for eye tracking glasses. Behavior Research Methods. doi: 10.3758/s13428-023-02105-5"
            reference_bibtex  = r"""@article{niehorster2023glassesValidator,
    Author = {Niehorster, Diederick C. and
              Hessels, Roy S. and
              Benjamins, Jeroen S. and
              Nystr{\"o}m, Marcus and
              Hooge, Ignace T. C.},
    Journal = {Behavior Research Methods},
    Number = {},
    Title = {{GlassesValidator}: A data quality tool for eye tracking glasses},
    Year = {2023},
    doi = {10.3758/s13428-023-02105-5}
}
"""
            imgui.text(reference)
            if imgui.begin_popup_context_item(f"##reference_context_glassesValidator"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            gt_gui.utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")

            imgui.pop_text_wrap_pos()
        return gt_gui.utils.popup("About gazeMapper", popup_content, closable=True, outside=True)