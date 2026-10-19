import pathlib
import typing
import shutil
import os
import asyncio
import subprocess
import pathvalidate
import threading
import natsort
import dataclasses
import copy
from imgui_bundle import imgui, imspinner, icons_fontawesome_6 as ifa6

from glassesTools import annotation, aruco, async_thread, camera_recording, data_types, eyetracker, gui as gt_gui, marker as gt_marker, naming as gt_naming, platform, process_pool, recording, video_utils
from glassesTools.validation import config as val_config, default_poster, dynamic as val_dynamic, export
from glassesTools.gui import colors

from . import settings_editor, utils
from ... import config, marker, marker_assembly, process, session
from ...process import export_trials

_picker_last_folder = None


def _snapshot_export_preferences(export_config: export_trials.ExportConfig) -> dict:
    preferences = {}
    for action_field in dataclasses.fields(export_config):
        action_name = action_field.name
        action = getattr(export_config, action_name)
        action_preferences = {
            setting.name: copy.deepcopy(getattr(action, setting.name))
            for setting in dataclasses.fields(action)
            if setting.name not in {'available', 'sess', 'recs'}
            and not (action_name == 'validation' and setting.name == 'episodes')
        }
        if action_name == 'validation':
            action_preferences['episodes'] = {
                event_name: {
                    setting.name: copy.deepcopy(getattr(episode_config, setting.name))
                    for setting in dataclasses.fields(episode_config)
                    if setting.name not in {'available', 'do_it', 'sess', 'recs', 'df'}
                }
                for event_name, episode_config in action.episodes.items()
            }
        preferences[action_name] = action_preferences
    return preferences


def _restore_export_preferences(export_config: export_trials.ExportConfig, preferences: dict | None, export_filters: dict):
    if preferences is None:
        return
    for action_name, action_preferences in preferences.items():
        action = getattr(export_config, action_name, None)
        if action is None:
            continue
        for setting_name, value in action_preferences.items():
            if action_name == 'validation' and setting_name == 'episodes':
                for event_name, episode_preferences in value.items():
                    episode_config = action.episodes.get(event_name)
                    if episode_config is None:
                        continue
                    for episode_setting, episode_value in episode_preferences.items():
                        if not hasattr(episode_config, episode_setting):
                            continue
                        current_value = getattr(episode_config, episode_setting)
                        if isinstance(current_value, dict) and isinstance(episode_value, dict):
                            for key in current_value.keys() & episode_value.keys():
                                current_value[key] = copy.deepcopy(episode_value[key])
                        else:
                            setattr(episode_config, episode_setting, copy.deepcopy(episode_value))
                continue
            if not hasattr(action, setting_name):
                continue
            if isinstance(value, set):
                valid_options = export_filters.get(action_name, {}).get(setting_name)
                if valid_options is not None:
                    value = value.intersection(valid_options)
            setattr(action, setting_name, copy.deepcopy(value))


def get_folder_picker(g, reason: str, *args, **kwargs):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    def select_callback(selected: list[pathlib.Path]):
        global _picker_last_folder
        if len(selected)==1 and selected[0].is_dir():
            _picker_last_folder = selected[0]
        else:
            _picker_last_folder = selected[0].parent
        match reason:
            case 'loading' | 'creating':
                try_load_project(g, selected, action=reason)
            case 'add_et_recordings':
                add_eyetracking_recordings(g, selected, *args, **kwargs)
            case 'add_cam_recordings':
                camera_show_glob_filter_config(g, selected, *args, **kwargs)
            case 'set_default_cam_cal':
                set_default_cam_cal(g, selected[0], *args, **kwargs)
            case 'set_cam_cal':
                set_cam_cal(selected[0], *args, **kwargs)
            case 'deploy_aruco':
                aruco.deploy_marker_images(selected[0], 1000, *args, **kwargs)
            case 'deploy_gv_poster_pdf':
                default_poster.deploy_default_pdf(selected[0])
            case 'export':
                show_export_config(g, selected[0], *args, **kwargs)
            case 'new_validation_config_dir':
                kwargs['callback'](selected[0])
            case 'deploy_gv_dynamic_script':
                not_copied = val_dynamic.deploy_setup_and_script(selected[0])
                if not_copied:
                    buttons = {
                        ifa6.ICON_FA_CHECK+" Yes": lambda: val_dynamic.deploy_setup_and_script(selected[0], overwrite=True),
                        ifa6.ICON_FA_CIRCLE_XMARK+" No": None
                    }
                    files = '- '+('\n- '.join(not_copied))
                    gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Overwrite files", f"The following files were not deployed as they already exist\nin the selected folder:\n{files}\n\nDo you want to overwrite these files?", gt_gui.msg_box.MsgBox.question, buttons)
            case 'gv_dynamic_import_setup':
                set_dynamic_validation_plane_config(g, selected[0], **kwargs)
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
        case 'new_validation_config_dir' | 'gv_dynamic_import_setup':
            header = "Select or drop setup json file for dynamic validation PsychoPy script"
            allow_multiple = False
            picker_type = gt_gui.file_picker.FilePicker
        case 'deploy_gv_dynamic_script':
            header = "Select folder to store dynamic validation PsychoPy script and default configuration"
            allow_multiple = False
            picker_type = gt_gui.file_picker.DirPicker
        case _:
            raise ValueError(f'reason "{reason}" not understood')
    picker = picker_type(title=header, allow_multiple=allow_multiple, callback=select_callback, start_dir=_picker_last_folder)
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

