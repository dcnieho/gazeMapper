import typing
import builtins
import inspect
import pathlib
import enum
import copy

from imgui_bundle import imgui, imgui_md, icons_fontawesome_6 as ifa6

import glassesTools
import glassesTools.gui
from glassesTools.gui.timeline import color_darken

from ... import config, type_utils, typed_dict_defaults


TYPE_TO_STR_REGISTRY: dict[typing.Type, dict[typing.Any, str]|typing.Callable[[typing.Any], str]] = {}
def register_formatter(ttype: typing.Type, formatter: dict[typing.Any, str]|typing.Callable[[typing.Any], str]):
    TYPE_TO_STR_REGISTRY[ttype] = formatter
register_formatter(type_utils.ArucoDictType, glassesTools.aruco.dict_id_to_str)

_C  = typing.TypeVar("_C")
_C2 = typing.TypeVar("_C2")
_T  = typing.TypeVar("_T")
_T2 = typing.TypeVar("_T2")

_gui_instance = None
def set_gui_instance(gui):
    global _gui_instance
    _gui_instance = gui

def draw(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[], set[typing.Any]]], parent_obj: _C2|None=None, actual_types: dict[typing.Any, typing.Type]=None, problems: type_utils.ProblemDict|None=None, documentation: dict[str,type_utils.GUIDocInfo]|None=None, fixed: type_utils.NestedDict|None=None) -> tuple[bool,_C,dict[typing.Any, typing.Type]|None]:
    if not fields:
        return

    table_is_started, changed, _, obj, _, actual_types = _draw_impl(obj, fields, types, defaults, possible_value_getters, parent_obj, problems or {}, documentation or {}, fixed or {}, actual_types or {})
    if table_is_started:
        imgui.end_table()

    return changed, obj, actual_types

def _replace_type_arg(f_type: typing.Type, base_type: typing.Type, v_type, n_type, fail_is_ok=False) -> typing.Type:
    o_types = typing.get_args(f_type)
    which = tuple(o==v_type for o in o_types)
    exc = None
    if sum(which)>1:
        exc = ValueError(f'Input type ({f_type}) has more than one subscripted types that match the type of the set of possible values ({v_type}), cannot replace {v_type} with {n_type}')
    elif not any(which):
        # try to recurse. find subscripted types
        subscripted = tuple(t!=(ot:=typing.get_origin(t)) and ot!=typing.Literal for t in o_types)
        if not any(subscripted):
            exc = ValueError(f'Input type ({f_type}) has no subscripted types that match the type of the set of possible values ({v_type}), cannot replace {v_type} with {n_type}')
        else:
            for i,b in enumerate(subscripted):
                if not b:
                    continue
                n_sub_type = _replace_type_arg(o_types[i], typing.get_origin(o_types[i]), v_type, n_type, fail_is_ok=True)
                if n_sub_type!=o_types[i]:
                    # success. substitute and return
                    t_args = list(o_types[:i]) + [n_sub_type] + list(o_types[i+1:])
                    return base_type[tuple(t_args)]
    else:
        # now, replace type
        return base_type[tuple(n_type if r else o for o,r in zip(o_types, which))]
    if exc and not fail_is_ok:
        raise exc
    return f_type   # failed, but its ok, return unchanged

def _get_base_type(f_type: typing.Type) -> typing.Type:
    return typing.get_origin(f_type) or f_type  # for instance str[int]->str, and str->str

def _get_field_type(field: str, obj: _T, f_type: typing.Type, possible_value_getter: typing.Callable[[],set[_T]]|None) -> tuple[bool, typing.Type, typing.Type, bool]:
    # peel off union with None, if any
    f_type, nullable = glassesTools.utils.unpack_none_union(f_type)
    base_type = _get_base_type(f_type)
    o_types = typing.get_args(f_type)
    if callable(possible_value_getter) or (isinstance(possible_value_getter, list) and all([callable(c) for c in possible_value_getter])):
        if not isinstance(possible_value_getter,list):
            possible_value_getter = [possible_value_getter]
        n_type: list[typing.Type] = []
        v_type: list[typing.Type] = []
        for pvg in possible_value_getter:
            # we have a set of possible values known at runtime: override unconstrained type to a Literal
            vals = tuple(pvg())
            if (num_types:=len({type(v) for v in vals}))>1:
                raise ValueError(f'Cannot perform type replacement. possible_value_getter should return a set of values that all have the same type')
            elif num_types==1:
                v_type.append(type(vals[0]))
            else:
                # no types, use function signature
                val_types = typing.get_args(inspect.signature(pvg).return_annotation)
                if len(val_types)!=1:
                    raise ValueError(f'Cannot perform type replacement. possible_value_getter either has no type annotation or can return more than one type')
                v_type.append(val_types[0])
            n_type.append(typing.Literal[vals])
    else:
        n_type = None
    if base_type==typing.Any and obj and field in obj:
        f_type = base_type = type(obj[field])
    match base_type:
        case builtins.bool | builtins.str | builtins.int | builtins.float | typing.Literal:
            is_dict = False
            if n_type:
                # apply type override
                f_type = n_type[0]
                base_type = typing.Literal

        case _ if typing.is_typeddict(f_type):
            is_dict = True
        case _ if typed_dict_defaults.is_typeddictdefault(f_type):
            is_dict = True
        case _ if type_utils.is_NamedTuple_type(f_type):
            is_dict = f_type != config.RgbColor # NB: we handle config.RGBColor separately
        case builtins.dict | builtins.list | builtins.set:
            is_dict = base_type==builtins.dict
            # possibly replace inner type of container
            if n_type is not None:
                for vt,nt in zip(v_type,n_type):
                    f_type = _replace_type_arg(f_type, base_type, vt, nt)
        case typing.Union if f_type==typing.Union[str, pathlib.Path]:
            is_dict = False
        case _ if issubclass(f_type, enum.Enum):
            is_dict = False
        case _:
            raise ValueError(f'type of {field} ({f_type}) not handled')
    return is_dict, base_type, o_types, f_type, nullable

