import enum
import sys

from glassesTools import async_thread

from ._impl.gui import GUI as _GUI


class Os(enum.Enum):
    Windows = enum.auto()
    MacOS   = enum.auto()
    Linux   = enum.auto()

if sys.platform.startswith("win"):
    os = Os.Windows
elif sys.platform.startswith("linux"):
    os = Os.Linux
elif sys.platform.startswith("darwin"):
    os = Os.MacOS
else:
    print("Your system is not officially supported at the moment!\n"
          "You can let me know on GitHub, or you can try porting yourself ;)")
    sys.exit(1)

# GUI requires some setup, call these functions
def set_up():
    async_thread.setup()
def clean_up():
    async_thread.cleanup()

def run():
    if os!=Os.Windows:
        # Install uvloop on MacOS and Linux
        try:
            import uvloop
            uvloop.install()
        except Exception:
            pass

    gui = _GUI()
    gui.run()