import pathlib
import typing
import shutil
import os
import asyncio
import subprocess
import pathvalidate
import threading
from imgui_bundle import imgui, imspinner, hello_imgui, icons_fontawesome_6 as ifa6

from glassesTools import annotation, aruco, async_thread, camera_recording, eyetracker, gui as gt_gui, naming as gt_naming, platform, recording, video_utils
from glassesTools.validation import config as val_config, DataQualityType, export, get_DataQualityType_explanation

from . import colors, utils
from ... import config, marker, naming, plane, process, session

def get_folder_picker(g, reason: str, *args, **kwargs):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    def select_callback(selected: list[pathlib.Path]):
        match reason:
            case 'loading' | 'creating':
                try_load_project(g, selected, action=reason)
            case 'add_et_recordings':
                add_eyetracking_recordings(g, selected, *args, **kwargs)
            case 'add_cam_recordings':
                camera_show_glob_filter_config(g, selected, *args, **kwargs)
            case 'set_default_cam_cal':
                set_default_cam_cal(selected[0], *args, **kwargs)
            case 'set_cam_cal':
                set_cam_cal(selected[0], *args, **kwargs)
            case 'deploy_aruco':
                aruco.deploy_marker_images(selected[0], 1000, *args, **kwargs)
            case 'deploy_gv_poster_pdf':
                val_config.plane.deploy_default_pdf(selected[0])
            case 'export':
                show_export_config(g, selected[0], *args, **kwargs)
            case _:
                raise ValueError(f'reason "{reason}" not understood')

    match reason:
        case 'loading' | 'creating':
            header = "Select or drop project folder"
            allow_multiple = False
            picker_type = gt_gui.file_picker.DirPicker
        case 'add_et_recordings':
            header = "Select or drop recording folders"
            allow_multiple = True
            picker_type = gt_gui.file_picker.DirPicker
        case 'add_cam_recordings':
            header = "Select or drop recording folders or files"
            allow_multiple = True
            picker_type = gt_gui.file_picker.FilePicker
        case 'set_default_cam_cal' | 'set_cam_cal':
            header = "Select or drop calibration xml file"
            allow_multiple = False
            picker_type = gt_gui.file_picker.FilePicker
        case 'deploy_aruco':
            header = "Select folder to store ArUco marker images"
            allow_multiple = False
            picker_type = gt_gui.file_picker.DirPicker
        case 'deploy_gv_poster_pdf':
            header = "Select folder to store default glassesValidator poster pdf"
            allow_multiple = False
            picker_type = gt_gui.file_picker.DirPicker
        case 'export':
            header = "Select folder to store results export"
            allow_multiple = False
            picker_type = gt_gui.file_picker.DirPicker
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
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Project opening error", "A single project directory should be provided. None provided so cannot open.", gt_gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
            return
        elif len(path)>1:
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Project opening error", f"Only a single project directory should be provided, but {len(path)} were provided. Cannot open multiple projects.", gt_gui.msg_box.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
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
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Create new project", "The selected folder is already a project folder.\nDo you want to open it?", gt_gui.msg_box.MsgBox.question, buttons)
        else:
            g.load_project(path)
    elif any(path.iterdir()):
        if action=='creating':
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Project creation error", "The selected folder is not empty. Cannot be used to create a project folder.", gt_gui.msg_box.MsgBox.error)
        else:
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Project opening error", "The selected folder is not a project folder. Cannot open.", gt_gui.msg_box.MsgBox.error)
    else:
        def init_project_and_ask():
            utils.init_project_folder(path)
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: g.load_project(path),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Open new project", "Do you want to open the new project folder?", gt_gui.msg_box.MsgBox.question, buttons)
        if action=='creating':
            init_project_and_ask()
        else:
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: init_project_and_ask(),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Create new project", "The selected folder is empty. Do you want to use it as a new project folder?", gt_gui.msg_box.MsgBox.warn, buttons)

def set_default_cam_cal(cal_path: str|pathlib.Path, rec_def: session.RecordingDefinition, rec_def_path: pathlib.Path):
    rec_def.set_default_cal_file(cal_path, rec_def_path)