def _draw_impl(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[], set[typing.Any]]], parent_obj: _C2|None, problems: type_utils.ProblemDict, documentation: dict[str,type_utils.GUIDocInfo], fixed: type_utils.NestedDict, actual_types: dict[typing.Any, typing.Type]=None, level=0, table_is_started=False, has_remove=False) -> tuple[bool,bool,bool,_C,str|None,dict[typing.Any, typing.Type]]:
    changed = False
    max_fields_width = get_fields_text_width(fields, documentation)*1.08    # little bit of extra space for bold font
    ret_new_obj = False
    removed_field = None
    for f in fields:
        if isinstance(actual_types,dict) and f in actual_types and not isinstance(actual_types[f],dict):
            tp = actual_types[f]
        else:
            tp = types[f] if f in types else list(types.values())[0]    # backup only needed when we have an invalid config (e.g. trying to show planes_per_episode entry for an episode that is no longer set to be coded)
        possible_value_getter = (possible_value_getters.get(f,None) or possible_value_getters.get(None,None)) if possible_value_getters else None
        is_dict, base_type, o_type_args, f_type, nullable = _get_field_type(f, obj, tp, possible_value_getter)
        doc = documentation.get(f,None) or documentation.get(None,None)
        if isinstance(doc,dict):
            this_lbl = doc[f].display_string if f in doc else f
            this_explanation = doc[f].doc_str if f in doc else None
            this_child_doc = doc[f].children if f in doc and isinstance(doc[f],type_utils.GUIDocInfo) else doc if doc is not None else {}
        else:
            this_lbl = doc.display_string if doc is not None else f
            this_explanation = doc.doc_str if doc is not None else None
            this_child_doc = doc.children if isinstance(doc,type_utils.GUIDocInfo) else doc if doc is not None else {}

        this_obj = obj.get(f,None) if isinstance(obj,dict) else getattr(obj,f)
        if is_dict and this_obj is not None:
            this_parent = None
            if parent_obj is not None:
                this_parent = parent_obj.get(f,None) if isinstance(parent_obj,dict) else getattr(parent_obj,f)
                if this_obj is None:
                    # don't draw, can't overwrite group if it isn't set at all in parent
                    continue
            if table_is_started:
                imgui.end_table()
                table_is_started = False
            if (has_problem:=problems and f in problems):
                imgui.push_style_color(imgui.Col_.text, glassesTools.gui.colors.error)
                def _hover_draw_fun():
                    if isinstance(problems[f],str) or (isinstance(problems[f],dict) and 'problem_with_this_key' in problems[f]):
                        msg = problems[f] if isinstance(problems[f],str) else problems[f]['problem_with_this_key']
                        glassesTools.gui.utils.draw_hover_text(msg, text='')
            if imgui.tree_node_ex(this_lbl,imgui.TreeNodeFlags_.framed):
                if has_problem:
                    _hover_draw_fun()
                    imgui.pop_style_color()
                if this_explanation:
                    glassesTools.gui.utils.draw_hover_text(this_explanation, text='')
                this_changed, made_obj, new_sub_obj, removed, actual_types_ = draw_dict_editor(this_obj, f_type, level+1, actual_types.get(f,{}), defaults=defaults.get(f,None) if defaults else None, possible_value_getters=possible_value_getter, parent_obj=this_parent, problems=problems.get(f,None) if isinstance(problems, dict) else {}, documentation=this_child_doc, fixed=fixed.get(f,None), nullable=nullable, removable=has_remove)
                if actual_types_:
                    actual_types[f] = actual_types_
                if removed:
                    removed_field = f
                    actual_types.pop(f,None)
                changed |= this_changed
                if this_changed and made_obj:
                    if isinstance(obj,dict):
                        obj[f] = new_sub_obj
                    else:
                        setattr(obj,f,new_sub_obj)
                imgui.tree_pop()
            else:
                if has_problem:
                    _hover_draw_fun()
                    imgui.pop_style_color()
                if this_explanation:
                    glassesTools.gui.utils.draw_hover_text(this_explanation, text='')
            continue

        # simple field, set up for drawing
        if not table_is_started:
            table_is_started = _start_table(level, max_fields_width)
            if not table_is_started:
                continue

        this_problem = False
        if f in problems:
            this_problem = problems[f] if problems[f] is not None else True
        this_changed, new_f_obj, removed = _draw_field(f, obj, base_type, f_type, o_type_args, nullable, defaults.get(f,None), parent_obj, problem=this_problem, documentation=doc, fixed=f in fixed, has_remove=has_remove)
        if removed:
            removed_field = f
        changed |= this_changed
        if this_changed and new_f_obj is not None:
            ret_new_obj = True
            obj = new_f_obj
    return table_is_started, changed, ret_new_obj, obj, removed_field, actual_types

