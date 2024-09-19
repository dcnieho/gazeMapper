from glassesTools import async_thread, platform

from ._impl.gui import GUI as _GUI


# GUI requires some setup, call these functions
def set_up():
    async_thread.setup()
def clean_up():
    async_thread.cleanup()

def run():
    if platform.os!=platform.Os.Windows:
        # Install uvloop on MacOS and Linux
        try:
            import uvloop
            uvloop.install()
        except Exception:
            pass

    gui = _GUI()
    gui.run()