def set_dynamic_validation_plane_config(g, config_file_path: pathlib.Path, p_def: marker_assembly.Definition_GlassesValidator):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    path = config.guess_config_dir(g.study_config.working_directory)
    p_dir = path / p_def.name
    try:
        if config_file_path.suffix!='.json':
            raise ValueError('The selected file is not a json file. Please select a json file.')
        auto_coding_setup = val_dynamic.setup_to_plane_config(p_dir, config_file_path.parent, config_file_path.name)
    except Exception as exc:
        gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Error converting setup", f"There was an error trying to load the dynamic validation setup:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
        return
    # N.B.: all we have to do here for the validator definition. File watcher will catch the change and reload it.
    set_auto_coding_for_dynamic_validation_plane(g, p_def, auto_coding_setup)

def set_auto_coding_for_dynamic_validation_plane(g, p_def: marker_assembly.Definition_GlassesValidator, auto_coding_setup: dict[str,list[gt_marker.MarkerID]]):
    def _apply_individual_markers_if_needed():
        all_markers = set(auto_coding_setup['start_markers'])
        all_markers.update(auto_coding_setup['end_markers'])
        applied = False
        for m in all_markers:
            if not any((im.id==m.m_id and im.aruco_dict_id==m.aruco_dict_id for im in g.study_config.individual_markers)):
                g.study_config.individual_markers.append(marker.Marker(m.m_id, detect_only=True, aruco_dict_id=m.aruco_dict_id, marker_border_bits=auto_coding_setup['marker_border_bits']))
                applied = True
        return applied
    def _apply_automatic_val_coding():
        cs = [cs for cs in g.study_config.coding_setup if cs['event_type']==annotation.EventType.Validate and p_def.name in cs['marker_assemblies']]
        auto_code  = config.AutoCodeEpisodes(
            start_markers = auto_coding_setup['start_markers'],
            end_markers   = auto_coding_setup['end_markers'],
        )
        auto_code.apply_defaults()
        validation_setup = None
        if auto_coding_setup['split_consecutive']:
            validation_setup = config.ValidationSetup(
                dynamic_split_consecutive = True
            )
            validation_setup.apply_defaults()
        if not cs:
            g.study_config.coding_setup.append(config.EventSetup(
                event_type = annotation.EventType.Validate,
                name = f'Dynamic validation ({p_def.name})',
                description = f'Dynamic validation setup for {p_def.name}',
                marker_assemblies = set([p_def.name]),
                auto_code  = auto_code,
                validation_setup = validation_setup
            ))
        else:
            if cs[0]['auto_code'] is None:
                cs[0]['auto_code'] = auto_code
            else:
                cs[0]['auto_code']['start_markers'] = auto_coding_setup['start_markers']
                cs[0]['auto_code']['end_markers']   = auto_coding_setup['end_markers']
            if validation_setup is not None and cs[0]['validation_setup'] is None:
                cs[0]['validation_setup'] = validation_setup
            elif cs[0]['validation_setup'] is not None:
                cs[0]['validation_setup']['dynamic_split_consecutive'] = auto_coding_setup['split_consecutive']
        _apply_individual_markers_if_needed()
        # persist changed config
        g.study_config.store_as_json()

    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    cs = [cs for cs in g.study_config.coding_setup if cs['event_type']==annotation.EventType.Validate and p_def.name in cs['marker_assemblies']]
    exists = not not cs
    if exists:
        if len(cs)>1:
            raise RuntimeError(f'Multiple coding setups found for {annotation.tooltip_map[annotation.EventType.Validate]}s for plane {p_def.name}, cannot proceed')
        matches = cs[0]['auto_code'] and all((cs[0]['auto_code'][f]==auto_coding_setup[f] for f in ('start_markers','end_markers')))
        matches = matches and ((cs[0]['validation_setup'] is None and auto_coding_setup['split_consecutive']) or (cs[0]['validation_setup'] is not None and cs[0]['validation_setup']['dynamic_split_consecutive']==auto_coding_setup['split_consecutive']))
        if matches:
            # nothing to do, do check all individual markers are also defined
            if _apply_individual_markers_if_needed():
                # persist changed config
                g.study_config.store_as_json()
            return
    # ask user if they want to apply settings
    buttons = {
        ifa6.ICON_FA_CHECK+" Yes": lambda: _apply_automatic_val_coding(),
        ifa6.ICON_FA_CIRCLE_XMARK+" No": None
    }
    msg = f'The following setup for automatic coding of {annotation.tooltip_map[annotation.EventType.Validate]}s was found upon import of the dynamic validation setup for the {p_def.name} plane:\n'
    for f in ('start_markers','end_markers'):
        msg += f'{f}: ['+(', '.join((gt_marker.marker_ID_to_str(m) for m in auto_coding_setup[f])))+']\n'
    msg += f"Split consecutive repetitions: {auto_coding_setup['split_consecutive']}\n"
    if exists:
        msg += f'\nThe following setup for automatic coding of {annotation.tooltip_map[annotation.EventType.Validate]}s using the {p_def.name} plane is already set:\n'
        for f in ('start_markers','end_markers'):
            msg += f'{f}: ['+(', '.join((gt_marker.marker_ID_to_str(m) for m in cs[0]['auto_code'][f])))+']\n'
        msg += f"Split consecutive repetitions: {False if cs[0]['validation_setup'] is None else cs[0]['validation_setup']['dynamic_split_consecutive']}\n"
        msg += '\nDo you want to overwrite this configuration with the above?'
    else:
        msg += '\nDo you want to apply this configuration?'
    gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, ("Overwrite" if exists else "Apply")+f" automated {annotation.tooltip_map[annotation.EventType.Validate]} coding config?", msg, gt_gui.msg_box.MsgBox.question, buttons)

