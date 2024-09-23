import pathlib
import typing
import shutil
import os
import asyncio
import subprocess
import pathvalidate
import threading
from imgui_bundle import imgui, imspinner, hello_imgui, icons_fontawesome_6 as ifa6

import glassesTools
import glassesTools.gui
from glassesValidator.config import deploy_validation_config, get_validation_setup

from . import colors, utils
from ... import config, marker, plane, process, session

def get_folder_picker(g, reason: str, *args, **kwargs):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    def select_callback(selected):
        match reason:
            case 'loading' | 'creating':
                try_load_project(g, selected, action=reason)
            case 'add_et_recordings':
                add_eyetracking_recordings(g, selected, *args, **kwargs)
            case 'add_cam_recordings':
                add_camera_recordings(g, selected, *args, **kwargs)
            case _:
                raise ValueError(f'reason "{reason}" not understood')

    match reason:
        case 'loading' | 'creating':
            header = "Select or drop project folder"
            allow_multiple = False
            picker_type = glassesTools.gui.file_picker.DirPicker
        case 'add_et_recordings':
            header = "Select or drop recording folders"
            allow_multiple = True
            picker_type = glassesTools.gui.file_picker.DirPicker
        case 'add_cam_recordings':
            header = "Select or drop recording folders or files"
            allow_multiple = True
            picker_type = glassesTools.gui.file_picker.FilePicker
        case _:
            raise ValueError(f'reason "{reason}" not understood')
    picker = picker_type(title=header, allow_multiple=allow_multiple, callback=select_callback)
    picker.set_show_only_dirs(False)
    return picker

def try_load_project(g, path: str|pathlib.Path, action='loading'):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if isinstance(path,list):
        if not path:
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Project opening error", "A single project directory should be provided. None provided so cannot open.", glassesTools.gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
            return
        elif len(path)>1:
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Project opening error", f"Only a single project directory should be provided, but {len(path)} were provided. Cannot open multiple projects.", glassesTools.gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
            return
        else:
            path = path[0]
    path = pathlib.Path(path)

    if utils.is_project_folder(path):
        if action=='creating':
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: g.load_project(path),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Create new project", "The selected folder is already a project folder.\nDo you want to open it?", glassesTools.gui.msg_box.MsgBox.question, buttons)
        else:
            g.load_project(path)
    elif any(path.iterdir()):
        if action=='creating':
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Project creation error", "The selected folder is not empty. Cannot be used to create a project folder.", glassesTools.gui.msg_box.MsgBox.error)
        else:
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Project opening error", "The selected folder is not a project folder. Cannot open.", glassesTools.gui.msg_box.MsgBox.error)
    else:
        def init_project_and_ask():
            utils.init_project_folder(path)
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: g.load_project(path),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Open new project", "Do you want to open the new project folder?", glassesTools.gui.msg_box.MsgBox.question, buttons)
        if action=='creating':
            init_project_and_ask()
        else:
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: init_project_and_ask(),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Create new project", "The selected folder is empty. Do you want to use it as a new project folder?", glassesTools.gui.msg_box.MsgBox.warn, buttons)

def make_plane(study_config: config.Study, p_type: plane.Type, name: str):
    path = config.guess_config_dir(study_config.working_directory)
    p_dir = path / name
    # make plane
    p_def = plane.make(p_dir, p_type, name)
    # store to file
    if not p_dir.is_dir():
        p_dir.mkdir()
    p_def.store_as_json(p_dir)
    # append to known planes
    study_config.planes.append(p_def)

def delete_plane(study_config: config.Study, plane: plane.Definition):
    # remove config directory for the plane
    path = config.guess_config_dir(study_config.working_directory)
    p_dir = path / plane.name
    shutil.rmtree(p_dir)
    # remove from known planes
    study_config.planes = [p for p in study_config.planes if p.name!=plane.name]

def glasses_validator_plane_check_config(study_config: config.Study, pl: plane.Definition_GlassesValidator):
    if not isinstance(pl, plane.Definition_GlassesValidator) or pl.use_default:
        return
    # check if there are already are validation setup files
    working_dir = config.guess_config_dir(study_config.working_directory)/pl.name
    try:
        get_validation_setup(working_dir)
    except:
        # no config file, deploy
        deploy_validation_config(working_dir)
    else:
        # already exists, nothing to do
        pass

def make_recording_definition(study_config: config.Study, r_type: session.RecordingType, name: str):
    # append to defined recordings
    study_config.session_def.recordings.append(session.RecordingDefinition(name,r_type))
    # store config
    path = config.guess_config_dir(study_config.working_directory)
    study_config.session_def.store_as_json(path)