def set_cam_cal(cal_path: str|pathlib.Path, working_directory: str|pathlib.Path):
    cal_path = pathlib.Path(cal_path)
    working_directory = pathlib.Path(working_directory)
    shutil.copyfile(str(cal_path), str(working_directory / gt_naming.scene_camera_calibration_fname))

def delete_cam_cal(working_directory: str|pathlib.Path):
    pathlib.Path(working_directory / gt_naming.scene_camera_calibration_fname).unlink(missing_ok=True)

def make_plane(study_config: config.Study, p_type: plane.Type, name: str):
    path = config.guess_config_dir(study_config.working_directory)
    p_dir = path / name
    # make plane
    p_def = plane.make(p_type, name, p_dir)
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
        val_config.get_validation_setup(working_dir)
    except:
        # no config file, deploy
        val_config.deploy_validation_config(working_dir)
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
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Add session", _add_sess_popup, buttons = buttons, outside=False))

def make_session(project_dir: pathlib.Path, session_name: str):
    sess_dir = project_dir/session_name
    sess_dir.mkdir(exist_ok=True)
    session.get_action_states(sess_dir, for_recording=False, create_if_missing=True)

def open_url(path: str):
    # this works for files, folders and URLs
    if platform.os==platform.Os.Windows:
        os.startfile(path)
    else:
        if platform.os==platform.Os.Linux:
            open_util = "xdg-open"
        elif platform.os==platform.Os.MacOS:
            open_util = "open"
        async_thread.run(asyncio.create_subprocess_exec(
            open_util, path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ))

def open_folder(path: pathlib.Path):
    if not path.is_dir():
        gt_gui.utils.push_popup(globals, gt_gui.msg_box.msgbox, "Folder not found", f"The folder you're trying to open\n{path}\ncould not be found.", gt_gui.msg_box.MsgBox.warn)
        return
    open_url(str(path))

def remove_folder(folder: pathlib.Path):
    if folder.is_dir():
        shutil.rmtree(folder)

def show_action_options(g, session_name: str, rec_name: str|None, action: process.Action):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    # NB: `show_visualization` is always an option, the others below are
    # shown when `show_visualization` is true (else they don't apply)
    match action:
        case process.Action.MAKE_GAZE_OVERLAY_VIDEO:
            options = {'show_visualization': [
                    False, 'Show visualization', 'Show a viewer that allows to follow the generation of the video. Each frame is shown with gaze overlaid as it is written into the video file.'
                ]}
        case process.Action.DETECT_MARKERS:
            options = {'show_visualization': [
                    False, 'Show visualization', 'Show a viewer that allows to follow the processing of the video. Each frame is shown overlaid with info about detected markers and planes.'
                ], 'visualization_show_rejected_markers': [
                    False, 'Show rejected markers', 'Rejected ArUco marker candidates are also shown in the viewer. Possibly useful for debug.'
                ]}
        case process.Action.GAZE_TO_PLANE:
            options = {'show_visualization': [
                    False, 'Show visualization', 'Show a viewer that visualizes the mapped gaze to plane. Each frame of the video is shown in the viewer, overlaid with info about detected planes and projected gaze.'
                ], 'show_planes': [
                    True, 'Show planes', "Additional viewer window(s) are opened, one per plane, with gaze in this plane's space is drawn on it."
                ], 'show_only_intervals': [
                    True, 'Show only intervals', 'Only the coded mapping episodes (if available) are shown in the viewer while the rest of the scene video is skipped past.'
                ]}
        case process.Action.MAKE_MAPPED_GAZE_VIDEO:
            options = {'show_visualization': [
                    False, 'Show visualization', 'The generated video(s) are shown in a viewer as they are created.'
                ]}
        case _:
            raise ValueError(f'Action option setting GUI not implemented for a {action} action')

    def set_action_options_popup():
        nonlocal options
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.text_unformatted(f'Settings for this run of the {action.displayable_name} action\nSession: "{session_name}", recording: "{rec_name}":')
        imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
        _, options['show_visualization'][0] = imgui.checkbox(f'{options["show_visualization"][1]}##{session_name}_{rec_name}', options['show_visualization'][0])
        imgui.same_line()
        gt_gui.utils.draw_hover_text(options['show_visualization'][2])
        if options['show_visualization'][0]:
            for o in options:
                if o=='show_visualization':
                    continue
                _, options[o][0] = imgui.checkbox(f'{options[o][1]}##{session_name}_{rec_name}', options[o][0])
                imgui.same_line()
                gt_gui.utils.draw_hover_text(options[o][2])

        imgui.end_group()

    buttons = {
        ifa6.ICON_FA_CHECK+f" Continue##{session_name}_{rec_name}": lambda: g.launch_task(session_name, rec_name, action, **{o:options[o][0] for o in options}),
        ifa6.ICON_FA_CIRCLE_XMARK+f" Cancel##{session_name}_{rec_name}": None
    }

    # show configurable options for action
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup(f"Set options##{session_name}_{rec_name}", set_action_options_popup, buttons = buttons, closable=True, outside=False))