def set_default_cam_cal(g, cal_path: str|pathlib.Path, rec_def: session.RecordingDefinition, rec_def_path: pathlib.Path):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    rec_def.set_default_cal_file(cal_path, rec_def_path)
    g._load_calibration(rec_def)

def set_cam_cal(cal_path: str|pathlib.Path, working_directory: str|pathlib.Path):
    cal_path = pathlib.Path(cal_path)
    working_directory = pathlib.Path(working_directory)
    shutil.copyfile(str(cal_path), str(working_directory / gt_naming.scene_camera_calibration_fname))

def delete_cam_cal(working_directory: str|pathlib.Path):
    (pathlib.Path(working_directory) / gt_naming.scene_camera_calibration_fname).unlink(missing_ok=True)

def make_marker_assembly(g, assembly_type: marker_assembly.Type, assembly_name: str, is_dynamic: bool, validation_config_file: str):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    path = config.guess_config_dir(g.study_config.working_directory)
    assembly_dir = path / assembly_name
    # deal with dynamic glassesValidator plane
    extra = {}
    if is_dynamic:
        extra['use_default'] = not validation_config_file
        extra['is_dynamic'] = True
        # deploy converted config files
        assembly_dir.mkdir(exist_ok=True)
        try:
            if validation_config_file:
                dcf = pathlib.Path(validation_config_file)
                auto_coding_setup = val_dynamic.setup_to_plane_config(assembly_dir, dcf.parent, dcf.name)
            else:
                auto_coding_setup = val_dynamic.setup_to_plane_config(assembly_dir)
        except Exception as exc:
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Error converting setup", f"There was an error trying to load the dynamic validation\nsetup when creating the new validation plane:\n{exc}", gt_gui.msg_box.MsgBox.error, more=gt_gui.utils.get_traceback(type(exc), exc, exc.__traceback__))
            shutil.rmtree(assembly_dir)
            return
    # make marker assembly
    p_def = marker_assembly.make_definition(assembly_type, assembly_name, assembly_dir, **extra)
    # store to file
    if not assembly_dir.is_dir():
        assembly_dir.mkdir()
    p_def.store_as_json(assembly_dir)
    # append to known marker assemblies
    g.study_config.marker_assemblies.append(p_def)
    # deal with further setup for dynamic validation
    if is_dynamic:
        set_auto_coding_for_dynamic_validation_plane(g, p_def, auto_coding_setup)

def make_coding_setup(g, coding_name: str, coding_type: annotation.EventType):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    # append to known coding setups
    g.study_config.coding_setup.append(config.EventSetup(name=coding_name, event_type=coding_type))
    # set default hotkey if first event of this type
    cs_of_type = [cs for cs in g.study_config.coding_setup if cs['event_type']==coding_type]
    if len(cs_of_type)==1 and coding_type in annotation.default_hotkeys:
        cs_of_type[0]['hotkey'] = annotation.default_hotkeys[coding_type]
    # store config
    g.study_config.store_as_json()

def delete_marker_assembly(study_config: config.Study, assembly: marker_assembly.Definition):
    # remove config directory for the marker assembly
    path = config.guess_config_dir(study_config.working_directory)
    assembly_dir = path / assembly.name
    if assembly_dir.is_dir():
        shutil.rmtree(assembly_dir)

def glasses_validator_plane_check_or_deploy_config(study_config: config.Study, pl: marker_assembly.Definition_GlassesValidator):
    if not isinstance(pl, marker_assembly.Definition_GlassesValidator) or pl.use_default:
        return
    # check if there are already are validation setup files
    working_dir = config.guess_config_dir(study_config.working_directory)/pl.name
    try:
        val_config.get_validation_setup(working_dir)
    except:
        # no config file, deploy
        default_poster.deploy_config(working_dir)
    else:
        # already exists, nothing to do
        pass

