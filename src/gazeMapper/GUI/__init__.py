import argparse
import ctypes
import sys

from glassesTools import async_thread, platform

from ._impl import gui


def run():
    if platform.os==platform.Os.Windows:
        # Hide conhost if frozen or release
        parser = argparse.ArgumentParser(description="gazeMapper")
        # default: hide when frozen, don't hide otherwise
        parser.add_argument('--nohide', action='store_false', help="hide console window", default=not getattr(sys, "frozen", False))
        args = parser.parse_args()

        if not args.nohide:
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    else:
        # Install uvloop on MacOS and Linux
        try:
            import uvloop
            uvloop.install()
        except Exception:
            pass

    g = gui.GUI()
    async_thread.setup()
    g.run()
    async_thread.cleanup()