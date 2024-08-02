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
            is_container = False
            if n_type is not None:
                # apply type override
                f_type = n_type
                base_type = typing.Literal

        case _ if typing.is_typeddict(f_type):
            is_container = True
        case builtins.dict | builtins.list:
            is_container = True
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
    return is_container, base_type, f_type

def _draw_impl(obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]], level=0, table_is_started=False) -> tuple[bool,bool]:
    changed = False
    max_fields_width = max([imgui.calc_text_size(f) for f in fields], key=lambda x: x.x).x*1.1   # 10% extra to be safe
    for f in fields:
        need_new_level, base_type, f_type = _get_field_type(f, obj, types[f], possible_value_getters[f] if f in possible_value_getters else None)

        if need_new_level:
            if base_type==builtins.list:
                # temporary, this does not need a new level but a special input type
                # should be rendered as a tag list [A x][B x] with either text input if
                # unconstrained subtype (e.g. int, str, use typecheck utility), or
                # dropdown if known list of options
                imgui.text(f'{f}: {f_type}')
                continue
            # recurse
            if table_is_started:
                imgui.end_table()
                table_is_started = False
            if imgui.tree_node_ex(f,imgui.TreeNodeFlags_.framed):
                imgui.tree_pop()
            continue

        # simple field, set up for drawing
        if not table_is_started:
            table_is_started = imgui.begin_table(f"##settings_level_{level}", 2)
            if not table_is_started:
                continue
            imgui.table_setup_column("setting", imgui.TableColumnFlags_.width_fixed, init_width_or_weight=max_fields_width)
            imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)

        changed |= _draw_field(f, obj, base_type, f_type, defaults[f] if f in defaults else None)
    return table_is_started, changed

def _draw_field(field: str, obj: _T, base_type: typing.Type, f_type: typing.Type, default: _T|None) -> bool:
    imgui.table_next_row()
    imgui.table_next_column()
    val = getattr(obj,field)
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
        case typing.Literal:
            values = typing.get_args(f_type)
            p_idx = values.index(val)
            _,p_idx = imgui.combo(f"##{field}", p_idx, values, popup_max_height_in_items=min(10,len(values)))
            new_val = values[p_idx]

    if (changed := new_val!=val):
        setattr(obj,field,new_val)

    return changed