def glasses_validator_deploy_config(g, pl: marker_assembly.Definition_GlassesValidator):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if not isinstance(pl, marker_assembly.Definition_GlassesValidator):
        return
    working_dir = config.guess_config_dir(g.study_config.working_directory)/pl.name
    not_deployed = default_poster.deploy_config(working_dir)
    if not_deployed:
        buttons = {
            ifa6.ICON_FA_CHECK+" Yes": lambda: default_poster.deploy_config(working_dir,overwrite=True),
            ifa6.ICON_FA_CIRCLE_XMARK+" No": None
        }
        gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Overwrite config?", "The following files already existed and were not deployed:\n- "+('\n- '.join(not_deployed))+"\nDo you want to overwrite these files?", gt_gui.msg_box.MsgBox.warn, buttons)

def make_recording_definition(study_config: config.Study, r_type: session.RecordingType, name: str):
    # append to defined recordings
    study_config.session_def.recordings.append(session.RecordingDefinition(name,r_type, None, None))
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

def make_individual_marker(study_config: config.Study, mark_id: int, aruco_dict_id: int, mark_detect_only: bool, mark_size: float=None):
    # append to defined recordings
    study_config.individual_markers.append(marker.Marker(mark_id, mark_detect_only, None if mark_detect_only else mark_size, aruco_dict_id))
    # store config
    study_config.store_as_json()

def delete_individual_marker(study_config: config.Study, mark: marker.Marker|None, index: int|None=None):
    # remove from defined individual markers
    if index is not None:
        del study_config.individual_markers[index]
    else:
        study_config.individual_markers = [m for m in study_config.individual_markers if m.id!=mark.id]
    # store config
    study_config.store_as_json()

def add_individual_marker_settings(study_config: config.Study, name: str, copy_from: str|None = None):
    study_config.add_individual_marker_settings(name, copy_from)
    study_config.store_as_json()