def show_export_config(g, path: str|pathlib.Path, sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker

    # for the sessions, see what we have to export
    to_export: dict[str,bool] = {}
    rec_dirs: list[pathlib.Path] = []
    for s_name in sessions:
        s = g.sessions.get(s_name,None)
        if s is None:
            continue
        if 'plane gaze' not in to_export:
            if annotation.Event.Trial in g.study_config.planes_per_episode and any((s.recordings[r].state[process.Action.GAZE_TO_PLANE]==process.State.Completed for r in s.recordings)):
                to_export['plane gaze'] = True
        if recs:=[s.recordings[r].info.working_directory for r in s.recordings if s.recordings[r].state[process.Action.VALIDATE]==process.State.Completed]:
            to_export['validation'] = True
            rec_dirs.extend(recs)
        if 'gaze overlay video' not in to_export:
            if any((s.recordings[r].state[process.Action.MAKE_GAZE_OVERLAY_VIDEO]==process.State.Completed for r in s.recordings)):
                to_export['gaze overlay video'] = True
        if 'mapped gaze video' not in to_export:
            if s.state[process.Action.MAKE_MAPPED_GAZE_VIDEO]==process.State.Completed:
                to_export['mapped gaze video'] = True

    dq_df, dq_set = None, None
    if rec_dirs:
        dq_df, default_dq_type, dq_targets = export.collect_data_quality(rec_dirs, {p:f'{naming.validation_prefix}{p}_data_quality.tsv' for p in g.study_config.planes_per_episode[annotation.Event.Validate]}, col_for_parent='session')
        if dq_df is None:
            to_export.pop('validation', None)
        else:
            # prep config for validation export
            dq_set = {}

            # data quality type
            type_idx = dq_df.index.names.index('type')
            dq_set['dq_types'] = {k:False for k in sorted(list(dq_df.index.levels[type_idx]), key=lambda dq: dq.value)}
            for dq in DataQualityType:
                if g.study_config.validate_dq_types is not None and dq in g.study_config.validate_dq_types and dq in dq_set['dq_types']:
                    dq_set['dq_types'][dq] = True
            if not any(dq_set['dq_types'].values()):
                dq_set['dq_types'][default_dq_type] = True

            # targets
            dq_set['targets']     = {t:True for t in dq_targets}
            dq_set['targets_avg'] = False

            # other settings
            dq_set['include_data_loss'] = g.study_config.validate_include_data_loss

    def set_export_config_popup():
        nonlocal to_export, dq_set
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.text_unformatted(f'Select below what you wish to export to the folder\n{path}')
        imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
        for e in to_export:
            _, to_export[e] = imgui.checkbox(e, to_export[e])

        if 'validation' in to_export and to_export['validation'] and imgui.tree_node('Validation export settings'):
            right_width = hello_imgui.dpi_window_size_factor()*90
            if len(dq_set['dq_types'])>1:
                if imgui.tree_node('Data quality types'):
                    imgui.text_unformatted("Indicate which type(s) of\ndata quality to export.")
                    if imgui.begin_table("##export_popup_validation", columns=2, flags=imgui.TableFlags_.no_clip):
                        imgui.table_setup_column("##settings_validation_left", imgui.TableColumnFlags_.width_stretch)
                        imgui.table_setup_column("##settings_validation_right", imgui.TableColumnFlags_.width_fixed)
                        imgui.table_next_row()
                        imgui.table_set_column_index(1)  # Right
                        imgui.dummy((right_width, 1))

                        for dq in dq_set['dq_types']:
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.align_text_to_frame_padding()
                            t,ht = get_DataQualityType_explanation(dq)
                            imgui.text(t)
                            gt_gui.utils.draw_hover_text(ht, text="")
                            imgui.table_next_column()
                            _, dq_set['dq_types'][dq] = imgui.checkbox(f"##{dq.name}", dq_set['dq_types'][dq])

                        imgui.end_table()
                        imgui.spacing()
                    imgui.tree_pop()

            if imgui.tree_node('Targets'):
                imgui.text_unformatted("Indicate for which target(s) you\nwant to export data quality metrics.")
                if imgui.begin_table("##export_popup_targets", columns=2, flags=imgui.TableFlags_.no_clip):
                    imgui.table_setup_column("##settings_targets_left", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_setup_column("##settings_targets_right", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_next_row()
                    imgui.table_set_column_index(1)  # Right
                    imgui.dummy((right_width, 1))

                    for t in dq_set['targets']:
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        imgui.text(f"target {t}:")
                        imgui.table_next_column()
                        _, dq_set['targets'][t] = imgui.checkbox(f"##target_{t}", dq_set['targets'][t])

                    imgui.end_table()
                    imgui.spacing()
                imgui.tree_pop()

            if imgui.begin_table("##export_popup_targets_avg", columns=2, flags=imgui.TableFlags_.no_clip):
                imgui.table_setup_column("##settings_targets_avg_left", imgui.TableColumnFlags_.width_stretch)
                imgui.table_setup_column("##settings_targets_avg_right", imgui.TableColumnFlags_.width_fixed)
                imgui.table_next_row()
                imgui.table_set_column_index(1)  # Right
                imgui.dummy((right_width, 1))

                imgui.table_next_row()
                imgui.table_next_column()
                imgui.align_text_to_frame_padding()
                imgui.text("Average over selected targets:")
                imgui.table_next_column()
                _, dq_set['targets_avg'] = imgui.checkbox("##average_over_targets", dq_set['targets_avg'])

                imgui.end_table()
                imgui.spacing()

            imgui.tree_pop()

        imgui.end_group()

    def launch_export():
        exp = []
        if 'plane gaze' in to_export and to_export['plane gaze']:
            exp.append('planeGaze')
        if 'gaze overlay video' in to_export and to_export['gaze overlay video']:
            exp.append('gaze_overlay_video')
        if 'mapped gaze video' in to_export and to_export['mapped gaze video']:
            exp.append('mapped_gaze_video')
        for s in sessions:
            g.launch_task(s, None, process.Action.EXPORT_TRIALS, export_path=path, to_export=exp)

        # export data quality for all recordings in all sessions
        if 'validation' in to_export and to_export['validation']:
            dq_types = [dq for dq in dq_set['dq_types'] if dq_set['dq_types'][dq]]
            targets  = [t for t in dq_set['targets'] if dq_set['targets'][t]]
            export.summarize_and_store_data_quality(dq_df, path/'data_quality.tsv', dq_types, targets, dq_set['targets_avg'], dq_set['include_data_loss'])

    buttons = {
        ifa6.ICON_FA_CHECK+f" Continue": (launch_export, lambda: not any((to_export[e] for e in to_export))),
        ifa6.ICON_FA_CIRCLE_XMARK+f" Cancel": None
    }

    # ask what to export
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup(f"Set what to export", set_export_config_popup, buttons = buttons, closable=True, outside=False))


async def _show_addable_recordings(g, rec_getter: typing.Callable[[],list[recording.Recording|camera_recording.Recording]], dev_type: session.RecordingType, dev: eyetracker.EyeTracker|None, sessions: list[str], generic_device_name: str=None):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker

    if dev_type==session.RecordingType.Camera:
        dev_rec_lbl = 'Camera recordings'
    else:
        if dev==eyetracker.EyeTracker.Generic:
            dev_rec_lbl = f'generic recordings for a {generic_device_name} eye tracker'
        else:
            dev_rec_lbl = f'{dev.value} eye tracker recordings'

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
        text = f'Searching the path(s) you provided for {dev_rec_lbl}.'
        imgui.text_unformatted(text)
        imgui.dummy((0,3*imgui.get_style().item_spacing.y))
        text_size = imgui.calc_text_size(text)
        size_mult = hello_imgui.dpi_window_size_factor()
        spinner_radii = [x*size_mult for x in [22, 16, 10]]
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x()+(text_size.x-2*spinner_radii[0])/2)
        imspinner.spinner_ang_triple('waitSpinner', *spinner_radii, 3.5*size_mult, c1=imgui.get_style_color_vec4(imgui.Col_.text), c2=colors.warning, c3=imgui.get_style_color_vec4(imgui.Col_.text))
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.end_group()

    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Preparing import", prepping_recs_popup, buttons = None, closable=False, outside=False))

    # step 1, find what recordings of this type of eye tracker are in the path
    recs = rec_getter()
    all_recs: list[recording.Recording] = []
    dup_recs: list[recording.Recording] = []
    known_rec_dirs = [g.sessions[s].recordings[r].info.get_source_directory() for s in g.sessions for r in g.sessions[s].recordings if g.sessions[s].recordings[r].definition.type==dev_type]
    known_rec_names= [g.sessions[s].recordings[r].info.name for s in g.sessions for r in g.sessions[s].recordings if g.sessions[s].recordings[r].definition.type==dev_type]
    for rec in recs:
        # skip duplicates. For eye tracker recordings, this checks for the source folder. For camera recordings, its checks for the full file path
        if dev_type==session.RecordingType.Camera:
            dup = rec.get_source_directory()/rec.video_file in (g.sessions[s].recordings[r].info.get_source_directory()/g.sessions[s].recordings[r].info.video_file for s in g.sessions for r in g.sessions[s].recordings if g.sessions[s].recordings[r].definition.type==dev_type)
        elif dev_type==session.RecordingType.Eye_Tracker:
            dup = any([a and b for a,b in zip([rec.get_source_directory()==d for d in known_rec_dirs],[rec.name==n for n in known_rec_names])])
        if dup:
            dup_recs.append(rec)
        else:
            all_recs.append(rec)

    # get ready to show result
    # 1. remove prepping recordings popup
    del g.popup_stack[-1]

    # 2. if nothing importable found, notify
    if not all_recs:
        if dup_recs:
            msg = f"{dev_rec_lbl} were found in the specified import paths, but could not be imported as they are already part of sessions in this gazeMapper project."
            more= "Duplicates that were not imported:\n"+('\n'.join([str(r.get_source_directory()) for r in dup_recs]))
        else:
            msg = f"No {dev_rec_lbl} were found among the specified import paths."
            more = None

        gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Nothing to import", msg, gt_gui.msg_box.MsgBox.warn, more=more)
        return

    # 3. if something importable found, show to user so they can assign them to recordings in sessions
    recordings_to_add = {i:r for i,r in enumerate(all_recs)}
    recording_assignment: dict[str, dict[str, int]] = {}
    rec_names = [r.name for r in g.study_config.session_def.recordings]
    selected_slot: tuple[str,str] = None

    def _recording_context_menu(iid: int) -> bool:
        nonlocal selected_slot
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + f" Open folder##{iid}", False)[0]:
            open_folder(recordings_to_add[iid].get_source_directory())
        if selected_slot is not None and imgui.selectable(ifa6.ICON_FA_ARROW_LEFT + f" Assign to selected recording", False)[0]:
            recording_assignment[selected_slot[0]][selected_slot[1]] = iid
            selected_slot = None
            recording_list.require_sort = True
        return False

    def _add_new_session(new_sess: str):
        sessions.append(new_sess)

    recording_lock = threading.Lock()
    recording_list = gt_gui.recording_table.RecordingTable(recordings_to_add, recording_lock, None, item_context_callback=_recording_context_menu)
    recording_list.set_local_item_remover()
    recording_list.set_act_as_drag_drop_source(True)
    if dev_type==session.RecordingType.Camera:
        recording_list.show_hide_columns({'Eye Tracker': False, 'Participant': False, 'Source Directory': True, 'Video File': True})
    not_assigned_filter = gt_gui.recording_table.Filter(lambda iid, _: iid not in (recording_assignment[s][r] for s in recording_assignment for r in recording_assignment[s]))
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
                    if sess.definition.get_recording_def(r).type!=dev_type:
                        continue
                    disable = False
                    rec: recording.Recording = None
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
                            if dev_type==session.RecordingType.Eye_Tracker:
                                recording_list.draw_eye_tracker_widget(rec, align=True)
                            else:
                                imgui.text(rec.video_file)
                        imgui.table_next_column()
                        if has_rec:
                            if dev_type==session.RecordingType.Eye_Tracker:
                                imgui.text(rec.name)
                            else:
                                imgui.text(str(rec.source_directory))
                        else:
                            imgui.text('drop recording here to assign')
                        imgui.table_next_column()
                        if has_rec and dev_type==session.RecordingType.Eye_Tracker:
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

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": lambda: async_thread.run(_import_recordings(g, recordings_to_add, recording_assignment, generic_device_name)),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Assign and import recordings", list_recs_popup, buttons = buttons, closable=True, outside=False))