def draw_dict_editor(obj: _T, o_type: typing.Type, level: int, actual_types: dict[typing.Any, typing.Type], fields: list=None, types: dict[typing.Any, typing.Type]=None, defaults:dict[typing.Any, typing.Any]=None, possible_value_getters: typing.Callable[[_T], set[typing.Any]]|list[typing.Callable[[_T], set[typing.Any]]]|dict[str,typing.Callable[[_T], set[typing.Any]]]=None, parent_obj: _C2|None=None, problems: type_utils.ProblemDict=None, documentation: dict[str,type_utils.GUIDocInfo]=None, fixed: type_utils.NestedDict=None, nullable=False, removable=False) -> tuple[bool,bool,_T,bool,dict[typing.Any, typing.Type]|None]:
    made_or_replaced_obj = False
    if (made_or_replaced_obj := obj is None):
        obj = o_type()

    has_add = has_remove = False
    missing_fields = None
    if typing.is_typeddict(o_type):
        types = o_type.__annotations__.copy()
        fields = list(types.keys())
        if not problems and not made_or_replaced_obj:   # don't mark as problem if the obj was unset (None)
            problems = {k:f'{k} is required' for k in o_type.__required_keys__ if k not in obj}
    elif typed_dict_defaults.is_typeddictdefault(o_type):
        types = o_type.__annotations__.copy()
        fields = list(types.keys())
        if not problems and not made_or_replaced_obj:   # don't mark as problem if the obj was unset (None)
            problems = {k:f'{k} is required' for k in o_type.__required_keys__ if k not in obj}
    elif type_utils.is_NamedTuple_type(o_type):
        types = o_type.__annotations__.copy()
        fields= list(o_type._fields)
    else:
        all_fields = None
        all_type = None
        kv_type = typing.get_args(o_type)
        if possible_value_getters and not isinstance(possible_value_getters, dict):
            if isinstance(possible_value_getters,list):
                all_fields = possible_value_getters[0]() # first one should be for keys
                possible_value_getters = possible_value_getters[1]  # second one for values, and thus the one to be passed on
            else:
                all_fields = possible_value_getters()
        else:
            if kv_type:
                if typing.get_origin(kv_type[0])==typing.Literal:
                    all_fields = list(typing.get_args(kv_type[0]))
                elif issubclass(kv_type[0], enum.Enum):
                    all_fields = [e for e in kv_type[0]]
        if kv_type and len(kv_type)==2:
            # get value type, if meaningful
            if kv_type[1]!=typing.Any and typing.get_origin(kv_type[1]) not in [typing.Union]:
                if all_fields:
                    types = {k:kv_type[1] for k in all_fields}
                else:
                    all_type = kv_type[1]

        has_add = has_remove = fields is None
        if fields is None:
            fields = list(obj.keys())
            if all_fields is not None:
                missing_fields = set(all_fields)-set(fields)
                missing_fields = [f for f in all_fields if f in missing_fields] # preserve order
                if not missing_fields:
                    # nothing more to add, all possible keys exhausted
                    has_add = False
                    # but keep has_remove to True, if it was
            if not types:
                if all_type:
                    types = {k:all_type for k in obj}
                else:
                    types = {k:type(obj[k]) for k in obj}
                    if not actual_types:
                        actual_types = copy.deepcopy(types)
    if defaults is None:
        if typed_dict_defaults.is_typeddictdefault(o_type):
            defaults = o_type._field_defaults.copy()
        elif type_utils.is_NamedTuple_type(o_type):
            defaults = o_type._field_defaults.copy()
        else:
            defaults = {}
    else:
        # ensure dict
        if not isinstance(defaults, dict):
            defaults = {f: getattr(defaults,f) for f in fields}

    first_column_width = max([get_fields_text_width(fields, documentation, backup_str='xadd itemx'), get_fields_text_width(['xadd itemx'],{})])*1.08    # little bit of extra space for bold font
    table_is_started = _start_table(level, first_column_width)
    if not table_is_started:
        return False, made_or_replaced_obj, obj, False
    table_is_started, changed, ret_new_obj, obj, removed_field, actual_types = _draw_impl(obj, fields, types, defaults, possible_value_getters if isinstance(possible_value_getters,dict) else None, parent_obj, problems if isinstance(problems,dict) else {}, documentation or {}, fixed or {}, actual_types, level, table_is_started, has_remove=has_remove)
    if removed_field:
        obj.pop(removed_field)
        changed = True
    made_or_replaced_obj |= ret_new_obj
    if table_is_started:
        table_is_started = False
        imgui.end_table()
    if has_add:
        iid = imgui.get_id('##adder')
        if draw_dict_editor.new_item and iid==draw_dict_editor.new_item[0]:
            obj = draw_dict_editor.new_item[1]
            changed = True
            made_or_replaced_obj = True
            actual_types[draw_dict_editor.new_item[2]] = draw_dict_editor.new_item[3]
            draw_dict_editor.new_item = None
        if imgui.button('add item'):
            new_item_name = ''
            new_item_type: typing.Type = None
            def _do_add_item():
                nonlocal obj
                nonlocal iid
                nonlocal missing_fields
                nonlocal types
                nonlocal new_item_name
                nonlocal new_item_type
                if missing_fields:
                    obj[new_item_name] = types[new_item_name]()
                    t = types[new_item_name]
                else:
                    if new_item_type.startswith('list'):
                        t = getattr(builtins,'list')
                        t2= getattr(builtins,new_item_type[5:-1])
                        t = t[t2]
                    else:
                        t = getattr(builtins,new_item_type)
                    obj[new_item_name] = t()
                draw_dict_editor.new_item = (iid,obj,new_item_name,t)
            def _valid_item_name():
                nonlocal missing_fields
                nonlocal new_item_name
                return True if missing_fields else new_item_name and not new_item_name in obj
            def _add_item_popup():
                nonlocal missing_fields
                nonlocal new_item_name
                nonlocal new_item_type
                imgui.dummy((30*imgui.calc_text_size('x').x,0))
                if imgui.begin_table("##new_item_info",2):
                    imgui.table_setup_column("##new_item_infos_left", imgui.TableColumnFlags_.width_fixed)
                    imgui.table_setup_column("##new_item_infos_right", imgui.TableColumnFlags_.width_stretch)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.align_text_to_frame_padding()
                    invalid = not _valid_item_name()
                    if invalid:
                        imgui.push_style_color(imgui.Col_.text, glassesTools.gui.colors.error)
                    imgui.text("Item name")
                    if invalid:
                        imgui.pop_style_color()
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    if missing_fields:
                        items = sorted(list(missing_fields), key=lambda x: x.value if isinstance(x, enum.Enum) else x)
                        items_str, tooltips = _get_str_values(items, type(items[0]), tuple(), documentation)
                        idx = items.index(new_item_name) if new_item_name else -1
                        _,idx = glassesTools.gui.utils.tooltip_combo("##item_selector", idx, items_str, tooltips)
                        new_item_name = None if idx==-1 else items[idx]
                    else:
                        _,new_item_name = imgui.input_text("##new_item_name",new_item_name)
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.align_text_to_frame_padding()
                        invalid = new_item_type is None
                        if invalid:
                            imgui.push_style_color(imgui.Col_.text, glassesTools.gui.colors.error)
                        imgui.text("Item type")
                        if invalid:
                            imgui.pop_style_color()
                        imgui.table_next_column()
                        imgui.set_next_item_width(-1)
                        types = ['bool','str','int','float','list[str]','list[float]','list[int]']
                        t_idx = types.index(new_item_type) if new_item_type is not None else -1
                        _,t_idx = imgui.combo("##item_type_selector", t_idx, types)
                        new_item_type = None if t_idx==-1 else types[t_idx]
                    imgui.end_table()

            buttons = {
                ifa6.ICON_FA_CHECK+f" {'Add' if missing_fields else 'Create'} item": (_do_add_item, lambda: not _valid_item_name() or (not missing_fields and new_item_type is None)),
                ifa6.ICON_FA_CIRCLE_XMARK+" Cancel": None
            }
            glassesTools.gui.utils.push_popup(_gui_instance, lambda: glassesTools.gui.utils.popup("Add item", _add_item_popup, buttons=buttons, button_keymap={0:imgui.Key.enter}, outside=False))
    if nullable and not made_or_replaced_obj:
        if has_add:
            imgui.same_line()
        if imgui.button(ifa6.ICON_FA_HANDS_BUBBLES+ f' unset group'):
            obj = None
            made_or_replaced_obj = True
            changed = True
    removed = False
    if removable:
        if has_add or (nullable and not made_or_replaced_obj):
            imgui.same_line()
        if imgui.button(ifa6.ICON_FA_TRASH_CAN+f' remove group'):
            removed = True
    if parent_obj is not None and obj!=parent_obj:
        if has_add or (nullable and not made_or_replaced_obj) or removable:
            imgui.same_line()
        if imgui.button(f' parent'):
            changed = True
            made_or_replaced_obj = True
            obj = parent_obj

    return changed, made_or_replaced_obj, obj, removed, actual_types
