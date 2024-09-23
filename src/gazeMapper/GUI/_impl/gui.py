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
import pathvalidate

import imgui_bundle
from imgui_bundle import imgui, immapp, imgui_md, hello_imgui, glfw_utils, icons_fontawesome_6 as ifa6
import glfw
import OpenGL
import OpenGL.GL as gl

import glassesTools
import glassesTools.gui
import glassesValidator

from ... import config, config_watcher, marker, plane, process, session, type_utils, version
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
        self._session_lister = session_lister.List(self.sessions, self._sessions_lock, self._selected_sessions, info_callback=self._open_session_detail, item_context_callback=self._session_context_menu)

        self.recording_config_overrides: dict[str, dict[str, config.StudyOverride]]             = {}
        self._recording_listers  : dict[str, glassesTools.gui.recording_table.RecordingTable]   = {}
        self._selected_recordings: dict[str, dict[str, bool]]                                   = {}

        self._possible_value_getters: dict[str] = {}

        self.need_setup_recordings  = True
        self.need_setup_plane       = True
        self.need_setup_episode     = True
        self.can_accept_sessions    = False

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

        # Show errors in threads
        def asyncexcepthook(future: asyncio.Future):
            try:
                exc = future.exception()
            except concurrent.futures.CancelledError:
                return
            if not exc:
                return
            tb = glassesTools.gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
            if isinstance(exc, asyncio.TimeoutError):
                glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", glassesTools.gui.msg_box.MsgBox.warn, more=tb)
                return
            glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", glassesTools.gui.msg_box.MsgBox.error)
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
        self._icon_font = glassesTools.gui.msg_box.icon_font = \
            hello_imgui.load_font("fonts/Font_Awesome_6_Free-Solid-900.otf", msg_box_size, large_icons_params)

    def _setup_glfw(self):
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_drop_callback(win, self._drop_callback)

    def _drop_callback(self, _: glfw._GLFWwindow, items: list[str]):
        paths = [pathlib.Path(item) for item in items]
        if self.popup_stack and isinstance(picker := self.popup_stack[-1], glassesTools.gui.file_picker.FilePicker):
            picker.set_dir(paths)
        else:
            if self.project_dir is not None:
                # import recordings
                callbacks.add_eyetracking_recordings(self, paths, [s for s in self._selected_sessions if self._selected_sessions[s] and not self.sessions[s].has_all_recordings()])
            else:
                # load project
                if len(paths)!=1 or not (path := paths[0]).is_dir():
                    glassesTools.gui.utils.push_popup(glassesTools.gui.msg_box.msgbox, "Project opening error", "Only a single project directory should be drag-dropped on the glassesValidator GUI.", glassesTools.gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in paths])))
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
        self._action_list_pane = self._make_main_space_window("Processing Queue", self._action_list_pane_drawer, is_visible=False)
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
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.is_visible or w.label in ['Project settings', 'Processing Queue']]
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

    def _show_menu_gui(self):
        # this is always called, so we handle popups and other state here
        self._check_project_setup_state()
        glassesTools.gui.utils.handle_popup_stack(self.popup_stack)
        self._update_jobs_and_process_pool()
        # also handle showing of debug windows
        if self._show_demo_window:
            self._show_demo_window = imgui.show_demo_window(self._show_demo_window)

        # now actual menu
        if imgui.begin_menu("Help"):
            if imgui.menu_item("About", "", False)[0]:
                glassesTools.gui.utils.push_popup(self, self._about_popup_drawer)
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
                            self.sessions[sess].recordings.pop(rec)
                            if sess in self._selected_recordings:
                                self._selected_recordings[sess].pop(rec, None)
                        elif rec not in self.sessions[sess].recordings: # don't replace if already present
                            self.sessions[sess].add_existing_recording(rec)
                            if sess in self._selected_recordings:
                                self._selected_recordings[sess] |= {rec: False}
                case _:
                    pass    # ignore, not of interest

    def launch_task(self, sess: str, recording: str|None, action: process.Action):
        # NB: this is run under lock, so sess and recording are valid
        job = utils.JobInfo(action, sess, recording)
        if job in self._get_pending_running_job_list():
            # already scheduled, nothing to do
            return
        if action==process.Action.IMPORT:
            # NB: when adding recording, immediately do
            # rec_info = glassesTools.importing.get_recording_info(bla, bla)
            # self.sessions[sess].add_recording_from_info()
            # that creates it only in memory. Then immediate call launch_task for import
            # if import fails, remove directory, which removes recording (automatically thanks to watcher)
            func = self.sessions[sess].import_recording
            args = (recording,)
        else:
            func = process.action_to_func(action)
            if recording:
                working_dir = self.sessions[sess].recordings[recording].info.working_directory
            else:
                working_dir = self.sessions[sess].working_directory
            args = (working_dir,)
        exclusive_id = 1 if action.needs_GUI else None

        # add to scheduler
        payload = process_pool.JobPayload(func, args, {})
        self.job_scheduler.add_job(job, payload, self._action_done_callback, exclusive_id=exclusive_id)

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
        # NB: triggered for each job by _update_jobs_and_process_pool() above
        # _update_jobs_and_process_pool() already holds the lock, so not needed here
        if job.session not in self.sessions:
            return False
        if job.recording and job.recording not in self.sessions[job.session].recordings:
            return False
        return True

    def _update_job_states_impl(self, job: utils.JobInfo, job_state: process.State):
        # NB: self._sessions_lock should be acquired, and check should have been
        # performed that job.session and job.recording exist
        if job.recording:
            self.sessions[job.session].recordings[job.recording].state[job.action] = job_state
        else:
            self.sessions[job.session].state[job.action] = job_state

    def _action_done_callback(self, future: process_pool.ProcessFuture, job_id: int, job: utils.JobInfo, state: process.State):
        # if process failed, notify error
        session_level = job.recording is None
        if state==process.State.Failed:
            exc = future.exception()    # should not throw exception since CancelledError is already encoded in state and future is done
            tb = glassesTools.gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
            lbl = f'session "{job.session}"'
            if not session_level:
                lbl += f', recording "{job.recording}"'
            lbl += f' (work item {job_id}, action {job.action.displayable_name})'
            if isinstance(exc, concurrent.futures.TimeoutError):
                glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Processing error", f"A worker process has failed for {lbl}:\n{type(exc).__name__}: {str(exc) or 'No further details'}\n\nPossible causes include:\n - You are running with too many workers, try lowering them in settings", glassesTools.gui.msg_box.MsgBox.warn, more=tb)
                return
            glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Processing error", f"Something went wrong in a worker process for {lbl}:\n\n{tb}", glassesTools.gui.msg_box.MsgBox.error)

        # clean up, if needed, when a task failed or was canceled
        if job.action==process.Action.IMPORT and state in [process.State.Canceled, process.State.Failed]:
            # remove working directory if this was an import task
            async_thread.run(callbacks.remove_recording_working_dir(self.project_dir, job.session, job.recording))
            return
        # get final task state when completed, load from file. Need to do this because change listener may fire before task
        # completes, and its output is then overwritten in _update_jobs_and_process_pool()
        with self._sessions_lock:
            if job.session not in self.sessions:
                return
            if job.recording and job.recording not in self.sessions[job.session].recordings:
                return
            if job.recording:
                if job.action==process.Action.IMPORT:
                    # the import call has created a working directory for the recording, and may have updated the info in other
                    # ways (e.g. filled in recording length that wasn't known from metadata). Read from file and update what we
                    # hold in memory. NB: mustr be loaded from file as recording update is run in a different process
                    rec_info = self.sessions[job.session].load_recording_info(job.recording)
                    self.sessions[job.session].update_recording_info(job.recording, rec_info)
                self.sessions[job.session].recordings[job.recording].load_action_states(False)
            else:
                self.sessions[job.session].load_action_states(False)

    def load_project(self, path: pathlib.Path):
        self.project_dir = path
        try:
            config_dir = config.guess_config_dir(self.project_dir)
            self.study_config = config.Study.load_from_json(config_dir, strict_check=False)
            self._reload_sessions()
        except Exception as e:
            glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Project loading error", f"Failed to load the project at {self.project_dir}:\n{e}\n\n{utils.glassesTools.gui.get_traceback(e)}", glassesTools.gui.msg_box.MsgBox.error)
            self.close_project()
            return

        self.config_watcher_stop_event = asyncio.Event()
        self.config_watcher = async_thread.run(config_watcher.watch_and_report_changes(self.project_dir, self._config_change_callback, self.config_watcher_stop_event, watch_filter=config_watcher.ConfigFilter(('.gazeMapper',), True, {config_dir}, True, True)))

        def _get_known_recordings() -> set[str]:
            return {r.name for r in self.study_config.session_def.recordings}
        def _get_known_individual_markers() -> set[str]:
            return {m.id for m in self.study_config.individual_markers}
        def _get_known_planes() -> set[str]:
            return {p.name for p in self.study_config.planes}
        def _get_episodes_to_code_for_planes() -> set[glassesTools.annotation.Event]:
            return {e for e in self.study_config.episodes_to_code if e!=glassesTools.annotation.Event.Sync_Camera}
        self._possible_value_getters = {
            'video_make_which': _get_known_recordings,
            'video_recording_colors': _get_known_recordings,
            'sync_ref_recording': _get_known_recordings,
            'sync_ref_average_recordings': _get_known_recordings,
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
        self._session_lister_set_actions_to_show(self._session_lister)

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

        actions = process.get_actions_for_config(self.study_config, exclude_session_level=False)
        lister.set_actions_to_show(actions)

    def _recording_lister_set_actions_to_show(self, lister: glassesTools.gui.recording_table.RecordingTable, sess: str):
        config = self.session_config_overrides[sess].apply(self.study_config, strict_check=False)
        actions = process.get_actions_for_config(config, exclude_session_level=True)
        def _draw_status(action: process.Action, item: session.Recording):
            if process.is_action_possible_for_recording_type(action, item.definition.type):
                session_lister.draw_process_state(item.state[action])
            else:
                imgui.text('-')
                glassesTools.gui.utils.draw_hover_text(f'Not applicable to a {item.definition.type.value} recording','')
        def _get_sort_value(action: process.Action, iid: int):
            item = self.sessions[sess].recordings[iid]
            if process.is_action_possible_for_recording_type(action, item.definition.type):
                return item.state[action]
            else:
                return 999

        # build set of column, trigger column rebuild
        columns = [
            glassesTools.gui.recording_table.ColumnSpec(1,ifa6.ICON_FA_SIGNATURE+" Recording name",imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide, lambda rec: imgui.text(rec.definition.name), lambda iid: iid, "Recording name")
        ]+[
            glassesTools.gui.recording_table.ColumnSpec(2+c, a.displayable_name, imgui.TableColumnFlags_.angled_header, lambda rec, a=a: _draw_status(a, rec), lambda iid, a=a: _get_sort_value(a, iid)) for c,a in enumerate(actions)
        ]
        lister.build_columns(columns)

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
        self.project_dir = None
        self._possible_value_getters = {}
        self.study_config = None
        self.sessions.clear()
        self._selected_sessions.clear()
        self._need_set_window_title = True


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
            glassesTools.gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_et_recordings', sessions=[s for s in self._selected_sessions if self._selected_sessions[s] and not self.sessions[s].has_all_recordings()]))
        if any((r.type==session.RecordingType.Camera for r in self.study_config.session_def.recordings)):
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import camera recordings'):
                pass
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
            glassesTools.gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='creating'))
        imgui.same_line(spacing=10*imgui.get_style().item_spacing.x)
        if imgui.button(ifa6.ICON_FA_FOLDER_OPEN+" Open project", size=(but_width, but_height)):
            glassesTools.gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='loading'))

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
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, None, self._problems_cache)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", glassesTools.gui.msg_box.MsgBox.error)
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()
                self._session_lister_set_actions_to_show(self._session_lister)
                for sess in self._recording_listers:
                    self._recording_lister_set_actions_to_show(self._recording_listers[sess], sess)

    def _action_list_pane_drawer(self):
        if not self.job_scheduler.jobs:
            imgui.text('No actions have been enqueued or performed')
            return

        # gather all file actions
        jobs = self.job_scheduler.jobs.copy()
        job_ids = sorted(jobs.keys())
        job_states ={job_id:jobs[job_id].get_state() for job_id in job_ids}
        if any((job_states[i] in [process.State.Pending, process.State.Running] for i in job_states)):
            if imgui.button(ifa6.ICON_FA_HAND+' Cancel all'):
                for job_id in job_ids:
                    if job_states[job_id] in [process.State.Pending, process.State.Running]:
                        self.job_scheduler.cancel_job(job_id)

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
                            if job_state in [process.State.Pending, process.State.Running]:
                                imgui.same_line()
                                if imgui.button(ifa6.ICON_FA_HAND+f' Cancel##{job_id}'):
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
        table_is_started = imgui.begin_table(f"##session_def_list", 2)
        if not table_is_started:
            return
        imgui.table_setup_column("recording", imgui.TableColumnFlags_.width_fixed, init_width_or_weight=settings_editor.get_fields_text_width([r.name for r in self.study_config.session_def.recordings],'recording'))
        imgui.table_setup_column("type", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        for r in self.study_config.session_def.recordings:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.text(r.name)
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.text(r.type.value)
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete recording##{r.name}'):
                callbacks.delete_recording_definition(self.study_config, r)
                self._reload_sessions()
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
            glassesTools.gui.utils.push_popup(self, lambda: glassesTools.gui.utils.popup("Add recording", _add_rec_popup, buttons = buttons, outside=False))

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
                if problem_fields:
                    imgui.pop_style_color()
                changed, _, new_p, _ = settings_editor.draw_dict_editor(copy.deepcopy(p), type(p), 0, list(plane.definition_parameter_types[p.type].keys()), plane.definition_parameter_types[p.type], plane.definition_defaults[p.type], problems=problem_fields, fixed=fixed_fields)
                if changed:
                    # persist changed config
                    plane_dir = config.guess_config_dir(self.study_config.working_directory)/p.name
                    new_p.store_as_json(plane_dir)
                    if new_p.type==plane.Type.GlassesValidator and not new_p.use_default:
                        callbacks.glasses_validator_plane_check_config(self.study_config, new_p)
                    # recreate plane so any settings changes (e.g. applied defaults) are reflected in the gui
                    self.study_config.planes[i] = plane.Definition.load_from_json(plane_dir)
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
            glassesTools.gui.utils.push_popup(self, lambda: glassesTools.gui.utils.popup("Add plane", _add_plane_popup, buttons = buttons, outside=False))

    def _episode_setup_pane_drawer(self):
        if not self.study_config.episodes_to_code:
            imgui.text_colored(colors.error,'*At minimum one episode should be selected to be coded')
        if not self.study_config.planes_per_episode:
            imgui.text_colored(colors.error,'*At minimum one plane should be linked to at minimum one episode')
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
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), ['episodes_to_code', 'planes_per_episode'], config.study_parameter_types, {}, self._possible_value_getters, None, self._problems_cache)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", glassesTools.gui.msg_box.MsgBox.error)
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()

    def _individual_marker_setup_pane_drawer(self):
        table_is_started = imgui.begin_table(f"##markers_def_list", 4)
        if not table_is_started:
            return
        imgui.table_setup_column("marker ID", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("size", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("aruco_dict", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("marker_border_bits", imgui.TableColumnFlags_.width_stretch)
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
            new_val = settings_editor.draw_value(f'size_{m.id}', m.size, marker.marker_parameter_types['size'], False, marker.marker_defaults.get('size',None), None, False, False)[0]
            if (this_changed:=m.size!=new_val):
                m.size = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'aruco_dict_{m.id}', m.aruco_dict, marker.marker_parameter_types['aruco_dict'], False, marker.marker_defaults.get('aruco_dict',None), None, False, False)[0]
            if (this_changed:=m.aruco_dict!=new_val):
                m.aruco_dict = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'marker_border_bits_{m.id}', m.marker_border_bits, marker.marker_parameter_types['marker_border_bits'], False, marker.marker_defaults.get('marker_border_bits',None), None, False, False)[0]
            if (this_changed:=m.marker_border_bits!=new_val):
                m.marker_border_bits = new_val
                changed |= this_changed
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete marker##{m.id}'):
                callbacks.delete_individual_marker(self.study_config, m)
        if changed:
            self.study_config.store_as_json()
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
            glassesTools.gui.utils.push_popup(self, lambda: glassesTools.gui.utils.popup("Add marker", _add_rec_popup, buttons = buttons, outside=False))

    def _get_pending_running_job_list(self) -> set[utils.JobInfo]:
        active_jobs: set[utils.JobInfo] = set()
        for job_id in self.job_scheduler.jobs:
            if self.job_scheduler.jobs[job_id].get_state() in [process.State.Pending, process.State.Running]:
                active_jobs.add(self.job_scheduler.jobs[job_id].user_data)
        return active_jobs

    def _session_context_menu(self, session_name: str) -> bool:
        sess = self.sessions[session_name]
        actions = process.get_possible_actions(sess.state, {r:sess.recordings[r].state for r in sess.recordings}, {a for a in process.Action if a!=process.Action.IMPORT}, self.study_config)
        return self._draw_session_context_menu(session_name, None, actions)
    def _recording_context_menu(self, session_name: str, rec_name: str) -> bool:
        sess = self.sessions[session_name]
        actions = process.get_possible_actions(sess.state, {rec_name:sess.recordings[rec_name].state}, {a for a in process.Action if a!=process.Action.IMPORT and not process.is_session_level_action(a)}, self.study_config)
        return self._draw_session_context_menu(session_name, rec_name, actions)
    def _filter_session_context_menu_actions(self, session_name: str, rec_name: str|None, actions: dict[process.Action,bool|list[str]]) -> dict[process.Action,bool|list[str]]:
        # filter out running and pending tasks
        if not actions:
            return {}

        actions_filt: dict[process.Action,bool|list[str]] = {}
        active_jobs = self._get_pending_running_job_list()
        for a in actions:
            if process.is_session_level_action(a):
                if utils.JobInfo(a, session_name) not in active_jobs:
                    actions_filt[a] = actions[a]
            else:
                if rec_name:
                    if utils.JobInfo(a, session_name, rec_name) not in active_jobs:
                        actions_filt[a] = actions[a]
                else:
                    # check each recording
                    recs = [r for r in actions[a] if utils.JobInfo(a, session_name, r) not in active_jobs]
                    if recs:
                        actions_filt[a] = recs
        return actions_filt
    def _draw_session_context_menu(self, session_name: str, rec_name: str|None, actions: dict[process.Action,bool|list[str]]) -> bool:
        changed = False
        actions = self._filter_session_context_menu_actions(session_name, rec_name, actions)
        # draw menu
        for a in actions:
            if process.is_session_level_action(a):
                hover_text = f'Run {a.displayable_name} for session: {session_name}'
                status = self.sessions[session_name].state[a]
            else:
                hover_text = f'Run {a.displayable_name} for recordings:\n'+'\n'.join(actions[a])
                status = max([self.sessions[session_name].recordings[r].state[a] for r in actions[a]])
            icon = ifa6.ICON_FA_PLAY if status<process.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
            if imgui.selectable(icon+f" {a.displayable_name}##{session_name}", False)[0]:
                if process.is_session_level_action(a):
                    self.launch_task(session_name, None, a)
                else:
                    for r in actions[a]:
                        self.launch_task(session_name, r, a)
            glassesTools.gui.utils.draw_hover_text(hover_text, '')
        lbl = session_name + rec_name if rec_name else ''
        if rec_name:
            working_directory = self.sessions[session_name].recordings[rec_name].info.working_directory
            source_directory = self.sessions[session_name].recordings[rec_name].info.source_directory
            if source_directory.is_dir() and imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + f" Open source folder##{lbl}", False)[0]:
                callbacks.open_folder(source_directory)
            but_lbls = ('working', 'recording')
        else:
            working_directory = self.sessions[session_name].working_directory
            but_lbls = ('session', 'session')
        if working_directory and imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + f" Open {but_lbls[0]} folder##{lbl}", False)[0]:
            callbacks.open_folder(working_directory)
        if working_directory and imgui.selectable(ifa6.ICON_FA_TRASH_CAN + f" Delete {but_lbls[1]}##{lbl}", False)[0]:
            callbacks.remove_folder(working_directory)
            changed = True
        return changed

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
            self._recording_listers[sess.name] = glassesTools.gui.recording_table.RecordingTable(sess.recordings, self._sessions_lock, self._selected_recordings[sess.name], None, lambda r: r.info, item_context_callback=lambda rec_name: self._recording_context_menu(sess.name, rec_name))
            self._recording_listers[sess.name].dont_show_empty = True
            self.session_config_overrides[sess.name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)
            self.recording_config_overrides[sess.name] = {}
            for r in sess.recordings:
                self.recording_config_overrides[sess.name][r] = config.load_or_create_override(config.OverrideLevel.Recording, sess.recordings[r].info.working_directory)
            self._recording_lister_set_actions_to_show(self._recording_listers[sess.name], sess.name)

    def _session_detail_GUI(self, sess: session.Session):
        missing_recs = sess.missing_recordings()
        if missing_recs:
            imgui.text_colored(colors.error,'*The following recordings are missing for this session:\n'+'\n'.join(missing_recs))
        show_import_et = any((sess.definition.get_recording_def(r).type==session.RecordingType.Eye_Tracker for r in missing_recs))
        show_import_cam = any((sess.definition.get_recording_def(r).type==session.RecordingType.Camera for r in missing_recs))
        if show_import_et and imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import eye tracker recordings'):
            glassesTools.gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_et_recordings', sessions=[sess.name]))
        if show_import_cam:
            if show_import_et:
                imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import camera recordings'):
                pass
        self._recording_listers[sess.name].draw(limit_outer_size=True)
        sess_changed = False
        if imgui.tree_node_ex('Setting overrides for this session',imgui.TreeNodeFlags_.framed):
            fields = config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Session)[0]
            effective_config = self.session_config_overrides[sess.name].apply(self.study_config, strict_check=False)
            sess_changed, new_config = settings_editor.draw(effective_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, self.study_config, effective_config.field_problems())
            if sess_changed:
                try:
                    new_config.check_valid(strict_check=False)
                    self.session_config_overrides[sess.name] = config.StudyOverride.from_study_diff(new_config, self.study_config, config.OverrideLevel.Session)
                except Exception as e:
                    # do not persist invalid config, inform user of problem
                    glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for session {sess.name}:\n{e}", glassesTools.gui.msg_box.MsgBox.error)
                else:
                    # persist changed config
                    self.session_config_overrides[sess.name].store_as_json(sess.working_directory)
            imgui.tree_pop()
        for r in sess.recordings:
            if imgui.tree_node_ex(f'Setting overrides for {r} recording',imgui.TreeNodeFlags_.framed):
                fields = config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Recording)[0]
                effective_config_for_session = self.session_config_overrides[sess.name].apply(self.study_config, strict_check=False)
                effective_config = self.recording_config_overrides[sess.name][r].apply(effective_config_for_session, strict_check=False)
                changed, new_config = settings_editor.draw(effective_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, effective_config_for_session, effective_config.field_problems())
                if changed or sess_changed: # NB: also need to update file when parent has changed
                    try:
                        new_config.check_valid(strict_check=False)
                        self.recording_config_overrides[sess.name][r] = config.StudyOverride.from_study_diff(new_config, effective_config_for_session, config.OverrideLevel.Recording)
                    except Exception as e:
                        # do not persist invalid config, inform user of problem
                        glassesTools.gui.utils.push_popup(self, glassesTools.gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for recording {r} in session {sess.name}:\n{e}", glassesTools.gui.msg_box.MsgBox.error)
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
            match glassesTools.platform.os:
                case glassesTools.platform.Os.Linux:
                    imgui.text(f"{platform.system()} {platform.release()}")
                case glassesTools.platform.Os.Windows:
                    rel = 11 if sys.getwindowsversion().build>22000 else platform.release()
                    imgui.text(f"{platform.system()} {rel} {platform.win32_edition()} ({platform.version()})")
                case glassesTools.platform.Os.MacOS:
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
            imgui.spacing()
            reference         = r"Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. & Hooge, I.T.C. (in prep). gazeMapper: A tool for automated world-based analysis of wearable eye tracker data"
            reference_bibtex  = r"""@article{niehorster2025gazeMapper,
    Author = {Niehorster, Diederick C. and Hessels, R. S. and Nystr{\"o}m, Marcus and Benjamins, J. S. and Hooge, I. T. C.},
    Journal = {},
    Number = {},
    Pages = {},
    Title = {gazeMapper: A tool for automated world-based analysis of wearable eye tracker data},
    Year = {in prep},
    doi = {}
}
"""
            imgui.text(reference)
            if imgui.begin_popup_context_item(f"##reference_context"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            glassesTools.gui.utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")

            imgui.pop_text_wrap_pos()
        return glassesTools.gui.utils.popup("About gazeMapper", popup_content, closable=True, outside=True)