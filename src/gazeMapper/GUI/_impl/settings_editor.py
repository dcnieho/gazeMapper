import typing
import builtins
import inspect
import pathlib

from imgui_bundle import imgui, imgui_md

from glassesTools.timeline_gui import color_darken

from ... import plane
from . import colors

def is_NamedTuple_type(x):
  return (inspect.isclass(x) and issubclass(x, tuple) and
          hasattr(x, '_asdict') and callable(x._asdict) and
          hasattr(x, '__annotations__') and
          getattr(x, '_fields', None) is not None)

val_to_str_registry: dict[typing.Type, dict[typing.Any, str]] = {
    plane.ArucoDictType: plane.aruco_dicts_to_str
}

_C = typing.TypeVar("_C")
_T = typing.TypeVar("_T")
def draw(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]]) -> tuple[bool,_C]:
    if not fields:
        return

    table_is_started, changed, _, obj = _draw_impl(obj, fields, types, defaults, possible_value_getters, [])
    if table_is_started:
        imgui.end_table()

    return changed, obj


def _get_field_type(field: str, obj: _T, f_type: typing.Type, possible_value_getter: typing.Callable[[_C],tuple[_T]]|None) -> tuple[bool, typing.Type, typing.Type]:
    base_type = typing.get_origin(f_type) or f_type  # for instance str[int]->str, and or for str->str
    if possible_value_getter:
        # we have a set of possible values known at runtime: override unconstrained type to a Literal
        vals = possible_value_getter(obj)
        if (num_types:=len({type(v) for v in vals}))>1:
            raise ValueError(f'Cannot perform type replacement. possible_value_getter should return a set of values that all have the same type')
        elif num_types==1:
            v_type = type(vals[0])
        else:
            # no types, use function signature
            val_types = typing.get_args(inspect.signature(possible_value_getter).return_annotation)
            if len(val_types)!=1:
                raise ValueError(f'Cannot perform type replacement. possible_value_getter either has no type annotation or can return more than one type')
            v_type = val_types[0]
        n_type = typing.Literal[vals]
    else:
        n_type = None
    match base_type:
        case builtins.bool | builtins.str | builtins.int | builtins.float | typing.Literal:
            is_dict = False
            if n_type is not None:
                # apply type override
                f_type = n_type
                base_type = typing.Literal

        case _ if typing.is_typeddict(f_type):
            is_dict = True
        case _ if is_NamedTuple_type(f_type):
            is_dict = True
        case builtins.dict | builtins.list | builtins.set:
            is_dict = base_type==builtins.dict
            # possibly replace inner type of container
            if n_type is not None:
                o_types = typing.get_args(f_type)
                which = tuple(o==v_type for o in o_types)
                if sum(which)!=1:
                    raise ValueError(f'Input type ({f_type}) has no or more than one subscripted types that match the type of the set of possible values ({v_type}), cannot replace {v_type} with {n_type}')
                # now, replace type
                f_type = base_type[tuple(n_type if r else o for o,r in zip(o_types, which))]
        case typing.Union if f_type==typing.Union[str, pathlib.Path]:
            is_dict = False
        case _:
            raise ValueError(f'type of {field} ({f_type}) not handled')
    return is_dict, base_type, f_type

def _draw_impl(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]], mark: list[str], level=0, table_is_started=False) -> tuple[bool,bool,bool,_C]:
    changed = False
    max_fields_width = _get_fields_text_width(fields)*1.1   # 10% extra to be safe
    ret_new_obj = False
    for f in fields:
        is_dict, base_type, f_type = _get_field_type(f, obj, types[f], possible_value_getters[f] if f in possible_value_getters else None)

        if is_dict and (not mark or not f in mark):
            if table_is_started:
                imgui.end_table()
                table_is_started = False
            if imgui.tree_node_ex(f,imgui.TreeNodeFlags_.framed):
                this_changed, made_obj, new_sub_obj = draw_dict_editor(obj.get(f,None) if isinstance(obj,dict) else getattr(obj,f), f_type, level+1)
                changed |= this_changed
                if this_changed and made_obj:
                    setattr(obj,f,new_sub_obj)
                imgui.tree_pop()
            continue

        # simple field, set up for drawing
        if not table_is_started:
            table_is_started = _start_table(level, max_fields_width)
            if not table_is_started:
                continue

        this_changed, new_f_obj = _draw_field(f, obj, base_type, f_type, defaults[f] if f in defaults else None, mark=mark is not None and f in mark)
        changed |= this_changed
        if this_changed and new_f_obj is not None:
            ret_new_obj = True
            obj = new_f_obj
    return table_is_started, changed, ret_new_obj, obj

