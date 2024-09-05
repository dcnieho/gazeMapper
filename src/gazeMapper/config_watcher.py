from watchfiles import Change, DefaultFilter, awatch
import asyncio
import pathlib
from typing import Callable

class ConfigFilter(DefaultFilter):
    def __init__(self, extensions=('.json', '.gazeMapper'), do_report_directories:bool=False, exclude_paths:set[pathlib.Path]=None):
        super().__init__(ignore_paths=exclude_paths)
        self.extensions             = extensions
        self.do_report_directories  = do_report_directories

    def __call__(self, change: Change, path: str) -> bool:
        return (
            super().__call__(change, path) and (
                (not pathlib.PurePath(path).suffix and self.do_report_directories) or
                path.endswith(self.extensions)
            )
        )

async def watch_and_report_changes(path: pathlib.Path, callback: Callable, stop_event: asyncio.Event, watch_filter=ConfigFilter()):
    async for changes in awatch(path, debounce=500, watch_filter=watch_filter, stop_event=stop_event):
        for change_type,change_path in changes:
            callback(change_path, change_type.raw_str())