def new_session_button(g, notify_func: typing.Callable[[str], None]|None = None):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    new_sess_name = ''
    def _valid_sess_name():
        if not new_sess_name:
            return False, 'Session name cannot be empty'
        if not pathvalidate.is_valid_filename(new_sess_name, "auto"):
            return False, 'Session name is not a valid filename'
        if new_sess_name=='config':
            return False, 'Session name cannot be "config"'
        if g.sessions.get(new_sess_name, None) is not None:
            return False, f'Session with this name already exists, choose a unique name'
        return True, ''
    def _add_sess_popup():
        nonlocal new_sess_name
        imgui.dummy((30*imgui.calc_text_size('x').x,0))
        valid, reason = _valid_sess_name()
        if imgui.begin_table("##new_sess_info",2):
            imgui.table_setup_column("##new_sess_infos_left", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column("##new_sess_infos_right", imgui.TableColumnFlags_.width_stretch)
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.align_text_to_frame_padding()
            if not valid:
                imgui.push_style_color(imgui.Col_.text, colors.error)
            imgui.text("Session name")
            if not valid:
                imgui.pop_style_color()
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            _,new_sess_name = imgui.input_text("##new_sess_name",new_sess_name)
            imgui.end_table()
        if not valid:
            imgui.text_colored(colors.error, reason)

    def _make_session():
        make_session(g.project_dir, new_sess_name)
        if notify_func is not None:
            notify_func(new_sess_name)

    buttons = {
        ifa6.ICON_FA_CHECK+" Create session": (_make_session, lambda: not _valid_sess_name()[0]),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Add session", _add_sess_popup, buttons = buttons, button_keymap={0:imgui.Key.enter}, outside=False))

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

def open_folder(g, path: pathlib.Path):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if not path.is_dir():
        gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Folder not found", f"The folder you're trying to open\n{path}\ncould not be found.", gt_gui.msg_box.MsgBox.warn)
        return
    open_url(str(path))

def remove_folder(g, folder: pathlib.Path):
    if folder.is_dir():
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Could not delete folder", f"Failed to delete {folder}:\n{exc}\n\nClose any files or folders open inside it, then try again if still visible in the app, or manually delete the folder.", gt_gui.msg_box.MsgBox.warn)

def show_action_options(session_name: str, rec_name: str|None, action: process.Action, g):
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
                    False, 'Show visualization', 'Show a viewer that allows to follow the processing of the video. Each frame is shown overlaid with info about detected markers and assemblies.'
                ], 'visualization_show_rejected_markers': [
                    False, 'Show rejected markers', 'Rejected ArUco marker candidates are also shown in the viewer. Possibly useful for debug.'
                ]}
        case process.Action.RUN_SYNC_FUNCTION:
            options = {'show_visualization': [
                    False, 'Show visualization', 'Show a viewer that allows to follow the processing of the video. Each frame is shown overlaid with output of the synchronization function.'
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
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
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
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup(f"Set options##{session_name}_{rec_name}", set_action_options_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))


def show_export_config(g, path: str|pathlib.Path, sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    path = pathlib.Path(path)

    export_config, anything_to_do = export_trials.prep_export(g.study_config.working_directory, sessions)
    if not anything_to_do:
        gt_gui.utils.push_popup(g, gt_gui.msg_box.msgbox, "Nothing to export", "No data available for export.", gt_gui.msg_box.MsgBox.error)
        return

    configured_planes = {
        definition.name for definition in g.study_config.marker_assemblies
        if isinstance(definition, marker_assembly.Definition_Plane_Aruco)
    }
    plane_gaze_events = [
        event for event in process.get_specific_event_types(g.study_config, check_specific_fields=['marker_assemblies'])
        if any(plane in configured_planes for plane in event['marker_assemblies'])
    ]
    gaze_offset_events = process.get_specific_event_types(g.study_config, check_specific_fields=['gaze_offset_setup'])
    export_filters = {
        'plane_gaze': {
            'planes': sorted({plane for event in plane_gaze_events for plane in event['marker_assemblies'] if plane in configured_planes}),
            'episodes': sorted(event['name'] for event in plane_gaze_events),
        },
        'head_pose': {
            'planes': sorted(configured_planes),
            'episodes': export_trials.get_head_pose_episode_options(g.study_config.coding_setup, g.study_config.marker_assemblies),
        },
        'gaze_offsets': {
            'planes': export_trials.get_gaze_offset_plane_options(g.study_config.marker_assemblies, gaze_offset_events),
            'episodes': sorted(event['name'] for event in gaze_offset_events),
        },
    }
    _restore_export_preferences(export_config, getattr(g, '_last_export_preferences', None), export_filters)

    c_fields = [f for f in dataclasses.fields(export_config)]
    def set_export_config_popup():
        nonlocal export_config
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.text_unformatted(f'Select below what you wish to export to the folder\n{path}')
        imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
        for f in c_fields:
            fld = getattr(export_config, f.name)
            if not fld.available:
                continue
            field_doc = export_trials.export_config_doc.get(f.name)
            field_label = field_doc.display_string if field_doc else f.name
            _, fld.do_it = imgui.checkbox(field_label, fld.do_it)
            if field_doc and field_doc.doc_str:
                imgui.same_line()
                gt_gui.utils.draw_hover_text(field_doc.doc_str, text='')

            t_fields = [ff for ff in dataclasses.fields(fld) if ff.name not in ('available','do_it','sess','recs')]
            if t_fields and fld.do_it and imgui.tree_node(f'{field_label} settings'):
                right_width = imgui.get_window_dpi_scale()*90
                if f.name=='validation':
                    unit_header_doc = field_doc.children.get('include_unit_header_row') if field_doc else None
                    unit_header_label = unit_header_doc.display_string if unit_header_doc else 'Include units row'
                    _, fld.include_unit_header_row = imgui.checkbox(f'{unit_header_label}##validation_unit_header_row', fld.include_unit_header_row)
                    if unit_header_doc and unit_header_doc.doc_str:
                        imgui.same_line()
                        gt_gui.utils.draw_hover_text(unit_header_doc.doc_str, text='')
                    imgui.spacing()
                    need_node = len(fld.episodes)>1
                    for nm in fld.episodes:
                        cs = [c for c in g.study_config.coding_setup if c['name']==nm][0]
                        if need_node:
                            if not imgui.tree_node(f'{nm} ({cs["description"]})'):
                                continue

                        if len(fld.episodes[nm].d_types)>1:
                            if imgui.tree_node('Data quality types'):
                                imgui.text_unformatted("Indicate which type(s) of\ndata quality to export.")
                                if imgui.begin_table("##export_popup_validation", columns=2, flags=imgui.TableFlags_.no_clip):
                                    imgui.table_setup_column("##settings_validation_left", imgui.TableColumnFlags_.width_stretch)
                                    imgui.table_setup_column("##settings_validation_right", imgui.TableColumnFlags_.width_fixed)
                                    imgui.table_next_row()
                                    imgui.table_set_column_index(1)  # Right
                                    imgui.dummy((right_width, 1))

                                    for dq in fld.episodes[nm].d_types:
                                        imgui.table_next_row()
                                        imgui.table_next_column()
                                        imgui.align_text_to_frame_padding()
                                        t,ht = data_types.get_explanation(dq)
                                        imgui.text(t)
                                        gt_gui.utils.draw_hover_text(ht, text="")
                                        imgui.table_next_column()
                                        _, fld.episodes[nm].d_types[dq] = imgui.checkbox(f"##{dq.name}", fld.episodes[nm].d_types[dq])

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

                                for t in fld.episodes[nm].targets:
                                    imgui.table_next_row()
                                    imgui.table_next_column()
                                    imgui.align_text_to_frame_padding()
                                    imgui.text(f"target {t}:")
                                    imgui.table_next_column()
                                    _, fld.episodes[nm].targets[t] = imgui.checkbox(f"##target_{t}", fld.episodes[nm].targets[t])

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
                            targets_avg_doc = field_doc.children.get('targets_avg') if field_doc else None
                            targets_avg_label = targets_avg_doc.display_string if targets_avg_doc else 'Average over selected targets'
                            imgui.text(targets_avg_label+':')
                            if targets_avg_doc and targets_avg_doc.doc_str:
                                imgui.same_line()
                                gt_gui.utils.draw_hover_text(targets_avg_doc.doc_str, text='')
                            imgui.table_next_column()
                            _, fld.episodes[nm].targets_avg = imgui.checkbox("##average_over_targets", fld.episodes[nm].targets_avg)

                            imgui.end_table()
                            imgui.spacing()

                        if need_node:
                            imgui.tree_pop()
                else:
                    if imgui.begin_table(f"##export_popup_{f.name}", columns=2, flags=imgui.TableFlags_.no_clip):
                        imgui.table_setup_column(f"##settings_{f.name}_left", imgui.TableColumnFlags_.width_stretch)
                        imgui.table_setup_column(f"##settings_{f.name}_right", imgui.TableColumnFlags_.width_fixed)
                        imgui.table_next_row()
                        imgui.table_set_column_index(1)  # Right
                        imgui.dummy((right_width, 1))

                        for tf in t_fields:
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.align_text_to_frame_padding()
                            if f.name == 'plane_gaze' and tf.name in 'plane_gaze_types':
                                gaze_types_field = next(tf for tf in t_fields if tf.name == 'plane_gaze_types')
                                gaze_types_doc = field_doc.children.get('plane_gaze_types') if field_doc else None
                                gaze_types_label = gaze_types_doc.display_string if gaze_types_doc else 'Gaze types'
                                imgui.text(gaze_types_label + ':')
                                if gaze_types_doc and gaze_types_doc.doc_str:
                                    gt_gui.utils.draw_hover_text(gaze_types_doc.doc_str, text='')
                                imgui.table_next_column()
                                imgui.set_next_item_width(imgui.get_content_region_avail().x)
                                fld.plane_gaze_types = settings_editor.draw_list_set_editor(
                                    'plane_gaze_types', fld.plane_gaze_types, gaze_types_field.type,
                                    typing.get_args(gaze_types_field.type),
                                    gaze_types_doc.children if gaze_types_doc else {},
                                )
                                continue
                            if f.name == 'plane_gaze' and tf.name in 'head_ref_gaze_types':
                                head_ref_types_field = next(tf for tf in t_fields if tf.name == 'head_ref_gaze_types')
                                head_ref_types_doc = field_doc.children.get('head_ref_gaze_types') if field_doc else None
                                head_ref_types_label = head_ref_types_doc.display_string if head_ref_types_doc else 'Head-referenced gaze types'
                                imgui.text(head_ref_types_label + ':')
                                if head_ref_types_doc and head_ref_types_doc.doc_str:
                                    gt_gui.utils.draw_hover_text(head_ref_types_doc.doc_str, text='')
                                imgui.table_next_column()
                                imgui.set_next_item_width(imgui.get_content_region_avail().x)
                                fld.head_ref_gaze_types = settings_editor.draw_list_set_editor(
                                    'head_ref_gaze_types', fld.head_ref_gaze_types, head_ref_types_field.type,
                                    typing.get_args(head_ref_types_field.type),
                                    head_ref_types_doc.children if head_ref_types_doc else {},
                                )
                                continue
                            if f.name in export_filters and tf.name in export_filters[f.name]:
                                selector_name, options = tf.name, export_filters[f.name][tf.name]
                                selector_doc = field_doc.children.get(selector_name) if field_doc else None
                                selector_label = selector_doc.display_string if selector_doc else selector_name.capitalize()
                                imgui.align_text_to_frame_padding()
                                imgui.text(selector_label + ':')
                                if selector_doc and selector_doc.doc_str:
                                    gt_gui.utils.draw_hover_text(selector_doc.doc_str, text='')
                                imgui.table_next_column()
                                if options:
                                    imgui.set_next_item_width(imgui.get_content_region_avail().x)
                                    literal_type = typing.Literal.__getitem__(tuple(options))
                                    selector_type = set[literal_type]
                                    setattr(fld, selector_name, settings_editor.draw_list_set_editor(
                                        f'{f.name}_{selector_name}', getattr(fld, selector_name),
                                        selector_type, (literal_type,), {},
                                    ))
                                else:
                                    imgui.text_disabled('No configured choices')
                                continue
                            field_settings_doc = field_doc.children.get(tf.name) if field_doc else None
                            setting_label = field_settings_doc.display_string if field_settings_doc else tf.name.replace('_',' ').capitalize()
                            imgui.text(setting_label+':')
                            if field_settings_doc and field_settings_doc.doc_str:
                                gt_gui.utils.draw_hover_text(field_settings_doc.doc_str, text='')
                            imgui.table_next_column()
                            tfld = getattr(fld, tf.name)
                            _, tmp = imgui.checkbox(f'##{tf.name}', tfld)
                            setattr(fld, tf.name, tmp)
                        imgui.end_table()
                imgui.tree_pop()
            imgui.spacing()

        imgui.end_group()

    def launch_export():
        g._last_export_preferences = _snapshot_export_preferences(export_config)
        fields = [f for f in dataclasses.fields(export_config) if f.name not in ('validation','eye_tracker_sync','trial_data_quality')]
        sess = list({s for f in fields for s in getattr(export_config, f.name).sess})
        for s in sess:
            this_config = copy.deepcopy(export_config)
            this_config.validation.episodes.clear()  # validation export is handled separately below, clear here to avoid copying large amounts of data
            # check there is anything to do for this session, for each of the tasks
            do_it = False
            for f in fields:
                exp = getattr(this_config, f.name)
                if (this_do_it:=exp.do_it):
                    exp.recs = [r for ss,r in zip(exp.sess, exp.recs) if ss==s]
                    exp.sess = s
                this_do_it = this_do_it and bool(exp.recs)
                do_it = do_it or this_do_it
            if do_it:
                g.launch_task(s, None, process.Action.EXPORT_TRIALS, export_path=path, export_config=this_config)

        # export trial data quality for all selected recordings
        if export_config.trial_data_quality.do_it:
            sess_recs = list(zip(export_config.trial_data_quality.sess, export_config.trial_data_quality.recs))
            export_trials.export_trial_data_quality(g.study_config.working_directory, sess_recs, path)

        # export data quality for all recordings in all sessions
        if export_config.validation.do_it:
            export_trials.export_validation(path, export_config.validation)

        # export et sync for all recordings in all sessions
        if export_config.eye_tracker_sync.do_it:
            sess_recs = [(s,r) for s,r in zip(export_config.eye_tracker_sync.sess, export_config.eye_tracker_sync.recs) if s in sess]
            export_trials.export_et_sync(g.study_config.working_directory, sess_recs, path)

    buttons = {
        ifa6.ICON_FA_CHECK+f" Continue": (launch_export, lambda: not any(getattr(export_config, f.name).do_it for f in c_fields)),
        ifa6.ICON_FA_CIRCLE_XMARK+f" Cancel": None
    }

    # ask what to export
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup(f"Set what to export", set_export_config_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))


def _get_addable_recordings_label(dev_type: session.RecordingType, dev: eyetracker.EyeTracker|None, generic_device_name: str|None = None) -> str:
    if dev_type==session.RecordingType.Camera:
        return 'Camera recordings'
    if dev==eyetracker.EyeTracker.Generic:
        return f'generic recordings for a {generic_device_name} eye tracker'
    return f'{dev.value} eye tracker recordings'


def _push_preparing_recordings_popup(g, dev_type: session.RecordingType, dev: eyetracker.EyeTracker|None, generic_device_name: str|None = None):
    dev_rec_lbl = _get_addable_recordings_label(dev_type, dev, generic_device_name)

    def prepping_recs_popup():
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
        imgui.text_colored(color, ifa6.ICON_FA_CIRCLE_INFO)
        imgui.pop_font()
        imgui.same_line(spacing=spacing)

        imgui.begin_group()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        text = f'Searching the path(s) you provided for {dev_rec_lbl}.'
        imgui.text_unformatted(text)
        imgui.dummy((0,3*imgui.get_style().item_spacing.y))
        text_size = imgui.calc_text_size(text)
        size_mult = imgui.get_window_dpi_scale()
        spinner_radii = [x*size_mult for x in [22, 16, 10]]
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x()+(text_size.x-2*spinner_radii[0])/2)
        imspinner.spinner_ang_triple('waitSpinner', *spinner_radii, 3.5*size_mult, c1=imgui.get_style_color_vec4(imgui.Col_.text), c2=colors.warning, c3=imgui.get_style_color_vec4(imgui.Col_.text))
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        imgui.end_group()

    return gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Preparing import", prepping_recs_popup, buttons=None, button_keymap={0:imgui.Key.enter}, closable=False, outside=False))