draw_dict_editor.new_item = None

def get_fields_text_width(fields: list[str], documentation: dict[str, type_utils.GUIDocInfo], backup_str='xxxxx'):
    fields = [documentation[f].display_string if f in documentation else f for f in fields] # get display strings, if available
    if fields and isinstance(fields[0], enum.Enum):
        fields = [f.value for f in fields]
    return max([imgui.calc_text_size(f) for f in fields], key=lambda x: x.x, default=imgui.calc_text_size(backup_str)).x

def _start_table(level, first_column_width):
    table_is_started = imgui.begin_table(f"##settings_level_{level}", 2)
    if not table_is_started:
        return table_is_started
    imgui.table_setup_column("setting", imgui.TableColumnFlags_.width_fixed, init_width_or_weight=first_column_width)
    imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)
    return table_is_started

def _draw_field(field: str, obj: _T, base_type: typing.Type, f_type: typing.Type, o_type_args: tuple[typing.Type], nullable: bool, default: typing.Any|None, parent_obj: _T2|None, problem: bool|str, documentation: type_utils.GUIDocInfo|None, fixed: bool, has_remove: bool) -> bool:
    imgui.table_next_row()
    imgui.table_next_column()
    special_val = '**special_val_when_not_found'
    val = obj.get(field,special_val) if isinstance(obj,dict) else getattr(obj,field)
    parent_val = None
    if parent_obj is not None:
        if isinstance(parent_obj,dict) and field in parent_obj:
            parent_val = parent_obj.get(field,None)
        elif hasattr(parent_obj,field):
            parent_val = getattr(parent_obj,field)
    is_none = val is None
    if val==special_val:
        try:
            val = f_type()
        except:
            # cannot get or construct val, fall back to None (e.g. happens when type is a literal)
            val = None
    if documentation and documentation.display_string:
        field_lbl = documentation.display_string
    else:
        field_lbl = field
        if isinstance(field_lbl, enum.Enum):
            field_lbl = field_lbl.value
        if not isinstance(field_lbl, str):
            field_lbl = str(field_lbl)
    is_default = val==default
    is_parent = parent_obj is not None and val==parent_val
    if fixed:
        imgui.begin_disabled()
    if problem:
        imgui.align_text_to_frame_padding()
        imgui.text_colored(glassesTools.gui.colors.error, field_lbl)
        if isinstance(problem,str):
            imgui.push_style_color(imgui.Col_.text, glassesTools.gui.colors.error)
            glassesTools.gui.utils.draw_hover_text(problem,text='')
            imgui.pop_style_color()
    elif is_default or is_parent or is_none or fixed:
        imgui.align_text_to_frame_padding()
        imgui.text_colored(color_darken(imgui.ImColor(imgui.get_style_color_vec4(imgui.Col_.text)), .75).value, field_lbl)
    else:
        imgui_md.render(f'**{field_lbl}**')
    if documentation and documentation.doc_str:
        glassesTools.gui.utils.draw_hover_text(documentation.doc_str, text='')
    imgui.table_next_column()
    value_documentation = documentation.children.get(None,{}) or documentation.children if documentation is not None else {}
    new_val, new_edit, removed = draw_value(field_lbl, val, f_type, o_type_args, nullable, default, parent_val, fixed, value_documentation, has_remove, is_none, base_type)

    new_obj = None
    if (changed := new_val!=val or new_edit):
        if isinstance(obj,dict):
            obj[field] = new_val
        elif type_utils.is_NamedTuple_type(type(obj)):
            # tuples are immutable, have to return new instance
            new_obj = obj._replace(**{field:new_val})
        else:
            setattr(obj,field,new_val)

    return changed, new_obj, removed

