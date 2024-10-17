import cx_Freeze
import pathlib
import sys
import site
import os

path = pathlib.Path(__file__).absolute().parent
sys.path.append(str(path/'src'))

def get_include_files():
    files = [path / "LICENSE"]

    # ffpyplayer bin deps
    for d in site.getsitepackages():
        d = pathlib.Path(d) / 'share' / 'ffpyplayer'
        for lib in ('ffmpeg', 'sdl'):
            for f in (d/lib/'bin').glob('*'):
                if f.is_file() and f.suffix=='' or f.suffix in ['.dll', '.exe']:
                    files.append((f,pathlib.Path('lib')/f.name))
    return files

def get_zip_include_files():
    files = []
    for d in site.getsitepackages():
        base = pathlib.Path(d)
        p = base / 'glassesValidator' / 'config'

        for f in p.rglob('*'):
            if f.is_file() and f.suffix not in ['.py','.pyc']:
                files.append((f, pathlib.Path(os.path.relpath(f,base))))
    return files

main_ns = {}
ver_path = pathlib.Path('src/gazeMapper/version.py')
with open(ver_path) as ver_file:
    exec(ver_file.read(), main_ns)

build_options = {
    "build_exe": {
        "optimize": 1,
        "packages": ['OpenGL','gazeMapper',
            'ffpyplayer.player','ffpyplayer.threading',      # some specific subpackages that need to be mentioned to be picked up correctly
            'imgui_bundle._imgui_bundle'
        ],
        "excludes":["tkinter"],
        "zip_includes": get_zip_include_files(),
        "zip_include_packages": "*",
        "zip_exclude_packages": [
            "OpenGL_accelerate",
            "glfw",
            "imgui_bundle",
        ],
        "silent_level": 1,
        "include_msvcr": True
    },
    "bdist_mac": {
        "bundle_name": "gazeMapper",
        "codesign_identity": None,
        "plist_items": [
            ("CFBundleName", "gazeMapper"),
            ("CFBundleDisplayName", "gazeMapper"),
            ("CFBundleIdentifier", "com.github.dcnieho.gazeMapper"),
            ("CFBundleVersion", main_ns['__version__']),
            ("CFBundlePackageType", "APPL"),
            ("CFBundleSignature", "????"),
        ]
    }
}
if sys.platform.startswith("win"):
    build_options["build_exe"]["include_files"] = get_include_files()

cx_Freeze.setup(
    name="gazeMapper",
    version=main_ns['__version__'],
    description=main_ns['__description__'],
    executables=[
        cx_Freeze.Executable(
            script=path / "main.py",
            target_name="gazeMapper"
        )
    ],
    options=build_options,
    py_modules=[]
)
