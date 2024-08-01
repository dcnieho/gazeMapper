import pathlib
import asyncio
import concurrent
import sys
import platform
import webbrowser
from typing import Callable

import imgui_bundle
from imgui_bundle import imgui, immapp, imgui_md, hello_imgui, glfw_utils, icons_fontawesome_6 as ifa6
import glfw
import OpenGL
import OpenGL.GL as gl

import glassesTools
import glassesValidator

from ... import config, session, version
from .. import async_thread
from . import callbacks, filepicker, msgbox, utils


class GUI:
    def __init__(self):
        self.popup_stack = []
        self.running     = False

        self.project_dir: pathlib.Path = None
        self.study_config: config.Study = None
        self.sessions: list[session.Session] = None
        self.can_accept_sessions = False


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
                utils.push_popup(self, msgbox.msgbox, "Processing error", f"A background process has failed:\n{type(exc).__name__}: {str(exc) or 'No further details'}", msgbox.MsgBox.warn, more=tb)
                return
            utils.push_popup(self, msgbox.msgbox, "Processing error", f"Something went wrong in an asynchronous task of a separate thread:\n\n{tb}", msgbox.MsgBox.error)
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
        self._icon_font = msgbox.icon_font = \
            hello_imgui.load_font("fonts/Font_Awesome_6_Free-Solid-900.otf", msg_box_size, large_icons_params)

    def _setup_glfw(self):
        win = glfw_utils.glfw_window_hello_imgui()
        glfw.set_drop_callback(win, self._drop_callback)

    def _drop_callback(self, _: glfw._GLFWwindow, items: list[str]):
        paths = [pathlib.Path(item) for item in items]
        if self.popup_stack and isinstance(picker := self.popup_stack[-1], filepicker.FilePicker):
            picker.set_dir(paths)
        else:
            if self.project_dir is not None:
                # import recordings
                pass
            else:
                # load project
                if len(paths)!=1 or not (path := paths[0]).is_dir():
                    utils.push_popup(msgbox.msgbox, "Project opening error", "Only a single project directory should be drag-dropped on the glassesValidator GUI.", msgbox.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in paths])))
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
        self.running = False

    def _make_main_space_window(self, name, gui_func, can_be_closed=False, is_visible=True):
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
        # this is always called, so we handle popups here
        utils.handle_popup_stack(self.popup_stack)
        # also handle showing of debug windows
        if self._show_demo_window:
            self._show_demo_window = imgui.show_demo_window(self._show_demo_window)

        # now actual menu
        if imgui.begin_menu("Help"):
            if imgui.menu_item("About", "", False)[0]:
                utils.push_popup(self, self._draw_about_popup)
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


    def load_project(self, path: pathlib.Path):
        self.project_dir = path
        self.study_config = config.Study.load_from_json(config.guess_config_dir(path))
        self.sessions = session.get_sessions_from_directory(path)
        self._determine_can_accept_sessions()

        self._need_set_window_title = True
        self._project_settings_pane.is_visible = True
        # trigger update so visibility change is honored
        self._window_list = [self._sessions_pane, self._project_settings_pane]
        self._to_focus = self._sessions_pane.label  # ensure sessions pane remains focused

    def _determine_can_accept_sessions(self):
        # need to have:
        # 1. at least one recording defined in the session;
        # 2. one plane set up
        # 3. one episode to code
        # 4. one plane linked to one episode
        self.can_accept_sessions = \
            self.study_config.session_def.recordings and \
            self.study_config.planes and \
            self.study_config.episodes_to_code and \
            self.study_config.planes_per_episode

    def close_project(self):
        self._project_settings_pane.is_visible = False
        # trigger update so visibility change is honored, also delete other windows in the process
        self._window_list = [self._sessions_pane, self._project_settings_pane]

        # defer rest of unloading until windows deleted, as some of these variables will be accessed during this draw loop
        self._after_window_update_callback = self._finish_unload_project

    def _finish_unload_project(self):
        self.project_dir = None
        self.study_config = None
        self.sessions = None
        self._need_set_window_title = True


    def _sessions_pane_drawer(self):
        if not self._main_dock_node_id:
            # this window is docked to the right dock node, if we don't
            # have it yet, query id of this dock node as we'll need it for later
            # windows
            self._main_dock_node_id = imgui.get_window_dock_id()
        if not self.project_dir:
            self._draw_unopened_interface()
            return
        elif not self.can_accept_sessions:
            imgui.text('This study does not have at least one defined recording and one defined plane.')
            imgui.align_text_to_frame_padding()
            imgui.text('Set these up in the')
            imgui.same_line()
            if imgui.button('Project settings##button'):
                self._to_focus = self._project_settings_pane.label
            imgui.same_line()
            imgui.text('tab before you can continue.')
            return

    def _draw_unopened_interface(self):
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
        pass

    def _draw_about_popup(self):
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