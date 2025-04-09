from watchfiles import Change, DefaultFilter, awatch
import asyncio
import pathlib
from typing import Callable

from glassesTools import utils

class ChangeFilter(DefaultFilter):
    def __init__(self, extensions={'.json', '.gazeMapper'}, do_report_directories:bool=False, exclude_paths:set[pathlib.Path]=None, do_report_files=False):
        super().__init__(ignore_paths=exclude_paths)
        self.extensions             = extensions
        self.do_report_directories  = do_report_directories
        self.exclude_paths          = exclude_paths or set()
        self.do_report_files        = do_report_files

        # to be able to correctly detect directories, we have a problem. We cannot use is_dir() on a path, would always be false for deletes
        # so instead keep a list of all known directories. For adds and modifies go out to the file system, for deletes, check this list
        # checking for a directory with PurePath.suffix is not reliable as it would filter out all directories with a dot (.) in their name
        self.base_dir: pathlib.Path = None
        self._known_dirs: set[pathlib.Path] = None

    def set_base_dir(self, base_dir: pathlib.Path):
        self.base_dir = base_dir
        self._known_dirs = set(utils.fast_scandir(self.base_dir))
        # filter out excluded paths
        for p in self.exclude_paths:
            self._known_dirs = {kp for kp in self._known_dirs if not kp.is_relative_to(p)}

    def __call__(self, change: Change, path: str) -> bool:
        if not super().__call__(change, path):
            return False

        # handle deletes separately from modifies and adds
        path = pathlib.Path(path)
        if change==Change.deleted:
            if path in self._known_dirs:
                self._known_dirs.discard(path)
                return self.do_report_directories
            elif not self.do_report_files:
                return False
            else:
                return path.suffix in self.extensions
        else:
            if path.is_dir():
                self._known_dirs.add(path)
                return self.do_report_directories
            elif not self.do_report_files:
                return False
            else:
                return path.suffix in self.extensions

async def watch_and_report_changes(path: pathlib.Path, callback: Callable, stop_event: asyncio.Event, watch_filter=ChangeFilter()):
    watch_filter.set_base_dir(path)

    async for changes in awatch(path, debounce=500, watch_filter=watch_filter, stop_event=stop_event):
        for change_type,change_path in changes:
            callback(pathlib.Path(change_path), change_type.raw_str())