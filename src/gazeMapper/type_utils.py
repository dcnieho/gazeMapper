import typing
import inspect
import cv2
import dataclasses

from . import typed_dict_defaults

ProblemDict = dict[str,typing.Union[None,str,'ProblemDict']]
NestedDict = dict[str,typing.Union[None,'NestedDict']]

@dataclasses.dataclass
class GUIDocInfo:
    display_string: str
    doc_str:        str
    children:       dict[str,'GUIDocInfo'] = dataclasses.field(default_factory=lambda: {})

aruco_dicts_to_str = {getattr(cv2.aruco,k):k for k in ['DICT_4X4_50', 'DICT_4X4_100', 'DICT_4X4_250', 'DICT_4X4_1000', 'DICT_5X5_50', 'DICT_5X5_100', 'DICT_5X5_250', 'DICT_5X5_1000', 'DICT_6X6_50', 'DICT_6X6_100', 'DICT_6X6_250', 'DICT_6X6_1000', 'DICT_7X7_50', 'DICT_7X7_100', 'DICT_7X7_250', 'DICT_7X7_1000', 'DICT_ARUCO_ORIGINAL', 'DICT_APRILTAG_16H5', 'DICT_APRILTAG_25H9', 'DICT_APRILTAG_36H10', 'DICT_APRILTAG_36H11', 'DICT_ARUCO_MIP_36H12']}
ArucoDictType = typing.Literal[tuple(aruco_dicts_to_str.keys())]


def merge_problem_dicts(a: ProblemDict, b: ProblemDict):
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_problem_dicts(a[key], b[key])
            elif isinstance(a[key], dict) or isinstance(b[key], dict):
                if isinstance(a[key], dict):
                    if 'problem_with_this_key' in a[key]:
                        a[key]['problem_with_this_key'] = '\n'.join([a[key]['problem_with_this_key'], b[key]])
                    else:
                        a[key]['problem_with_this_key'] = b[key]
                else:
                    temp = a[key]
                    a[key] = b[key].copy()
                    if 'problem_with_this_key' in a[key]:
                        a[key]['problem_with_this_key'] = '\n'.join([a[key]['problem_with_this_key'], temp])
                    else:
                        a[key]['problem_with_this_key'] = temp
            elif a[key] is None:
                a[key] = b[key]
            elif b[key] is None:
                pass    # do nothing
            else:
                a[key] = '\n'.join([a[key], b[key]])
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