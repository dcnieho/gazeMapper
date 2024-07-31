#!/usr/bin/env python
import sys
import os
import multiprocessing
import argparse
import ctypes


if getattr(sys, "frozen", False):
    if not sys.platform.startswith("win"):
        raise "Executable is only supported on Windows"

    # need to call this so that code in __init__ of ffpyplayer
    # doesn't encounter a None in site.USER_BASE
    import site
    site.getuserbase()

    # need to put packaged ffmpeg executable on path
    p = os.path.join(os.path.dirname(sys.executable),'lib')
    os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
    os.add_dll_directory(p)

if __name__=="__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="gazeMapper")
    parser.add_argument('--hide', action='store_true', help="hide console window")
    args = parser.parse_args()

    if args.hide:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    import gazeMapper.GUI
    gazeMapper.GUI.set_up()
    gazeMapper.GUI.run()
    gazeMapper.GUI.clean_up()