def draw_value(field_lbl: str, val: _T, f_type: typing.Type, o_type_args: tuple[typing.Type], nullable: bool, default: _T|None, parent_val: _T|None, fixed: bool, documentation: dict[typing.Any,type_utils.GUIDocInfo], has_remove: bool, is_none=False, base_type: typing.Type=None) -> tuple[_T|None, bool, bool]:
    if base_type is None:
        base_type = _get_base_type(f_type)
    is_default = val==default
    is_parent  = val==parent_val
    new_edit = False
    removed = False
    field_lbl = field_lbl.translate({ord("#"): None})   # ensure there are no # in the lbl that would confuse imgui internals
    if val is None and draw_value.should_edit_id and draw_value.should_edit_id==imgui.get_id(field_lbl):
        if default is not None:
            val = default
        else:
            if base_type==typing.Literal:
                val = typing.get_args(f_type)[0]
            elif base_type==typing.Union and f_type==typing.Union[str, pathlib.Path]:
                val = ''
            else:
                val = f_type()
        draw_value.should_edit_id = None
        new_edit = True
    match base_type:
        case _ if val is None:
            new_val = val
            imgui.text('<not set>, click to set')
            if imgui.is_item_clicked(imgui.MouseButton_.left):
                draw_value.should_edit_id = imgui.get_id(field_lbl)
        case builtins.bool:
            new_val = imgui.checkbox(f'##{field_lbl}', val)[1]
        case builtins.str:
            new_val = imgui.input_text(f'##{field_lbl}', val)[1]
        case typing.Union if f_type==typing.Union[str, pathlib.Path]:
            new_val = imgui.input_text(f'##{field_lbl}', str(val))[1]
            if isinstance(val,pathlib.Path):
                new_val = pathlib.Path(new_val)
        case builtins.int:
            extra = {}
            if fixed:
                extra['step']       = 0
                extra['step_fast']  = 0
            new_val = imgui.input_int(f'##{field_lbl}', val, **extra)[1]
        case builtins.float:
            new_val = imgui.input_double(f'##{field_lbl}', val)[1]
        case builtins.list | builtins.set:
            new_val = draw_list_set_editor(field_lbl, val, f_type, o_type_args, documentation)
        case _ if base_type==typing.Literal or (inspect.isclass(base_type) and issubclass(base_type, enum.Enum)):
            if base_type == typing.Literal:
                values = list(typing.get_args(f_type))
            else:
                values = [x for x in base_type]
            if val is None:
                values.insert(0,None)
            is_known_value = val in values
            if is_known_value:
                p_idx = values.index(val)
            else:
                p_idx = 0
                values.insert(0,f'*unknown value: {val}*')
            str_values, tooltips = _get_str_values(values, f_type, o_type_args, documentation)
            imgui.set_next_item_width(get_fields_text_width(str_values,{})+imgui.get_frame_height()+2*imgui.get_style().frame_padding.x)
            changed,p_idx = glassesTools.gui.utils.tooltip_combo(f"##{field_lbl}", p_idx, str_values, tooltips, popup_max_height_in_items=min(10,len(values)))
            if tooltips[p_idx]:
                glassesTools.gui.utils.draw_hover_text(tooltips[p_idx],'')
            if is_known_value or (changed and p_idx>0):
                new_val = values[p_idx]
            else:
                new_val = val
        case config.RgbColor:
            new_val = imgui.color_edit3(f'##{field_lbl}', [x/255. for x in val], imgui.ColorEditFlags_.picker_hue_wheel | imgui.ColorEditFlags_.uint8 | imgui.ColorEditFlags_.display_rgb | imgui.ColorEditFlags_.input_rgb)[1]
            new_val = config.RgbColor(*tuple(int(x*255) for x in new_val))
        case _:
            if new_edit:
                new_val = val
            else:
                imgui.text(f'type {f_type} not handled')
                new_val = None
    if fixed:
        imgui.end_disabled()
    else:
        if nullable and not is_none:
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_HANDS_BUBBLES+ f' unset##{field_lbl}'):
                new_val = None
        if not is_default and default is not None:
            imgui.same_line()
            if imgui.button(f' default##{field_lbl}'):
                new_val = default
        if not is_parent and parent_val is not None:
            imgui.same_line()
            if imgui.button(f' parent##{field_lbl}'):
                new_val = parent_val
        if has_remove:
            imgui.same_line()
            if imgui.button(ifa6.ICON_FA_TRASH_CAN+f'##{field_lbl}'):
                removed = True

    return new_val, new_edit, removed