async def _show_addable_recordings(g, rec_getter: typing.Callable[[],list[recording.Recording]|list[camera_recording.Recording]], dev_type: session.RecordingType, dev: eyetracker.EyeTracker|None, sessions: list[str], generic_device_name: str=None, prepping_popup: typing.Callable|None = None):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker

    sessions = natsort.os_sorted(sessions)
    dev_rec_lbl = _get_addable_recordings_label(dev_type, dev, generic_device_name)

    # step 1, find what recordings of this type of eye tracker/camera files are in the path
    recs = rec_getter()
    all_recs: list[recording.Recording|camera_recording.Recording] = []
    dup_recs: list[recording.Recording|camera_recording.Recording] = []
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
    g.close_popup(prepping_popup)

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
    selected_slot: tuple[str,str]|None = None

    def _recording_context_menu(iid: int) -> bool:
        nonlocal selected_slot
        if imgui.selectable(ifa6.ICON_FA_FOLDER_OPEN + f" Open folder##{iid}", False)[0]:
            open_folder(g, recordings_to_add[iid].get_source_directory())
        if selected_slot is not None and imgui.selectable(ifa6.ICON_FA_ARROW_LEFT + f" Assign to selected recording", False)[0]:
            recording_assignment[selected_slot[0]][selected_slot[1]] = iid
            selected_slot = None
            recording_list.require_sort = True
        return False

    def _add_new_session(new_sess: str):
        nonlocal sessions
        sessions.append(new_sess)
        sessions = natsort.os_sorted(sessions)

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

        size_mult = imgui.get_window_dpi_scale()
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
                    rec_def = sess.definition.get_recording_def(r)
                    if rec_def.type!=dev_type:
                        continue
                    disable = False
                    rec: recording.Recording|camera_recording.Recording|None = None
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
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Assign and import recordings", list_recs_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))

