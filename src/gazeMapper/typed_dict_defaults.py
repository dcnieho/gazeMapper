import typing
import sys

# version of typing.TypedDict (from Python 3.10) that supports defaults for the fields
# changes:
# 1. has a new field _field_defaults to list the provided default values (liked collection.namedtuple)
# 2. adds a function obj.apply_defaults() that in-place modified the dict by adding the default
#    parameters (optionally overwriting)

class DefaultsMixin:
    def apply_defaults(obj, overwrite: bool = False):
        for f in obj._field_defaults:
            if f not in obj or overwrite:
                obj[f] = obj._field_defaults[f]

class _TypedDictDefaultMeta(type):
    def __new__(cls, name, bases, ns, total=True):
        """Create new typed dict class object.

        This method is called when TypedDictDefault is subclassed,
        or when TypedDictDefault is instantiated. This way
        TypedDictDefault supports all three syntax forms described in its docstring.
        Subclasses and instances of TypedDictDefault return actual dictionaries.
        """
        for base in bases:
            if type(base) not in [_TypedDictDefaultMeta, typing._TypedDictMeta]:
                raise TypeError('cannot inherit from both a TypedDictDefault type '
                                'and a non-TypedDictDefault base class')
        tp_dict = type.__new__(_TypedDictDefaultMeta, name, (dict,DefaultsMixin), ns)

        annotations = {}
        own_annotations = ns.get('__annotations__', {})
        own_annotation_keys = set(own_annotations.keys())
        msg = "TypedDictDefault('Name', {f0: t0, f1: t1, ...}); each t must be a type"
        own_annotations = {
            n: typing._type_check(tp, msg, module=tp_dict.__module__)
            for n, tp in own_annotations.items()
        }
        required_keys = set()
        optional_keys = set()

        for base in bases:
            annotations.update(base.__dict__.get('__annotations__', {}))
            required_keys.update(base.__dict__.get('__required_keys__', ()))
            optional_keys.update(base.__dict__.get('__optional_keys__', ()))

        annotations.update(own_annotations)
        if total:
            required_keys.update(own_annotation_keys)
        else:
            optional_keys.update(own_annotation_keys)

        default_names = []
        for field_name in annotations:
            if field_name in ns:
                default_names.append(field_name)
                delattr(tp_dict, field_name)    # clean up
            elif default_names:
                raise TypeError(f"Non-default TypedDict field {field_name} "
                                f"cannot follow default field"
                                f"{'s' if len(default_names) > 1 else ''} "
                                f"{', '.join(default_names)}")

        tp_dict.__annotations__ = annotations
        tp_dict.__required_keys__ = frozenset(required_keys)
        tp_dict.__optional_keys__ = frozenset(optional_keys)
        tp_dict._field_defaults = {n:ns[n] for n in default_names}
        if not hasattr(tp_dict, '__total__'):
            tp_dict.__total__ = total
        return tp_dict

    def __call__(self, *args, **kwargs):
        val = super().__call__(*args, **kwargs)
        val.apply_defaults()
        return val

    def __subclasscheck__(cls, other):
        # Typed dicts are only for static structural subtyping.
        raise TypeError('TypedDictDefault does not support instance and class checks')

    __instancecheck__ = __subclasscheck__


def TypedDictDefault(typename, fields=None, /, *, total=True, **kwargs):
    """A simple typed namespace. At runtime it is equivalent to a plain dict.

    TypedDictDefault creates a dictionary type that expects all of its
    instances to have a certain set of keys, where each key is
    associated with a value of a consistent type. This expectation
    is not checked at runtime but is only enforced by type checkers.
    Usage::

        class Point2D(TypedDictDefault):
            x: int
            y: int
            label: str

        a: Point2D = {'x': 1, 'y': 2, 'label': 'good'}  # OK
        b: Point2D = {'z': 3, 'label': 'bad'}           # Fails type check

        assert Point2D(x=1, y=2, label='first') == dict(x=1, y=2, label='first')

    The type info can be accessed via the Point2D.__annotations__ dict, and
    the Point2D.__required_keys__ and Point2D.__optional_keys__ frozensets.
    TypedDictDefault supports two additional equivalent forms::

        Point2D = TypedDictDefault('Point2D', x=int, y=int, label=str)
        Point2D = TypedDictDefault('Point2D', {'x': int, 'y': int, 'label': str})

    By default, all keys must be present in a TypedDictDefault. It is possible
    to override this by specifying totality.
    Usage::

        class point2D(TypedDictDefault, total=False):
            x: int
            y: int

    This means that a point2D TypedDictDefault can have any of the keys omitted. A type
    checker is only expected to support a literal False or True as the value of
    the total argument. True is the default, and makes all items defined in the
    class body be required.

    The class syntax is only supported in Python 3.6+, while two other
    syntax forms work for Python 2.7 and 3.2+
    """
    if fields is None:
        fields = kwargs
    elif kwargs:
        raise TypeError("TypedDictDefault takes either a dict or keyword arguments,"
                        " but not both")

    ns = {'__annotations__': dict(fields)}
    try:
        # Setting correct module is necessary to make typed dict classes pickleable.
        ns['__module__'] = sys._getframe(1).f_globals.get('__name__', '__main__')
    except (AttributeError, ValueError):
        pass

    return _TypedDictDefaultMeta(typename, (), ns, total=total)

_TypedDictDefault = type.__new__(_TypedDictDefaultMeta, 'TypedDictDefault', (), {})
TypedDictDefault.__mro_entries__ = lambda bases: (_TypedDictDefault,)

def is_typeddictdefault(tp):
    """Check if an annotation is a TypedDictDefault class

    For example::
        class Film(TypedDictDefault):
            title: str
            year: int

        is_typeddictdefault(Film)  # => True
        is_typeddictdefault(Union[list, str])  # => False
    """
    return isinstance(tp, _TypedDictDefaultMeta)

# register type checker with typegaurd. Can use their builtin checker for TypedDict
from typeguard import TypeCheckerCallable, checker_lookup_functions
from typeguard._checkers import check_typed_dict
def checker_lookup(
    origin_type: typing.Any, args: tuple[typing.Any, ...], extras: tuple[typing.Any, ...]
) -> TypeCheckerCallable | None:
    if is_typeddictdefault(origin_type):
        return check_typed_dict

    return None
checker_lookup_functions.append(checker_lookup)