async def _import_recordings(g, recordings: list[recording.Recording|camera_recording.Recording], recording_assignment: dict[str, dict[str, int]], generic_device_name: str|None):
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
            g.launch_task(s, r, process.Action.IMPORT, generic_device_name=generic_device_name)

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
            gt_gui.utils.push_popup(g,
                gt_gui.msg_box.msgbox, "About the filter bar",
                "This is the filter bar. By typing inside it you can search your recording list inside the eye tracker, name, participant and project properties.",
                gt_gui.msg_box.MsgBox.info
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
    invalid = False
    generic_et_idx = -1
    generic_et_name = ''
    eye_tracker = eyetracker.EyeTracker(eyetracker.eye_tracker_names[combo_value])
    sessions = get_and_filter_eligible_sessions(g, sessions, session.RecordingType.Eye_Tracker)

    def add_recs_popup():
        nonlocal combo_value, eye_tracker, generic_et_idx, generic_et_name
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
        changed, combo_value = imgui.combo("##select_eye_tracker", combo_value, eyetracker.eye_tracker_names)
        if changed:
            eye_tracker = eyetracker.EyeTracker(eyetracker.eye_tracker_names[combo_value])
            if eye_tracker!=eyetracker.EyeTracker.Generic:
                invalid = False
                generic_et_idx = -1
                generic_et_name = ''
        imgui.pop_item_width()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        if eye_tracker==eyetracker.EyeTracker.Generic:
            imgui.text_unformatted("Choose which generic eye tracker you want")
            imgui.text_unformatted("to import data for:")
            imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
            invalid = not g.study_config.import_known_custom_eye_trackers
            if invalid:
                imgui.push_style_color(imgui.Col_.text, colors.error)
                imgui.text("No custom eye trackers configured, fix project settings")
                imgui.pop_style_color()
            else:
                full_width = imgui.get_content_region_avail().x
                imgui.set_cursor_pos_x(full_width*.3)
                imgui.push_item_width(full_width*.6)
                changed, generic_et_idx = imgui.combo('##generic_et_name_selector', generic_et_idx, g.study_config.import_known_custom_eye_trackers)
                if changed:
                    generic_et_name = g.study_config.import_known_custom_eye_trackers[generic_et_idx]
                imgui.pop_item_width()
            imgui.dummy((0,2*imgui.get_style().item_spacing.y))

        imgui.end_group()

        return combo_value, eye_tracker

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": (lambda: async_thread.run(_show_addable_recordings(g, lambda: recording.find_recordings(paths, eye_tracker, None if not generic_et_name else generic_et_name), session.RecordingType.Eye_Tracker, eye_tracker, sessions, None if not generic_et_name else generic_et_name)), lambda: invalid or (eye_tracker==eyetracker.EyeTracker.Generic and generic_et_idx==-1)),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }

    # ask what type of eye tracker we should be looking for
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select eye tracker", add_recs_popup, buttons = buttons, closable=True, outside=False))

def camera_show_glob_filter_config(g, paths, sessions):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    glob_filter = '*.mp4,*.mov,*.avi,*.mkv'

    def setting_popup():
        nonlocal glob_filter
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(g._icon_font)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        glob_filter = _set_glob_filter_for_camera(glob_filter)
        imgui.end_group()

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": lambda: add_camera_recordings(g, paths, glob_filter, sessions),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select device", setting_popup, buttons = buttons, closable=True, outside=False))

