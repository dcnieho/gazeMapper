import typing
import inspect
import dataclasses
import enum

from glassesTools import aruco, marker as gt_marker

from . import typed_dict_defaults

class ProblemLevel(enum.Enum):
    Information = enum.auto()
    Warning     = enum.auto()
    Error       = enum.auto()

ProblemKey      = str | int | gt_marker.MarkerFamilyID
# A leaf message: level + optional text
ProblemMessage  = tuple[ProblemLevel, str | None]
# A node is either a nested dict (branch) or a leaf tuple
ProblemEntry    = typing.Union['ProblemDict', ProblemMessage]
# Recursive problem tree
ProblemDict     = dict[ProblemKey, ProblemEntry]

NestedDict = dict[str,typing.Union[None,'NestedDict']]

def get_error_level(problem: ProblemDict|ProblemMessage) -> ProblemLevel|None:
    """Return the highest severity present, or None when there are no messages."""
    if isinstance(problem, tuple):
        return problem[0]
    problem_level = None
    for entry in problem.values():
        level = get_error_level(entry)
        if level == ProblemLevel.Error:
            return level
        if level is not None and (problem_level is None or level.value > problem_level.value):
            problem_level = level
    return problem_level


@dataclasses.dataclass
class GUIDocInfo:
    display_string: str
    doc_str:        str
    children:       dict[str,'GUIDocInfo'] = dataclasses.field(default_factory=lambda: {})

if typing.TYPE_CHECKING:
    ArucoDictType: typing.TypeAlias = int
else:
    # Runtime validation and GUI choices use the supported dictionary IDs.
    ArucoDictType = typing.Literal[tuple(aruco.dict_id_to_str.keys())]


def merge_problem_messages(a: ProblemMessage, b: ProblemMessage) -> ProblemMessage:
    level = max(a[0], b[0], key=lambda level: level.value)
    texts = [text for text in (a[1], b[1]) if text is not None]
    return level, '\n'.join(texts) if texts else None


def _merge_problem_message_into_dict(branch: ProblemDict, message: ProblemMessage):
    # Store a problem about the group itself alongside its child-field problems.
    key = 'problem_with_this_key'
    if key in branch:
        current = branch[key]
        if not isinstance(current, tuple):
            raise TypeError('problem_with_this_key must contain a problem message')
        message = merge_problem_messages(current, message)
    branch[key] = message


def merge_problem_dicts(a: ProblemDict, b: ProblemDict) -> ProblemDict:
    for key, incoming in b.items():
        if key not in a:
            # New field: retain its incoming message or subtree.
            a[key] = incoming
            continue
        current = a[key]
        if isinstance(current, dict):
            if isinstance(incoming, dict):
                # Two subtrees: merge their child-field problems recursively.
                merge_problem_dicts(current, incoming)
            else:
                # Existing subtree plus a new problem about the whole group.
                _merge_problem_message_into_dict(current, incoming)
        elif isinstance(incoming, dict):
            # Existing group problem plus a new subtree; don't modify b's root.
            branch = incoming.copy()
            _merge_problem_message_into_dict(branch, current)
            a[key] = branch
        else:
            # Two messages for the same field: keep both texts and highest severity.
            a[key] = merge_problem_messages(current, incoming)
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