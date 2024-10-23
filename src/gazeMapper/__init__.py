import pkgutil
import importlib

def import_submodules(package_name=__name__):
    return list({modname: importlib.import_module(package_name + '.' + modname)
         for _, modname, _ in pkgutil.iter_modules(__path__)}.keys())

__all__ = import_submodules()