draw_value.should_edit_id = None

def _get_str_values(values: list[typing.Any], f_type: typing.Type, o_type_args: tuple[typing.Type], documentation: dict[typing.Any,type_utils.GUIDocInfo]):
    if f_type in TYPE_TO_STR_REGISTRY or (len(o_type_args)==1 and o_type_args[0] in TYPE_TO_STR_REGISTRY):
        key = f_type if f_type in TYPE_TO_STR_REGISTRY else o_type_args[0]
        str_values = ['' if v is None else TYPE_TO_STR_REGISTRY[key][v] if isinstance(TYPE_TO_STR_REGISTRY[key],dict) else TYPE_TO_STR_REGISTRY[key](v) for v in values]
    else:
        str_values = ['' if v is None else str(documentation[v].display_string if v in documentation else v.value if issubclass(type(v), enum.Enum) and isinstance(v.value,str) else v) for v in values]
    tooltips = [documentation[v].doc_str if v in documentation else None for v in values]
    return str_values, tooltips

def draw_list_set_editor(field_lbl: str, val: _T, f_type: typing.Type, o_type_args: tuple[typing.Type], documentation: dict[typing.Any,type_utils.GUIDocInfo]):
    val = val.copy()
    win = imgui.internal.get_current_window()
    if win.skip_items:
        return val

    # prep
    field_lbl = field_lbl.translate({ord("#"): None})   # ensure there are no # in the lbl that would confuse imgui internals
    # get possible values and their order
    v_types = typing.get_args(f_type)
    v_type = v_types[0] if v_types else int # fallback to something, int
    fixed_value_set = True
    if typing.get_origin(v_type)==typing.Literal:
        all_values = typing.get_args(v_type)
    elif issubclass(v_type, enum.Enum):
        all_values = tuple(e for e in v_type)
    else:
        all_values = []
        fixed_value_set = False

    # for sets, make sure they are consistently (and logically) ordered, same order as the drop-down
    disp_val = val
    has_order = isinstance(val,list)
    if not has_order:
        if all_values:
            # preserve order
            disp_val= [v for v in all_values if v in val]
            # make sure this doesn't filter out any values however
            for v in val:
                if v not in disp_val:
                    disp_val.append(v)
    # get width of drawing space
    item_w = imgui.calc_item_width()
    h_edge_spacing = imgui.get_style().cell_padding.x
    # determine items to show
    tsx = imgui.calc_text_size('x')
    x_padding = imgui.get_style().frame_padding.x
    val_txt, val_tooltips = _get_str_values(disp_val,f_type,o_type_args,documentation)
    t_sizes = [imgui.calc_text_size(t) for t in val_txt]
    w_sizes = [ts + (4*x_padding, 0) + (tsx.x, 0) for ts in t_sizes]
    # prep value adder, if needed
    adder_width = None
    if (miss_values := list(set(all_values)-set(val))):
        miss_values = [v for v in all_values if v in miss_values]   # preserve order
        str_values, tooltips  = _get_str_values(miss_values,f_type,o_type_args,documentation)
        adder_width = get_fields_text_width(str_values,{})+imgui.get_frame_height()+2*imgui.get_style().frame_padding.x
    # prep value entry box, if needed
    inputter_width = None
    if not fixed_value_set:
        inputter_width = imgui.calc_text_size('x'*10).x + 2*imgui.get_style().cell_padding.x

    # determine size of editor: how many lines we need to fit all elements
    line_break_idxs = []    # codes *before* which element we need to move to the next line
    w = h_edge_spacing
    for i in range(len(disp_val)):
        if i>0 and w+w_sizes[i].x+h_edge_spacing > item_w:
            line_break_idxs.append(i)
            w = h_edge_spacing + w_sizes[i].x
        else:
            w += w_sizes[i].x + imgui.get_style().item_spacing.x
    if val and adder_width is not None:
        if w+adder_width+h_edge_spacing > item_w:
            line_break_idxs.append(i+1)
            w = h_edge_spacing+adder_width
        else:
            w += adder_width + imgui.get_style().item_spacing.x
    if (val or adder_width is not None) and inputter_width is not None:
        if w+inputter_width+h_edge_spacing > item_w:
            line_break_idxs.append(i+1)
            w = h_edge_spacing+inputter_width
        else:
            w += inputter_width + imgui.get_style().item_spacing.x

    pos = imgui.get_cursor_screen_pos()
    n_lines = 1+len(line_break_idxs)
    bb = imgui.internal.ImRect(pos, pos+(item_w, 2*imgui.get_style().frame_padding.y+tsx.y*n_lines+imgui.get_style().item_spacing.y*(n_lines-1)))
    if imgui.internal.item_add(bb, imgui.get_id('##set_list_editor')):
        imgui.push_clip_rect(bb.min, bb.max, False)

        # draw background rect
        frame_col = imgui.get_color_u32(imgui.Col_.frame_bg)
        imgui.internal.render_frame(bb.min,bb.max,frame_col,True,imgui.get_style().frame_rounding)

        # draw items
        to_remove = None
        to_add    = None
        line = 0
        bbs = []
        for i,v in enumerate(disp_val):
            if i>0 and i not in line_break_idxs:
                imgui.same_line()
            else:
                line += 1
                imgui.set_cursor_screen_pos(imgui.get_cursor_screen_pos()+(h_edge_spacing, 0))
            imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
            imgui.begin_group()
            if line==1:
                imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + imgui.get_style().frame_padding.y)

            # prep for drawing widget: determine if visible
            t_pos   = imgui.get_cursor_screen_pos()
            t_bb    = imgui.internal.ImRect(t_pos, t_pos+w_sizes[i])
            bbs.append(t_bb)

            imgui.internal.item_size(w_sizes[i])
            # if visible
            iid = imgui.get_id(f'{val_txt[i]}##{field_lbl}_{i}')
            if imgui.internal.item_add(t_bb, iid):
                # enable interaction
                if (has_order and len(val)>1) or val_tooltips[i]:
                    _, hovered, held = imgui.internal.button_behavior(t_bb, iid, False, False, imgui.internal.ButtonFlagsPrivate_.allow_overlap)
                if val_tooltips[i]:
                    glassesTools.gui.utils.draw_hover_text(val_tooltips[i],'')
                if has_order and len(val)>1:
                    if held and hovered:
                        clr = imgui.get_color_u32(imgui.Col_.button_active)
                    elif hovered:
                        clr = imgui.get_color_u32(imgui.Col_.button_hovered)
                    else:
                        clr = imgui.get_color_u32(imgui.Col_.button)
                else:
                    clr = imgui.get_color_u32(imgui.Col_.button)
                # draw frame
                imgui.internal.render_frame(t_bb.min, t_bb.max, clr, True, imgui.get_style().frame_rounding)
                # draw text on top (need to go super low-level, as it seems that imgui.internal.render_text_clipped() has some issue on the mac, can't figure it out)
                imgui.get_current_context().current_window.draw_list.add_text(
                    imgui.get_current_context().font, 0., (t_bb.min.x+x_padding, t_bb.min.y), imgui.get_color_u32(imgui.Col_.text), val_txt[i], None, 0., t_bb.to_vec4())
                if has_order and len(val)>1:
                    glassesTools.gui.utils.draw_hover_text("Drag to reorder",'')
                    if imgui.begin_drag_drop_source(imgui.DragDropFlags_.payload_auto_expire):
                        # Set payload to carry the index of our item
                        imgui.set_drag_drop_payload_py_id(field_lbl, i)
                        # Display preview
                        imgui.text(val_txt[i])
                        imgui.end_drag_drop_source()

                imgui.set_cursor_screen_pos((t_bb.min.x+2*x_padding+t_sizes[i].x,t_pos.y))
                if imgui.small_button(f'x##{field_lbl}_{i}'):
                    to_remove = v

            imgui.end_group()
            imgui.pop_style_var()

        # draw value adder, if needed
        if miss_values:
            same_line = False
            if val:
                same_line = t_bb.max.x+imgui.get_style().item_spacing.x+adder_width+h_edge_spacing <= bb.max.x
                if same_line:
                    imgui.same_line()
                else:
                    line += 1
                if line>1:
                    imgui.set_cursor_screen_pos(imgui.get_cursor_screen_pos()-(0, imgui.get_style().frame_padding.y))
            if not same_line:   # NB: also true when no values
                imgui.set_cursor_screen_pos(imgui.get_cursor_screen_pos()+(h_edge_spacing, 0))
            imgui.set_next_item_width(adder_width)
            selected,p_idx = glassesTools.gui.utils.tooltip_combo(f"##{field_lbl}", -1, str_values, tooltips, popup_max_height_in_items=min(10,len(str_values)))
            if selected:
                to_add = miss_values[p_idx]
        if not fixed_value_set:
            if field_lbl not in draw_list_set_editor.inputter_temp or not isinstance(draw_list_set_editor.inputter_temp[field_lbl],v_type):
                draw_list_set_editor.inputter_temp[field_lbl] = v_type()
            kwargs = {}
            flags = imgui.InputTextFlags_.escape_clears_all
            match v_type:
                case builtins.int:
                    fun = imgui.input_int
                    kwargs['step'] = 0
                case builtins.float:
                    fun = imgui.input_float
                    kwargs['step'] = 0.
                case _:
                    fun = imgui.input_text
            same_line = False
            if val or miss_values:
                last = t_bb.max.x if not miss_values else imgui.get_cursor_screen_pos()
                same_line = last+imgui.get_style().item_spacing.x+inputter_width+h_edge_spacing <= bb.max.x
                if same_line:
                    imgui.same_line()
                else:
                    line += 1
                if line>1:
                    imgui.set_cursor_screen_pos(imgui.get_cursor_screen_pos()-(0, imgui.get_style().frame_padding.y))
            if not same_line:   # NB: also true when no values
                imgui.set_cursor_screen_pos(imgui.get_cursor_screen_pos()+(h_edge_spacing, 0))
            imgui.set_next_item_width(inputter_width)
            active_id = imgui.get_current_context().active_id
            _,draw_list_set_editor.inputter_temp[field_lbl] = fun(f'##inputter_{field_lbl}', draw_list_set_editor.inputter_temp[field_lbl], flags=flags, **kwargs)
            item_id = imgui.get_item_id()
            validated = item_id==active_id and imgui.is_key_pressed(imgui.Key.enter) or imgui.is_key_pressed(imgui.Key.keypad_enter)
            if validated or imgui.is_item_deactivated_after_edit():
                to_add = draw_list_set_editor.inputter_temp[field_lbl]
                draw_list_set_editor.inputter_temp.pop(field_lbl)

        # deal with drag-drop
        if has_order and len(val)>1:
            # draw invisible buttons between each item as drop targets
            drag_drop_result = None
            for i,t_bb in enumerate(bbs):
                imgui.set_cursor_screen_pos(t_bb.min-(imgui.get_style().item_spacing.x, 0))
                imgui.invisible_button(f"##{field_lbl}_before_{i}",(imgui.get_style().item_spacing.x, t_bb.get_height()))
                if imgui.begin_drag_drop_target():
                    payload = imgui.accept_drag_drop_payload_py_id(field_lbl)
                    if payload is not None:
                        drag_drop_result = (payload.data_id, i)
                    imgui.end_drag_drop_target()
                if i==len(bbs)-1 or i+1 in line_break_idxs:
                    imgui.set_cursor_screen_pos((t_bb.max.x, t_bb.min.y))
                    imgui.invisible_button(f"##{field_lbl}_after_{i}",(imgui.get_style().item_spacing.x, t_bb.get_height()))
                    if imgui.begin_drag_drop_target():
                        payload = imgui.accept_drag_drop_payload_py_id(field_lbl)
                        if payload is not None:
                            drag_drop_result = (payload.data_id, i+1)
                        imgui.end_drag_drop_target()
            # if we have a drop, process it
            if drag_drop_result is not None:
                ori_pos, new_pos = drag_drop_result
                if new_pos-ori_pos>0:
                    new_pos -= 1    # indices change when object is popped at ori_pos
                to_move = val.pop(ori_pos)
                val.insert(new_pos, to_move)

        imgui.pop_clip_rect()
        # allocate size
        imgui.set_cursor_screen_pos(pos)
        imgui.internal.item_size(bb)

        # process edits
        if to_remove is not None:
            val.remove(to_remove)   # NB: both list and set have remove()
        if to_add is not None:
            if isinstance(val,list):
                val.append(to_add)
            elif isinstance(val,set):
                val.add(to_add)

    return val
draw_list_set_editor.inputter_temp: dict[str,typing.Any] = {}