async def _import_recordings(g, recordings: typing.Mapping[int,recording.Recording|camera_recording.Recording], recording_assignment: dict[str, dict[str, int]], generic_device_name: str|None):
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
    generic_et_idx = -1
    generic_et_name = ''
    eye_tracker = eyetracker.EyeTracker(eyetracker.eye_tracker_names[combo_value])
    sessions = get_and_filter_eligible_sessions(g, sessions, session.RecordingType.Eye_Tracker)

    def _valid_eye_tracker():
        if eye_tracker != eyetracker.EyeTracker.Generic:
            return True, ''
        if not g.study_config.import_known_custom_eye_trackers:
            return False, 'No custom eye trackers configured, fix project settings'
        if not 0 <= generic_et_idx < len(g.study_config.import_known_custom_eye_trackers):
            return False, 'Select a generic eye tracker to import recordings for'
        return True, ''

    def add_recs_popup():
        nonlocal combo_value, eye_tracker, generic_et_idx, generic_et_name
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
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
                generic_et_idx = -1
                generic_et_name = ''
        imgui.pop_item_width()
        imgui.dummy((0,2*imgui.get_style().item_spacing.y))
        if eye_tracker==eyetracker.EyeTracker.Generic:
            imgui.text_unformatted("Choose which generic eye tracker you want")
            imgui.text_unformatted("to import data for:")
            imgui.dummy((0,1.5*imgui.get_style().item_spacing.y))
            if g.study_config.import_known_custom_eye_trackers:
                full_width = imgui.get_content_region_avail().x
                imgui.set_cursor_pos_x(full_width*.3)
                imgui.push_item_width(full_width*.6)
                changed, generic_et_idx = imgui.combo('##generic_et_name_selector', generic_et_idx, g.study_config.import_known_custom_eye_trackers)
                if changed:
                    generic_et_name = g.study_config.import_known_custom_eye_trackers[generic_et_idx]
                imgui.pop_item_width()
            imgui.dummy((0,2*imgui.get_style().item_spacing.y))

        valid, reason = _valid_eye_tracker()
        if not valid:
            imgui.text_colored(colors.error, reason)
        imgui.end_group()

        return combo_value, eye_tracker

    def _launch_search():
        selected_generic_et_name = None if not generic_et_name else generic_et_name
        prepping_popup = _push_preparing_recordings_popup(g, session.RecordingType.Eye_Tracker, eye_tracker, selected_generic_et_name)
        g.run_async_task(
            _show_addable_recordings(g, lambda: recording.find_recordings(paths, eye_tracker, selected_generic_et_name), session.RecordingType.Eye_Tracker, eye_tracker, sessions, selected_generic_et_name, prepping_popup),
            popup=prepping_popup
        )

    buttons = {
        ifa6.ICON_FA_CHECK+" Continue": (_launch_search, lambda: not _valid_eye_tracker()[0]),
        ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
    }

    # ask what type of eye tracker we should be looking for
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select eye tracker", add_recs_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))