def delete_recording_definition(study_config: config.Study, recording: session.RecordingDefinition):
    # remove from defined recordings
    study_config.session_def.recordings = [r for r in study_config.session_def.recordings if r.name!=recording.name]
    # store config
    path = config.guess_config_dir(study_config.working_directory)
    study_config.session_def.store_as_json(path)

async def remove_recording_working_dir(project_dir: pathlib.Path, session: str, recording: str):
    rec_dir = project_dir / session / recording
    if rec_dir.is_dir():
        shutil.rmtree(rec_dir)

def make_individual_marker(study_config: config.Study, mark_id: int, mark_size: float):
    # append to defined recordings
    study_config.individual_markers.append(marker.Marker(mark_id, mark_size))
    # store config
    study_config.store_as_json()

def delete_individual_marker(study_config: config.Study, mark: marker.Marker):
    # remove from defined individual markers
    study_config.individual_markers = [m for m in study_config.individual_markers if m.id!=mark.id]
    # store config
    study_config.store_as_json()

def new_session_button(g, notify_func: typing.Callable[[str], None]|None = None):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    new_sess_name = ''
    def _valid_sess_name():
        return new_sess_name and pathvalidate.is_valid_filename(new_sess_name, "auto") and g.sessions.get(new_sess_name, None) is None
    def _add_sess_popup():
        nonlocal new_sess_name
        imgui.dummy((30*imgui.calc_text_size('x').x,0))
        if imgui.begin_table("##new_sess_info",2):
            imgui.table_setup_column("##new_sess_infos_left", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column("##new_sess_infos_right", imgui.TableColumnFlags_.width_stretch)
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            invalid = not _valid_sess_name()
            if invalid:
                imgui.push_style_color(imgui.Col_.text, colors.error)
            imgui.text("Session name")
            if invalid:
                imgui.pop_style_color()
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            _,new_sess_name = imgui.input_text("##new_sess_name",new_sess_name)
            imgui.end_table()
        return 0 if imgui.is_key_released(imgui.Key.enter) else None

    def _make_session():
        make_session(g.project_dir, new_sess_name)
        if notify_func is not None:
            notify_func(new_sess_name)

    buttons = {
        ifa6.ICON_FA_CHECK+" Create session": (_make_session, lambda: not _valid_sess_name()),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }
    glassesTools.gui.utils.push_popup(g, lambda: glassesTools.gui.utils.popup("Add session", _add_sess_popup, buttons = buttons, outside=False))

def make_session(project_dir: pathlib.Path, session_name: str):
    sess_dir = project_dir/session_name
    sess_dir.mkdir(exist_ok=True)
    session.get_action_states(sess_dir, for_recording=False, create_if_missing=True)

def open_url(path: str):
    # this works for files, folders and URLs
    if glassesTools.platform.os==glassesTools.platform.Os.Windows:
        os.startfile(path)
    else:
        if glassesTools.platform.os==glassesTools.platform.Os.Linux:
            open_util = "xdg-open"
        elif glassesTools.platform.os==glassesTools.platform.Os.MacOS:
            open_util = "open"
        glassesTools.async_thread.run(asyncio.create_subprocess_exec(
            open_util, path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ))

def open_folder(path: pathlib.Path):
    if not path.is_dir():
        glassesTools.gui.utils.push_popup(globals, glassesTools.gui.msg_box.msgbox, "Folder not found", f"The folder you're trying to open\n{path}\ncould not be found.", glassesTools.gui.msg_box.MsgBox.warn)
        return
    open_url(str(path))

def remove_folder(folder: pathlib.Path):
    if folder.is_dir():
        shutil.rmtree(folder)

async def _show_addable_recordings(g, paths: list[pathlib.Path], eye_tracker: glassesTools.eyetracker.EyeTracker, sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    # notify we're preparing the recordings to be opened
    def prepping_recs_popup():
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        text = f'Searching the path(s) you provided for {eye_tracker.value} recordings.'
        imgui.text_unformatted(text)
        imgui.dummy((0,3*imgui.get_style().item_spacing.y))
        text_size = imgui.calc_text_size(text)
        size_mult = hello_imgui.dpi_window_size_factor()
        spinner_radii = [x*size_mult for x in [22, 16, 10]]
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x()+(text_size.x-2*spinner_radii[0])/2)
        imspinner.spinner_ang_triple('waitSpinner', *spinner_radii, 3.5*size_mult, c1=imgui.get_style_color_vec4(imgui.Col_.text), c2=colors.warning, c3=imgui.get_style_color_vec4(imgui.Col_.text))
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.end_group()

        imgui.same_line(spacing=spacing)
        imgui.dummy((0, 0))
    glassesTools.gui.utils.push_popup(g, lambda: glassesTools.gui.utils.popup("Preparing import", prepping_recs_popup, buttons = None, closable=False, outside=False))

    # step 1, find what recordings of this type of eye tracker are in the path
    recs = glassesTools.recording.find_recordings(paths, eye_tracker)
    all_recs: list[glassesTools.recording.Recording] = []
    dup_recs: list[glassesTools.recording.Recording] = []
    for rec in recs:
        # skip duplicates
        if rec.source_directory not in (g.sessions[s].recordings[r].info.source_directory for s in g.sessions for r in g.sessions[s].recordings if g.sessions[s].recordings[r].definition.type==session.RecordingType.Eye_Tracker):
            all_recs.append(rec)
        else:
            dup_recs.append(rec)

    # get ready to show result
    # 1. remove prepping recordings popup
    del g.popup_stack[-1]

    # 2. if nothing importable found, notify
    if not all_recs:
        if dup_recs:
            msg = f"{eye_tracker.value} recordings were found in the specified import paths, but could not be imported as they are already part of sessions in this gazeMapper project."
            more= "Duplicates that were not imported:\n"+('\n'.join([str(r.source_directory) for r in dup_recs]))
        else:
            msg = f"No {eye_tracker.value} recordings were found among the specified import paths."
            more = None

        glassesTools.gui.utils.push_popup(g, glassesTools.gui.msg_box.msgbox, "Nothing to import", msg, glassesTools.gui.msg_box.MsgBox.warn, more=more)
        return

    # 3. if something importable found, show to user so they can assign them to recordings in sessions
    recordings_to_add = {i:r for i,r in enumerate(all_recs)}
    recording_assignment: dict[str, dict[str, int]] = {}
    rec_names = [r.name for r in g.study_config.session_def.recordings]
    selected_slot: tuple(str,str) = None

    def _recording_context_menu(iid: int) -> bool:
        nonlocal selected_slot
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + f" Open folder##{iid}", False)[0]:
            open_folder(recordings_to_add[iid].source_directory)
        if selected_slot is not None and imgui.selectable(ifa6.ICON_FA_ARROW_LEFT + f" Assign to selected recording", False)[0]:
            recording_assignment[selected_slot[0]][selected_slot[1]] = iid
            selected_slot = None
            recording_list.require_sort = True
        return False

    def _add_new_session(new_sess: str):
        sessions.append(new_sess)

    recording_lock = threading.Lock()
    recording_list = glassesTools.gui.recording_table.RecordingTable(recordings_to_add, recording_lock, None, item_context_callback=_recording_context_menu)
    recording_list.set_local_item_remover()
    recording_list.set_act_as_drag_drop_source(True)
    not_assigned_filter = glassesTools.gui.recording_table.Filter(lambda iid, _: iid not in (recording_assignment[s][r] for s in recording_assignment for r in recording_assignment[s]))
    recording_list.add_filter(not_assigned_filter)
    def list_recs_popup():
        nonlocal selected_slot
        spacing = 2 * imgui.get_style().item_spacing.x
        imgui.same_line(spacing=spacing)

        imgui.text_unformatted("Select which recordings you would like to import. Assign a recording from the list on the right to a recording in a session on the left by dragging and dropping it. Add new sessions if needed")
        imgui.dummy((0,1*imgui.get_style().item_spacing.y))

        size_mult = hello_imgui.dpi_window_size_factor()
        imgui.begin_child("##main_frame_adder", size=(1260*size_mult,min(700*size_mult,max(400*size_mult,(len(recording_list.recordings)+2)*imgui.get_frame_height_with_spacing()))))
        imgui.begin_child("##session_list", size=(600*size_mult,0))
        if imgui.button('+ new session'):
            new_session_button(g, _add_new_session)
        for s in sessions:
            sess = g.sessions.get(s, None)
            if sess is None:
                continue
            if sess.name not in recording_assignment:
                recording_assignment[sess.name] = {}
            if imgui.tree_node_ex(sess.name):
                table_opened = False
                for r in rec_names:
                    if sess.definition.get_recording_def(r).type!=session.RecordingType.Eye_Tracker:
                        continue
                    disable = False
                    rec: glassesTools.recording.Recording = None
                    if r in sess.recordings:
                        rec = sess.recordings[r].info
                        disable = True
                    elif r in recording_assignment[sess.name]:
                        rec = recordings_to_add[recording_assignment[sess.name][r]]
                    has_rec = rec is not None
                    if disable:
                        imgui.begin_disabled()
                    if table_opened or imgui.begin_table(f'##{sess.name}', 4, imgui.TableFlags_.sizing_fixed_fit):
                        table_opened = True
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        if not disable:
                            selected = False if not selected_slot else (sess.name,r)==selected_slot
                            interacted, was_selected = imgui.selectable(r, selected, imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap)
                            if has_rec:
                                if imgui.begin_popup_context_item(f"##{sess.name}_{r}_context"):
                                    if imgui.selectable(ifa6.ICON_FA_ARROW_RIGHT + f" Unassign this recording", False)[0]:
                                        recording_assignment[sess.name].pop(r, None)
                                        recording_list.require_sort = True
                                    imgui.end_popup()
                            else:
                                if interacted:
                                    if was_selected:
                                        selected_slot = (sess.name,r)
                                    else:
                                        selected_slot = None
                                if imgui.begin_drag_drop_target():
                                    payload = imgui.accept_drag_drop_payload_py_id("RECORDING")
                                    if payload is not None:
                                        recording_assignment[sess.name][r] = payload.data_id
                                        recording_list.require_sort = True
                                    imgui.end_drag_drop_target()
                        else:
                            imgui.text(r)
                        imgui.table_next_column()
                        if has_rec:
                            recording_list.draw_eye_tracker_widget(rec, align=True)
                        imgui.table_next_column()
                        if has_rec:
                            imgui.text(rec.name)
                        else:
                            imgui.text('drop recording here to assign')
                        imgui.table_next_column()
                        if has_rec:
                            imgui.text(rec.participant)
                    if disable:
                        imgui.end_disabled()

                if table_opened:
                    table_opened = False
                    imgui.end_table()
                imgui.tree_pop()

        imgui.end_child()
        imgui.same_line()
        imgui.begin_child("##import_source")
        imgui.begin_child("##recording_list_frame", size=(0,-imgui.get_frame_height_with_spacing()), window_flags=imgui.WindowFlags_.horizontal_scrollbar)
        recording_list.draw()
        imgui.end_child()
        imgui.begin_child("##recording_list_bottombar_frame")
        recording_list.filter_box_text, recording_list.require_sort = \
            draw_filterbar(g, recording_list.filter_box_text, recording_list.require_sort)
        imgui.end_child()
        imgui.end_child()
        imgui.end_child()

        imgui.same_line(spacing=spacing)
        imgui.dummy((0,6*imgui.get_style().item_spacing.y))

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": lambda: glassesTools.async_thread.run(_import_recordings(g, recordings_to_add, recording_assignment)),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }
    glassesTools.gui.utils.push_popup(g, lambda: glassesTools.gui.utils.popup("Assign and import recordings", list_recs_popup, buttons = buttons, closable=True, outside=False))

