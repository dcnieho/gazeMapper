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
import datetime
import typing
import pathvalidate
import natsort

import imgui_bundle
from imgui_bundle import imgui, immapp, imgui_md, hello_imgui, glfw_utils, icons_fontawesome_6 as ifa6
import glfw
import OpenGL
import OpenGL.GL as gl
import ETDQualitizer

import glassesTools
from glassesTools import annotation, aruco, camera_recording, gui as gt_gui, naming as gt_naming, marker as gt_marker, marker_assembly as gt_marker_assembly, ocv, plane as gt_plane, platform as gt_platform, process_pool, utils as gt_utils
from glassesTools.gui import colors

from ... import camera_calibration, config, marker, marker_assembly, process, project_watcher, session, type_utils, version
from .. import async_thread
from . import callbacks, image_helper, session_lister, settings_editor, utils


# add formatter for Marker IDs (marker id + aruco dict pairs)
settings_editor.register_formatter(gt_marker.MarkerID, gt_marker.marker_ID_to_str)

class GUI:
    def __init__(self):
        self.popup_stack = []
        self.running     = False
        settings_editor.set_gui_instance(self)

        self.project_dir      : pathlib.Path|None                                           = None
        self.study_config     : config.Study|None                                           = None
        self.assembly_configs : dict[str,gt_marker_assembly.MarkerAssembly|Exception|None]  = {}
        self.cam_calibrations : dict[str,camera_calibration.Inspection]                     = {}
        self._dict_type_rec   : dict[str, typing.Type]|None             = None
        self._coding_setup_dict_type_rec: dict[str, dict[typing.Any, typing.Type]]          = {}

        self.sessions                           : dict[str, session.Session]                        = {}
        self.session_config_overrides           : dict[str, tuple[config.StudyOverride, dict[str,config.StudyOverride]]] = {}
        self._session_effective_config_cache    : dict[str, config.Study]                           = {}
        self._session_override_problems_cache   : dict[str, type_utils.ProblemDict]                 = {}
        self._session_problem_entries_cache     : dict[str, list[tuple[type_utils.ProblemLevel, str, str | None]]] = {}
        self._session_dict_type_rec             : dict[str, dict[int|str, dict[str, typing.Type]]]  = {}
        self._sessions_lock                     : threading.Lock                                    = threading.Lock()
        self._selected_sessions                 : dict[str, bool]                                   = {}
        self._session_lister                                                                        = session_lister.List(self.sessions, self._sessions_lock, self._selected_sessions, info_callback=self._open_session_detail, draw_action_status_callback=self._session_action_status, item_context_callback=self._session_context_menu)
        self._et_widget_drawer                                                                      = gt_gui.recording_table.EyeTrackerName()

        self.recording_config_overrides : dict[str, dict[str, tuple[config.StudyOverride, dict[str,config.StudyOverride]]]] = {}
        self._recording_effective_config_cache: dict[str, dict[str, config.Study]]                  = {}
        self._recording_override_problems_cache: dict[str, dict[str, type_utils.ProblemDict]]       = {}
        self._recording_dict_type_rec   : dict[str, dict[str, dict[int|str, dict[str, typing.Type]]]] = {}
        self._recording_listers         : dict[str, gt_gui.recording_table.RecordingTable]          = {}
        self._recording_calibrations    : dict[tuple[str, str], camera_calibration.Inspection]      = {}
        self._selected_recordings       : dict[str, dict[str, bool]]                                = {}
        self._pending_session_removals  : set[str]                                                   = set()
        self._pending_recording_removals: set[tuple[str, str]]                                       = set()
        self._pending_session_pane_removals: set[str]                                                = set()

        self._possible_value_getters                : dict[str, typing.Callable]    = {}

        self.setup_recordings_error_level           : type_utils.ProblemLevel|None  = None
        self.setup_assembly_error_level             : type_utils.ProblemLevel|None  = None
        self.setup_coding_error_level               : type_utils.ProblemLevel|None  = None
        self.setup_individual_markers_error_level   : type_utils.ProblemLevel|None  = None
        self.can_accept_sessions                                                    = False
        self._session_actions                       : set[process.Action]           = set()
        self._problems_cache                        : type_utils.ProblemDict        = {}
        self._setup_state_dirty                     : bool                          = True

        self._session_watcher           : concurrent.futures.Future|None = None
        self._session_watcher_stop_event: asyncio.Event|None             = None
        self._config_watcher            : concurrent.futures.Future|None = None
        self._config_watcher_stop_event : asyncio.Event|None             = None

        self.process_pool                                                = process_pool.ProcessPool()
        self.job_scheduler                                               = process_pool.JobScheduler[utils.JobInfo](self.process_pool, self._check_job_valid, self._check_job_preconditions)
        self._active_jobs_cache         : dict[utils.JobInfo, int]       = {}

        self._window_list                   : list[hello_imgui.DockableWindow]  = []
        self._to_dock: list[str]                                                = []
        self._to_focus                                                          = None
        self._after_window_update_callback  : Callable[[],None]|None            = None
        self._need_set_window_title                                             = False
        self._main_dock_node_id                                                 = None

        self._sessions_pane         : hello_imgui.DockableWindow|None   = None
        self._project_settings_pane : hello_imgui.DockableWindow|None   = None
        self._action_list_pane      : hello_imgui.DockableWindow|None   = None
        self._show_demo_window                                          = False
        self._main_window_dpi_scale                                     : float|None = None

        self._big_font_size_multiplier                                  = 1.75
        self._icon_font_size_multiplier                                 = 4.2

        self._marker_preview_cache  : dict[tuple[int,int,int], image_helper.ImageHelper]= {}
        self._assembly_preview_cache: dict[str               , image_helper.ImageHelper]= {}
        self._async_task_popups     : dict[concurrent.futures.Future, typing.Callable]  = {}
        self._async_task_popup_lock : threading.Lock                                    = threading.Lock()
        self._last_export_preferences: dict|None                                        = None

        # handle async task completion (e.g., errors in async tasks)
        async_thread.done_callback = self._handle_async_task_done

    def run_async_task(self, coroutine: typing.Coroutine, popup: typing.Callable|None = None) -> concurrent.futures.Future:
        future = async_thread.run(coroutine)
        if popup is None:
            return future

        with self._async_task_popup_lock:
            self._async_task_popups[future] = popup
        return future

    def close_popup(self, popup: typing.Callable|None):
        if popup is None:
            return
        try:
            self.popup_stack.remove(popup)
        except ValueError:
            pass

    def _close_tracked_async_popup(self, future: concurrent.futures.Future):
        with self._async_task_popup_lock:
            tracked_popup = self._async_task_popups.pop(future, None)
        self.close_popup(tracked_popup)

    def _handle_async_task_done(self, future: concurrent.futures.Future):
        self._close_tracked_async_popup(future)
        try:
            exc = future.exception()
        except concurrent.futures.CancelledError:
            return
        if not exc:
            return
        # if we get here, an error occurred that we should notify about
        tb = gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
        if isinstance(exc, asyncio.TimeoutError):
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", f"A background process has failed:\n{exc}: {str(exc) or 'No further details'}", gt_gui.msg_box.MsgBox.warn, more=tb)
            return
        gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", "Something went wrong in an asynchronous task of a separate thread", gt_gui.msg_box.MsgBox.error, more=tb)

    def _load_fonts(self):
        hello_imgui.load_font_ttf_with_font_awesome_icons("fonts/Roboto/Roboto-Regular.ttf", 16.)

    def _setup_imgui_config(self):
        io = imgui.get_io()
        io.config_dpi_scale_fonts = True
        io.config_dpi_scale_viewports = True

    def _setup_glfw(self):
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_drop_callback(win, self._drop_callback)

    def _scale_main_window_on_dpi_change(self):
        win = glfw_utils.glfw_window_hello_imgui()
        dpi_scale, _ = glfw.get_window_content_scale(win)
        if dpi_scale <= 0:
            return
        if self._main_window_dpi_scale is None:
            self._main_window_dpi_scale = dpi_scale
            return
        if dpi_scale == self._main_window_dpi_scale:
            return

        width, height = glfw.get_window_size(win)
        if width > 0 and height > 0:
            scale = dpi_scale / self._main_window_dpi_scale
            glfw.set_window_size(win, round(width * scale), round(height * scale))
            self._main_window_dpi_scale = dpi_scale

    def _pre_new_frame(self):
        self._scale_main_window_on_dpi_change()
        self._update_windows()

    def _drop_callback(self, _: glfw._GLFWwindow, items: list[str]):
        paths = [pathlib.Path(item) for item in items]
        if self.popup_stack and isinstance(picker := self.popup_stack[-1], gt_gui.file_picker.FilePicker):
            picker.set_dir(paths)
        else:
            if self.project_dir is not None:
                # import recordings
                if not self.can_accept_sessions:
                    return
                callbacks.add_recordings(self, paths, [])
            else:
                # load project
                if len(paths)!=1 or not (path := paths[0]).is_dir():
                    gt_gui.utils.push_popup(gt_gui.msg_box.msgbox, "Project opening error", "Only a single project directory should be drag-dropped on the gazeMapper GUI.", gt_gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in paths])))
                else:
                    callbacks.try_load_project(self, path, 'loading')

    def run(self):
        # Hello ImGui params (they hold the settings as well as the Gui callbacks)
        runner_params = hello_imgui.RunnerParams()
        runner_params.ini_disable = True

        runner_params.app_window_params.window_title = self._get_window_title()

        runner_params.app_window_params.window_geometry.size = (1400, 700)
        runner_params.app_window_params.restore_previous_geometry = False
        runner_params.callbacks.setup_imgui_config = self._setup_imgui_config
        runner_params.callbacks.default_icon_font = hello_imgui.DefaultIconFont.font_awesome6
        runner_params.callbacks.post_init_add_platform_backend_callbacks = self._setup_glfw
        runner_params.callbacks.load_additional_fonts = self._load_fonts
        runner_params.callbacks.pre_new_frame = self._pre_new_frame
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

    def _make_main_space_window(self, name: str, gui_func: Callable[[],None], can_be_closed=False, is_visible=True, window_id: str|None=None):
        main_space_view = hello_imgui.DockableWindow()
        main_space_view.label = f'{name}###{window_id or self._window_identity_for_name(name)}'
        main_space_view.dock_space_name = "MainDockSpace"
        main_space_view.gui_function = gui_func
        main_space_view.can_be_closed = can_be_closed
        main_space_view.is_visible = is_visible
        main_space_view.remember_is_visible = False
        return main_space_view

    @staticmethod
    def _window_identity(label: str) -> str:
        return label.split('###', 1)[1] if '###' in label else label

    @staticmethod
    def _window_identity_for_name(name: str) -> str:
        return f'main:{name}'

    @staticmethod
    def _problem_indicator(level: type_utils.ProblemLevel|None) -> str:
        if level == type_utils.ProblemLevel.Error:
            return ifa6.ICON_FA_CIRCLE_XMARK + ' '
        if level == type_utils.ProblemLevel.Warning:
            return ifa6.ICON_FA_TRIANGLE_EXCLAMATION + ' '
        return ''

    @classmethod
    def _window_label(cls, name: str, identity: str, level: type_utils.ProblemLevel|None=None) -> str:
        return f'{cls._problem_indicator(level)}{name}###{identity}'

    def _set_window_problem_indicator(self, window: hello_imgui.DockableWindow, name: str, identity: str, level: type_utils.ProblemLevel|None):
        window.label = self._window_label(name, identity, level)

    def _update_problem_tab_labels(self):
        project_problem_levels = (type_utils.get_error_level(self._problems_cache), self.setup_recordings_error_level, self.setup_assembly_error_level, self.setup_coding_error_level, self.setup_individual_markers_error_level)
        project_problem_level = max((level for level in project_problem_levels if level is not None), key=lambda level: level.value, default=None)
        project_pane_levels = {
            'Session definition': self.setup_recordings_error_level,
            'Plane & 3D Assembly Editor': self.setup_assembly_error_level,
            'Individual marker editor': self.setup_individual_markers_error_level,
            'Coding setup': self.setup_coding_error_level,
        }
        windows = hello_imgui.get_runner_params().docking_params.dockable_windows
        for window in windows:
            identity = self._window_identity(window.label)
            if identity == 'main:Sessions':
                self._set_window_problem_indicator(window, 'Sessions', identity, None)
            elif identity == 'main:Project settings':
                self._set_window_problem_indicator(window, 'Project settings', identity, project_problem_level)
            elif identity.startswith('project_pane:'):
                name = identity.removeprefix('project_pane:')
                if name in project_pane_levels:
                    self._set_window_problem_indicator(window, name, identity, project_pane_levels[name])
            elif identity.startswith('session_view:'):
                sess_name = identity.removeprefix('session_view:')
                if (sess := self.sessions.get(sess_name)) is not None:
                    level = self._problem_entries_level([(problem_level, diagnostic) for problem_level, diagnostic, _ in self._session_problem_entries(sess)])
                    self._set_window_problem_indicator(window, sess_name, identity, level)

    def _update_windows(self):
        if not self.running:
            # apply theme
            hello_imgui.apply_theme(hello_imgui.ImGuiTheme_.darcula_darker)
            # fix up the style: fully opaque window backgrounds
            window_bg = imgui.get_style().color_(imgui.Col_.window_bg)
            window_bg.w = 1
        self.running = True
        self._check_project_setup_state()
        self._update_jobs_and_process_pool()
        pending_session_pane_removals = self._process_pending_session_updates()
        if self._need_set_window_title:
            self._set_window_title()

        # update windows to be shown
        old_window_ids = {self._window_identity(w.label) for w in hello_imgui.get_runner_params().docking_params.dockable_windows}
        if self._window_list:
            hello_imgui.get_runner_params().docking_params.dockable_windows = self._window_list
            self._window_list = []
        else:
            # check if any session detail windows were closed. Those should be removed from the list
            hello_imgui.get_runner_params().docking_params.dockable_windows = \
                [w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if w.is_visible or self._window_identity(w.label) in ['main:Project settings', 'main:Processing queue']]
        if pending_session_pane_removals:
            runner_params = hello_imgui.get_runner_params()
            pane_ids = {f'session_view:{sess}' for sess in pending_session_pane_removals}
            runner_params.docking_params.dockable_windows = [window for window in runner_params.docking_params.dockable_windows if self._window_identity(window.label) not in pane_ids]
        self._update_problem_tab_labels()
        current_window_ids = {self._window_identity(w.label) for w in hello_imgui.get_runner_params().docking_params.dockable_windows}
        # some cleanup may be needed for some of the closed windows
        if (removed:=old_window_ids-current_window_ids):
            for r in removed:
                if r.startswith('session_view:'):
                    sess_name = r.removeprefix('session_view:')
                    # cleanup
                    self.session_config_overrides.pop(sess_name)
                    self._session_effective_config_cache.pop(sess_name, None)
                    self._session_override_problems_cache.pop(sess_name, None)
                    self._session_dict_type_rec.pop(sess_name)
                    self.recording_config_overrides.pop(sess_name)
                    self._recording_effective_config_cache.pop(sess_name, None)
                    self._recording_override_problems_cache.pop(sess_name, None)
                    self._recording_dict_type_rec.pop(sess_name)
                    self._selected_recordings.pop(sess_name)
                    self._recording_listers.pop(sess_name)

        # we also handle docking requests here
        if self._to_dock and self._main_dock_node_id:
            for identity in self._to_dock:
                window = next((w for w in hello_imgui.get_runner_params().docking_params.dockable_windows if self._window_identity(w.label)==identity), None)
                if window is not None:
                    imgui.internal.dock_builder_dock_window(window.label, self._main_dock_node_id)
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
                    if self._window_identity(w.label)==self._to_focus[0]:
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
        if imgui.menu_item(ifa6.ICON_FA_FOLDER_OPEN+" Open project folder", "", False)[0]:
            callbacks.open_folder(self, self.project_dir)
        if disabled:
            imgui.end_disabled()

    def _show_menu_gui(self):
        # this is always called, so handle popups here
        gt_gui.utils.handle_popup_stack(self.popup_stack)
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

    def _config_change_callback(self, change_path: pathlib.Path, change_type: str):
        # NB: for marker assemblies, this callback could filter out some changes, like irrelevant txt files
        # (maybe user added some notes or so), or csv/txt files for defaulted validation assemblies
        # (get ignored) but this is pretty quick, so not worth the additional complexity
        config_dir = config.guess_config_dir(change_path.parent)
        if change_path.parent==config_dir:
            # we're dealing with a change in the main config folder. Check if its a relevant file
            if change_path.name in (config.Study.default_json_file_name, session.SessionDefinition.default_json_file_name):
                # its a change in the study or session definition files, reload config
                try:
                    self.study_config = config.Study.load_from_json(config_dir, strict_check=False)
                    if change_path.name == session.SessionDefinition.default_json_file_name:
                        # session keep a ref to the session definition, so we need to reload sessions as well
                        self._reload_sessions()
                    else:
                        self._update_shown_actions_for_config()
                        self._session_lister_set_extra_columns_to_show()
                    self._reload_calibrations()
                except Exception as exc:
                    gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Project reloading error", f"The project's settings were changed, but failed to reload:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
            elif change_path.suffix == '.xml':
                # its a calibration xml file, figure out which recording it is for and update the calibration for that recording
                rec_name = change_path.name.removesuffix(f'_{session.RecordingDefinition.cal_file_name}')
                rec_defs = [r for r in self.study_config.session_def.recordings if r.name==rec_name]
                if not rec_defs:
                    return
                rec_def = rec_defs[0]
                # apply change
                if change_type=='deleted':
                    self._remove_calibration(rec_def)
                else:
                    self._load_calibration(rec_def)
            else:
                # anything else is irrelevant
                pass
            return
        elif change_path.parent.parent!=config_dir:
            # we're also not dealing with a marker assembly directory, skip
            return

        # if we got here, we're dealing with a marker assembly directory
        assembly_name = change_path.parent.name
        if change_type=='deleted':
            if change_path.name in (marker_assembly.Definition_Plane_Aruco.default_json_file_name, marker_assembly.Definition_MarkerAssembly_3D.default_json_file_name):
                # marker assembly removed
                pls = [pl for pl in self.study_config.marker_assemblies if pl.name==assembly_name]
                if pls: # may already have been removed
                    self._remove_marker_assembly(pls[0])
            return

        # for all addition or change events, reload the concerned marker assembly
        i = [i for i in range(len(self.study_config.marker_assemblies)) if self.study_config.marker_assemblies[i].name==assembly_name]
        try:
            p_def = marker_assembly.Definition.load_from_json(change_path.parent)
        except:
            # failed, just abort
            return
        if not i:
            if change_type=='added' and change_path.name in (marker_assembly.Definition_Plane_Aruco.default_json_file_name, marker_assembly.Definition_MarkerAssembly_3D.default_json_file_name):
                # new marker assembly
                self.study_config.marker_assemblies.append(p_def)
                i = len(self.study_config.marker_assemblies)-1
            else:
                # unknown marker assembly, ignore
                return
        else:
            i = i[0]
            self.study_config.marker_assemblies[i] = p_def
        # try to load marker assembly
        self._load_marker_assembly(self.study_config.marker_assemblies[i], defer_image_upload=True)

    def _request_session_sort(self):
        self._session_lister.request_sort()

    def _request_recording_sort(self, sess: str):
        if recording_lister := self._recording_listers.get(sess):
            recording_lister.request_sort()

    def _remove_session_from_gui(self, sess: str):
        self._invalidate_session_problem_cache(sess)
        self.sessions.pop(sess, None)
        self._selected_sessions.pop(sess, None)
        for key in [key for key in self._recording_calibrations if key[0]==sess]:
            self._recording_calibrations.pop(key, None)
        self._pending_session_pane_removals.add(sess)
        self._request_session_sort()

    def _remove_recording_from_gui(self, sess: str, rec: str):
        self._invalidate_session_problem_cache(sess)
        self.sessions[sess].recordings.pop(rec, None)
        self._recording_calibrations.pop((sess, rec), None)
        if sess in self._selected_recordings:
            self._selected_recordings[sess].pop(rec, None)
        if sess in self.session_config_overrides:
            self.recording_config_overrides.get(sess, {}).pop(rec, None)
            self._recording_dict_type_rec.get(sess, {}).pop(rec, None)
            self._invalidate_recording_override_state(sess, rec)
        self._request_recording_sort(sess)
        self._request_session_sort()

    def _process_pending_session_updates(self) -> set[str]:
        with self._sessions_lock:
            for sess in self._pending_session_removals:
                if sess in self.sessions:
                    self._remove_session_from_gui(sess)
            self._pending_session_removals.clear()
            for sess, rec in self._pending_recording_removals:
                if sess in self.sessions:
                    self._remove_recording_from_gui(sess, rec)
            self._pending_recording_removals.clear()
            pane_removals = self._pending_session_pane_removals.copy()
            self._pending_session_pane_removals.clear()
        return pane_removals

    def _add_recording_to_gui(self, sess: str, rec: str):
        session_obj = self.sessions[sess]
        recording_obj = session_obj.add_existing_recording(rec)
        if recording_obj is None:
            return
        self._invalidate_session_problem_cache(sess)
        self._load_recording_calibration(sess, recording_obj)
        if sess in self._selected_recordings:
            self._selected_recordings[sess][rec] = False
        if sess in self.session_config_overrides:
            effective_config = self._get_effective_session_config(sess)
            self._refresh_one_recording_override_state(session_obj, rec, effective_config)
            if sess in self._recording_listers:
                self._recording_lister_set_actions_to_show(self._recording_listers[sess], sess)
        self._request_recording_sort(sess)
        self._request_session_sort()

    def _reload_action_states(self, working_dir: pathlib.Path, for_recording: bool):
        for attempt in range(3):
            try:
                action_states = session.get_action_states(working_dir, for_recording, skip_if_missing=True)
            except Exception:
                if attempt<2:
                    time.sleep(.1)
                continue
            if action_states is None:
                return session._get_not_run_action_states(for_recording)
            return action_states
        return None

    def _session_change_callback(self, change_path: pathlib.Path, change_type: str):
        project_dir = self.project_dir
        study_config = self.study_config
        if project_dir is None or study_config is None:
            return
        if change_path.name == config.StudyOverride.default_json_file_name:
            self._reload_cached_override_from_file(change_path)
            return

        try:
            relative_path = change_path.relative_to(project_dir)
        except ValueError:
            return

        parts = relative_path.parts
        if not parts:
            return
        sess = parts[0]
        filename = change_path.name
        is_session_status = len(parts)==2 and filename==session.Session.status_file_name
        is_recording_status = len(parts)==3 and filename==session.Recording.status_file_name
        is_recording_info = len(parts)==3 and filename in (session.EyeTrackerRecording.default_json_file_name, session.CameraRecording.default_json_file_name)
        is_recording_calibration = len(parts)==3 and filename==gt_naming.scene_camera_calibration_fname

        if is_session_status:
            with self._sessions_lock:
                if change_type=='deleted':
                    if sess in self.sessions:
                        self._remove_session_from_gui(sess)
                    return
                if sess not in self.sessions:
                    try:
                        session_obj = session.get_session_from_directory(project_dir / sess, study_config.session_def)
                    except Exception:
                        return
                    self.sessions[sess] = session_obj
                    self._selected_sessions[sess] = False
                    self._load_session_recording_calibrations(sess, session_obj)
                else:
                    if action_states := self._reload_action_states(self.sessions[sess].working_directory, False):
                        self.sessions[sess].state = action_states
                self._request_session_sort()
            return

        if is_recording_status:
            with self._sessions_lock:
                session_obj = self.sessions.get(sess)
                rec = parts[1]
                if session_obj is None or rec not in session_obj.recordings:
                    return
                if action_states := self._reload_action_states(session_obj.recordings[rec].info.working_directory, True):
                    session_obj.recordings[rec].state = action_states
                self._request_recording_sort(sess)
                self._request_session_sort()
            return

        if is_recording_calibration:
            with self._sessions_lock:
                session_obj = self.sessions.get(sess)
                recording_obj = session_obj.recordings.get(parts[1]) if session_obj is not None else None
                if recording_obj is None:
                    return
                self._load_recording_calibration(sess, recording_obj)
                self._request_recording_sort(sess)
                self._request_session_sort()
            return

        if is_recording_info:
            with self._sessions_lock:
                session_obj = self.sessions.get(sess)
                rec = parts[1]
                if session_obj is None or not session_obj.definition.is_known_recording(rec):
                    return
                if change_type=='deleted':
                    self._remove_recording_from_gui(sess, rec)
                    return
                rec_info = None
                for _ in range(3):
                    try:
                        rec_info = session_obj.load_recording_info(rec)
                    except Exception:
                        time.sleep(.1)
                    else:
                        break
                if rec_info is None:
                    return
                if rec in session_obj.recordings:
                    session_obj.update_recording_info(rec, rec_info)
                    self._load_recording_calibration(sess, session_obj.recordings[rec])
                else:
                    session_obj.add_recording_from_info(rec, rec_info)
                    self._load_recording_calibration(sess, session_obj.recordings[rec])
                    if sess in self._selected_recordings:
                        self._selected_recordings[sess][rec] = False
                    if sess in self.session_config_overrides:
                        effective_config = self._get_effective_session_config(sess)
                        self._refresh_one_recording_override_state(session_obj, rec, effective_config)
                        if sess in self._recording_listers:
                            self._recording_lister_set_actions_to_show(self._recording_listers[sess], sess)
                self._request_recording_sort(sess)
                self._request_session_sort()
            return

        if change_type=='modified':
            return
        if change_type=='added' and not change_path.is_dir():
            return
        if change_path.suffix in ('.json', '.gazeMapper'):
            return

        # Handle session- and recording-directory add/delete events; file events are handled above.
        match len(parts):
            case 1:
                # session directory
                with self._sessions_lock:
                    if change_type=='deleted':
                        if sess in self.sessions:
                            self._remove_session_from_gui(sess)
                    else:
                        try:
                            session_obj = session.get_session_from_directory(project_dir / relative_path, study_config.session_def)
                        except Exception:
                            return
                        self.sessions[sess] = session_obj
                        self._selected_sessions[sess] = False
                        self._load_session_recording_calibrations(sess, session_obj)
                        self._request_session_sort()
            case 2:
                # recording directory
                rec = parts[1]
                with self._sessions_lock:
                    session_obj = self.sessions.get(sess)
                    if session_obj is None or not session_obj.definition.is_known_recording(rec):
                        return
                    if change_type=='deleted':
                        self._remove_recording_from_gui(sess, rec)
                    else:
                        self._add_recording_to_gui(sess, rec)

    def launch_task(self, sess: str, recording: str|None, action: process.Action, **kwargs):
        # NB: this is run under lock, so sess and recording are valid
        job = utils.JobInfo(action, sess, recording)
        if job in self._active_jobs_cache:
            # already scheduled, nothing to do
            return
        depends_on = self._get_action_job_dependencies(job, allow_pending=True)
        if depends_on is None:
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
        # Keep jobs for the same owner from running concurrently, and also only one job requiring a GUI can run at a time.
        exclusive_ids = [('session', sess)] if recording is None else [('session', sess, 'recording', recording)]
        needs_gui = action.needs_GUI or kwargs.get('show_visualization', False)
        if needs_gui:
            exclusive_ids.append(('gui',))
        priority = 1 if needs_gui else None

        # add to scheduler
        payload = process_pool.JobPayload(func, args, kwargs)
        progress_indicator = self.job_scheduler.get_progress_indicator() if action.has_progress_indication else None
        self.job_scheduler.add_job(job, payload, self._action_done_callback, exclusive_ids=exclusive_ids, priority=priority, progress_indicator=progress_indicator, depends_on=depends_on)

    def _update_jobs_and_process_pool(self):
        pending_warning_popups: list[tuple[int, utils.JobInfo, process_pool.JobWarning]] = []
        with self._sessions_lock:
            # tick job scheduler (under lock as checking job validity needs session lock)
            self.job_scheduler.update()

            # set pending and running states since these are not stored in the states file
            for job_id in self.job_scheduler.jobs:
                job_state = self.job_scheduler.jobs[job_id].get_state()
                self._update_job_states_impl(self.job_scheduler.jobs[job_id].user_data, job_state)

            # build cache for easy access to running jobs
            self._active_jobs_cache.clear()
            for job_id in self.job_scheduler.jobs:
                if self.job_scheduler.jobs[job_id].get_state() in [process_pool.State.Pending, process_pool.State.Running]:
                    self._active_jobs_cache[self.job_scheduler.jobs[job_id].user_data] = job_id

            for job_id in self._active_jobs_cache.values():
                job_desc = self.job_scheduler.jobs[job_id]
                for warning_index, warning in job_desc.get_unnotified_warnings():
                    job_desc.mark_warning_notified(warning_index)
                    pending_warning_popups.append((job_id, job_desc.user_data, warning))

        for job_id, job, warning in pending_warning_popups:
            self._notify_job_warning(job_id, job, warning)

        # if there are no jobs left, clean up process pool
        self.process_pool.cleanup_if_no_jobs()

    def _check_job_valid(self, job: utils.JobInfo) -> bool:
        sess = self.sessions.get(job.session,None)
        if sess is None:
            return False
        if job.recording and job.recording not in sess.recordings:
            return False
        return True

    def _check_job_preconditions(self, job: utils.JobInfo) -> bool:
        return self._get_action_job_dependencies(job, allow_pending=False) is not None

    def _get_action_job_dependencies(self, job: utils.JobInfo, allow_pending: bool) -> set[int]|None:
        sess = self.sessions.get(job.session)
        if sess is None or (job.recording is not None and job.recording not in sess.recordings):
            return None
        if job.action==process.Action.IMPORT:
            return set()

        cfg = self._get_effective_session_config(job.session) if job.session in self.session_config_overrides else self.study_config
        if cfg is None:
            return None
        rec_states = {job.recording:sess.recordings[job.recording].state} if job.recording is not None else {r:sess.recordings[r].state for r in sess.recordings}
        actions = process.get_possible_actions(sess.state, rec_states, {job.action}, cfg, allow_pending=allow_pending)
        action_state = actions.get(job.action)
        if action_state is None:
            return None
        available, prerequisites = action_state
        if job.recording is None:
            if not isinstance(available, bool) or not available:
                return None
        elif not isinstance(available, list) or job.recording not in available:
            return None

        dependency_ids = set()
        for prerequisite, recordings in prerequisites.items():
            owners = [None] if recordings is None else recordings
            for recording in owners:
                dependency_id = self._active_jobs_cache.get(utils.JobInfo(prerequisite, job.session, recording))
                if dependency_id is None:
                    return None
                dependency_ids.add(dependency_id)
        return dependency_ids

    def _get_active_job(self, action: process.Action, session_name: str, recording_name: str=None):
        j = utils.JobInfo(action, session_name, recording_name)
        if (job_id := self._active_jobs_cache.get(j, None)) is not None:
            return job_id, self.job_scheduler.jobs[job_id]
        else:
            return None, None

    def _get_job_notification_label(self, job_id: int, job: utils.JobInfo) -> tuple[str, str]:
        lbl = f'action "{job.action.displayable_name}" for session "{job.session}"'
        if job.recording is not None:
            lbl += f', recording "{job.recording}"'
        lbl_complete = lbl + f' (work item {job_id})'
        return lbl, lbl_complete

    def _notify_job_warning(self, job_id: int, job: utils.JobInfo, warning: process_pool.JobWarning):
        lbl, lbl_complete = self._get_job_notification_label(job_id, job)
        more = (
            f'{lbl_complete}\n\n'
            f'{warning.category}: {warning.message}\n'
            f'File "{warning.filename}", line {warning.lineno}'
        )
        if warning.line:
            more += f'\n\n{warning.line}'
        gt_gui.utils.push_popup(
            self,
            gt_gui.msg_box.msgbox,
            "Processing warning",
            f"A warning occurred when processing {lbl}:\n\n{warning.message}",
            gt_gui.msg_box.MsgBox.warn,
            more=more,
        )

    def _format_job_warning_details(self, warning: process_pool.JobWarning, number: int | None = None) -> str:
        prefix = f'{number}. ' if number is not None else ''
        details = f'{prefix}{warning.category}: {warning.message}\nFile "{warning.filename}", line {warning.lineno}'
        if warning.line:
            details += f'\n{warning.line}'
        return details

    def _get_job_queue_hover_text(self, job_state: process_pool.State, progress: tuple[float, str] | None, warnings: list[process_pool.JobWarning], error: str | None) -> str:
        if job_state == process_pool.State.Completed and warnings:
            hover_text = 'Completed with warnings'
        else:
            hover_text = job_state.displayable_name
            if job_state == process_pool.State.Running and progress is not None:
                hover_text += f' ({progress[1]})'

        if warnings:
            hover_text += '\n\nWarnings:\n' + '\n\n'.join(
                self._format_job_warning_details(warning, index)
                for index, warning in enumerate(warnings, start=1)
            )
        if error:
            hover_text += f'\n\nError:\n{error}'
        return hover_text

    def _draw_job_queue_state(self, job_state: process_pool.State, progress: tuple[float, str] | None, warnings: list[process_pool.JobWarning], error: str | None):
        if job_state == process_pool.State.Completed and warnings:
            imgui.text_colored(colors.warning, ifa6.ICON_FA_TRIANGLE_EXCLAMATION)
        else:
            gt_gui.utils.draw_process_state(job_state, have_hover_popup=False, progress=progress)
        gt_gui.utils.draw_hover_text(self._get_job_queue_hover_text(job_state, progress, warnings, error), text='')

    def _update_job_states_impl(self, job: utils.JobInfo, job_state: process_pool.State):
        sess = self.sessions.get(job.session,None)
        if sess is None:
            return
        if job.recording:
            rec = sess.recordings.get(job.recording,None)
            if rec is None:
                return
            if job_state!=  rec.state[job.action] and (job_state in [process_pool.State.Pending, process_pool.State.Running] or  rec.state[job.action] in [process_pool.State.Pending, process_pool.State.Running]):
                rec.state[job.action] = job_state
                self._request_recording_sort(job.session)
                self._request_session_sort()
        else:
            if job_state!= sess.state[job.action] and (job_state in [process_pool.State.Pending, process_pool.State.Running] or sess.state[job.action] in [process_pool.State.Pending, process_pool.State.Running]):
                sess.state[job.action] = job_state
                self._request_session_sort()

    def _action_done_callback(self, future: process_pool.ProcessFuture, job_id: int, job: utils.JobInfo, state: process_pool.State):
        # if process failed, notify error
        if state==process_pool.State.Failed:
            exc = future.exception()    # should not throw exception since CancelledError is already encoded in state and future is done
            tb = gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__)
            lbl, lbl_complete = self._get_job_notification_label(job_id, job)
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Processing error", f"An error occurred when processing {lbl}:\n\n{exc}", gt_gui.msg_box.MsgBox.error, more=f'{lbl_complete}\n\n{tb}')
            if job_id in self.job_scheduler.jobs:
                self.job_scheduler.jobs[job_id].error = tb

        # clean up, if needed, when a task failed or was canceled
        if job.action==process.Action.IMPORT and state in [process_pool.State.Canceled, process_pool.State.Failed]:
            # remove working directory if this was an import task
            async_thread.run(callbacks.remove_recording_working_dir(self.project_dir, job.session, job.recording))
            # also update GUI state, might be needed if there was no working directory yet for instance
            if job.session in self.sessions:
                self._remove_recording_from_gui(job.session, job.recording)
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
                self._load_recording_calibration(job.session, rec)
            rec.load_action_states(False, False)
            self._request_recording_sort(job.session)
            self._request_session_sort()
        else:
            sess.load_action_states(False, False)
            self._request_session_sort()

    def load_project(self, path: pathlib.Path):
        self._last_export_preferences = None
        self.project_dir = path
        try:
            config_dir = config.guess_config_dir(self.project_dir)
            self.study_config = config.Study.load_from_json(config_dir, strict_check=False)
            self._reload_sessions()
            self._reload_marker_assemblies()
            self._reload_calibrations()
            self.process_pool.set_num_workers(self.study_config.gui_num_workers)
        except Exception as exc:
            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Project loading error", f"Failed to load the project at {self.project_dir}:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
            self.close_project()
            return

        self._session_watcher_stop_event = asyncio.Event()
        self._session_watcher = async_thread.run(project_watcher.watch_and_report_changes(self.project_dir, self._session_change_callback, self._session_watcher_stop_event, watch_filter=project_watcher.ChangeFilter(('.json','.gazeMapper','.xml'), True, {config_dir}, True)))
        self._config_watcher_stop_event = asyncio.Event()
        self._config_watcher = async_thread.run(project_watcher.watch_and_report_changes(config_dir, self._config_change_callback, self._config_watcher_stop_event, watch_filter=project_watcher.ChangeFilter(('.json','.csv','.txt','.xml'), do_report_directories=True, do_report_files=True)))

        def _get_known_recordings(filter_ref=False, dev_types:list[session.RecordingType]|None=None, dev_sub_types:list[camera_recording.Type]|None=None) -> list[str]:
            recs = [r.name for r in self.study_config.session_def.recordings]
            if filter_ref and self.study_config.sync_ref_recording:
                recs = [r for r in recs if r!=self.study_config.sync_ref_recording]
            if dev_types:
                recs = [r for r in recs if self.study_config.session_def.get_recording_def(r).type in dev_types]
                if dev_sub_types:
                    recs = [r for r in recs if self.study_config.session_def.get_recording_def(r).camera_recording_type in dev_sub_types]
            return sorted(recs)
        def _get_known_recordings_no_ref() -> list[str]:
            return _get_known_recordings(filter_ref=True)
        def _get_known_recordings_only_eye_tracker() -> list[str]:
            return _get_known_recordings(dev_types=[session.RecordingType.Eye_Tracker])
        def _get_known_recordings_only_camera_head_attached() -> list[str]:
            return _get_known_recordings(dev_types=[session.RecordingType.Camera], dev_sub_types=[camera_recording.Type.Head_attached])
        def _get_known_individual_markers() -> list[gt_marker.MarkerID]:
            return sorted([gt_marker.MarkerID(m.id, m.aruco_dict_id) for m in self.study_config.individual_markers])
        def _get_known_assemblies() -> list[str]:
            return sorted([p.name for p in self.study_config.marker_assemblies])
        self._possible_value_getters = {
            'mapped_video_make_which': _get_known_recordings,
            'mapped_video_recording_colors': _get_known_recordings_only_eye_tracker,
            'mapped_video_show_gaze_on_plane_in_which': _get_known_recordings,
            'mapped_video_show_camera_in_which': _get_known_recordings,
            'mapped_video_show_gaze_vec_in_which': _get_known_recordings,
            'sync_ref_recording': _get_known_recordings,
            'sync_ref_average_recordings': _get_known_recordings_no_ref,
            'head_attached_recordings_replace_et_scene': _get_known_recordings_only_camera_head_attached,
            'interpolate_plane_pose_recordings': _get_known_recordings_only_eye_tracker,
            'session_def_et_recordings': _get_known_recordings_only_eye_tracker,
            'marker_assemblies': _get_known_assemblies,
            'auto_code': {'markers': _get_known_individual_markers, 'start_markers': _get_known_individual_markers, 'end_markers': _get_known_individual_markers},
            'which_recordings': _get_known_recordings
        }

        self._need_set_window_title = True
        self._project_settings_pane.is_visible = True
        self._action_list_pane.is_visible = True
        # trigger update so visibility change is honored
        self._window_list = [self._sessions_pane, self._project_settings_pane, self._action_list_pane]
        self._to_focus = self._window_identity(self._sessions_pane.label)  # ensure sessions pane remains focused

    def _get_possible_value_getter_for_event(self, event: str):
        cs = [cs for cs in self.study_config.coding_setup if cs['name']==event]
        if len(cs)!=1:
            return {}
        cs = cs[0]
        # get plane assemblies and target IDs
        planes = {p:pc for p in cs['marker_assemblies'] if p in self.assembly_configs and isinstance((pc:=self.assembly_configs[p]),gt_plane.Plane)}
        target_planes = {p:pc for p,pc in planes.items() if isinstance(pc,gt_plane.TargetPlane)}
        if not planes:
            return {}
        out = {'trial_data_quality_setup': {'planes': lambda: set(planes)}}
        if target_planes:
            # NB: use immediately invoked lambda to capture p correctly (avoid late binding issues)
            out['gaze_offset_setup'] = {p: {'which_targets': (lambda x: lambda: set(target_planes[x].get_target_IDs()))(p)} for p in target_planes}
        return out

    def _reload_sessions(self):
        sessions = session.get_sessions_from_project_directory(self.project_dir, self.study_config.session_def)
        with self._sessions_lock:
            self.sessions.clear()
            self.sessions |= {s.name:s for s in sessions}
            selected = self._selected_sessions.copy()
            self._selected_sessions.clear()
            self._selected_sessions |= {k:(selected[k] if k in selected else False) for k in self.sessions}
        self._update_shown_actions_for_config()
        self._session_lister_set_extra_columns_to_show()    # only place we have to add this. Adding or deleting a session def from the study config triggers the current function

    def _reload_marker_assemblies(self):
        self._invalidate_project_setup_state()
        self.assembly_configs.clear()
        self._assembly_preview_cache.clear()
        for p_def in self.study_config.marker_assemblies:
            self._load_marker_assembly(p_def)
    def _load_marker_assembly(self, p_def: marker_assembly.Definition, defer_image_upload=False):
        self._invalidate_project_setup_state()
        config_dir = config.guess_config_dir(self.study_config.working_directory)
        if not p_def.has_complete_setup(config_dir / p_def.name):
            self.assembly_configs[p_def.name] = None
            self._assembly_preview_cache.pop(p_def.name,None)
            return

        try:
            self.assembly_configs[p_def.name] = marker_assembly.get_marker_assembly_from_definition(p_def, config_dir/p_def.name)
        except Exception as exc:
            self.assembly_configs[p_def.name] = exc
        else:
            if isinstance(self.assembly_configs[p_def.name], gt_marker_assembly.MarkerAssembly):
                if defer_image_upload:
                    self._assembly_preview_cache[p_def.name] = 'needs_upload'
                else:
                    self._assembly_preview_cache[p_def.name] = utils.load_image_with_helper(self.assembly_configs[p_def.name].get_ref_image(as_RGB=True))
    def _upload_assembly_image(self, p_def: marker_assembly.Definition):
        self._assembly_preview_cache[p_def.name] = utils.load_image_with_helper(self.assembly_configs[p_def.name].get_ref_image(as_RGB=True))
    def _remove_marker_assembly(self, p_def: marker_assembly.Definition):
        self._invalidate_project_setup_state()
        callbacks.delete_marker_assembly(self.study_config, p_def)
        self.study_config.marker_assemblies = [p for p in self.study_config.marker_assemblies if p.name!=p_def.name]
        self.assembly_configs.pop(p_def.name, None)
        self._assembly_preview_cache.pop(p_def.name, None)

    def _reload_calibrations(self):
        self._invalidate_project_setup_state()
        self.cam_calibrations.clear()
        for r in self.study_config.session_def.recordings:
            self._load_calibration(r)
        self._reload_recording_calibrations()
    def _load_calibration(self, r_def: session.RecordingDefinition):
        self._invalidate_project_setup_state()
        cal_path = config.guess_config_dir(self.study_config.working_directory) / f'{r_def.name}_{session.RecordingDefinition.cal_file_name}'
        self.cam_calibrations[r_def.name] = camera_calibration.inspect(cal_path, self._calibration_requires_extrinsics(r_def))
    def _remove_calibration(self, r_def: session.RecordingDefinition):
        self._load_calibration(r_def)

    def _calibration_requires_extrinsics(self, recording: session.RecordingDefinition) -> bool:
        return (
            recording.type == session.RecordingType.Camera
            and recording.camera_recording_type == camera_recording.Type.Head_attached
            and self.study_config.head_attached_recordings_replace_et_scene is not None
            and recording.name in self.study_config.head_attached_recordings_replace_et_scene
        )

    def _load_recording_calibration(self, session_name: str, recording: session.Recording):
        working_dir = pathlib.Path(recording.info.working_directory) if recording.info is not None and recording.info.working_directory is not None else None
        cal_path = working_dir / gt_naming.scene_camera_calibration_fname if working_dir is not None else None
        key = (session_name, recording.definition.name)
        inspection = camera_calibration.inspect(cal_path, self._calibration_requires_extrinsics(recording.definition))
        self._recording_calibrations[key] = inspection
        self._invalidate_session_problem_cache(session_name)

    def _reload_recording_calibrations(self):
        self._recording_calibrations.clear()
        for session_name, sess in self.sessions.items():
            self._load_session_recording_calibrations(session_name, sess)

    def _load_session_recording_calibrations(self, session_name: str, sess: session.Session):
        for recording in sess.recordings.values():
            self._load_recording_calibration(session_name, recording)

    def _session_calibration_inspections(self, sess: session.Session) -> list[tuple[str, camera_calibration.Inspection]]:
        return [
            (name, self._recording_calibrations[(sess.name, name)])
            for name in sess.recordings
            if (sess.name, name) in self._recording_calibrations
        ]

    @staticmethod
    def _flatten_problem_dict(problems: type_utils.ProblemDict, path: tuple[str, ...] = ()) -> list[tuple[type_utils.ProblemLevel, str]]:
        entries = []
        for key, problem in problems.items():
            current_path = path if key == 'problem_with_this_key' else path + (str(key),)
            if isinstance(problem, tuple):
                label = ' > '.join(current_path)
                diagnostic = f'{label}: {problem[1]}' if label and problem[1] else problem[1] or label
                entries.append((problem[0], diagnostic))
            else:
                entries.extend(GUI._flatten_problem_dict(problem, current_path))
        return entries

    @staticmethod
    def _new_problem_entries(problems: type_utils.ProblemDict, inherited_problems: type_utils.ProblemDict) -> list[tuple[type_utils.ProblemLevel, str]]:
        inherited = set(GUI._flatten_problem_dict(inherited_problems))
        return [entry for entry in GUI._flatten_problem_dict(problems) if entry not in inherited]

    @staticmethod
    def _problem_entries_level(entries: list[tuple[type_utils.ProblemLevel, str]]) -> type_utils.ProblemLevel | None:
        return max((level for level, _ in entries), key=lambda level: level.value, default=None)

    def _ensure_session_override_state(self, sess: session.Session):
        if sess.name not in self.session_config_overrides:
            self.session_config_overrides[sess.name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)
            self._session_dict_type_rec.setdefault(sess.name, {-1: {}})
            self._refresh_session_override_state(sess)
            effective_config = self._get_effective_session_config(sess.name)
            self._refresh_recording_override_state(sess, effective_config)
            return

        self._session_dict_type_rec.setdefault(sess.name, {-1: {}})
        self.recording_config_overrides.setdefault(sess.name, {})
        self._recording_dict_type_rec.setdefault(sess.name, {})
        effective_config = self._get_effective_session_config(sess.name)
        for recording_name in sess.recordings:
            if recording_name not in self.recording_config_overrides[sess.name]:
                self._refresh_one_recording_override_state(sess, recording_name, effective_config)

    def _session_override_problem_entries(self, sess: session.Session) -> list[tuple[type_utils.ProblemLevel, str]]:
        if self.study_config is None or not hasattr(self.study_config, 'field_problems'):
            return []
        self._ensure_session_override_state(sess)
        problems = self._session_override_field_problems(sess.name, self._get_effective_session_config(sess.name))
        return self._new_problem_entries(problems, self.study_config.field_problems())

    def _recording_override_problem_entries(self, sess: session.Session, recording_name: str) -> list[tuple[type_utils.ProblemLevel, str]]:
        if self.study_config is None or not hasattr(self.study_config, 'field_problems'):
            return []
        self._ensure_session_override_state(sess)
        effective_config = self._get_effective_recording_config(sess.name, recording_name)
        problems = self._recording_override_field_problems(sess.name, recording_name, effective_config)
        inherited = self._session_override_field_problems(sess.name, self._get_effective_session_config(sess.name))
        return self._new_problem_entries(problems, inherited)

    def _session_problem_entries(self, sess: session.Session) -> list[tuple[type_utils.ProblemLevel, str, str | None]]:
        if sess.name in self._session_problem_entries_cache:
            return self._session_problem_entries_cache[sess.name]

        entries = [
            (result.level, f'{name}: {result.diagnostic}', None)
            for name, result in self._session_calibration_inspections(sess)
            if result.level != type_utils.ProblemLevel.Information
        ]
        session_override_level = self._problem_entries_level(self._session_override_problem_entries(sess))
        if session_override_level is not None:
            entries.append((session_override_level, 'Problem in session overrides', None))
        recording_override_names = []
        recording_override_level = None
        for recording_name in sess.recordings:
            problem_level = self._problem_entries_level(self._recording_override_problem_entries(sess, recording_name))
            if problem_level is not None:
                recording_override_names.append(recording_name)
                if recording_override_level is None or problem_level.value > recording_override_level.value:
                    recording_override_level = problem_level
        if recording_override_level is not None:
            recording_names = ', '.join(recording_override_names)
            entries.append((recording_override_level, f'Problem in recording overrides for recordings: {recording_names}', None))
        if missing_recordings := sess.missing_recordings():
            entries.append((type_utils.ProblemLevel.Error, 'Missing recordings:\n' + '\n'.join(missing_recordings), None))
        self._session_problem_entries_cache[sess.name] = entries
        return entries

    def _recording_problem_entries(self, session_name: str, recording: session.Recording) -> list[tuple[type_utils.ProblemLevel, str, str | None]]:
        sess = self.sessions.get(session_name)
        if sess is None:
            return []
        entries = []
        result = self._recording_calibrations.get((session_name, recording.definition.name))
        if result is None:
            if recording.definition.type != session.RecordingType.Eye_Tracker:
                entries.append((type_utils.ProblemLevel.Warning, 'Calibration has not been inspected, loading...', None))
        elif result.level != type_utils.ProblemLevel.Information:
            entries.append((result.level, result.diagnostic, result.extra_info))
        override_level = self._problem_entries_level(self._recording_override_problem_entries(sess, recording.definition.name))
        if override_level is not None:
            entries.append((override_level, 'Problem in recording overrides', None))
        return entries

    def _draw_problem_status(self, entries: list[tuple[type_utils.ProblemLevel, str, str | None]]):
        if not entries:
            self._draw_calibration_status(type_utils.ProblemLevel.Information, '')
            return
        if len(entries) == 1:
            level, diagnostic, extra_info = entries[0]
            self._draw_calibration_status(level, diagnostic, extra_info=extra_info)
            return
        level = max((entry[0] for entry in entries), key=lambda item: item.value)
        icon = self._problem_indicator(level).strip()
        color = colors.error if level == type_utils.ProblemLevel.Error else colors.warning
        imgui.text_colored(color, icon)
        self._draw_calibration_tooltip_entries([
            (colors.error if item_level == type_utils.ProblemLevel.Error else colors.warning, diagnostic, extra_info)
            for item_level, diagnostic, extra_info in entries
        ])

    def _session_calibration_sort_key(self, session_name: int | str) -> int:
        sess = self.sessions.get(str(session_name))
        if sess is None:
            return 0
        return max((level.value for level, _, _ in self._session_problem_entries(sess)), default=0)

    def _draw_calibration_status(self, level: type_utils.ProblemLevel, details: str, label: str | None = None, extra_info: str | None = None):
        match level:
            case type_utils.ProblemLevel.Error:
                icon, color = ifa6.ICON_FA_CIRCLE_XMARK, colors.error
            case type_utils.ProblemLevel.Warning:
                icon, color = ifa6.ICON_FA_TRIANGLE_EXCLAMATION, colors.warning
            case _:
                icon, color = ifa6.ICON_FA_CIRCLE_CHECK, colors.ok
        imgui.text_colored(color, icon)
        if level != type_utils.ProblemLevel.Information:
            self._draw_calibration_tooltip(color, details, extra_info)
        if label is not None:
            imgui.same_line()
            imgui.text_colored(color, label)
            if level != type_utils.ProblemLevel.Information:
                self._draw_calibration_tooltip(color, details, extra_info)

    def _draw_calibration_tooltip_entries(self, entries: list[tuple[object, str, str | None]]):
        if not imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip | imgui.HoveredFlags_.delay_normal):
            return
        imgui.begin_tooltip()
        imgui.push_text_wrap_pos(min(imgui.get_font_size() * 35, imgui.get_io().display_size.x))
        for diagnostic_color, diagnostic, extra_info in entries:
            imgui.text_colored(diagnostic_color, diagnostic)
            if extra_info:
                imgui.text(extra_info)
        imgui.pop_text_wrap_pos()
        imgui.end_tooltip()

    def _draw_calibration_tooltip(self, diagnostic_color, diagnostic: str, extra_info: str | None):
        self._draw_calibration_tooltip_entries([(diagnostic_color, diagnostic, extra_info)])

    def _draw_session_calibration_status(self, sess: session.Session):
        self._draw_problem_status(self._session_problem_entries(sess))

    def _draw_recording_calibration_status(self, session_name: str, recording: session.Recording):
        self._draw_problem_status(self._recording_problem_entries(session_name, recording))

    def _recording_calibration_sort_key(self, session_name: str, recording_id: str) -> int:
        sess = self.sessions.get(session_name)
        recording = sess.recordings.get(recording_id) if sess is not None else None
        if recording is None:
            return 0
        return max((level.value for level, _, _ in self._recording_problem_entries(session_name, recording)), default=0)

    def _invalidate_session_problem_cache(self, sess_name: str | None = None):
        if sess_name is None:
            self._session_problem_entries_cache.clear()
        else:
            self._session_problem_entries_cache.pop(sess_name, None)

    def _has_coding_setup_needing_marker_assembly(self) -> bool:
        if self.study_config is None:
            return False
        return any(
            cs.get('event_type') in (annotation.EventType.Trial, annotation.EventType.Validate)
            or (
                cs.get('event_type') == annotation.EventType.Sync_ET_Data
                and (sync_setup := cs.get('sync_setup')) is not None
                and sync_setup.get('get_cam_movement_method') == 'assembly'
            )
            for cs in self.study_config.coding_setup
        )

    def _invalidate_project_setup_state(self):
        self._invalidate_session_problem_cache()
        self._setup_state_dirty = True

    def _check_project_setup_state(self):
        if not self._setup_state_dirty:
            return

        if self.study_config is None:
            self._problems_cache = {}
            self.setup_recordings_error_level = type_utils.ProblemLevel.Error
            self.setup_assembly_error_level = self.setup_coding_error_level = self.setup_individual_markers_error_level = None
            self.can_accept_sessions = False
            self._setup_state_dirty = False
            return

        self._problems_cache = self.study_config.field_problems()
        self._check_markers()
        self._check_camera_calibrations()

        def setup_error_level(*keys, invalid=False):
            if invalid:
                return type_utils.ProblemLevel.Error
            return type_utils.get_error_level({k: self._problems_cache[k] for k in keys if k in self._problems_cache})

        # need to have:
        #   1. at least one recording defined in the session
        # if you want to do more than only export gaze overlay videos, you also need to:
        #   2. one marker assembly set up
        #   3. one episode to code
        #   4. one marker assembly linked to one episode
        #   5. no individual marker problems

        # First check recording definition problems, which cannot be allowed in any case.
        # Check for a missing recording setup or trouble with the camera calibrations if any are defined.
        self.setup_recordings_error_level = setup_error_level('session_def')
        if any((self.study_config.marker_assemblies, self.study_config.coding_setup, self.study_config.individual_markers)):
            # This is not overlay-only, perform relevant checks.
            needs_marker_assembly = self._has_coding_setup_needing_marker_assembly()
            invalid_marker_assembly = (
                (needs_marker_assembly and not self.study_config.marker_assemblies)
                or any(not isinstance(self.assembly_configs.get(p.name), gt_marker_assembly.MarkerAssembly) for p in self.study_config.marker_assemblies)
            )
            self.setup_assembly_error_level = setup_error_level('marker_assemblies', invalid=invalid_marker_assembly)
            self.setup_coding_error_level = setup_error_level('coding_setup', invalid=not self.study_config.coding_setup)
            self.setup_individual_markers_error_level = setup_error_level('individual_markers', 'individual_marker_settings')
        else:
            # setup for only exporting gaze overlay videos. Clear any problems that may have been cached for the other setup types, since they are not relevant in this case.
            self.setup_assembly_error_level = self.setup_coding_error_level = self.setup_individual_markers_error_level = None
            for key in ('marker_assemblies', 'coding_setup', 'individual_markers', 'individual_marker_settings'):
                self._problems_cache.pop(key, None)

        self.can_accept_sessions = type_utils.ProblemLevel.Error not in (
            type_utils.get_error_level(self._problems_cache), self.setup_recordings_error_level,
            self.setup_assembly_error_level, self.setup_coding_error_level, self.setup_individual_markers_error_level)
        self._setup_state_dirty = False

    @typing.overload
    def _get_markers(self, use_family: typing.Literal[False] = False) -> dict[str,dict[str|int,list[gt_marker.MarkerID]]]: ...
    @typing.overload
    def _get_markers(self, use_family: typing.Literal[True]) -> dict[str,dict[str|int,list[gt_marker.MarkerFamilyID]]]: ...
    def _get_markers(self, use_family: bool = False) -> dict[str,dict[str|int,list[gt_marker.MarkerID]]] | dict[str,dict[str|int,list[gt_marker.MarkerFamilyID]]]:
        markers: dict[str,dict[str|int,list[gt_marker.MarkerID]]] = {}
        for p in self.assembly_configs:
            if isinstance(self.assembly_configs[p], gt_marker_assembly.MarkerAssembly):
                markers[p] = self.assembly_configs[p].get_marker_IDs()
        markers['xx_individual_markers_xx'] = {'markers': [gt_marker.MarkerID(m.id, m.aruco_dict_id) for m in self.study_config.individual_markers]}
        if use_family:
            return {name: {group: [m.to_family() for m in ids] for group, ids in groups.items()} for name, groups in markers.items()}
        return markers

    def _check_markers(self):
        seen_markers: set[gt_marker.MarkerFamilyID] = set()
        used_markers = self._get_markers(True)
        for s in used_markers:
            for m in used_markers[s]:
                # check unique
                seen: set[gt_marker.MarkerFamilyID] = set()
                if (duplicates := {x for x in used_markers[s][m] if x in seen or seen.add(x)}):
                    if s=='xx_individual_markers_xx':
                        msg = f'The individual markers contain'
                    else:
                        msg = f'The set of {m} markers of the {s} "assembly" contains'
                    msg += (f'{msg} duplicates: {gt_marker.format_duplicate_markers_msg(duplicates)}.' + (' Markers must be unique, fix this duplication.' if not self.study_config.allow_duplicated_markers else ''))
                    if s=='xx_individual_markers_xx':
                        type_utils.merge_problem_dicts(self._problems_cache, {'individual_markers': {d: (type_utils.ProblemLevel.Warning if self.study_config.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg) for d in duplicates}})
                    else:
                        type_utils.merge_problem_dicts(self._problems_cache, {'marker_assemblies': {s: {'marker_file' if m in ('plane', 'assembly') else 'target_file': (type_utils.ProblemLevel.Warning if self.study_config.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg)}}})
                # check no collisions
                if seen_markers.intersection(used_markers[s][m]):
                    # markers not unique, make error. Find exactly where the overlap is
                    # yes, this is bad algorithmic complexity, but it only runs in failure cases
                    for s2 in used_markers:
                        for m2 in used_markers[s2]:
                            if s2==s and m2==m:
                                continue
                            if (overlap:=set(used_markers[s2][m2]).intersection(used_markers[s][m])):
                                # format the error message
                                mark_msgs: list[str] = []
                                for sx,mx in zip((s,s2),(m,m2)):
                                    if sx=='xx_individual_markers_xx':
                                        mark_msgs.append(f'individual markers')
                                    else:
                                        lbl = f'{mx} markers' if isinstance(mx,str) else f'dynamic validation target markers (column {mx})'
                                        mark_msgs.append(f'{lbl} of the "{sx}" assembly')
                                msg = f'The following markers are encountered in the setup both as {mark_msgs[0]} and as {mark_msgs[1]}: {gt_marker.format_duplicate_markers_msg(overlap)}.' + (' Markers must be unique, fix this duplication.' if not self.study_config.allow_duplicated_markers else '')
                                # add error message to problem dict
                                if 'xx_individual_markers_xx' in (s,s2):
                                    type_utils.merge_problem_dicts(self._problems_cache, {'individual_markers': {d: (type_utils.ProblemLevel.Warning if self.study_config.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg) for d in overlap if d in used_markers['xx_individual_markers_xx']['markers']}})
                                for sx,mx in zip((s,s2),(m,m2)):
                                    if sx!='xx_individual_markers_xx':
                                        type_utils.merge_problem_dicts(self._problems_cache, {'marker_assemblies': {sx: {'marker_file' if mx in ('plane', 'assembly') else 'target_file': (type_utils.ProblemLevel.Warning if self.study_config.allow_duplicated_markers else type_utils.ProblemLevel.Error, msg)}}})
                seen_markers.update(used_markers[s][m])

    def _check_camera_calibrations(self):
        for recording_name, inspection in self.cam_calibrations.items():
            if inspection.level == type_utils.ProblemLevel.Information:
                continue
            message = '\n'.join(inspection.messages)
            type_utils.merge_problem_dicts(self._problems_cache, {'session_def': {recording_name: (inspection.level, message)}})

    def _session_lister_set_actions_to_show(self, lister: session_lister.List):
        if self.study_config is None:
            lister.set_actions_to_show(set())
            return

        self._session_actions = process.get_actions_for_config(self.study_config, exclude_session_level=False)
        lister.set_actions_to_show(self._session_actions)

    def _recording_lister_set_actions_to_show(self, lister: gt_gui.recording_table.RecordingTable, sess: str):
        cfg = self._get_effective_session_config(sess)
        actions = process.get_actions_for_config(cfg, exclude_session_level=True)
        def _draw_status(action: process.Action, item: session.Recording):
            if process.is_action_possible_for_recording(item.definition.name, item.definition.type, action, cfg):
                progress = None
                if item.state[action]==process_pool.State.Running and item.info.working_directory and (job_desc:=self._get_active_job(action, item.info.working_directory.parent.name, item.definition.name)[1]):
                    progress = job_desc.progress.get_progress() if job_desc.progress is not None else None
                gt_gui.utils.draw_process_state(item.state[action], progress=progress)
            else:
                imgui.text('-')
        def _get_sort_value(action: process.Action, iid: int):
            item = self.sessions[sess].recordings[iid]
            if process.is_action_possible_for_recording(item.definition.name, item.definition.type, action, cfg):
                return item.state[action]
            else:
                return 999

        # build set of column, trigger column rebuild
        columns = [
            gt_gui.recording_table.ColumnSpec(1,ifa6.ICON_FA_SIGNATURE+" Recording name",imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide, lambda rec: imgui.text(rec.definition.name), lambda iid: iid, "Recording name"),
            gt_gui.recording_table.ColumnSpec(2, "Problems", imgui.TableColumnFlags_.angled_header | imgui.TableColumnFlags_.no_hide, lambda rec: self._draw_recording_calibration_status(sess, rec), lambda iid: self._recording_calibration_sort_key(sess, iid))
        ]+[
            gt_gui.recording_table.ColumnSpec(3+c, a.displayable_name, imgui.TableColumnFlags_.angled_header, lambda rec, a=a: _draw_status(a, rec), lambda iid, a=a: _get_sort_value(a, iid)) for c,a in enumerate(actions)
        ]
        lister.build_columns(columns)

    def _update_shown_actions_for_config(self):
        self._invalidate_project_setup_state()
        self._refresh_open_override_states()
        self._session_lister_set_actions_to_show(self._session_lister)
        for sess in self._recording_listers:
            self._recording_lister_set_actions_to_show(self._recording_listers[sess], sess)

    def _session_lister_set_extra_columns_to_show(self):
        columns = [session_lister.ColumnSpec(
            'Problems',
            imgui.TableColumnFlags_.angled_header | imgui.TableColumnFlags_.no_hide,
            self._draw_session_calibration_status,
            self._session_calibration_sort_key,
            before_recordings=True,
        )]
        if len(self.study_config.session_def.recordings)==1:
            # only a single recording per session, show some extra info about the recording in the session view:
            # Eye Tracker, Recording name, Participant and duration
            def _draw_status(which: str, item: session.Session):
                rec = next(iter(item.recordings.values())).info if item.recordings else None
                if rec is None:
                    imgui.text('-')
                    return
                # NB: guaranteed to be an eye tracking recording, as we only get here with single recordings
                # and there must be at least one eye tracking recording
                match which:
                    case "Eye Tracker":
                        self._et_widget_drawer.draw(rec, align=True)
                    case "Recording Name":
                        imgui.text(rec.name)
                    case "Participant":
                        imgui.text(rec.participant or "Unknown")
                    case "Duration":
                        imgui.text("Unknown" if (d:=rec.duration) is None else str(datetime.timedelta(seconds=d//1000)))
            def _get_sort_key(which: str):
                def _get_rec(iid: int, attr: str):
                    rec = next(iter(self.sessions[iid].recordings.values())).info if self.sessions[iid].recordings else None
                    if rec is None or not hasattr(rec,attr):
                        return None
                    return getattr(rec,attr)

                match which:
                    case "Eye Tracker":
                        return lambda iid: (e.value if (e:=_get_rec(iid,'eye_tracker')) is not None else 'zzzzz').lower()
                    case "Recording Name":
                        return natsort.os_sort_keygen(key=lambda iid: (n if (n:=_get_rec(iid,'name')) is not None else 'zzzzz').lower())
                    case "Participant":
                        return natsort.os_sort_keygen(key=lambda iid: (p if (p:=_get_rec(iid,'participant')) is not None else 'zzzzz').lower())
                    case "Duration":
                        return lambda iid: d if (d:=_get_rec(iid,'duration')) is not None else 999

            columns.extend([
                session_lister.ColumnSpec(lbl,
                                          imgui.TableColumnFlags_.no_resize,
                                          lambda rec,w=lbl[2:]: _draw_status(w,rec),
                                          _get_sort_key(lbl[2:]),
                                          lbl[2:])
                for lbl in (ifa6.ICON_FA_EYE+" Eye Tracker", ifa6.ICON_FA_SIGNATURE+" Recording Name", ifa6.ICON_FA_USER_TIE+" Participant", ifa6.ICON_FA_STOPWATCH+" Duration")
            ])
        self._session_lister.set_extra_columns(columns)

    def close_project(self):
        self._project_settings_pane.is_visible = False
        self._action_list_pane.is_visible = False
        # trigger update so visibility change is honored, also delete other windows in the process
        self._window_list = [self._sessions_pane, self._project_settings_pane, self._action_list_pane]

        # stop listening for session changes
        if self._session_watcher_stop_event is not None:
            self._session_watcher_stop_event.set()
        if self._session_watcher is not None:
            try:
                self._session_watcher.result()
            except:
                pass
        self._session_watcher = None
        self._session_watcher_stop_event = None
        # stop listening for setup changes
        if self._config_watcher_stop_event is not None:
            self._config_watcher_stop_event.set()
        if self._config_watcher is not None:
            try:
                self._config_watcher.result()
            except:
                pass
        self._config_watcher = None
        self._config_watcher_stop_event = None

        # defer rest of unloading until windows deleted, as some of these variables will be accessed during this draw loop
        self._after_window_update_callback = self._finish_unload_project

    def _finish_unload_project(self):
        self.job_scheduler.clear()
        self.project_dir = None
        self._last_export_preferences = None
        self._possible_value_getters = {}
        self.study_config = None
        self.assembly_configs.clear()
        self.cam_calibrations.clear()
        self.sessions.clear()
        self._selected_sessions.clear()
        self._need_set_window_title = True
        self.recording_config_overrides.clear()
        self._session_effective_config_cache.clear()
        self._session_override_problems_cache.clear()
        self._recording_effective_config_cache.clear()
        self._recording_override_problems_cache.clear()
        self._recording_listers.clear()
        self._recording_calibrations.clear()
        self._selected_recordings.clear()
        self._pending_session_removals.clear()
        self._pending_recording_removals.clear()
        self._pending_session_pane_removals.clear()
        self._pending_session_removals.clear()
        self._pending_recording_removals.clear()
        self._pending_session_pane_removals.clear()
        self._assembly_preview_cache.clear()
        self._marker_preview_cache.clear()
        self._problems_cache.clear()
        self._invalidate_project_setup_state()
        self._active_jobs_cache.clear()


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
                self._to_focus = self._window_identity(self._project_settings_pane.label)
            imgui.same_line()
            imgui.text_colored(colors.error, 'tab before you can import and process recording sessions.')
            return

        if imgui.button('+ new session'):
            callbacks.new_session_button(self)
        imgui.same_line()
        if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import eye tracker recordings'):
            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_et_recordings', sessions=[]))
        if any((r.type==session.RecordingType.Camera for r in self.study_config.session_def.recordings)):
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_FILE_IMPORT+' import camera recordings'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='add_cam_recordings', sessions=[]))
        self._session_lister.draw()

    def _unopened_interface_drawer(self):
        avail      = imgui.get_content_region_avail()
        dpi_scale  = imgui.get_window_dpi_scale()
        but_width  = 200*dpi_scale
        but_height = 100*dpi_scale

        but_x = (avail.x - 2*but_width - 10*imgui.get_style().item_spacing.x) / 2
        but_y = (avail.y - but_height) / 2

        imgui.push_font(None, imgui.get_style().font_size_base*self._big_font_size_multiplier)
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
        def _indicate_needs_attention(problem_level: type_utils.ProblemLevel):
            if problem_level==type_utils.ProblemLevel.Warning:
                imgui.push_style_color(imgui.Col_.button,         colors.warning_dark)
                imgui.push_style_color(imgui.Col_.button_hovered, colors.warning)
                imgui.push_style_color(imgui.Col_.button_active,  colors.warning_bright)
            else:
                imgui.push_style_color(imgui.Col_.button,         colors.error_dark)
                imgui.push_style_color(imgui.Col_.button_hovered, colors.error)
                imgui.push_style_color(imgui.Col_.button_active,  colors.error_bright)
        # options handled in separate panes
        new_win_params: tuple[str,Callable[[],None]] = None
        if self.setup_recordings_error_level is not None:
            _indicate_needs_attention(self.setup_recordings_error_level)
        if imgui.button("Edit session definition"):
            new_win_params = ('Session definition', self._session_definition_pane_drawer)
        if self.setup_recordings_error_level is not None:
            imgui.pop_style_color(3)
        imgui.same_line()

        if self.setup_assembly_error_level is not None:
            _indicate_needs_attention(self.setup_assembly_error_level)
        if imgui.button("Edit planes & 3D assemblies"):
            new_win_params = ('Plane & 3D Assembly Editor', self._marker_assembly_editor_pane_drawer)
        if self.setup_assembly_error_level is not None:
            imgui.pop_style_color(3)
        imgui.same_line()

        if self.setup_individual_markers_error_level is not None:
            _indicate_needs_attention(self.setup_individual_markers_error_level)
        if imgui.button("Edit individual markers"):
            new_win_params = ('Individual marker editor', self._individual_marker_setup_pane_drawer)
        if self.setup_individual_markers_error_level is not None:
            imgui.pop_style_color(3)
        imgui.same_line()

        if self.setup_coding_error_level is not None:
            _indicate_needs_attention(self.setup_coding_error_level)
        if imgui.button("Coding setup"):
            new_win_params = ('Coding setup', self._coding_setup_pane_drawer)
        if self.setup_coding_error_level is not None:
            imgui.pop_style_color(3)

        if new_win_params is not None:
            pane_id = f'project_pane:{new_win_params[0]}'
            windows = hello_imgui.get_runner_params().docking_params.dockable_windows
            new_win = next((w for w in windows if self._window_identity(w.label)==pane_id), None)
            if new_win is None:
                new_win = self._make_main_space_window(*new_win_params, can_be_closed=True, window_id=pane_id)
                if not self._window_list:
                    self._window_list = windows
                self._window_list.append(new_win)
                self._to_dock.append(pane_id)
            self._to_focus = pane_id

        # rest of settings handled here in a settings tree
        if any((k not in ['session_def', 'marker_assemblies', 'coding_setup', 'individual_markers'] for k in self._problems_cache)):
            imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved.\nHover over red text to get information about the error')

        fields = [k for k in config.study_parameter_types.keys() if k in config.study_defaults and k != 'individual_marker_settings']
        changed, new_config, self._dict_type_rec = settings_editor.draw(copy.deepcopy(self.study_config), fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, None, self._dict_type_rec, self._problems_cache, config.study_parameter_doc)
        if changed:
            try:
                new_config.check_valid(strict_check=False)
            except Exception as exc:
                # do not persist invalid config, inform user of problem
                gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
            else:
                # persist changed config
                self.study_config = new_config
                self.study_config.store_as_json()
                self._update_shown_actions_for_config()
                self.process_pool.set_num_workers(self.study_config.gui_num_workers)

    def _action_list_pane_drawer(self):
        changed, new_config, _ = settings_editor.draw(copy.deepcopy(self.study_config), ['gui_num_workers'], config.study_parameter_types, config.study_defaults, self._possible_value_getters, None, None, self._problems_cache, config.study_parameter_doc)
        if changed:
            self.study_config = new_config
            self.study_config.store_as_json()
            self._invalidate_project_setup_state()
            self.process_pool.set_num_workers(self.study_config.gui_num_workers)

        if not self.job_scheduler.jobs:
            imgui.text('No actions have been enqueued or performed')
            return

        # gather all jobs. copy to avoid concurrency issues
        jobs = self.job_scheduler.jobs.copy()
        job_ids = sorted(jobs.keys())
        job_states = {job_id:jobs[job_id].get_state() for job_id in job_ids}
        if any((job_states[i] in [process_pool.State.Pending, process_pool.State.Running] for i in job_states)):
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
        if imgui.begin_table(f"##processing_queue",columns=6,flags=table_flags):
            imgui.table_setup_column("ID", imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 0
            imgui.table_setup_column("Status", imgui.TableColumnFlags_.no_hide)  # 1
            imgui.table_setup_column("Session", imgui.TableColumnFlags_.no_hide)  # 2
            imgui.table_setup_column("Recording", imgui.TableColumnFlags_.no_hide)  # 3
            imgui.table_setup_column("Action", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_hide)  # 4
            imgui.table_setup_column("Waiting / dependencies", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_hide)  # 5
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
                    case 5:     # dependencies
                        key = lambda idx: tuple(sorted(jobs[idx].depends_on or ()))

                job_ids.sort(key=key, reverse=sort_spec.get_sort_direction()==imgui.SortDirection.descending)

            # render actions
            for job_id in job_ids:
                imgui.table_next_row()

                for ci in range(6):
                    if not (imgui.table_get_column_flags(ci) & imgui.TableColumnFlags_.is_enabled):
                        continue
                    imgui.table_set_column_index(ci)

                    match ci:
                        case 0:
                            # ID
                            imgui.text(f'{job_id}')
                        case 1:
                            # Status
                            job_state = jobs[job_id].get_state()
                            progress = jobs[job_id].progress.get_progress() if job_state==process_pool.State.Running and jobs[job_id].progress is not None else None
                            job_warnings = jobs[job_id].warnings
                            self._draw_job_queue_state(job_state, progress, job_warnings, jobs[job_id].error)
                            if jobs[job_id].error:
                                imgui.same_line()
                                if imgui.small_button(ifa6.ICON_FA_COPY+f'##{job_id}_copy_error'):
                                    imgui.set_clipboard_text(jobs[job_id].error)
                                gt_gui.utils.draw_hover_text('Copy error to clipboard', text='')
                            if job_warnings:
                                imgui.same_line()
                                if imgui.small_button(ifa6.ICON_FA_COPY+f'##{job_id}_copy_warnings'):
                                    warning_details = '\n\n'.join(self._format_job_warning_details(warning, index) for index, warning in enumerate(job_warnings, start=1))
                                    imgui.set_clipboard_text(warning_details)
                                gt_gui.utils.draw_hover_text('Copy warnings to clipboard', text='')
                            if job_state in [process_pool.State.Pending, process_pool.State.Running]:
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
                        case 5:
                            # wait reasons and dependency states
                            waiting_reasons = self.job_scheduler.get_waiting_reasons(job_id) if job_states[job_id]==process_pool.State.Pending else []
                            if waiting_reasons:
                                imgui.text(waiting_reasons[0])
                                gt_gui.utils.draw_hover_text('- ' + ('\n- '.join(waiting_reasons)), text='')
                            for dependency_index, dependency_id in enumerate(sorted(jobs[job_id].depends_on or ())):
                                if waiting_reasons or dependency_index:
                                    imgui.same_line()
                                dependency = jobs.get(dependency_id)
                                if dependency is None:
                                    dependency_state = process_pool.State.Failed
                                    dependency_text = f'Job #{dependency_id}: missing prerequisite'
                                else:
                                    dependency_state = dependency.get_state()
                                    dependency_owner = dependency.user_data
                                    dependency_text = f'Job #{dependency_id}: {dependency_state.displayable_name} - {dependency_owner.action.displayable_name}, {dependency_owner.session}'
                                    if dependency_owner.recording:
                                        dependency_text += f', {dependency_owner.recording}'
                                dependency_icon = {
                                    process_pool.State.Completed: ifa6.ICON_FA_CIRCLE_CHECK,
                                    process_pool.State.Pending: ifa6.ICON_FA_CLOCK,
                                    process_pool.State.Running: ifa6.ICON_FA_PLAY,
                                    process_pool.State.Canceled: ifa6.ICON_FA_CIRCLE_XMARK,
                                    process_pool.State.Failed: ifa6.ICON_FA_TRIANGLE_EXCLAMATION,
                                }.get(dependency_state, ifa6.ICON_FA_MINUS)
                                imgui.text(dependency_icon)
                                gt_gui.utils.draw_hover_text(dependency_text, text='')

            imgui.end_table()


    def _session_definition_pane_drawer(self):
        if not self.study_config.session_def.recordings:
            imgui.text_colored(colors.error,'*At minimum one recording should be defined')
        if 'session_def' in self._problems_cache and (not isinstance(self._problems_cache['session_def'],dict) or 'problem_with_this_key' in self._problems_cache['session_def']):
            problem = self._problems_cache['session_def']['problem_with_this_key'] if isinstance(self._problems_cache['session_def'],dict) else self._problems_cache['session_def']
            imgui.text_colored(colors.error if problem[0]==type_utils.ProblemLevel.Error else colors.warning, f"*{problem[1]}")
        have_camera_recordings = any((r.type==session.RecordingType.Camera for r in self.study_config.session_def.recordings))
        table_is_started = imgui.begin_table(f"##session_def_list", 3+2*have_camera_recordings)
        if not table_is_started:
            return
        imgui.table_setup_column("Recording", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Type", imgui.TableColumnFlags_.width_fixed)
        if have_camera_recordings:
            imgui.table_setup_column("Camera type", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column("Associated recording", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Camera calibration", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        config_path = config.guess_config_dir(self.study_config.working_directory)
        changed = False
        for r in self.study_config.session_def.recordings:
            problem = None if 'session_def' not in self._problems_cache or not isinstance(self._problems_cache['session_def'],dict) else self._problems_cache['session_def'].get(r.name,None)
            calibration = self.cam_calibrations.get(r.name)
            if calibration is not None and calibration.level == type_utils.ProblemLevel.Error:
                msg = 'There is a problem with the configured calibration'
                if problem is None:
                    problem = (type_utils.ProblemLevel.Error, msg)
                else:
                    assert isinstance(problem, tuple)
                    problem = type_utils.merge_problem_messages(problem, (type_utils.ProblemLevel.Error, msg))
            imgui.table_next_row()
            imgui.table_next_column()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f'##{r.name}'):
                callbacks.delete_recording_definition(self.study_config, r)
                self._reload_sessions()
                self._reload_calibrations()
            imgui.same_line()
            if problem is not None:
                imgui.push_style_color(imgui.Col_.text, colors.error if problem[0]==type_utils.ProblemLevel.Error else colors.warning)
            imgui.text(r.name)
            if problem is not None:
                gt_gui.utils.draw_hover_text(problem[1], '')
                imgui.pop_style_color()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            imgui.text(r.type.value)
            if have_camera_recordings:
                imgui.table_next_column()
                if r.type==session.RecordingType.Camera:
                    new_val = settings_editor.draw_value(f'recording_type_{r.name}', r.camera_recording_type, camera_recording.Type, tuple(), False, None, None, False, {}, False, is_none=r.camera_recording_type==None)[0]
                    if r.camera_recording_type!=new_val:
                        r.camera_recording_type = new_val
                        changed = True
                    imgui.table_next_column()
                    if r.camera_recording_type==camera_recording.Type.Head_attached or r.associated_recording is not None:
                        new_val = settings_editor.draw_value(f'assoc_recording_{r.name}', r.associated_recording, typing.Literal[tuple(self._possible_value_getters['session_def_et_recordings']())], tuple(), True, None, None, False, {}, False, is_none=r.associated_recording==None, base_type=typing.Literal)[0]
                        if r.associated_recording!=new_val:
                            r.associated_recording = new_val
                            changed = True
                    else:
                        imgui.align_text_to_frame_padding()
                        imgui.text('-')
                else:
                    imgui.align_text_to_frame_padding()
                    imgui.text('-')
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    imgui.text('-')
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            if calibration is None or calibration.level == type_utils.ProblemLevel.Warning:
                label = 'Default camera calibration not set'
                diagnostic = calibration.diagnostic if calibration is not None else label
                if r.type == session.RecordingType.Eye_Tracker:
                    diagnostic += '\nThis is not a problem for most eye trackers, as the camera calibration is retrieved from the recording upon import'
                self._draw_calibration_status(
                    type_utils.ProblemLevel.Warning,
                    diagnostic,
                    label,
                    calibration.extra_info if calibration is not None else None,
                )
            else:
                self._draw_calibration_status(calibration.level, calibration.diagnostic, calibration.label, calibration.extra_info)
                imgui.same_line()
                if calibration.path is not None and calibration.path.is_file() and imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete calibration##{r.name}'):
                    r.remove_default_cal_file(config_path)
                    self._load_calibration(r)
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_DOWNLOAD+f' select calibration xml##cal_{r.name}'):
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='set_default_cam_cal', rec_def=r, rec_def_path=config_path))
        imgui.end_table()
        if changed:
            self.study_config.store_as_json()
            self._update_shown_actions_for_config()
        if imgui.button('+ new recording'):
            new_rec_name = ''
            new_rec_type: session.RecordingType = None
            def _valid_rec_name():
                if not new_rec_name:
                    return False, 'Recording name cannot be empty'
                if not pathvalidate.is_valid_filename(new_rec_name, "auto"):
                    return False, 'Recording name is not a valid filename'
                if any(r.name.casefold()==new_rec_name.casefold() for r in self.study_config.session_def.recordings):
                    return False, 'A recording with this name already exists, choose a unique name'
                return True, ''
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
                    valid = _valid_rec_name()[0]
                    if not valid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Recording name")
                    if not valid:
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
                valid, reason = _valid_rec_name()
                if not valid:
                    imgui.text_colored(colors.error, reason)
                if new_rec_type is None:
                    imgui.text_colored(colors.error, 'Select a recording type')

            buttons = {
                ifa6.ICON_FA_CHECK+" Create recording": (lambda: (callbacks.make_recording_definition(self.study_config, new_rec_type, new_rec_name), self._reload_sessions(), self._reload_calibrations()), lambda: not _valid_rec_name()[0] or new_rec_type is None),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add recording", _add_rec_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, outside=False))

    def _marker_assembly_editor_pane_drawer(self):
        if self.setup_assembly_error_level is not None:
            if self.setup_assembly_error_level==type_utils.ProblemLevel.Error:
                imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved.\nHover over red text to get information about the error.')
            else:
                imgui.text_colored(colors.warning,'*There are potential problems in the below setup.\nHover over yellow text to get information about the warning.')
        if self.setup_assembly_error_level is not None and (not self.study_config or not self.study_config.marker_assemblies):
            has_coding_setup_needing_assembly = self._has_coding_setup_needing_marker_assembly()
            has_individual_markers = not not self.study_config.individual_markers
            if has_coding_setup_needing_assembly and has_individual_markers:
                imgui.text_colored(colors.error,'*At minimum one marker assembly should be defined when a coding setup needing a marker assembly and individual markers are defined')
            elif has_coding_setup_needing_assembly:
                imgui.text_colored(colors.error,'*At minimum one marker assembly should be defined when a coding setup needing a marker assembly is defined')
        def _hover_img_error_popup(p_def: marker_assembly.Definition, load_error: Exception):
            if imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip|imgui.HoveredFlags_.delay_normal):
                imgui.begin_tooltip()
                if load_error:
                    imgui.text(str(load_error))
                elif isinstance(self.assembly_configs.get(p.name), gt_marker_assembly.MarkerAssembly) and p.name in self._assembly_preview_cache:
                    if isinstance(self._assembly_preview_cache[p_def.name],str) and self._assembly_preview_cache[p_def.name]=='needs_upload':
                        self._upload_assembly_image(p_def)
                    self._assembly_preview_cache[p_def.name].render(largest=400*imgui.get_window_dpi_scale())
                imgui.end_tooltip()
        for p in self.study_config.marker_assemblies:
            assembly_problems = self._problems_cache.get('marker_assemblies', {})
            problem_fields = assembly_problems.get(p.name, {}) if isinstance(assembly_problems, dict) else {}
            fixed_fields   = p.fixed_fields()
            load_error     = self.assembly_configs[p.name] if p.name in self.assembly_configs and isinstance(self.assembly_configs[p.name], Exception) else None
            extra = ''
            lbl = f'{p.name} ({p.type.value})'
            if (has_error:=type_utils.get_error_level(problem_fields) is not None or load_error is not None):
                extra = '*'
                imgui.push_style_color(imgui.Col_.text, colors.error if type_utils.get_error_level(problem_fields)==type_utils.ProblemLevel.Error or (load_error is not None) else colors.warning)
            if imgui.tree_node_ex(f'{extra}{lbl}###{lbl}', imgui.TreeNodeFlags_.framed):
                if has_error:
                    imgui.pop_style_color()
                _hover_img_error_popup(p, load_error)
                assembly_dir = config.guess_config_dir(self.study_config.working_directory)/p.name
                if p.type in (marker_assembly.Type.Plane_2D, marker_assembly.Type.Target_Plane_2D) and imgui.button(ifa6.ICON_FA_BARCODE+f' get ArUco markers'):
                    gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='deploy_aruco', ArUco_dict_id=p.aruco_dict_id, marker_border_bits=p.marker_border_bits))
                changed, _, new_p, _, _ = settings_editor.draw_dict_editor(copy.deepcopy(p), type(p), 0, {}, list(marker_assembly.definition_parameter_types[p.type].keys()), marker_assembly.definition_parameter_types[p.type], marker_assembly.definition_defaults[p.type], problems=problem_fields, fixed=fixed_fields, documentation=marker_assembly.definition_parameter_doc)
                if changed:
                    # persist changed config
                    new_p.store_as_json(assembly_dir)
                    if new_p.type==marker_assembly.Type.GlassesValidator and not new_p.use_default:
                        callbacks.glasses_validator_plane_check_or_deploy_config(self.study_config, new_p)
                    # NB: reloading of marker assembly happens in change watcher
                if imgui.button(ifa6.ICON_FA_FOLDER_OPEN+' open marker assembly configuration folder'):
                    callbacks.open_folder(self, assembly_dir)
                imgui.same_line()
                if p.type==marker_assembly.Type.GlassesValidator:
                    p = typing.cast(marker_assembly.Definition_GlassesValidator, p)
                    if p.is_dynamic:
                        if p.use_default and imgui.button(ifa6.ICON_FA_TICKET+f' get glassesValidator dynamic script'):
                            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='deploy_gv_dynamic_script'))
                        elif not p.use_default and imgui.button(ifa6.ICON_FA_FILE_IMPORT+f' import dynamic setup'):
                            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='gv_dynamic_import_setup', p_def=p))
                    else:
                        imgui.same_line()
                        if p.use_default and imgui.button(ifa6.ICON_FA_TICKET+f' get glassesValidator poster pdf'):
                            gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='deploy_gv_poster_pdf'))
                        elif not p.use_default and imgui.button(ifa6.ICON_FA_CLIPBOARD_LIST+f' deploy default config'):
                            callbacks.glasses_validator_deploy_config(self, p)
                imgui.same_line()
                if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete plane or 3D marker assembly###{lbl}'):
                    self._remove_marker_assembly(p)
                imgui.tree_pop()
            else:
                if has_error:
                    imgui.pop_style_color()
                _hover_img_error_popup(p, load_error)
        if imgui.button('+ new plane or 3D marker assembly'):
            new_assembly_name = ''
            new_assembly_type: marker_assembly.Type|None = None
            new_validation_is_dynamic = False
            new_validation_config_file: str = ''
            def _valid_assembly_name():
                if not new_assembly_name.strip():
                    return False, 'Plane or marker assembly name cannot be empty'
                if not pathvalidate.is_valid_filename(new_assembly_name, "auto"):
                    return False, 'Plane or marker assembly name is not a valid filename'
                if any(p.name.casefold()==new_assembly_name.casefold() for p in self.study_config.marker_assemblies):
                    return False, 'A plane or marker assembly with this name already exists, choose a unique name'
                return True, ''
            def _valid_validation_config_file():
                if new_assembly_type != marker_assembly.Type.GlassesValidator or not new_validation_is_dynamic or not new_validation_config_file:
                    return True, ''
                path = pathlib.Path(new_validation_config_file)
                if path.suffix != '.json':
                    return False, 'Dynamic validation config file must have a .json extension'
                if not path.is_file():
                    return False, 'Dynamic validation config file does not exist or is not a file'
                return True, ''
            def _add_marker_assembly_popup():
                nonlocal new_assembly_name
                nonlocal new_assembly_type
                nonlocal new_validation_is_dynamic
                nonlocal new_validation_config_file
                imgui.dummy(((30+30*(new_assembly_type==marker_assembly.Type.GlassesValidator))*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_marker_assembly_info",2):
                    imgui.table_setup_column("##new_marker_assembly_info_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_marker_assembly_info_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_assembly_name()[0]
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Plane/assembly name")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_assembly_name = imgui.input_text("##new_marker_assembly_name",new_assembly_name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = new_assembly_type is None
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Plane/assembly type")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    p_idx = marker_assembly.types.index(new_assembly_type) if new_assembly_type is not None else -1
                    _,p_idx = imgui.combo("##marker_assembly_type_selector", p_idx, [marker_assembly.type_display_names[p] for p in marker_assembly.types])
                    new_assembly_type = None if p_idx==-1 else marker_assembly.types[p_idx]

                    if new_assembly_type==marker_assembly.Type.GlassesValidator:
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        imgui.text('Dynamic validation procedure?')
                        imgui.table_next_column()
                        _, new_validation_is_dynamic = imgui.checkbox('##validation_is_dynamic', new_validation_is_dynamic)
                        if new_validation_is_dynamic:
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.align_text_to_frame_padding()
                            invalid = not _valid_validation_config_file()[0]
                            if invalid:
                                imgui.push_style_color(imgui.Col_.text, colors.error)
                            imgui.text('Config file')
                            if invalid:
                                imgui.pop_style_color()
                            gt_gui.utils.draw_hover_text('If not selected, the default dynamic validation procedure will be used','')
                            imgui.table_next_column()
                            but_txt = 'Select file'
                            imgui.set_next_item_width(-imgui.get_style().item_spacing.x-2*imgui.get_style().frame_padding.x-imgui.calc_text_size(but_txt).x)
                            _, new_validation_config_file = imgui.input_text('##validation_config_file', new_validation_config_file)
                            imgui.same_line()
                            if imgui.button(but_txt):
                                def sel_callback(path: pathlib.Path):
                                    nonlocal new_validation_config_file
                                    new_validation_config_file = str(path)
                                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self,'new_validation_config_dir', callback=sel_callback))
                    imgui.end_table()
                valid, reason = _valid_assembly_name()
                if not valid:
                    imgui.text_colored(colors.error, reason)
                if new_assembly_type is None:
                    imgui.text_colored(colors.error, 'Select a plane/assembly type')
                valid, reason = _valid_validation_config_file()
                if not valid:
                    imgui.text_colored(colors.error, reason)

            buttons = {
                ifa6.ICON_FA_CHECK+" Create plane or assembly": (lambda: callbacks.make_marker_assembly(self, new_assembly_type, new_assembly_name, new_validation_is_dynamic, new_validation_config_file), lambda: not _valid_assembly_name()[0] or new_assembly_type is None or not _valid_validation_config_file()[0]),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add plane or assembly", _add_marker_assembly_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, outside=False))

    @staticmethod
    def _selectable_coding_event_types() -> list[annotation.EventType]:
        return [event_type for event_type in annotation.event_types if event_type not in annotation.internal_types]

    def _coding_setup_pane_drawer(self):
        if self.setup_coding_error_level is not None:
            if self.setup_coding_error_level==type_utils.ProblemLevel.Error:
                imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved.\nHover over red text to get information about the error.')
            else:
                imgui.text_colored(colors.warning,'*There are potential problems in the below setup.\nHover over yellow text to get information about the warning.')
        if self.setup_coding_error_level is not None and (not self.study_config or not self.study_config.coding_setup):
            has_marker_assembly = not not self.study_config.marker_assemblies
            has_individual_markers = not not self.study_config.individual_markers
            if has_marker_assembly and has_individual_markers:
                imgui.text_colored(colors.error,'*At minimum one coding setup should be defined when marker assemblies and individual markers are defined')
            elif has_marker_assembly:
                imgui.text_colored(colors.error,'*At minimum one coding setup should be defined when a marker assembly is defined')
            elif has_individual_markers:
                imgui.text_colored(colors.error,'*At minimum one coding setup should be defined when individual markers are defined')
        fields = [f for f in config.event_setup_field_order if f in config.EventSetup.__optional_keys__] + [f for f in config.EventSetup.__optional_keys__ if f not in config.event_setup_field_order]
        fixed_fields = type_utils.NestedDict({k:None for k in ['name', 'event_type']})
        marked_for_deletion = []
        for i,cs in enumerate(self.study_config.coding_setup):
            problem_fields = None
            if 'coding_setup' in self._problems_cache and i in self._problems_cache['coding_setup']:
                problem_fields = self._problems_cache['coding_setup'][i]
            extra = ''
            lbl = f"{cs['name']} ({annotation.tooltip_map[cs['event_type']]})"
            if (has_error:=type_utils.get_error_level(problem_fields or {}) is not None):
                extra = '*'
                imgui.push_style_color(imgui.Col_.text, colors.error if type_utils.get_error_level(problem_fields)==type_utils.ProblemLevel.Error else colors.warning)
            imgui.push_id(i)
            if imgui.tree_node_ex(f'{extra}{lbl}###coding_setup_tree', imgui.TreeNodeFlags_.framed):
                if has_error:
                    imgui.pop_style_color()
                changed, new_coding_config, self._coding_setup_dict_type_rec[cs['name']] = settings_editor.draw(
                    copy.deepcopy(self.study_config.coding_setup[i]),
                    fields,
                    config.EventSetup.__annotations__ | {'auto_code': typing.Union[config.AutoCodeEpisodes if annotation.type_map[cs['event_type']]==annotation.Type.Interval else config.AutoCodeSyncPoints,None]},
                    config.EventSetup._field_defaults,
                    self._possible_value_getters|self._get_possible_value_getter_for_event(cs['name']),
                    actual_types=self._coding_setup_dict_type_rec.setdefault(cs['name'], {}), problems=problem_fields, fixed=fixed_fields, documentation=config.event_setup_doc)
                if changed:
                    try:
                        new_config = copy.deepcopy(self.study_config)
                        new_config.coding_setup[i] = new_coding_config
                        new_config.check_valid(strict_check=False)
                    except Exception as exc:
                        # do not persist invalid config, inform user of problem
                        gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the project's settings:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
                    else:
                        # persist changed config
                        self.study_config = new_config
                        self.study_config.store_as_json()
                        self._update_shown_actions_for_config()
                if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete "{cs["name"]}" coding##delete_coding_setup'):
                    marked_for_deletion.append(i)
                imgui.tree_pop()
            else:
                if has_error:
                    imgui.pop_style_color()
            imgui.pop_id()
        if marked_for_deletion:
            for idx in sorted(marked_for_deletion, reverse=True):
                self._coding_setup_dict_type_rec.pop(self.study_config.coding_setup[idx]['name'], None)
                del self.study_config.coding_setup[idx]
            self.study_config.store_as_json()
            self._update_shown_actions_for_config()
        if imgui.button('+ new coding'):
            new_coding_name = ''
            new_coding_type: annotation.EventType|None = None
            selectable_event_types = self._selectable_coding_event_types()
            def _valid_coding_name():
                if not new_coding_name:
                    return False, 'Coding name cannot be empty'
                if not pathvalidate.is_valid_filename(new_coding_name, "auto"):
                    return False, 'Coding name is not a valid filename'
                if any(cs['name']==new_coding_name for cs in self.study_config.coding_setup):
                    return False, 'A coding with this name already exists, choose a unique name'
                return True, ''
            def _add_coding_popup():
                nonlocal new_coding_name
                nonlocal new_coding_type
                imgui.dummy((30*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_coding_info",2):
                    imgui.table_setup_column("##new_coding_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_coding_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_coding_name()[0]
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Coding name")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_coding_name = imgui.input_text("##new_coding_name",new_coding_name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = new_coding_type is None
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("Coding type")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    c_idx = selectable_event_types.index(new_coding_type) if new_coding_type is not None else -1
                    _,c_idx = imgui.combo("##coding_type_selector", c_idx, [annotation.tooltip_map[c] for c in selectable_event_types])
                    new_coding_type = None if c_idx==-1 else selectable_event_types[c_idx]
                    imgui.end_table()
                valid, reason = _valid_coding_name()
                if not valid:
                    imgui.text_colored(colors.error, reason)
                if new_coding_type is None:
                    imgui.text_colored(colors.error, 'Select a coding type')

            buttons = {
                ifa6.ICON_FA_CHECK+" Create coding": (lambda: callbacks.make_coding_setup(self, new_coding_name, new_coding_type), lambda: not _valid_coding_name()[0] or new_coding_type is None),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add coding", _add_coding_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, outside=False))

    def _individual_marker_setup_pane_drawer(self):
        if self.setup_individual_markers_error_level is not None:
            if self.setup_individual_markers_error_level==type_utils.ProblemLevel.Error:
                imgui.text_colored(colors.error,'*There are problems in the below setup that need to be resolved.\nHover over red text to get information about the error')
            else:
                imgui.text_colored(colors.warning,'*There are potential problems in the below setup.\nHover over yellow text to get information about the warning.')
        table_is_started = imgui.begin_table(f"##markers_def_list", 6)
        if not table_is_started:
            return
        imgui.table_setup_column("Marker ID", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Detect only", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Size", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("ArUco dictionary", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Marker border bits", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("Detection settings", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        changed = False
        marker_problems = self._problems_cache.get('individual_markers', {})
        for i,m in enumerate(self.study_config.individual_markers):
            p_key = gt_marker.MarkerID(m.id, m.aruco_dict_id).to_family()
            problem = marker_problems.get(p_key) if isinstance(marker_problems, dict) else None
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            if problem:
                imgui.push_style_color(imgui.Col_.text, colors.error if type_utils.get_error_level(problem)==type_utils.ProblemLevel.Error else colors.warning)
            imgui.set_next_item_width(-1)
            new_val = imgui.input_int(f'##id_input_{i}', m.id, 0, 0)[1]
            if (this_changed:=m.id!=new_val):
                m.id = new_val
                changed |= this_changed
            if problem:
                imgui.pop_style_color()
            problem_text = (problem[1] if isinstance(problem, tuple) else str(problem)) if problem else None
            self._draw_individual_marker_preview_tooltip(m, problem_text, create_preview=not problem)
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'detect_only_{m.id}##{i}', m.detect_only, marker.marker_parameter_types['detect_only'], tuple(), False, marker.marker_defaults.get('detect_only',None), None, False, {}, False)[0]
            if (this_changed:=m.detect_only!=new_val):
                m.detect_only = new_val
                changed |= this_changed
            imgui.table_next_column()
            imgui.set_next_item_width(imgui.calc_text_size('xxxxx.xxxxxx').x+2*imgui.get_style().frame_padding.x)
            f_type = gt_utils.unpack_none_union(marker.marker_parameter_types['size'])[0]
            new_val = settings_editor.draw_value(f'size_{m.id}##{i}', m.size, f_type, tuple(), True, marker.marker_defaults.get('size',None), None, False, {}, False, m.size is None, f_type)[0]
            if (this_changed:=m.size!=new_val):
                m.size = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'aruco_dict_id_{m.id}##{i}', m.aruco_dict_id, marker.marker_parameter_types['aruco_dict_id'], tuple(), False, marker.marker_defaults.get('aruco_dict_id',None), None, False, {}, False)[0]
            if (this_changed:=m.aruco_dict_id!=new_val):
                m.aruco_dict_id = new_val
                changed |= this_changed
            imgui.table_next_column()
            new_val = settings_editor.draw_value(f'marker_border_bits_{m.id}##{i}', m.marker_border_bits, int, tuple(), False, marker.marker_defaults['marker_border_bits'], None, False, {}, False)[0]
            gt_gui.utils.draw_hover_text(config.aruco_marker_border_doc.doc_str, text='')
            if (this_changed:=m.marker_border_bits!=new_val):
                m.marker_border_bits = new_val
                changed |= this_changed
            imgui.table_next_column()
            names = list(self.study_config.individual_marker_settings)
            selected = names.index(m.detection_settings) if m.detection_settings in names else -1
            if selected < 0:
                imgui.push_style_color(imgui.Col_.text, colors.error)
            picked, index = imgui.combo(f'##marker_detection_settings_{i}', selected, names)
            if selected < 0:
                imgui.pop_style_color()
                gt_gui.utils.draw_hover_text(f'Unknown settings group: {m.detection_settings}', text='')
            if picked and index >= 0:
                m.detection_settings = names[index]
                changed = True
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' delete marker##{m.id}##{i}'):
                callbacks.delete_individual_marker(self.study_config, None, i)
        if changed:
            self.study_config.store_as_json()
            self._update_shown_actions_for_config()
        imgui.end_table()
        if imgui.button('+ new individual marker'):
            new_mark_id = -1
            new_aruco_dict_id = aruco.default_dict
            new_mark_det_only = False
            new_mark_size = -1.
            def _valid_mark_id_dict():
                if new_mark_id < 0:
                    return False, 'Marker ID must be zero or greater'
                if new_mark_id >= aruco.get_dict_size(new_aruco_dict_id):
                    return False, f'Marker ID must be less than {aruco.get_dict_size(new_aruco_dict_id)} for the selected dictionary'
                new_family_id = gt_marker.MarkerID(new_mark_id, new_aruco_dict_id).to_family()
                if any(gt_marker.MarkerID(m.id, m.aruco_dict_id).to_family() == new_family_id for m in self.study_config.individual_markers):
                    return False, 'An individual marker with this ID already exists in the selected dictionary family'
                return True, ''
            def _valid_mark_size():
                if not new_mark_det_only and not new_mark_size > 0.:
                    return False, 'Marker size must be greater than zero unless Detect only is enabled'
                return True, ''
            def _add_marker_popup():
                nonlocal new_mark_id
                nonlocal new_mark_det_only
                nonlocal new_aruco_dict_id
                nonlocal new_mark_size
                imgui.dummy((45*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_mark_info",2):
                    imgui.table_setup_column("##new_mark_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_mark_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_mark_id_dict()[0]
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
                    invalid = not _valid_mark_id_dict()[0]
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text("ArUco dictionary")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _, new_aruco_dict_id = imgui.combo(f"##new_mark_aruco_dict", new_aruco_dict_id, list(aruco.dict_id_to_str.values()), popup_max_height_in_items=10)

                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    imgui.text("Detect only?")
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _,new_mark_det_only = imgui.checkbox("##new_mark_det_only",new_mark_det_only)
                    if not new_mark_det_only:
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        invalid = not _valid_mark_size()[0]
                        if invalid:
                            imgui.push_style_color(imgui.Col_.text, colors.error)
                        imgui.text("Marker size")
                        if invalid:
                            imgui.pop_style_color()
                        imgui.table_next_column()
                        _,new_mark_size = imgui.input_float("##new_mark_size",new_mark_size)
                    imgui.end_table()
                for valid, reason in (_valid_mark_id_dict(), _valid_mark_size()):
                    if not valid:
                        imgui.text_colored(colors.error, reason)

            buttons = {
                ifa6.ICON_FA_CHECK+" Create marker": (lambda: callbacks.make_individual_marker(self.study_config, new_mark_id, new_aruco_dict_id, new_mark_det_only, new_mark_size), lambda: not _valid_mark_id_dict()[0] or not _valid_mark_size()[0]),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup("Add marker", _add_marker_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, outside=False))

        self._marker_detection_settings_drawer()

    def _draw_individual_marker_preview_tooltip(self, m: marker.Marker, problem_text: str|None = None, create_preview=True):
        if not imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip|imgui.HoveredFlags_.delay_normal):
            return
        imgui.begin_tooltip()
        if problem_text is not None:
            imgui.text_colored(gt_gui.colors.error, problem_text)
        key = m.id, m.aruco_dict_id, m.marker_border_bits
        sz = int(200*imgui.get_window_dpi_scale())
        if key not in self._marker_preview_cache and create_preview:
            self._marker_preview_cache[key] = utils.get_aruco_marker_image(sz, *key)
        if key in self._marker_preview_cache and self._marker_preview_cache[key] is not None:
            self._marker_preview_cache[key].render(width=sz, height=sz)
        imgui.end_tooltip()

    def _marker_detection_settings_drawer(self):
        imgui.separator()
        imgui.text('Marker detection settings')
        changed = False
        all_problems = self.study_config._check_individual_marker_settings().get('individual_marker_settings', {})
        for name, params in list(self.study_config.individual_marker_settings.items()):
            problems = all_problems.get(name, {})
            if problems:
                imgui.push_style_color(imgui.Col_.text, colors.error)
            expanded = imgui.collapsing_header(f'{name}##marker_detection_group_{name}')
            if problems:
                imgui.pop_style_color()
                gt_gui.utils.draw_hover_text('\n'.join(message[1] for message in problems.values() if message[1]), text='')
            if expanded:
                imgui.push_id(name)
                if name != 'general':
                    delete = imgui.button(ifa6.ICON_FA_TRASH_CAN+' delete settings')
                    if delete:
                        del self.study_config.individual_marker_settings[name]
                        changed = True
                        imgui.pop_id()
                        continue
                edited, _, new_params, _, _ = settings_editor.draw_dict_editor(params, config.ArucoDetectorParameters, 0, {}, problems=problems, documentation=config.aruco_detector_parameter_doc)
                if edited:
                    self.study_config.individual_marker_settings[name] = new_params
                    changed = True
                imgui.pop_id()
        if changed:
            self.study_config.store_as_json()
            self._update_shown_actions_for_config()
        if imgui.button('+ new marker detection settings'):
            new_name = ''
            copy_group_names = list(self.study_config.individual_marker_settings)
            copy_group_options = ['Defaults', *copy_group_names]
            new_copy_from_idx = 0
            def valid_name():
                if not new_name.strip():
                    return False, 'Settings group name cannot be empty or contain only whitespace'
                if any(g.casefold()==new_name.strip().casefold() for g in self.study_config.individual_marker_settings):
                    return False, 'A settings group with this name already exists, choose a unique name'
                return True, ''
            def draw_new_group():
                nonlocal new_name
                nonlocal new_copy_from_idx
                imgui.dummy((30*imgui.calc_text_size('x').x, 0))
                if imgui.begin_table('##new_marker_settings_info', 2):
                    imgui.table_setup_column('##new_marker_settings_label', imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column('##new_marker_settings_value', imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not valid_name()[0]
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, colors.error)
                    imgui.text('Name')
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _, new_name = imgui.input_text('##new_marker_settings_name', new_name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    imgui.text('Copy settings from')
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    _, new_copy_from_idx = imgui.combo('##new_marker_settings_copy_from', new_copy_from_idx, copy_group_options)
                    imgui.end_table()
                valid, reason = valid_name()
                if not valid:
                    imgui.text_colored(colors.error, reason)
            def add_group():
                copy_from = None if new_copy_from_idx == 0 else copy_group_names[new_copy_from_idx - 1]
                callbacks.add_individual_marker_settings(self.study_config, new_name, copy_from)
                self._update_shown_actions_for_config()
            buttons = {
                ifa6.ICON_FA_CHECK+' Create': (add_group, lambda: not valid_name()[0]),
                ifa6.ICON_FA_CIRCLE_XMARK+' Cancel': None
            }
            gt_gui.utils.push_popup(self, lambda: gt_gui.utils.popup('Add marker detection settings', draw_new_group, buttons=buttons, button_keymap={0: imgui.Key.enter}, outside=False))

    def _marker_detection_overrides_drawer(self, current: config.Study, parent: config.Study,
                                          actual_types: dict,
                                          problems: type_utils.ProblemDict) -> bool:
        changed = False
        current_planes = {p.name: p for p in current.marker_assemblies if isinstance(p, marker_assembly.Definition_MarkerAssembly)}
        parent_planes = {p.name: p for p in parent.marker_assemblies if isinstance(p, marker_assembly.Definition_MarkerAssembly)}
        sections = {
            'marker_assemblies': ('Planes & 3D Assemblies', {name: p.aruco_settings for name, p in current_planes.items()},
                       {name: p.aruco_settings for name, p in parent_planes.items()},
                       config.ArucoSettings, config.aruco_settings_doc.children),
            'individual_marker_settings': ('Individual markers', current.individual_marker_settings,
                                           parent.individual_marker_settings,
                                           config.ArucoDetectorParameters, config.aruco_detector_parameter_doc)
        }

        def node(label, key, errors):
            if errors:
                imgui.push_style_color(imgui.Col_.text, colors.error)
            opened = imgui.tree_node_ex(f'{label}###{key}', imgui.TreeNodeFlags_.framed)
            if errors:
                imgui.pop_style_color()
            return opened

        assignment_errors = problems.get('individual_markers', {})
        has_problems = any(problems.get(field) for field in sections) or bool(problems.get('individual_markers'))
        if node('Marker detection settings', 'marker_detection_overrides', has_problems):
            for field, (label, values, inherited, cls, docs) in sections.items():
                errors = problems.get(field, {})
                if node(label, field, errors):
                    if field == 'individual_marker_settings':
                        parent_markers = {config.individual_marker_override_key(m): m for m in parent.individual_markers}
                        group_names = list(current.individual_marker_settings)
                        for m in current.individual_markers:
                            key = config.individual_marker_override_key(m)
                            inherited_marker = parent_markers.get(key)
                            marker_error = assignment_errors.get(gt_marker.MarkerID(m.id, m.aruco_dict_id).to_family()) if isinstance(assignment_errors, dict) else None
                            imgui.push_id(key)
                            if marker_error:
                                imgui.push_style_color(imgui.Col_.text, colors.error)
                            imgui.align_text_to_frame_padding()
                            imgui.text(f'{key}')
                            error_text = None
                            if marker_error:
                                imgui.pop_style_color()
                                if isinstance(marker_error, tuple):
                                    error_text = marker_error[1]
                                else:
                                    error_text = '\n'.join(message[1] for message in marker_error.values() if message[1])
                            self._draw_individual_marker_preview_tooltip(m, error_text, create_preview=not marker_error)
                            imgui.same_line()
                            selected = group_names.index(m.detection_settings) if m.detection_settings in group_names else -1
                            if selected < 0:
                                imgui.push_style_color(imgui.Col_.text, colors.error)
                            picked, index = imgui.combo('##individual_marker_group', selected, group_names)
                            if selected < 0:
                                imgui.pop_style_color()
                                gt_gui.utils.draw_hover_text(f'Unknown settings group: {m.detection_settings}', text='')
                            if picked and 0 <= index < len(group_names):
                                m.detection_settings = group_names[index]
                                changed = True
                            if inherited_marker is not None and m.detection_settings != inherited_marker.detection_settings:
                                imgui.same_line()
                                if imgui.button(' parent'):
                                    m.detection_settings = inherited_marker.detection_settings
                                    changed = True
                            imgui.pop_id()
                    for name, params in values.items():
                        field_errors = errors.get(name, {}) if isinstance(errors, dict) else {}
                        if field == 'marker_assemblies' and isinstance(field_errors, dict):
                            field_errors = field_errors.get('aruco_settings', {})
                        if not node(name, name, field_errors):
                            continue
                        # The editor's group reset must not alias the inherited settings.
                        edited, _, new_params, _, field_types = settings_editor.draw_dict_editor(
                            params, cls, 0, actual_types.setdefault(field, {}).get(name, {}),
                            parent_obj=copy.deepcopy(inherited[name]), problems=field_errors, documentation=docs)
                        actual_types[field][name] = field_types
                        if edited:
                            if field == 'marker_assemblies':
                                current_planes[name].aruco_settings = new_params
                            else:
                                current.individual_marker_settings[name] = new_params
                            changed = True
                        imgui.tree_pop()
                    imgui.tree_pop()
            imgui.tree_pop()
        return changed

    def _session_action_status(self, item: session.Session, action: process.Action):
        if process.is_session_level_action(action):
            progress = None
            if item.state[action]==process_pool.State.Running and (job_desc:=self._get_active_job(action, item.name)[1]):
                progress = job_desc.progress.get_progress() if job_desc.progress is not None else None
            gt_gui.utils.draw_process_state(item.state[action], progress=progress)
        else:
            cfg = self._get_effective_session_config(item.name) if item.name in self.session_config_overrides else self.study_config
            states = {r:item.recordings[r].state[action] for r in item.recordings if process.is_action_possible_for_recording(r, item.definition.get_recording_def(r).type, action, cfg)}
            not_completed = [r for r in states if states[r]!=process_pool.State.Completed]
            progress: dict[str,tuple[float,str]] = {}
            if any(st:={s for r in states if (s:=states[r]) in [process_pool.State.Pending, process_pool.State.Running]}):
                # progress marker
                if process_pool.State.Running in st:
                    # figure out progress, if available
                    for r in states:
                        if (job_desc:=self._get_active_job(action, item.name, r)[1]) and job_desc.progress is not None:
                            progress[r] = job_desc.progress.get_progress()
                gt_gui.utils.draw_process_state(process_pool.State.Running if process_pool.State.Running in st else process_pool.State.Pending, have_hover_popup=False, progress=(sum((progress[r][0] for r in progress))/len(progress),'') if progress else None)
            else:
                n_rec = len(states)
                clr = colors.error if not states or not_completed else colors.ok
                imgui.text_colored(clr, f'{n_rec-len(not_completed)}/{n_rec}')
            if not_completed:
                rec_strs: list[str] = []
                for r in not_completed:
                    extra = (', '+progress[r][1]) if r in progress else ''
                    rec_strs.append(f'{r} ({states[r].displayable_name}{extra})')
                gt_gui.utils.draw_hover_text('not completed for recordings:\n'+'\n'.join(rec_strs),'')

    def _session_context_menu(self, session_name: str) -> bool:
        # ignore input session name, get selected sessions
        sess = [self.sessions[s] for s in self.sessions if self._selected_sessions.get(s,False)]
        actions: dict[str, dict[process.Action,tuple[bool|list[str],dict[process.Action,list[str]]]]] = {}
        actions_running: dict[str, dict[process.Action, bool|list[str]]] = {}
        for s in sess:
            cfg = self._get_effective_session_config(s.name) if s.name in self.session_config_overrides else self.study_config
            actions[s.name] = process.get_possible_actions(s.state, {r:s.recordings[r].state for r in s.recordings}, {a for a in process.Action if a!=process.Action.IMPORT}, cfg, allow_pending=True)
            actions[s.name], actions_running[s.name] = self._filter_session_context_menu_actions(s.name, None, actions[s.name])
        # draw actions that can be run
        all_actions = {a for s in actions for a in actions[s]}
        all_actions |= {a for r in actions_running for a in actions_running[r]}
        all_actions = [a for a in process.Action if a in all_actions]   # ensure order
        def _runner(action: process.Action, which: list[tuple[str,str|None]], what, sort_order: list, *args, **kwargs):
            # sort which by GUI order, then submit
            which.sort(key=lambda x: sort_order.index(x[0]) if x[0] in sort_order else -1)
            for s,r in which:
                what(s, r, action, *args, **kwargs)
        sort_order = self._session_lister.sorted_ids
        for a in all_actions:
            running = [r for r in actions_running if a in actions_running[r]]
            val_coding_options: list[str] = []
            if running:
                possible = True
                if process.is_session_level_action(a):
                    running = [(s,None) for s in actions_running if a in actions_running[s]]
                    hover_text = f'Cancel running {a.displayable_name} for session(s):\n- '+'\n- '.join([s for s in actions_running if a in actions_running[s]])
                else:
                    running = [(s,r) for s in actions_running if a in actions_running[s] for r in actions_running[s][a]]
                    hover_text = f'Cancel running {a.displayable_name} for session(s):\n- '+'\n- '.join([f'{s}, recording(s):\n  - '+'\n  - '.join(actions_running[s][a]) for s in actions_running if a in actions_running[s]])
                icon = ifa6.ICON_FA_HAND
            else:
                if process.is_session_level_action(a):
                    to_run = [(s,None) for s in actions if a in actions[s] and actions[s][a][0]]
                    possible = not not to_run
                    if possible:
                        hover_text = f'Run {a.displayable_name} for session(s):\n- '+'\n- '.join([s for s in actions if a in actions[s] and actions[s][a][0]])
                    else:
                        hover_text = process.get_impossible_reason_text(a, actions, self.study_config)
                    status = max([self.sessions[s].state[a] for s in actions])
                else:
                    to_run = [(s,r) for s in actions if a in actions[s] for r in actions[s][a][0]]
                    possible = not not to_run
                    if possible:
                        hover_text = f'Run {a.displayable_name} for session(s):\n- '+'\n- '.join([f'{s}, recording(s):\n  - '+'\n  - '.join(actions[s][a][0]) for s in actions if a in actions[s] and actions[s][a][0]])
                    else:
                        hover_text = process.get_impossible_reason_text(a, actions, self.study_config)
                    status = [self.sessions[s].recordings[r].state[a] for s in actions if a in actions[s] for r in actions[s][a][0]]
                    status = max(status) if status else process_pool.State.Not_Run
                if a.has_options and possible:
                    hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                if a==process.Action.CODE_EPISODES and not running:
                    if (val_events:=process.get_specific_event_types(self.study_config, annotation.EventType.Validate)):
                        val_coding_options = [cs['name'] for cs in val_events]
                icon = ifa6.ICON_FA_PLAY if status<process_pool.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
            if not possible:
                imgui.begin_disabled()
            if val_coding_options:
                if imgui.begin_menu(icon+f" {a.displayable_name}"):
                    if imgui.selectable(a.displayable_name, False)[0]:
                        _runner(a, to_run, self.launch_task, sort_order)
                    for v in val_coding_options:
                        if imgui.selectable(f'Code targets for {v}', False)[0]:
                            # filter out recordings for which this validation coding episode is not set up
                            cs = [cs for cs in self.study_config.coding_setup if cs['name']==v][0]
                            if cs['which_recordings']:  # if not defined, then all recordings are valid targets
                                to_run = [(s,r) for s,r in to_run if r in cs['which_recordings']]
                            # run
                            _runner(a, to_run, self.launch_task, sort_order, val_coding_event=v)
                    imgui.end_menu()
            elif imgui.selectable(icon+f" {a.displayable_name}", False)[0]:
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
                        if a.has_options and imgui.get_io().key_shift:
                            # NB: reversed order so popup show in correct order (first is on bottom)
                            _runner(a, to_run, callbacks.show_action_options, sort_order[::-1], self)
                        else:
                            _runner(a, to_run, self.launch_task, sort_order)
            if not possible:
                imgui.end_disabled()
            gt_gui.utils.draw_hover_text(hover_text, '')
        # draw working folder interactions
        imgui.separator()
        changed = False
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open session folder", False)[0]:
            for s in sess:
                callbacks.open_folder(self, s.working_directory)
        if imgui.selectable(ifa6.ICON_FA_TRASH_CAN + " Delete session", False)[0]:
            for s in sess:
                # if any import actions are happening, cancel those first
                for r in (r for r in s.recordings if s.name in actions_running and process.Action.IMPORT in actions_running[s.name] and r in actions_running[s.name][process.Action.IMPORT]):
                    self.job_scheduler.cancel_job(actions_running[s.name][process.Action.IMPORT][r])
                callbacks.remove_folder(self, s.working_directory)
                if s.working_directory is not None and not s.working_directory.is_dir():
                    self._pending_session_removals.add(s.name)
            changed = True
        changed |= self._draw_context_menu_items_for_recording_folders(sess, None, actions_running)
        return changed
    def _recording_context_menu(self, session_name: str, rec_name: str) -> bool:
        # ignore input recording name, get selected sessions
        sess = self.sessions[session_name]
        recs = [r for r in sess.recordings if self._selected_recordings[session_name].get(r,False)]
        actions: dict[str, dict[process.Action, bool|list[str]]] = {}
        actions_running: dict[str, dict[process.Action, bool|list[str]]] = {}
        for r in recs:
            cfg = self._get_effective_session_config(session_name) if session_name in self.session_config_overrides else self.study_config
            actions[r] = process.get_possible_actions(sess.state, {r:sess.recordings[r].state}, {a for a in process.Action if a!=process.Action.IMPORT and not process.is_session_level_action(a)}, cfg, allow_pending=True)
            actions[r], actions_running[r] = self._filter_session_context_menu_actions(sess.name, r, actions[r])
        # draw actions that can be run
        all_actions = {a for r in actions for a in actions[r]}
        all_actions |= {a for r in actions_running for a in actions_running[r]}
        all_actions = [a for a in process.Action if a in all_actions]   # ensure order
        def _runner(action: process.Action, s: str, which: list[str], what, sort_order: list, *args, **kwargs):
            # sort which by GUI order, then submit
            which.sort(key=lambda x: sort_order.index(x) if x in sort_order else -1)
            for r in which:
                what(s, r, action, *args, **kwargs)
        sort_order = self._recording_listers[session_name].sorted_recordings_ids
        for a in all_actions:
            running= [r for r in actions_running if a in actions_running[r]]
            val_coding_options: list[str] = []
            if running:
                possible = True
                hover_text = f'Cancel running {a.displayable_name} for recordings:\n- '+'\n- '.join(running)
                icon = ifa6.ICON_FA_HAND
            else:
                to_run = [r for r in actions if a in actions[r] and actions[r][a][0]]
                possible = not not to_run
                if possible:
                    hover_text = f'Run {a.displayable_name} for recordings:\n- '+'\n- '.join(to_run)
                else:
                    hover_text = process.get_impossible_reason_text(a, actions, self.study_config, for_recording=True)
                status = [sess.recordings[r].state[a] for r in to_run]
                status = max(status) if status else process_pool.State.Not_Run
                if a.has_options and possible:
                    hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                if a==process.Action.CODE_EPISODES and not running:
                    if (val_events:=[cs for cs in process.get_specific_event_types(self.study_config, annotation.EventType.Validate) if not cs['which_recordings'] or any(r in cs['which_recordings'] for r in to_run)]):
                        val_coding_options = [cs['name'] for cs in val_events]
                icon = ifa6.ICON_FA_PLAY if status<process_pool.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
            if not possible:
                imgui.begin_disabled()
            if val_coding_options:
                if imgui.begin_menu(icon+f" {a.displayable_name}"):
                    if imgui.selectable(a.displayable_name, False)[0]:
                        _runner(a, session_name, to_run, self.launch_task, sort_order)
                    for v in val_coding_options:
                        if imgui.selectable(f'Code targets for {v}', False)[0]:
                            # filter out recordings for which this validation coding episode is not set up
                            cs = [cs for cs in self.study_config.coding_setup if cs['name']==v][0]
                            if cs['which_recordings']:  # if not defined, then all recordings are valid targets
                                to_run = [r for r in to_run if r in cs['which_recordings']]
                            # run
                            _runner(a, session_name, to_run, self.launch_task, sort_order, val_coding_event=v)
                    imgui.end_menu()
            elif imgui.selectable(icon+f" {a.displayable_name}##{session_name}", False)[0]:
                if running:
                    for r in running:
                        self.job_scheduler.cancel_job(actions_running[r][a])
                else:
                    if a.has_options and imgui.get_io().key_shift:
                        # NB: reversed order so popup show in correct order (first is on bottom)
                        _runner(a, session_name, to_run, callbacks.show_action_options, sort_order[::-1], self)
                    else:
                        _runner(a, session_name, to_run, self.launch_task, sort_order)
            if not possible:
                imgui.end_disabled()
            gt_gui.utils.draw_hover_text(hover_text, '')
        # draw source/working folder interactions
        imgui.separator()
        if len(recs)==1 and imgui.begin_menu('Camera calibration'):
            recording_info = sess.recordings[recs[0]].info
            working_directory = recording_info.working_directory if recording_info is not None else None
            inspection = self._recording_calibrations.get((sess.name, recs[0]))
            if inspection is not None:
                self._draw_calibration_status(inspection.level, inspection.diagnostic, inspection.label, inspection.extra_info)
            cal_file = working_directory / gt_naming.scene_camera_calibration_fname if working_directory else None
            if cal_file is not None and cal_file.is_file():
                if imgui.selectable(ifa6.ICON_FA_TRASH_CAN+' Delete calibration file', False)[0]:
                    callbacks.delete_cam_cal(working_directory)
            if working_directory and imgui.selectable(ifa6.ICON_FA_DOWNLOAD+' Set calibration XML', False)[0]:
                gt_gui.utils.push_popup(self, callbacks.get_folder_picker(self, reason='set_cam_cal', working_directory=working_directory))
            imgui.end_menu()
        changed = self._draw_context_menu_items_for_recording_folders([sess], recs, actions_running)
        return changed
    def _draw_context_menu_items_for_recording_folders(self, sess: list[session.Session], recs: list[str]|None, actions_running: dict[str, dict[process.Action, bool|list[str]]]):
        if recs is None:
            recs = [(i,r) for i,s in enumerate(sess) for r in s.recordings]
        else:
            if not len(sess)==1:
                raise RuntimeError('Programmer error. If recs argument is provided, there should only be a single session provided')
            recs = [(0,r) for r in recs]
        if not recs:
            return False
        plural = 's' if len(recs)>1 else ''
        source_directories = [sd for s,r in recs if (sd:=sess[s].recordings[r].info.get_source_directory()) is not None and sd.is_dir()]
        if source_directories and imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open recording source folder"+plural, False)[0]:
            for s in source_directories:
                callbacks.open_folder(self, s)
        changed = False
        working_recordings = [(s, r, working_directory) for s, r in recs if (working_directory := sess[s].recordings[r].info.working_directory) and working_directory.is_dir()]
        if working_recordings and imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + " Open recording working folder"+plural, False)[0]:
            for _, _, working_directory in working_recordings:
                callbacks.open_folder(self, working_directory)
        if working_recordings and imgui.selectable(ifa6.ICON_FA_TRASH_CAN + " Delete recording"+plural, False)[0]:
            for s, r, working_directory in working_recordings:
                # if an import action is currently running, cancel that first
                if sess[s].name in actions_running and process.Action.IMPORT in actions_running[sess[s].name] and r in actions_running[sess[s].name][process.Action.IMPORT]:
                    self.job_scheduler.cancel_job(actions_running[s.name][process.Action.IMPORT][r])
                callbacks.remove_folder(self, working_directory)
                if not working_directory.is_dir():
                    self._pending_recording_removals.add((sess[s].name, r))
            changed = True
        return changed
    def _filter_session_context_menu_actions(self, session_name: str, rec_name: str|None, actions: dict[process.Action,bool|list[str]]) -> tuple[dict[process.Action,bool|list[str]], dict[process.Action,int|dict[str,int]]]:
        # filter out running and pending tasks
        if not actions:
            return {}, {}

        # first get list of scheduled actions for this session/recordings
        actions_running : dict[process.Action,int|dict[str,int]] = {}
        for a in process.Action:
            if process.is_session_level_action(a):
                recs = None
            elif rec_name:
                recs = rec_name
            else:
                recs = [r.name for r in self.study_config.session_def.recordings]

            if isinstance(recs,list):
                recs = {r:job_id for r in recs if (job_id:=self._get_active_job(a, session_name, r)[0]) is not None}
                if recs:
                    actions_running[a] = recs
            else:
                if (job_id:=self._get_active_job(a, session_name, recs)[0]) is not None:
                    actions_running[a] = job_id

        # filter out running actions from possible actions
        actions_possible: dict[process.Action,bool|list[str]] = {}
        for a in actions:
            if a not in actions_running:
                actions_possible[a] = actions[a]
            elif not process.is_session_level_action(a) and not rec_name:
                # check each recording
                recs = [r for r in actions[a][0] if r not in actions_running[a]]
                if recs:
                    actions_possible[a] = recs
        return actions_possible, actions_running

    @staticmethod
    def _get_override_dump_state(overrides: tuple[config.StudyOverride, dict[str,config.StudyOverride]]) -> tuple[dict[str, typing.Any], dict[str, dict[str, typing.Any]]]:
        top_override, coding_overrides = overrides
        coding_dumps = {name: dump for name, override in coding_overrides.items() if (dump := override.get_dump())}
        return top_override.get_dump(), coding_dumps

    def _invalidate_session_override_state(self, sess_name: str):
        self._invalidate_session_problem_cache(sess_name)
        self._session_effective_config_cache.pop(sess_name, None)
        self._session_override_problems_cache.pop(sess_name, None)
        self._recording_effective_config_cache.pop(sess_name, None)
        self._recording_override_problems_cache.pop(sess_name, None)

    def _invalidate_recording_override_state(self, sess_name: str, recording_name: str):
        self._invalidate_session_problem_cache(sess_name)
        self._recording_effective_config_cache.get(sess_name, {}).pop(recording_name, None)
        self._recording_override_problems_cache.get(sess_name, {}).pop(recording_name, None)

    def _get_effective_session_config(self, sess_name: str) -> config.Study:
        if sess_name not in self._session_effective_config_cache:
            self._session_effective_config_cache[sess_name] = config.apply_all_overrides(self.study_config, self.session_config_overrides[sess_name], strict_check=False)
        return self._session_effective_config_cache[sess_name]

    def _get_effective_recording_config(self, sess_name: str, recording_name: str) -> config.Study:
        session_cache = self._recording_effective_config_cache.setdefault(sess_name, {})
        if recording_name not in session_cache:
            session_cache[recording_name] = config.apply_all_overrides(self._get_effective_session_config(sess_name), self.recording_config_overrides[sess_name][recording_name], strict_check=False)
        return session_cache[recording_name]

    def _session_override_field_problems(self, sess_name: str, effective_config: config.Study) -> type_utils.ProblemDict:
        if sess_name not in self._session_override_problems_cache:
            self._session_override_problems_cache[sess_name] = effective_config.field_problems()
        return self._session_override_problems_cache[sess_name]

    def _recording_override_field_problems(self, sess_name: str, recording_name: str, effective_config: config.Study) -> type_utils.ProblemDict:
        session_cache = self._recording_override_problems_cache.setdefault(sess_name, {})
        if recording_name not in session_cache:
            session_cache[recording_name] = effective_config.field_problems()
        return session_cache[recording_name]

    def _reload_cached_override_from_file(self, override_path: pathlib.Path):
        relative_path = override_path.relative_to(self.project_dir)
        parts = relative_path.parts
        if len(parts) == 2:
            sess_name = parts[0]
            if sess_name not in self.session_config_overrides or sess_name not in self.sessions:
                return
            sess = self.sessions[sess_name]
            self.session_config_overrides[sess_name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)
            self._session_dict_type_rec[sess_name] = {-1: {}}
            self._refresh_session_override_state(sess)
            effective_config = self._get_effective_session_config(sess_name)
            self._refresh_recording_override_state(sess, effective_config)
            if sess_name in self._recording_listers:
                self._recording_lister_set_actions_to_show(self._recording_listers[sess_name], sess_name)
        elif len(parts) == 3:
            sess_name, recording_name = parts[:2]
            if sess_name not in self.recording_config_overrides or recording_name not in self.recording_config_overrides[sess_name]:
                return
            sess = self.sessions.get(sess_name)
            if sess is None or recording_name not in sess.recordings:
                return
            recording = sess.recordings[recording_name]
            self.recording_config_overrides[sess_name][recording_name] = config.load_or_create_override(config.OverrideLevel.Recording, recording.info.working_directory, recording.definition.type)
            self._recording_dict_type_rec.setdefault(sess_name, {})[recording_name] = {-1: {}}
            effective_config = self._get_effective_session_config(sess_name)
            self._refresh_one_recording_override_state(sess, recording_name, effective_config)

    def _recreate_override_state(self, overrides: tuple[config.StudyOverride, dict[str,config.StudyOverride]], level: config.OverrideLevel, parent_config: config.Study, recording_type: session.RecordingType|None = None, recording_name: str|None = None) -> tuple[config.StudyOverride, dict[str,config.StudyOverride]]:
        top_override, coding_overrides = overrides

        allowed_fields = set(config.StudyOverride.get_allowed_parameters(level, recording_type, False)[0])
        top_dump = {k: v for k, v in top_override.get_dump().items() if k in allowed_fields}
        # Drop overrides whose named targets no longer exist
        for field in config.StudyOverride.marker_settings_fields.intersection(top_dump):
            if field == 'marker_assemblies':
                known = {p.name for p in parent_config.marker_assemblies if isinstance(p, marker_assembly.Definition_MarkerAssembly)}
                top_dump[field] = {name: params for name, params in top_dump[field].items() if name in known}
            elif field == 'individual_marker_settings':
                top_dump[field] = {name: params for name, params in top_dump[field].items() if name in parent_config.individual_marker_settings}
            else:
                known_markers = {config.individual_marker_override_key(m) for m in parent_config.individual_markers}
                known_groups = set(parent_config.individual_marker_settings)
                top_dump[field] = {key: group for key, group in top_dump[field].items() if key in known_markers and group in known_groups}
            if not top_dump[field]:
                del top_dump[field]
        rebuilt_top_override = config.StudyOverride(level, recording_type, **top_dump)

        event_allowed_fields = config.StudyOverride.get_allowed_parameters(level, recording_type, True)[0]
        rebuilt_coding_overrides: dict[str, config.StudyOverride] = {}
        for cs in parent_config.coding_setup:
            if level == config.OverrideLevel.Recording and cs['which_recordings'] and recording_name and recording_name not in cs['which_recordings']:
                continue
            relevant_fields = [f for f in event_allowed_fields if cs[f] is not None]
            if not relevant_fields:
                continue
            event_dump = coding_overrides[cs['name']].get_dump() if cs['name'] in coding_overrides else {}
            event_dump = {k: v for k, v in event_dump.items() if k in relevant_fields}
            rebuilt_coding_overrides[cs['name']] = config.StudyOverride(level, recording_type, for_event_setup=True, name=cs['name'], **event_dump)

        return rebuilt_top_override, rebuilt_coding_overrides

    @staticmethod
    def _sync_override_type_rec(type_rec: dict[int|str, dict[str, typing.Type]], coding_overrides: dict[str,config.StudyOverride]) -> dict[int|str, dict[str, typing.Type]]:
        type_rec = type_rec or {}
        return {-1: type_rec.get(-1, {})} | {name: type_rec.get(name, {}) for name in coding_overrides}

    def _refresh_session_override_state(self, sess: session.Session):
        self._invalidate_session_override_state(sess.name)
        if sess.name not in self.session_config_overrides:
            self.session_config_overrides[sess.name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)

        previous = self.session_config_overrides[sess.name]
        refreshed = self._recreate_override_state(previous, config.OverrideLevel.Session, self.study_config)
        self.session_config_overrides[sess.name] = refreshed
        self._session_dict_type_rec[sess.name] = self._sync_override_type_rec(self._session_dict_type_rec.get(sess.name, {}), refreshed[1])

        if self._get_override_dump_state(previous) != self._get_override_dump_state(refreshed):
            config.store_overrides_to_json(refreshed, sess.working_directory)

    def _refresh_recording_override_state(self, sess: session.Session, parent_config: config.Study):
        self._recording_effective_config_cache.pop(sess.name, None)
        self._recording_override_problems_cache.pop(sess.name, None)
        self.recording_config_overrides.setdefault(sess.name, {})
        self._recording_dict_type_rec.setdefault(sess.name, {})

        for r in list(self.recording_config_overrides[sess.name]):
            if r not in sess.recordings:
                self.recording_config_overrides[sess.name].pop(r, None)
        for r in list(self._recording_dict_type_rec[sess.name]):
            if r not in sess.recordings:
                self._recording_dict_type_rec[sess.name].pop(r, None)

        for r in sess.recordings:
            self._refresh_one_recording_override_state(sess, r, parent_config)

    def _refresh_one_recording_override_state(self, sess: session.Session, recording_name: str, parent_config: config.Study):
        self._invalidate_recording_override_state(sess.name, recording_name)
        self.recording_config_overrides.setdefault(sess.name, {})
        self._recording_dict_type_rec.setdefault(sess.name, {})
        recording = sess.recordings[recording_name]
        if recording_name not in self.recording_config_overrides[sess.name]:
            self.recording_config_overrides[sess.name][recording_name] = config.load_or_create_override(config.OverrideLevel.Recording, recording.info.working_directory, recording.definition.type)

        previous = self.recording_config_overrides[sess.name][recording_name]
        refreshed = self._recreate_override_state(previous, config.OverrideLevel.Recording, parent_config, recording.definition.type, recording_name)
        self.recording_config_overrides[sess.name][recording_name] = refreshed
        self._recording_dict_type_rec[sess.name][recording_name] = self._sync_override_type_rec(
            self._recording_dict_type_rec[sess.name].get(recording_name, {}), refreshed[1])

        if self._get_override_dump_state(previous) != self._get_override_dump_state(refreshed):
            config.store_overrides_to_json(refreshed, recording.info.working_directory)

    def _refresh_open_override_states(self):
        if self.study_config is None:
            return

        for sess_name in list(self.session_config_overrides):
            if (sess := self.sessions.get(sess_name, None)) is None:
                continue
            self._refresh_session_override_state(sess)
            effective_config_for_session = self._get_effective_session_config(sess.name)
            self._refresh_recording_override_state(sess, effective_config_for_session)

    def _open_session_detail(self, sess: session.Session):
        sess_name = sess.name
        window_id = f'session_view:{sess_name}'
        windows = hello_imgui.get_runner_params().docking_params.dockable_windows
        if win := next((w for w in windows if self._window_identity(w.label)==window_id), None):
            win.focus_window_at_next_frame = True
        else:
            window_list = windows
            window_list.append(self._make_main_space_window(sess_name, lambda sess_name=sess_name: self._session_detail_GUI(sess_name), can_be_closed=True, window_id=window_id))
            self._window_list = window_list
            self._to_dock = [window_id]
            self._to_focus = window_id
            self._selected_recordings[sess_name] = {k:False for k in sess.recordings}
            self._recording_listers[sess_name] = gt_gui.recording_table.RecordingTable(sess.recordings, self._sessions_lock, self._selected_recordings[sess_name], None, lambda r: r.info, item_context_callback=lambda rec_name, sess_name=sess_name: self._recording_context_menu(sess_name, rec_name))
            self._recording_listers[sess_name].dont_show_empty = True
            self.session_config_overrides[sess_name] = config.load_or_create_override(config.OverrideLevel.Session, sess.working_directory)
            self._session_dict_type_rec[sess_name] = {-1: {}}
            self.recording_config_overrides[sess_name] = {}
            self._recording_dict_type_rec[sess_name] = {}
            for r in sess.recordings:
                self.recording_config_overrides[sess_name][r] = config.load_or_create_override(config.OverrideLevel.Recording, sess.recordings[r].info.working_directory, sess.recordings[r].definition.type)
                self._recording_dict_type_rec[sess_name][r] = {-1: {}}
            self._refresh_session_override_state(sess)
            effective_config_for_session = self._get_effective_session_config(sess.name)
            self._refresh_recording_override_state(sess, effective_config_for_session)
            self._recording_lister_set_actions_to_show(self._recording_listers[sess_name], sess_name)

    def _session_detail_GUI(self, sess_name: str):
        with self._sessions_lock:
            if (sess := self.sessions.get(sess_name)) is None:
                return
            lister = self._recording_listers.get(sess_name)
            if lister is None:
                return
            lister.recordings = sess.recordings

        missing_recs = sess.missing_recordings()
        if missing_recs:
            imgui.text_colored(colors.error,'*The following recordings are missing for this session:\n'+'\n'.join(missing_recs))
        show_import_et = any((sess.definition.get_recording_def(r).type==session.RecordingType.Eye_Tracker for r in missing_recs))
        show_import_cam = any((sess.definition.get_recording_def(r).type==session.RecordingType.Camera for r in missing_recs))
        if imgui.button(ifa6.ICON_FA_FOLDER_OPEN + " Open working folder"):
            callbacks.open_folder(self, sess.working_directory)
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
        effective_config = self._get_effective_session_config(sess.name)
        possible_actions = process.get_possible_actions(sess.state, {r:sess.recordings[r].state for r in sess.recordings}, set(session_level_actions), effective_config, allow_pending=True)
        menu_actions,menu_actions_running = self._filter_session_context_menu_actions(sess.name, None, possible_actions)
        if session_level_actions and imgui.begin_table(f'##{sess.name}_session_level', 2, imgui.TableFlags_.sizing_fixed_fit):
            for a in session_level_actions:
                imgui.table_next_column()
                progress = None
                if sess.state[a]==process_pool.State.Running and (job_desc:=self._get_active_job(a, sess.name)[1]):
                    progress = job_desc.progress.get_progress() if job_desc.progress is not None else None
                gt_gui.utils.draw_process_state(sess.state[a], progress=progress)
                imgui.table_next_column()
                imgui.selectable(a.displayable_name, False, imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap)
                if (a in menu_actions or a in menu_actions_running) and imgui.begin_popup_context_item(f"##{sess.name}_{a}_context"):
                    if a in menu_actions_running:
                        hover_text = f'Cancel running {a.displayable_name} for session: {sess.name}'
                        icon = ifa6.ICON_FA_HAND
                        possible = True
                    else:
                        possible = menu_actions[a][0]
                        if possible:
                            hover_text = f'Run {a.displayable_name} for session: {sess.name}'
                        else:
                            hover_text = process.get_impossible_reason_text(a, menu_actions, self.study_config, for_single=True)
                        if a.has_options and possible:
                            hover_text += '\nShift-click to bring up a popup with configuration options for this run.'
                        status = self.sessions[sess.name].state[a]
                        icon = ifa6.ICON_FA_PLAY if status<process_pool.State.Completed else ifa6.ICON_FA_ARROW_ROTATE_RIGHT
                    if not possible:
                        imgui.begin_disabled()
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
                                    callbacks.show_action_options(sess.name, None, a, self)
                                else:
                                    self.launch_task(sess.name, None, a)
                    if not possible:
                        imgui.end_disabled()
                    gt_gui.utils.draw_hover_text(hover_text, '')
                    imgui.end_popup()
            imgui.end_table()
        self._recording_listers[sess.name].draw(limit_outer_size=True)
        sess_changed = False
        session_override_updated = False
        field_problems = self._session_override_field_problems(sess.name, effective_config)
        session_problem_level = self._problem_entries_level(self._session_override_problem_entries(sess))
        session_override_label = ('*' if session_problem_level is not None else '') + 'Setting overrides for this session##session_overrides'
        if session_problem_level is not None:
            imgui.push_style_color(imgui.Col_.text, colors.error if session_problem_level == type_utils.ProblemLevel.Error else colors.warning)
        session_overrides_open = imgui.tree_node_ex(session_override_label, imgui.TreeNodeFlags_.framed)
        if session_problem_level is not None:
            imgui.pop_style_color()
        if session_overrides_open:
            new_config = copy.deepcopy(effective_config)
            sess_changed = False
            fixed_fields = type_utils.NestedDict({k:None for k in ['name', 'event_type']})
            for c_name in self.session_config_overrides[sess.name][1]:
                i = [cs['name'] for cs in self.study_config.coding_setup].index(c_name)
                cs = self.study_config.coding_setup[i]
                fields = [f for f in config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Session, for_event_setup=True)[0] if cs[f] is not None]
                problem_fields = None
                if 'coding_setup' in field_problems and i in field_problems['coding_setup']:
                    problem_fields = field_problems['coding_setup'][i]
                extra = ''
                lbl = f"{cs['name']} ({annotation.tooltip_map[cs['event_type']]})"
                if (has_error:=type_utils.get_error_level(problem_fields or {}) is not None):
                    extra = '*'
                    imgui.push_style_color(imgui.Col_.text, colors.error if type_utils.get_error_level(problem_fields)==type_utils.ProblemLevel.Error else colors.warning)
                is_open = imgui.tree_node_ex(f'{extra}{lbl}###{i}', imgui.TreeNodeFlags_.framed)
                if has_error:
                    imgui.pop_style_color()
                if is_open:
                    this_changed, new_coding_config, self._session_dict_type_rec[sess.name][c_name] = settings_editor.draw(
                        new_config.coding_setup[i],
                        fields,
                        config.EventSetup.__annotations__ | {'auto_code': typing.Union[config.AutoCodeEpisodes if annotation.type_map[cs['event_type']]==annotation.Type.Interval else config.AutoCodeSyncPoints,None]},
                        config.EventSetup._field_defaults,
                        self._possible_value_getters|self._get_possible_value_getter_for_event(c_name),
                        self.study_config.coding_setup[i], self._session_dict_type_rec[sess.name][c_name], problem_fields, fixed=fixed_fields, documentation=config.event_setup_doc)
                    if this_changed:
                        new_config.coding_setup[i] = new_coding_config
                        sess_changed |= this_changed
                    imgui.tree_pop()
            sess_changed |= self._marker_detection_overrides_drawer(new_config, self.study_config, self._session_dict_type_rec[sess.name][-1], field_problems)
            fields = [f for f in config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Session)[0] if f not in config.StudyOverride.marker_settings_fields]
            this_changed, new_config, self._session_dict_type_rec[sess.name][-1] = settings_editor.draw(new_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, self.study_config, self._session_dict_type_rec[sess.name][-1], field_problems, config.study_parameter_doc)
            if sess_changed or this_changed:
                try:
                    new_config.check_valid(strict_check=False)
                    self.session_config_overrides[sess.name] = config.StudyOverride.from_study_diff(new_config, self.study_config, config.OverrideLevel.Session, which_coding_setups=list(self.session_config_overrides[sess.name][1].keys()))
                except Exception as exc:
                    # do not persist invalid config, inform user of problem
                    gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for session {sess.name}:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
                else:
                    # persist changed config
                    config.store_overrides_to_json(self.session_config_overrides[sess.name], sess.working_directory)
                    self._invalidate_session_override_state(sess.name)
                    session_override_updated = True
            imgui.tree_pop()

        if len(self.study_config.session_def.recordings)>1:
            # if more than one recording per session, show recording-level settings overrides
            # don't show if only one recording per session, would be redundant
            effective_config_for_session = self._get_effective_session_config(sess.name)
            if session_override_updated:
                self._refresh_recording_override_state(sess, effective_config_for_session)
            for r in sess.recordings:
                effective_config = self._get_effective_recording_config(sess.name, r)
                field_problems = self._recording_override_field_problems(sess.name, r, effective_config)
                recording_problem_level = self._problem_entries_level(self._recording_override_problem_entries(sess, r))
                recording_override_label = ('*' if recording_problem_level is not None else '') + f'Setting overrides for {r} recording##{r}_recording_overrides'
                if recording_problem_level is not None:
                    imgui.push_style_color(imgui.Col_.text, colors.error if recording_problem_level == type_utils.ProblemLevel.Error else colors.warning)
                recording_overrides_open = imgui.tree_node_ex(recording_override_label, imgui.TreeNodeFlags_.framed)
                if recording_problem_level is not None:
                    imgui.pop_style_color()
                if recording_overrides_open:
                    new_config = copy.deepcopy(effective_config)
                    rec_changed = False
                    fixed_fields = type_utils.NestedDict({k:None for k in ['name', 'event_type']})
                    for c_name in self.recording_config_overrides[sess.name][r][1]:
                        i = [cs['name'] for cs in self.study_config.coding_setup].index(c_name)
                        cs = self.study_config.coding_setup[i]
                        fields = [f for f in config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Recording, sess.recordings[r].definition.type, for_event_setup=True)[0] if cs[f] is not None]
                        problem_fields = None
                        if 'coding_setup' in field_problems and i in field_problems['coding_setup']:
                            problem_fields = field_problems['coding_setup'][i]
                        extra = ''
                        lbl = f"{cs['name']} ({annotation.tooltip_map[cs['event_type']]})"
                        if (has_error:=type_utils.get_error_level(problem_fields or {}) is not None):
                            extra = '*'
                            imgui.push_style_color(imgui.Col_.text, colors.error if type_utils.get_error_level(problem_fields)==type_utils.ProblemLevel.Error else colors.warning)
                        is_open = imgui.tree_node_ex(f'{extra}{lbl}###{i}', imgui.TreeNodeFlags_.framed)
                        if has_error:
                            imgui.pop_style_color()
                        if is_open:
                            this_changed, new_coding_config, self._recording_dict_type_rec[sess.name][r][c_name] = settings_editor.draw(
                                new_config.coding_setup[i],
                                fields,
                                config.EventSetup.__annotations__ | {'auto_code': typing.Union[config.AutoCodeEpisodes if annotation.type_map[cs['event_type']]==annotation.Type.Interval else config.AutoCodeSyncPoints,None]},
                                config.EventSetup._field_defaults,
                                self._possible_value_getters|self._get_possible_value_getter_for_event(c_name),
                                effective_config_for_session.coding_setup[i], self._recording_dict_type_rec[sess.name][r][c_name], problem_fields, fixed=fixed_fields, documentation=config.event_setup_doc)
                            if this_changed:
                                new_config.coding_setup[i] = new_coding_config
                                rec_changed |= this_changed
                            imgui.tree_pop()
                    rec_changed |= self._marker_detection_overrides_drawer(new_config, effective_config_for_session, self._recording_dict_type_rec[sess.name][r][-1], field_problems)
                    fields = [f for f in config.StudyOverride.get_allowed_parameters(config.OverrideLevel.Recording, sess.recordings[r].definition.type)[0] if f not in config.StudyOverride.marker_settings_fields]
                    if fields:
                        this_changed, new_config, self._recording_dict_type_rec[sess.name][r][-1] = settings_editor.draw(new_config, fields, config.study_parameter_types, config.study_defaults, self._possible_value_getters, effective_config_for_session, self._recording_dict_type_rec[sess.name][r][-1], field_problems, config.study_parameter_doc)
                    if rec_changed or (fields and this_changed): # NB: also need to update file when parent has changed
                        try:
                            new_config.check_valid(strict_check=False)
                            self.recording_config_overrides[sess.name][r] = config.StudyOverride.from_study_diff(new_config, effective_config_for_session, config.OverrideLevel.Recording, sess.recordings[r].definition.type, which_coding_setups=list(self.recording_config_overrides[sess.name][r][1].keys()))
                        except Exception as exc:
                            # do not persist invalid config, inform user of problem
                            gt_gui.utils.push_popup(self, gt_gui.msg_box.msgbox, "Settings error", f"You cannot make this change to the settings for recording {r} in session {sess.name}:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
                        else:
                            # persist changed config
                            config.store_overrides_to_json(self.recording_config_overrides[sess.name][r], sess.recordings[r].info.working_directory)
                            self._invalidate_recording_override_state(sess.name, r)
                    imgui.tree_pop()

    def _about_popup_drawer(self):
        base_content_width = 530
        def constrain_popup_size(data):
            dpi_scale = imgui.get_window_dpi_scale()
            data.desired_size.x = base_content_width*dpi_scale + 2*imgui.get_style().window_padding.x
        def popup_content():
            dpi_scale = imgui.get_window_dpi_scale()
            _60 = 60*dpi_scale
            _200 = 200*dpi_scale
            content_width = base_content_width*dpi_scale
            imgui.begin_group()
            imgui.dummy((_60, _200))
            imgui.same_line()
            imgui.dummy((_200, _200))   # space for a logo at some point
            imgui.same_line()
            imgui.begin_group()
            imgui.push_font(None, imgui.get_style().font_size_base*self._big_font_size_multiplier)
            imgui.text("gazeMapper")
            imgui.pop_font()
            imgui.text(f"Version {version.__version__}")
            imgui.text("Made by Diederick C. Niehorster")
            imgui.text("")
            imgui_md.render(f"[glassesTools {glassesTools.version.__version__}](https://github.com/dcnieho/glassesTools)")
            imgui_md.render(f"[ETDQualitizer {ETDQualitizer.version.__version__}](https://github.com/dcnieho/ETDQualitizer)")
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
            imgui.end_group()
            imgui.same_line()
            imgui.dummy((content_width-imgui.get_cursor_pos_x(), _200))
            imgui.end_group()
            imgui.spacing()
            btn_tot_width = (content_width - 3*imgui.get_style().item_spacing.x)
            if imgui.button("PyPI", size=(btn_tot_width/6, 0)):
                webbrowser.open("https://pypi.org/project/gazeMapper/")
            imgui.same_line()
            if imgui.button("Paper", size=(btn_tot_width/6, 0)):
                webbrowser.open("https://doi.org/10.3758/s13428-025-02704-4")
            imgui.same_line()
            if imgui.button("GitHub repo", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://github.com/dcnieho/gazeMapper")
            imgui.same_line()
            if imgui.button("Researcher homepage", size=(btn_tot_width/3, 0)):
                webbrowser.open("https://scholar.google.se/citations?user=uRUYoVgAAAAJ&hl=en")

            imgui.spacing()
            imgui.spacing()
            imgui.push_text_wrap_pos(content_width - imgui.get_style().frame_padding.x)
            imgui.text("This software is licensed under the MIT license and is provided to you for free. Furthermore, due to "
                       "its license, it is also free as in freedom: you are free to use, study, modify and share this software "
                       "in whatever way you wish as long as you keep the same license.")
            imgui.pop_text_wrap_pos()
            imgui.spacing()
            imgui.spacing()
            imgui_md.render(f"If you find bugs or have some feedback, please do let me know on [GitHub](https://github.com/dcnieho/gazeMapper) (using issues or pull requests).")
            imgui.spacing()
            imgui.spacing()
            imgui.dummy((0, 10*imgui.get_window_dpi_scale()))
            imgui.push_font(None, imgui.get_style().font_size_base*self._big_font_size_multiplier)
            size = imgui.calc_text_size("Reference")
            imgui.set_cursor_pos_x((content_width - size.x + imgui.get_style().scrollbar_size) / 2)
            imgui.text("Reference")
            imgui.pop_font()
            imgui.spacing()
            reference         = r"Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. & Hooge, I.T.C. (2025). gazeMapper: A tool for automated world-based analysis of gaze data from one or multiple wearable eye trackers. Behavior Research Methods. doi: [10.3758/s13428-025-02704-4](https://doi.org/10.3758/s13428-025-02704-4)"
            reference_bibtex  = r"""@article{niehorster2025gazeMapper,
    Author = {Niehorster, Diederick C. and
              Hessels, Roy S. and
              Nystr{\"o}m, Marcus and
              Benjamins, Jeroen S. and
              Hooge, Ignace T. C.},
    Journal = {Behavior Research Methods},
    Number = {},
    Title = {{gazeMapper}: A tool for automated world-based analysis of gaze data from one or multiple wearable eye trackers},
    Year = {2025},
    doi = {10.3758/s13428-025-02704-4}
}
"""
            imgui_md.render(reference)
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
            reference         = r"Niehorster, D.C., Hessels, R.S., Benjamins, J.S., Nyström, M. & Hooge, I.T.C. (2023). GlassesValidator: Data quality tool for eye tracking glasses. Behavior Research Methods. doi: [10.3758/s13428-023-02105-5](https://doi.org/10.3758/s13428-023-02105-5)"
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
            imgui_md.render(reference)
            if imgui.begin_popup_context_item(f"##reference_context_glassesValidator"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            gt_gui.utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")
            imgui.spacing()
            imgui.spacing()

            imgui_md.render(f"This tool also makes use of [ETDQualitizer](https://github.com/dcnieho/ETDQualitizer) ([paper](https://doi.org/10.3758/s13428-026-03039-4)), please also reference:")
            imgui.spacing()
            reference         = r"Niehorster, D.C., Nyström, M., Hessels, R.S., Benjamins, J.S., Andersson, R. & Hooge, I.T.C. (2026). The fundamentals of eye tracking, Part 7: Determining data quality. Behavior Research Methods. doi: [10.3758/s13428-026-03039-4](https://doi.org/10.3758/s13428-026-03039-4)"
            reference_bibtex  = r"""@article{niehorster2026Fundamentals7,
    Author = {Niehorster, Diederick C. and
              Nystr{\"o}m, Marcus and
              Hessels, Roy S. and
              Benjamins, Jeroen S. and
              Andersson, Richard and
              Hooge, Ignace T. C.},
    Journal = {Behavior Research Methods},
    Number = {},
    Title = {The fundamentals of eye tracking, Part 7: Determining data quality},
    Year = {2026},
    doi = {10.3758/s13428-026-03039-4}
}
"""
            imgui_md.render(reference)
            if imgui.begin_popup_context_item(f"##reference_context_ETDQualitizer"):
                if imgui.selectable("APA", False)[0]:
                    imgui.set_clipboard_text(reference)
                if imgui.selectable("BibTeX", False)[0]:
                    imgui.set_clipboard_text(reference_bibtex)
                imgui.end_popup()
            gt_gui.utils.draw_hover_text(text='', hover_text="Right-click to copy citation to clipboard")

        dpi_scale = imgui.get_window_dpi_scale()
        content_width = base_content_width*dpi_scale + 2*imgui.get_style().window_padding.x
        imgui.set_next_window_size_constraints((0.0, 0.0), (content_width, imgui.FLT_MAX), constrain_popup_size)
        return gt_gui.utils.popup("About gazeMapper", popup_content, closable=True, outside=True)