def camera_show_glob_filter_config(g, paths, sessions):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    glob_filter = '*.mp4,*.mov,*.avi,*.mkv'

    def setting_popup():
        nonlocal glob_filter
        spacing = 2 * imgui.get_style().item_spacing.x
        color = (0.45, 0.09, 1.00, 1.00)
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
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
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select device", setting_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))

def add_camera_recordings(g, paths: list[pathlib.Path], glob_filter: str, sessions: list[str]):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    sessions = get_and_filter_eligible_sessions(g, sessions, session.RecordingType.Camera)
    prepping_popup = _push_preparing_recordings_popup(g, session.RecordingType.Camera, None)
    g.run_async_task(
        _show_addable_recordings(g, lambda: _find_camera_recordings(paths, glob_filter), session.RecordingType.Camera, None, sessions, prepping_popup=prepping_popup),
        popup=prepping_popup
    )

def _find_camera_recordings(paths: list[pathlib.Path], glob_filter: str) -> list[camera_recording.Recording]:
    extensions = {'.'+x.strip('.* ') for x in glob_filter.split(',')}
    video_paths: list[pathlib.Path] = []
    for p in paths:
        if p.is_file():
            if p.suffix in extensions:
                video_paths.append(p)
        elif p.is_dir():
            video_paths.extend([pth.resolve() for pth in p.glob("**/*") if pth.suffix in extensions])
    return [camera_recording.Recording('',p.name,p.parent, duration=video_utils.get_video_duration(p)) for p in video_paths]

def get_and_filter_eligible_sessions(g, sessions: list[str], dev_type:session.RecordingType) -> list[str]:
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if not sessions:
        sessions = [s for s in g.sessions if g.sessions[s].missing_recordings(dev_type)]
    else:
        sessions = [s for s in   sessions if g.sessions[s].missing_recordings(dev_type)]
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
        imgui.push_font(None, imgui.get_style().font_size_base*g._icon_font_size_multiplier)
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
    gt_gui.utils.push_popup(g, lambda: gt_gui.utils.popup("Select device", choose_dev_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, closable=True, outside=False))
