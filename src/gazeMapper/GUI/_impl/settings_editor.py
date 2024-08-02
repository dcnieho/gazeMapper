import typing
import builtins

from imgui_bundle import imgui

_C = typing.TypeVar("_C")
_T = typing.TypeVar("_T")
class SettingEditor:
    def __init__(self, obj: _C, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any], possible_value_getters: dict[str, typing.Callable[[_C], tuple[typing.Any]]]):
        self.obj                    = obj
        self.fields                 = fields
        self.types                  = types
        self.defaults               = defaults
        self.possible_value_getters = possible_value_getters

    def _draw_impl(self, table_is_started = False):
        for f in self.fields:
            table_is_started = self._draw_field(f, self.obj, self.types[f], self.defaults[f] if f in self.defaults else None, self.possible_value_getters[f] if f in self.possible_value_getters else None, table_is_started)
        return table_is_started

    def _draw_field(self, field: str, obj: _T, f_type: typing.Type, default: _T|None, possible_value_getter: typing.Callable[[_C],tuple[_T]]|None, table_is_started: bool):
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

        if is_container:
            # recurse
            return table_is_started

        # simple field, draw
        if not table_is_started:
            table_is_started = imgui.begin_table("some_name", 2)
            if not table_is_started:
                return table_is_started
            imgui.table_setup_column("setting", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column("value", imgui.TableColumnFlags_.width_stretch)

        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        imgui.text(field)
        imgui.table_next_column()
        match base_type:
            case builtins.bool:
                setattr(obj,field,imgui.checkbox(f'##{field}', getattr(obj,field))[1])
            case builtins.str:
                setattr(obj,field,imgui.input_text(f'##{field}', getattr(obj,field))[1])
            case builtins.int:
                setattr(obj,field,imgui.input_int(f'##{field}', getattr(obj,field))[1])
            case builtins.float:
                setattr(obj,field,imgui.input_double(f'##{field}', getattr(obj,field))[1])
            case typing.Literal:
                values = typing.get_args(f_type)
                p_idx = values.index(getattr(obj,field))
                _,p_idx = imgui.combo(f"##{field}", p_idx, values, popup_max_height_in_items=min(10,len(values)))
                setattr(obj,field,values[p_idx])

        return table_is_started

    def draw(self):
        if not self.fields:
            return

        if self._draw_impl():
            imgui.end_table()