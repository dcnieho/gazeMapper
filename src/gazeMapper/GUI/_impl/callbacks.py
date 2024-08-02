import pathlib
import typing
from imgui_bundle import icons_fontawesome_6 as ifa6


from . import filepicker, msgbox, utils
from ... import config, plane

def get_folder_picker(g, reason: str):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    def select_callback(selected):
        match reason:
            case 'loading' | 'creating':
                try_load_project(g, selected, action=reason)
            case _:
                raise ValueError(f'reason "{reason}" not understood')

    match reason:
        case 'loading' | 'creating':
            header = "Select or drop project folder"
            allow_multiple = False
    picker = filepicker.DirPicker(title=header, allow_multiple=allow_multiple, callback=select_callback)
    picker.set_show_only_dirs(False)
    return picker

def try_load_project(g, path: str|pathlib.Path, action='loading'):
    from . import gui
    g = typing.cast(gui.GUI,g)  # indicate type to typechecker
    if isinstance(path,list):
        if not path:
            utils.push_popup(g, msgbox.msgbox, "Project opening error", "A single project directory should be provided. None provided so cannot open.", msgbox.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
            return
        elif len(path)>1:
            utils.push_popup(g, msgbox.msgbox, "Project opening error", f"Only a single project directory should be provided, but {len(path)} were provided. Cannot open multiple projects.", msgbox.MsgBox.error, more="Dropped paths:\n"+('\n'.join([str(p) for p in path])))
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
            utils.push_popup(g, msgbox.msgbox, "Create new project", "The selected folder is already a project folder.\nDo you want to open it?", msgbox.MsgBox.question, buttons)
        else:
            g.load_project(path)
    elif any(path.iterdir()):
        if action=='creating':
            utils.push_popup(g, msgbox.msgbox, "Project creation error", "The selected folder is not empty. Cannot be used to create a project folder.", msgbox.MsgBox.error)
        else:
            utils.push_popup(g, msgbox.msgbox, "Project opening error", "The selected folder is not a project folder. Cannot open.", msgbox.MsgBox.error)
    else:
        def init_project_and_ask():
            utils.init_project_folder(path)
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: g.load_project(path),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            utils.push_popup(g, msgbox.msgbox, "Open new project", "Do you want to open the new project folder?", msgbox.MsgBox.question, buttons)
        if action=='creating':
            init_project_and_ask()
        else:
            buttons = {
                ifa6.ICON_FA_CHECK+" Yes": lambda: init_project_and_ask(),
                ifa6.ICON_FA_CIRCLE_XMARK+" No": None
            }
            utils.push_popup(g, msgbox.msgbox, "Create new project", "The selected folder is empty. Do you want to use it as a new project folder?", msgbox.MsgBox.warn, buttons)

def make_plane(study_config: config.Study, type: plane.Type, name: str):
    # make plane
    p_def = plane.Definition(type, name)
    # store to file
    path = config.guess_config_dir(study_config.working_directory)
    p_dir = path / p_def.name
    if not p_dir.is_dir():
        p_dir.mkdir()
    p_def.store_as_json(p_dir)
    # append to known planes
    study_config.planes.append(p_def)