async def _import_recordings(g, recordings: list[glassesTools.recording.Recording], recording_assignment: dict[str, dict[str, int]]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    for s in recording_assignment:
        sess = g.sessions.get(s, None)
        if sess is None:
            continue
        for r in recording_assignment[s]:
            rec = recordings[recording_assignment[s][r]]
            # first create recording only in memory
            sess.add_recording_from_info(r, rec)
            # then launch import task
            g.launch_task(s, r, process.Action.IMPORT)


def draw_filterbar(g, filter_box_text: str, require_sort: bool):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    imgui.set_next_item_width(-imgui.FLT_MIN)
    _, value = imgui.input_text_with_hint(f"##filterbar", "Start typing to filter the list", filter_box_text, flags=imgui.InputTextFlags_.enter_returns_true)
    if imgui.begin_popup_context_item(f"##filterbar_context"):
        # Right click = more options context menu
        if imgui.selectable(ifa6.ICON_FA_CLIPBOARD+" Paste", False)[0]:
            value += imgui.get_clipboard_text() or ""
        imgui.separator()
        if imgui.selectable(ifa6.ICON_FA_CIRCLE_INFO+" More info", False)[0]:
            glassesTools.gui.utils.push_popup(g,
                glassesTools.gui.msg_box.msgbox, "About the filter bar",
                "This is the filter bar. By typing inside it you can search your recording list inside the eye tracker, name, participant and project properties.",
                glassesTools.gui.msg_box.MsgBox.info
            )
        imgui.end_popup()
    if value != filter_box_text:
        filter_box_text = value
        require_sort = True

    return filter_box_text, require_sort

def add_eyetracking_recordings(g, paths: list[pathlib.Path], sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    combo_value = 0
    eye_tracker = glassesTools.eyetracker.EyeTracker(glassesTools.eyetracker.eye_tracker_names[combo_value])
    if not sessions:
        sessions = [s for s in g.sessions if g.sessions[s].missing_recordings(session.RecordingType.Eye_Tracker)]

    def add_recs_popup():
        nonlocal combo_value, eye_tracker
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.text_unformatted("For which eye tracker would you like to import recordings?")
        imgui.dummy((0,3*imgui.get_style().item_spacing.y))
        full_width = imgui.get_content_region_avail().x
        imgui.push_item_width(full_width*.4)
        imgui.set_cursor_pos_x(full_width*.3)
        changed, combo_value = imgui.combo("##select_eye_tracker", combo_value, glassesTools.eyetracker.eye_tracker_names)
        imgui.pop_item_width()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))

        imgui.end_group()
        imgui.same_line(spacing=spacing)
        imgui.dummy((0, 0))

        if changed:
            eye_tracker = glassesTools.eyetracker.EyeTracker(glassesTools.eyetracker.eye_tracker_names[combo_value])

        return combo_value, eye_tracker

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": lambda: glassesTools.async_thread.run(_show_addable_recordings(g, paths, eye_tracker, sessions)),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }

    # ask what type of eye tracker we should be looking for
    glassesTools.gui.utils.push_popup(g, lambda: glassesTools.gui.utils.popup("Select eye tracker", add_recs_popup, buttons = buttons, closable=True, outside=False))

