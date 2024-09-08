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

import glassesTools.annotation
import imgui_bundle
from imgui_bundle import imgui, immapp, imgui_md, hello_imgui, glfw_utils, icons_fontawesome_6 as ifa6
import glfw
import OpenGL
import OpenGL.GL as gl

import glassesTools
import glassesValidator

from ... import config, config_watcher, marker, plane, process, session, type_utils, version
from .. import async_thread
from . import callbacks, colors, file_picker, image_helper, msg_box, process_pool, session_lister, settings_editor, utils


class GUI:
    def __init__(self):
        self.popup_stack = []
        self.running     = False
        settings_editor.set_gui_instance(self)

        self.project_dir: pathlib.Path = None
        self.study_config: config.Study = None

        self.sessions: dict[str, session.Session] = {}
        self._sessions_lock: threading.Lock      = threading.Lock()
        self._selected_sessions: dict[str, bool] = {}
        self._session_lister = session_lister.SessionList(self.sessions, self._sessions_lock, self._selected_sessions, info_callback=self._open_session_detail, item_context_callback=self._session_context_menu, item_action_context_callback=self._session_action_context_menu)

        self._recording_listers  : dict[str, session_lister.SessionList] = {}
        self._recordings_lock    : dict[str, threading.Lock]             = {}
        self._selected_recordings: dict[str, dict[str, bool]]            = {}

        self._possible_value_getters: dict[str] = {}

        self.need_setup_recordings  = True
        self.need_setup_plane       = True
        self.need_setup_episode     = True
        self.can_accept_sessions    = False

        self.config_watcher: concurrent.futures.Future  = None
        self.config_watcher_stop_event: asyncio.Event   = None

        self.process_pool                               = process_pool.ProcessPool(self._worker_process_done_hook)
        self.job_list: dict[int, utils.JobDescription]  = {}
        self._job_list_lock: threading.Lock             = threading.Lock()

        self._window_list: list[hello_imgui.DockableWindow] = []
        self._to_dock         = []
        self._to_focus        = None
        self._after_window_update_callback: Callable[[],None] = None
        self._need_set_window_title = False
        self._main_dock_node_id = None

        self._sessions_pane: hello_imgui.DockableWindow = None
        self._project_settings_pane: hello_imgui.DockableWindow = None
        self._show_demo_window = False

        self._icon_font: imgui.ImFont = None
        self._big_font: imgui.ImFont = None

        self._problems_cache: type_utils.ProblemDict = {}
        self._marker_preview_cache: dict[tuple[int,int,int], image_helper.ImageHelper] = {}

        # Show errors in threads
        def asyncexcepthook(future: asyncio.Future):
            try:
                exc = future.exception()
            except concurrent.futures.CancelledError:
                return
            if not exc:
                return
            tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
            if isinstance(exc, asyncio.TimeoutError):
                utils.push_popup(self, msg_box.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", msg_box.MsgBox.warn, more=tb)
                return
            utils.push_popup(self, msg_box.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", msg_box.MsgBox.error)
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
        self._icon_font = msg_box.icon_font = \
            hello_imgui.load_font("fonts/Font_Awesome_6_Free-Solid-900.otf", msg_box_size, large_icons_params)

    def _setup_glfw(self):
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_drop_callback(win, self._drop_callback)

    def _drop_callback(self, _: glfw._GLFWwindow, items: list[str]):
        paths = [pathlib.Path(item) for item in items]
        if self.popup_stack and isinstance(picker := self.popup_stack[-1], file_picker.FilePicker):
            picker.set_dir(paths)
        else:
            if self.project_dir is not None:
                # import recordings
                pass
            else:
                # load project
                if len(paths)!=1 or not (path := paths[0]).is_dir():
                    utils.push_popup(msg_box.msgbox, "Project opening error", "Only a single project directory should be drag-dropped on the glassesValidator GUI.", msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in paths])))
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
        # transmit them to HelloImGui
        runner_params.docking_params.dockable_windows = [
            self._sessions_pane,
            self._project_settings_pane,
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
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []
        else:
            # check if any computer detail windows were closed. Those should be removed from the list
            hello_imgui.get_runner_params().docking_params.dockable_windows = \
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.is_visible or w.label in ['Project settings']]

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
        self._check_project_setups_state()
        utils.handle_popup_stack(self.popup_stack)
        # also handle showing of debug windows
        if self._show_demo_window:
            self._show_demo_window = imgui.show_demo_window(self._show_demo_window)

        # now actual menu
        if imgui.begin_menu("Help"):
            if imgui.menu_item("About", "", False)[0]:
                utils.push_popup(self, self._about_popup_drawer)
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
            need_state_sync = None
            change_path = change_path.relative_to(self.project_dir)
            match len(change_path.parents):
                case 2:
                    # session-level states
                    sess = change_path.parent.name
                    with self._sessions_lock:
                        if sess not in self.sessions:
                            # some other folder apparently
                            return
                        self.sessions[sess].load_action_states(False)
                        need_state_sync = (sess,)
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
                        self.sessions[sess].recordings[rec].load_action_states(False)
                        need_state_sync = (sess, rec)
                case _:
                    pass    # ignore, not of interest
            # reapply pending and running state if needed
            if need_state_sync:
                with self._sessions_lock:
                    sess = need_state_sync[0]
                    rec = need_state_sync[1] if len(need_state_sync)>1 else None
                    if sess not in self.sessions:
                        return
                    if rec and rec not in self.sessions[sess].recordings:
                        return
                    with self._job_list_lock:
                        for jid in self.job_list:
                            # reset pending and processing states as those are not encoded in the file
                            job = self.job_list[jid]
                            if job.session==sess and (not rec or job.recording==rec):
                                job_state = self.process_pool.get_job_state(jid)
                                if job_state in [process_pool.ProcessState.Pending, process_pool.ProcessState.Running]:
                                    if rec:
                                        self.sessions[sess].recordings[rec].state[job.action] = job_state
                                    else:
                                        self.sessions[sess].state[job.action] = job_state
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
                        else:
                            self.sessions[sess].add_existing_recording(rec)
                            if sess in self._selected_recordings:
                                self._selected_recordings[sess] |= {rec: False}
                case _:
                    pass    # ignore, not of interest

    def _launch_task(self, sess: str, recording: str|None, action: process.Action):
        job = utils.JobDescription(action, session, recording)
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
            args = tuple()

        # launch task
        job_id = self.process_pool.run(func, *args)

        # store to job queue
        with self._job_list_lock:
            self.job_list[job_id] = job

    def _worker_process_done_hook(self, future: process_pool.ProcessFuture, job_id: int, job: utils.JobDescription, state: process_pool.ProcessState):
        with self._sessions_lock:
            # remove from active job list
            with self._job_list_lock:
                self.job_list.pop(job_id, None)

            # check there is a corresponding session (and recording)
            if job.session not in self.sessions:
                # unknown session, nothing to do
                return
            session_level = job.recording is None
            if not session_level and not job.recording in self.sessions[job.session].recordings:
                # unknown recording, nothing to do
                return

            match state:
                case process_pool.ProcessState.Canceled:
                    # just remove job, so no-op here
                    pass
                case process_pool.ProcessState.Completed:
                    # nothing to do, recording state updates are done by file system watcher
                    pass
                case process_pool.ProcessState.Failed:
                    exc = future.exception()    # should not throw exception since CancelledError is already encoded in state and future is done
                    tb = utils.get_traceback(type(exc), exc, exc.__traceback__)
                    lbl = f'session "{job.session}"'
                    if not session_level:
                        lbl += f', recording "{job.recording}"'
                    lbl += f' (work item {job_id}, action {job.action.displayable_name})'
                    if isinstance(exc, concurrent.futures.TimeoutError):
                        utils.push_popup(msg_box.msgbox, "Processing error", f"A worker process has failed for {lbl}:\n{type(exc).__name__}: {str(exc) or 'No further details'}\n\nPossible causes include:\n - You are running with too many workers, try lowering them in settings", msg_box.MsgBox.warn, more=tb)
                        return
                    utils.push_popup(msg_box.msgbox, "Processing error", f"Something went wrong in a worker process for {lbl}:\n\n{tb}", msg_box.MsgBox.error)

            # clean up when a task failed or was canceled
            if state in [process_pool.ProcessState.Canceled, process_pool.ProcessState.Failed]:
                if job.action==process.Action.IMPORT:
                    # remove working directory if this was an import task
                    async_thread.run(callbacks.remove_recording_working_dir(self.project_dir, job.session, job.recording))
                else:
                    # reset status of this aborted/failed task
                    if session_level:
                        session.update_action_states(self.sessions[job.session].working_directory, job.action, process.State.Not_Run, self.study_config)
                    else:
                        session.update_action_states(self.sessions[job.session].recordings[job.recording].info.working_directory, job.action, process.State.Not_Run, self.study_config)

            # if there are no jobs left, clean up process pool
            self.process_pool.cleanup_if_no_jobs()

    def load_project(self, path: pathlib.Path):
        self.project_dir = path
        try:
            config_dir = config.guess_config_dir(self.project_dir)
            self.study_config = config.Study.load_from_json(config_dir, strict_check=False)
            self._reload_sessions()
        except Exception as e:
            utils.push_popup(self, msg_box.msgbox, "Project loading error", f"Failed to load the project at {self.project_dir}:\n{e}\n\n{utils.get_traceback(e)}", msg_box.MsgBox.error)
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
        # trigger update so visibility change is honored
        self._window_list = [self._sessions_pane, self._project_settings_pane]
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

    def _check_project_setups_state(self):
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

    def _session_lister_set_actions_to_show(self, lister: session_lister.SessionList, for_recordings=False):
        if self.study_config is None:
            lister.set_actions_to_show(set())

        actions = process.get_actions_for_config(self.study_config, exclude_session_level=for_recordings)
        lister.set_actions_to_show(actions)

    def close_project(self):
        self._project_settings_pane.is_visible = False
        # trigger update so visibility change is honored, also delete other windows in the process
        self._window_list = [self._sessions_pane, self._project_settings_pane]

        # stop watching for config changes
        self.config_watcher_stop_event.set()
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
            utils.push_popup(self, callbacks.get_folder_picker(self, reason='creating'))
        imgui.same_line(spacing=10*imgui.get_style().item_spacing.x)
        if imgui.button(ifa6.ICON_FA_FOLDER_OPEN+" Open project", size=(but_width, but_height)):
            utils.push_popup(self, callbacks.get_folder_picker(self, reason='loading'))

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
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, self._problems_cache)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                utils.push_popup(self, msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", msg_box.MsgBox.error)
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()
                self._session_lister_set_actions_to_show(self._session_lister)
                for iid in self._recording_listers:
                    self._session_lister_set_actions_to_show(self._recording_listers[iid], for_recordings=True)


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
            utils.push_popup(self, lambda: utils.popup("Add recording", _add_rec_popup, buttons = buttons, outside=False))

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
            utils.push_popup(self, lambda: utils.popup("Add plane", _add_plane_popup, buttons = buttons, outside=False))

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
        changed, new_config = settings_editor.draw(copy.deepcopy(self.study_config), ['episodes_to_code', 'planes_per_episode'], config.study_parameter_types, {}, self._possible_value_getters, self._problems_cache)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as e:
                # do not persist invalid config, inform user of problem
                utils.push_popup(self, msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{e}", msg_box.MsgBox.error)
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
            new_val = settings_editor.draw_value(f'size_{m.id}', m.size, marker.marker_parameter_types['size'], False, marker.marker_defaults.get('size',None), False, False)[0]
            if (this_changed:=m.size!=new_val):
                m.size = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'aruco_dict_{m.id}', m.aruco_dict, marker.marker_parameter_types['aruco_dict'], False, marker.marker_defaults.get('aruco_dict',None), False, False)[0]
            if (this_changed:=m.aruco_dict!=new_val):
                m.aruco_dict = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'marker_border_bits_{m.id}', m.marker_border_bits, marker.marker_parameter_types['marker_border_bits'], False, marker.marker_defaults.get('marker_border_bits',None), False, False)[0]
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
            utils.push_popup(self, lambda: utils.popup("Add marker", _add_rec_popup, buttons = buttons, outside=False))

    def _session_context_menu(self, item: session.Session):
        pass
    def _session_action_context_menu(self, item: session.Session, action: process.Action):
        if process.is_session_level_action(action):
            state = item.state[action]
        else:
            states = {r:item.recordings[r].state[action] for r in item.recordings}

    def _open_session_detail(self, item: session.Session):
        win_name = f'{item.name}##session_view'
        if win := hello_imgui.get_runner_params().docking_params.dockable_window_of_name(win_name):
            win.focus_window_at_next_frame = True
        else:
            window_list = hello_imgui.get_runner_params().docking_params.dockable_windows
            window_list.append(
                self._make_main_space_window(win_name, lambda: self._session_detail_GUI(item), can_be_closed=True)
            )
            self._window_list = window_list
            self._to_dock = [win_name]
            self._to_focus= win_name
            self._recordings_lock[item.name] = threading.Lock()
            self._selected_recordings[item.name] = {k:False for k in item.recordings}
            self._recording_listers[item.name] = session_lister.SessionList(item.recordings, self._recordings_lock[item.name], self._selected_recordings[item.name], for_recordings=True)
            self._session_lister_set_actions_to_show(self._recording_listers[item.name], for_recordings=True)

    def _session_detail_GUI(self, item: session.Session):
        missing_recs = item.missing_recordings()
        if missing_recs:
            imgui.text_colored(colors.error,'*The following recordings are missing for this session:\n'+'\n'.join(missing_recs))
        self._recording_listers[item.name].draw()

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
            if sys.platform.startswith("linux"):
                imgui.text(f"{platform.system()} {platform.release()}")
            elif sys.platform.startswith("win"):
                rel = 11 if sys.getwindowsversion().build>22000 else platform.release()
                imgui.text(f"{platform.system()} {rel} {platform.win32_edition()} ({platform.version()})")
            elif sys.platform.startswith("darwin"):
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
            utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")

            imgui.pop_text_wrap_pos()
        return utils.popup("About gazeMapper", popup_content, closable=True, outside=True)