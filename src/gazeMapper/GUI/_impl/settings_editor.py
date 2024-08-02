import typing
import builtins

from imgui_bundle import imgui, imgui_md

from glassesTools.timeline_gui import color_darken

_C = typing.TypeVar("_C")
_T = typing.TypeVar("_T")
class SettingEditor:
    def __init__(self, obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]]):
        self.obj                    = obj
        self.fields                 = fields
        self.types                  = types
        self.defaults               = defaults
        self.possible_value_getters = possible_value_getters

    def draw(self) -> bool:
        if not self.fields:
            return

        table_is_started, changed = _draw_impl(self.obj, self.fields, self.types, self.defaults, self.possible_value_getters)
        if table_is_started:
            imgui.end_table()

        return changed


def _get_field_type(field: str, obj: _T, f_type: typing.Type, possible_value_getter: typing.Callable[[_C],tuple[_T]]|None) -> tuple[bool, typing.Type, typing.Type]:
    base_type = typing.get_origin(f_type) or f_type  # for instance str[int]->str, and or for str->str
    if possible_value_getter:
        # we have a set of possible values known at runtime: override unconstrained type to a Literal
        vals = possible_value_getter(obj)
        if len({type(v) for v in vals})!=1:
            raise ValueError(f'Cannot perform type replacement. possible_value_getter should return a set of values that all have the same type')
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
        case builtins.dict | builtins.list:
            is_dict = base_type==builtins.dict
            # possibly replace inner type of container
            if n_type is not None:
                v_type = type(typing.get_args(n_type)[0])
                o_types= typing.get_args(f_type)
                which = tuple(o==v_type for o in o_types)
                if sum(which)!=1:
                    raise ValueError(f'Input type ({f_type}) has no or more than one subscripted types that match the type of the set of possible values ({v_type}), cannot replace {v_type} with {n_type}')
                # now, replace type
                f_type = base_type[tuple(n_type if r else o for o,r in zip(o_types, which))]
        case _:
            raise ValueError(f'type of {field} ({f_type}) not handled')
    return is_dict, base_type, f_type

def _draw_impl(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]], level=0, table_is_started=False) -> tuple[bool,bool]:
    changed = False
    max_fields_width = _get_fields_text_width(fields)*1.1   # 10% extra to be safe
    for f in fields:
        is_dict, base_type, f_type = _get_field_type(f, obj, types[f], possible_value_getters[f] if f in possible_value_getters else None)

        if is_dict:
            if table_is_started:
                imgui.end_table()
                table_is_started = False
            if imgui.tree_node_ex(f,imgui.TreeNodeFlags_.framed):
                this_changed, made_obj, new_sub_obj = _draw_dict_editor(obj.get(f,None) if isinstance(obj,dict) else getattr(obj,f), f_type, level+1)
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

        changed |= _draw_field(f, obj, base_type, f_type, defaults[f] if f in defaults else None)
    return table_is_started, changed

def _draw_dict_editor(obj: _T, o_type: typing.Type, level: int):
    if (made_obj := obj is None):
        obj = o_type()

    if typing.is_typeddict(o_type):
        has_add_remove = False
        types = o_type.__annotations__
        fields = list(types.keys())
    else:
        has_add_remove = True
        fields = list(obj.keys())
        types = {k:type(obj[k]) for k in obj}

    first_column_width = _get_fields_text_width(fields, backup_str='xadd itemx')*1.1
    table_is_started = _start_table(level, first_column_width)
    if not table_is_started:
        return
    table_is_started, changed = _draw_impl(obj, fields, types, {}, {}, level, table_is_started)
    if not table_is_started and has_add_remove:
        table_is_started = _start_table(level, first_column_width)
        if not table_is_started:
            return
    if has_add_remove:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.button('add item')
    if table_is_started:
        imgui.end_table()

    return changed, made_obj, obj

def _get_fields_text_width(fields, backup_str='xxxxx'):
    return max([imgui.calc_text_size(f) for f in fields], key=lambda x: x.x, default=imgui.calc_text_size(backup_str)).x

def _start_table(level, first_column_width):
    table_is_started = imgui.begin_table(f"##settings_level_{level}", 2)
    if not table_is_started:
        return table_is_started
    imgui.table_setup_column("setting", imgui.TableColumnFlags_.width_fixed, init_width_or_weight=first_column_width)
    imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)
    return table_is_started

def _draw_field(field: str, obj: _T, base_type: typing.Type, f_type: typing.Type, default: _T|None) -> bool:
    imgui.table_next_row()
    imgui.table_next_column()
    val = obj.get(field,f_type()) if isinstance(obj,dict) else getattr(obj,field)
    if (is_default := val==default):
        imgui.align_text_to_frame_padding()
        imgui.text_colored(imgui.ImVec4(*color_darken(imgui.ImColor(imgui.get_style_color_vec4(imgui.Col_.text)), .75)), field)
    else:
        imgui_md.render(f'**{field}**')
    imgui.table_next_column()
    match base_type:
        case builtins.bool:
            new_val = imgui.checkbox(f'##{field}', val)[1]
        case builtins.str:
            new_val = imgui.input_text(f'##{field}', val)[1]
        case builtins.int:
            new_val = imgui.input_int(f'##{field}', val)[1]
        case builtins.float:
            new_val = imgui.input_double(f'##{field}', val)[1]
        case builtins.list:
            # temporary, this does not need a new level but a special input type
            # should be rendered as a tag list [A x][B x] with either text input if
            # unconstrained subtype (e.g. int, str, use typecheck utility), or
            # dropdown if known list of options
            imgui.text(f'{f_type}')
            new_val = val
        case typing.Literal:
            values = typing.get_args(f_type)
            p_idx = values.index(val)
            _,p_idx = imgui.combo(f"##{field}", p_idx, values, popup_max_height_in_items=min(10,len(values)))
            new_val = values[p_idx]

    if (changed := new_val!=val):
        if isinstance(obj,dict):
            obj[field] = new_val
        else:
            setattr(obj,field,new_val)

    return changed