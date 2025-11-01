import typing
import inspect
import dataclasses
import enum

from glassesTools import aruco

from . import typed_dict_defaults

class ProblemLevel(enum.Enum):
    Information = enum.auto()
    Warning     = enum.auto()
    Error       = enum.auto()

ProblemDict = dict[str,tuple[ProblemLevel,typing.Union[None,str,'ProblemDict']]]
NestedDict = dict[str,typing.Union[None,'NestedDict']]

def get_error_level(problem: ProblemDict|tuple[ProblemLevel,str]) -> ProblemLevel:
    problem_level = None
    # check if any error, then return error immediately. Recurse if needed
    if isinstance(problem, tuple):
        return problem[0]
    for key in problem:
        if isinstance(problem[key], dict):
            problem_level = get_error_level(problem[key])
            if problem_level==ProblemLevel.Error:
                return problem_level
        else:
            if problem[key][0]==ProblemLevel.Error:
                return ProblemLevel.Error
            elif problem[key][0]==ProblemLevel.Warning and problem_level!=ProblemLevel.Error:
                problem_level = ProblemLevel.Warning
    return problem_level or ProblemLevel.Error   # default to error

@dataclasses.dataclass
class GUIDocInfo:
    display_string: str
    doc_str:        str
    children:       dict[str,'GUIDocInfo'] = dataclasses.field(default_factory=lambda: {})

ArucoDictType = typing.Literal[tuple(aruco.dict_id_to_str.keys())]


def merge_problem_dicts(a: ProblemDict, b: ProblemDict):
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_problem_dicts(a[key], b[key])
            elif isinstance(a[key], dict) or isinstance(b[key], dict):
                if isinstance(a[key], dict):
                    if 'problem_with_this_key' in a[key]:
                        level = ProblemLevel.Error if ProblemLevel.Error in (a[key]['problem_with_this_key'][0], b[key][0]) else ProblemLevel.Warning
                        a[key]['problem_with_this_key'] = (level, '\n'.join([a[key]['problem_with_this_key'][1], b[key][1]]))
                    else:
                        a[key]['problem_with_this_key'] = b[key]
                else:
                    temp = a[key]
                    a[key] = b[key].copy()
                    if 'problem_with_this_key' in a[key]:
                        level = ProblemLevel.Error if ProblemLevel.Error in (a[key]['problem_with_this_key'][0], temp[0]) else ProblemLevel.Warning
                        a[key]['problem_with_this_key'] = (level, '\n'.join([a[key]['problem_with_this_key'][1], temp[1]]))
                    else:
                        a[key]['problem_with_this_key'] = temp
            elif a[key] is None:
                a[key] = b[key]
            elif b[key] is None:
                pass    # do nothing
            else:
                level = ProblemLevel.Error if ProblemLevel.Error in (a[key][0], b[key][0]) else ProblemLevel.Warning
                a[key] = (level, '\n'.join([a[key][1], b[key][1]]))
        else:
            a[key] = b[key]
    return a


def is_NamedTuple_type(x):
  return (inspect.isclass(x) and issubclass(x, tuple) and
          hasattr(x, '_asdict') and callable(x._asdict) and
          hasattr(x, '__annotations__') and
          getattr(x, '_fields', None) is not None)

def get_fields(obj) -> list[str]|None:
    if not isinstance(obj, typing.Type):
        tobj = type(obj)
    else:
        tobj = obj
    if typing.is_typeddict(tobj):
        return list(obj.__annotations__.keys())
    elif typed_dict_defaults.is_typeddictdefault(tobj):
        return list(obj.__annotations__.keys())
    elif is_NamedTuple_type(tobj):
        return list(obj._fields)
    elif isinstance(obj, dict):
        return list(obj.keys())
    return None

def get_annotations(obj) -> dict[str, typing.Type]|None:
    if not isinstance(obj, typing.Type):
        tobj = type(obj)
    else:
        tobj = obj
    if typing.is_typeddict(tobj):
        return obj.__annotations__.copy()
    elif typed_dict_defaults.is_typeddictdefault(tobj):
        return obj.__annotations__.copy()
    elif is_NamedTuple_type(tobj):
        return obj.__annotations__.copy()
    elif isinstance(obj, dict):
        return {k:type(obj[k]) for k in obj}
    return None