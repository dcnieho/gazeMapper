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

    def _draw_impl(self, field: str, obj: typing.Any, f_type: typing.Type, default: typing.Any|None):
        match typing.get_origin(f_type) or f_type:  # for instance str[int]->str, and or for str->str
            case builtins.bool:
                imgui.text(f'{field} is a bool')
            case builtins.str:
                imgui.text(f'{field} is a str')
            case builtins.int:
                imgui.text(f'{field} is an int')
            case builtins.float:
                imgui.text(f'{field} is a float')
            case td if typing.is_typeddict(f_type):
                imgui.text(f'{field} is a TypedDict')
            case builtins.dict:
                imgui.text(f'{field} is a dict')
            case builtins.list:
                imgui.text(f'{field} is a list')
            case _:
                imgui.text(f'{field} ({f_type}) unmatched')

    def draw(self):
        for f in self.fields:
            self._draw_impl(f, self.obj, self.types[f], self.defaults[f] if f in self.defaults else None)