def add_recordings(g, paths: list[pathlib.Path], sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    options: list[session.RecordingType] = []
    if any((r.type==session.RecordingType.Eye_Tracker for r in g.study_config.session_def.recordings)):
        options.append(session.RecordingType.Eye_Tracker)
    if any((r.type==session.RecordingType.Camera for r in g.study_config.session_def.recordings)):
        options.append(session.RecordingType.Camera)
    if not options:
        return
    combo_value = 0
    dev_type = options[combo_value]
    glob_filter = '*.mp4,*.avi'

    def choose_dev_popup():
        nonlocal combo_value, dev_type, glob_filter
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.text_unformatted("For which device would you like to import recordings?")
        imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
        full_width = imgui.get_content_region_avail().x
        imgui.push_item_width(full_width*.4)
        imgui.set_cursor_pos_x(full_width*.3)
        changed, combo_value = imgui.combo("##select_device", combo_value, [d.value for d in options])
        if changed:
            dev_type = options[combo_value]
        imgui.pop_item_width()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        if dev_type==session.RecordingType.Camera:
            imgui.text_unformatted("Extension filter to search for video files in")
            imgui.text_unformatted("the selected paths (leave empty to not filter)")
            imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
            full_width = imgui.get_content_region_avail().x
            imgui.push_item_width(full_width*.4)
            imgui.set_cursor_pos_x(full_width*.3)
            _, glob_filter = imgui.input_text('##file_filter', glob_filter)
            imgui.pop_item_width()
            imgui.dummy((0,2*imgui.get_style().item_spacing.y))

        imgui.end_group()
        imgui.same_line(spacing=spacing)
        imgui.dummy((0, 0))

        return combo_value, dev_type

    def _run(sessions: list[str]):
        if not sessions:
            sessions: list[str] = []
            for s in g.sessions:
                if not (mis_rec:=g.sessions[s].missing_recordings(dev_type)):
                    continue
                sessions.append(s)
        else:
            sessions = [s for s in sessions if g.sessions[s].missing_recordings(dev_type)]
        match dev_type:
            case session.RecordingType.Eye_Tracker:
                add_eyetracking_recordings(g, paths, sessions)
            case session.RecordingType.Camera:
                add_camera_recordings(g, paths, glob_filter, sessions)

    if len(options)==1 and options[0]==session.RecordingType.Eye_Tracker:
        # no need to show selection popup, as there is only one choice and nothing to configure for that choice
        _run(sessions)
        return

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": lambda: _run(sessions),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }

    # ask what type of device recordings we want to import
    glassesTools.gui.utils.push_popup(g, lambda: glassesTools.gui.utils.popup("Select device", choose_dev_popup, buttons = buttons, closable=True, outside=False))
