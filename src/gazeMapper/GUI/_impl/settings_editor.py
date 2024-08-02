import typing
import builtins
import collections

from imgui_bundle import imgui

class SettingEditor:
    def __init__(self, obj: typing.Any, fields: list[str], types: dict[str, typing.Type], defaults: dict[str, typing.Any]):
        self.obj        = obj
        self.fields     = fields
        self.types      = types
        self.defaults   = defaults

    def _draw_impl(self, table_is_started = False):
        for f in self.fields:
            table_is_started = self._draw_field(f, self.obj, self.types[f], self.defaults[f] if f in self.defaults else None, table_is_started)
        return table_is_started

    def _draw_field(self, field: str, obj: typing.Any, f_type: typing.Type, default: typing.Any|None, table_is_started: bool):
        base_type = typing.get_origin(f_type) or f_type  # for instance str[int]->str, and or for str->str
        match base_type:
            case builtins.bool | builtins.str | builtins.int | builtins.float | typing.Literal:
                is_container = False
            case _ if typing.is_typeddict(f_type):
                is_container = True
            case builtins.dict | builtins.list:
                is_container = True
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