def add_camera_recordings(g, paths: list[pathlib.Path], glob_filter: str, sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    sessions = get_and_filter_eligible_sessions(g, sessions, session.RecordingType.Camera)
    async_thread.run(_show_addable_recordings(g, lambda: _find_camera_recordings(paths, glob_filter), session.RecordingType.Camera, None, sessions))

def _find_camera_recordings(paths: list[pathlib.Path], glob_filter: str) -> list[camera_recording.Recording]:
    extensions = {'.'+x.strip('.* ') for x in glob_filter.split(',')}
    video_paths: list[pathlib.Path] = []
    for p in paths:
        if p.is_file():
            if p.suffix in extensions:
                video_paths.append(p)
        elif p.is_dir():
            video_paths.extend([pth.resolve() for pth in p.glob("**/*") if pth.suffix in extensions])
    # turn into list of recordings
    return [camera_recording.Recording('',p.name,p.parent, duration=video_utils.get_video_duration(p)) for p in video_paths]

def get_and_filter_eligible_sessions(g, sessions: list[str], dev_type:session.RecordingType) -> list[str]:
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if not sessions:
        sessions: list[str] = []
        for s in g.sessions:
            if not g.sessions[s].missing_recordings(dev_type):
                continue
            sessions.append(s)
    else:
        sessions = [s for s in sessions if g.sessions[s].missing_recordings(dev_type)]
    return sessions

def _set_glob_filter_for_camera(glob_filter):
    imgui.text_unformatted("Extension filter to search for video files in")
    imgui.text_unformatted("the selected paths (leave empty to not filter)")
    imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
    full_width = imgui.get_content_region_avail().x
    imgui.push_item_width(full_width*.8)
    _, glob_filter = imgui.input_text('##file_filter', glob_filter)
    imgui.pop_item_width()
    imgui.dummy((0,2*imgui.get_style().item_spacing.y))
    return glob_filter

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
    glob_filter = '*.mp4,*.mov,*.avi,*.mkv'

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
            glob_filter = _set_glob_filter_for_camera(glob_filter)

        imgui.end_group()

        return combo_value, dev_type

    def _run():
        match dev_type:
            case session.RecordingType.Eye_Tracker:
                add_eyetracking_recordings(g, paths, sessions)
            case session.RecordingType.Camera:
                add_camera_recordings(g, paths, glob_filter, sessions)

    if len(options)==1 and options[0]==session.RecordingType.Eye_Tracker:
        # no need to show selection popup, as there is only one choice and nothing to configure for that choice
        _run()
        return

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": _run,
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }

    # ask what type of device recordings we want to import
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select device", choose_dev_popup, buttons = buttons, closable=True, outside=False))