def draw_dict_editor(obj: _T, o_type: typing.Type, level: int, fields: list=None, types: dict[typing.Any, typing.Type]=None, defaults:dict[typing.Any, typing.Any]=None, mark: list[str]=None) -> tuple[bool,bool,_T]:
    made_or_replaced_obj = False
    if (made_or_replaced_obj := obj is None):
        obj = o_type()

    has_add = has_remove = False
    if typing.is_typeddict(o_type):
        types = o_type.__annotations__
        fields = list(types.keys())
    elif is_NamedTuple_type(o_type):
        types = o_type.__annotations__
        fields= list(o_type._fields)
    else:
        k_type = typing.get_args(o_type)
        all_fields = None
        if k_type and typing.get_origin(k_type[0])==typing.Literal:
            all_fields = set(typing.get_args(k_type))
        has_add = has_remove = fields is None
        if has_add:
            fields = list(obj.keys())
            if all_fields is not None and not (all_fields-set(fields)):
                # nothing more to add, all possible keys exhausted
                has_add = False
                # but keep has_remove to True
            types = {k:type(obj[k]) for k in obj}
    if defaults is None:
        defaults = {}

    first_column_width = _get_fields_text_width(fields, backup_str='xadd itemx')*1.1
    table_is_started = _start_table(level, first_column_width)
    if not table_is_started:
        return False, made_or_replaced_obj, obj
    table_is_started, changed, ret_new_obj, obj = _draw_impl(obj, fields, types, defaults, {}, mark, level, table_is_started)
    made_or_replaced_obj |= ret_new_obj
    if not table_is_started and has_add:
        table_is_started = _start_table(level, first_column_width)
        if not table_is_started:
            return changed, made_or_replaced_obj, obj
    if has_add:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.button('add item')
    if table_is_started:
        imgui.end_table()

    return changed, made_or_replaced_obj, obj

def _get_fields_text_width(fields, backup_str='xxxxx'):
    return max([imgui.calc_text_size(f) for f in fields], key=lambda x: x.x, default=imgui.calc_text_size(backup_str)).x

def _start_table(level, first_column_width):
    table_is_started = imgui.begin_table(f"##settings_level_{level}", 2)
    if not table_is_started:
        return table_is_started
    imgui.table_setup_column("setting", imgui.TableColumnFlags_.width_fixed, init_width_or_weight=first_column_width)
    imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)
    return table_is_started

def _draw_field(field: str, obj: _T, base_type: typing.Type, f_type: typing.Type, default: _T|None, mark: bool) -> bool:
    imgui.table_next_row()
    imgui.table_next_column()
    val = obj.get(field,f_type()) if isinstance(obj,dict) else getattr(obj,field)
    if mark:
        imgui.align_text_to_frame_padding()
        imgui.text_colored(colors.error, field)
    elif (is_default := val==default):
        imgui.align_text_to_frame_padding()
        imgui.text_colored(imgui.ImVec4(*color_darken(imgui.ImColor(imgui.get_style_color_vec4(imgui.Col_.text)), .75)), field)
    else:
        imgui_md.render(f'**{field}**')
    imgui.table_next_column()
    # TODO: should handle None value: print as <not set> and bring up editor upon clicking on it, or something like that
    # maybe store ID of currently editing None to bypass this print and go to the below editor?
    match base_type:
        case _ if val is None:
            new_val = val
            imgui.text('<not set>')
        case builtins.bool:
            new_val = imgui.checkbox(f'##{field}', val)[1]
        case builtins.str:
            new_val = imgui.input_text(f'##{field}', val)[1]
        case typing.Union if f_type==typing.Union[str, pathlib.Path]:
            new_val = imgui.input_text(f'##{field}', str(val))[1]
            if isinstance(val,pathlib.Path):
                new_val = pathlib.Path(new_val)
        case builtins.int:
            new_val = imgui.input_int(f'##{field}', val)[1]
        case builtins.float:
            new_val = imgui.input_double(f'##{field}', val)[1]
        case builtins.list | builtins.set:
            # temporary, this does not need a new level but a special input type
            # should be rendered as a tag list [A x][B x] with either text input if
            # unconstrained subtype (e.g. int, str, use typecheck utility), or
            # dropdown if known list of options
            imgui.text(f'{f_type}')
            new_val = val
        case typing.Literal:
            values = typing.get_args(f_type)
            p_idx = values.index(val)
            str_values = values
            if f_type in val_to_str_registry:
                str_values = [val_to_str_registry[f_type][v] for v in str_values]
            elif not isinstance(str_values[0],str):
                str_values = [str(v) for v in str_values]
            _,p_idx = imgui.combo(f"##{field}", p_idx, str_values, popup_max_height_in_items=min(10,len(values)))
            new_val = values[p_idx]

    new_obj = None
    if (changed := new_val!=val):
        if isinstance(obj,dict):
            obj[field] = new_val
        elif is_NamedTuple_type(type(obj)):
            # tuples are immutable, have to return new instance
            new_obj = obj._replace(**{field:new_val})
        else:
            setattr(obj,field,new_val)

    return changed, new_obj