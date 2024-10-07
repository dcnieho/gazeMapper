[![Downloads](https://static.pepy.tech/badge/gazeMapper)](https://pepy.tech/project/gazeMapper)
[![Citation Badge](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.juleskreuer.eu%2Fcitation-badge.php%3Fshield%26doi%3D10.3758%2Fs13428-023-02105-5&color=blue)](https://scholar.google.com/citations?view_op=view_citation&citation_for_view=uRUYoVgAAAAJ:uWQEDVKXjbEC)
[![PyPI Latest Release](https://img.shields.io/pypi/v/gazeMapper.svg)](https://pypi.org/project/gazeMapper/)
[![image](https://img.shields.io/pypi/pyversions/gazeMapper.svg)](https://pypi.org/project/gazeMapper/)

# gazeMapper v0.5.0
Tool for automated world-based analysis of wearable eye tracker data.
gazeMapper is an open-source tool for automated mapping and processing of eye-tracking data to enable automated world-based analysis.
gazeMapper can:
1) Transform head-centered data to one or multiple planar surfaces in the world.
2) Synchronize recordings from multiple participants, and external cameras.
3) Determine data quality measures, e.g., accuracy and precision using [glassesValidator](https://github.com/dcnieho/glassesValidator) ([Niehorster et al., 2023](https://doi.org/10.3758/s13428-023-02105-5)).

If you use this tool or any of the code in this repository, please cite:<br>
Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. and Hooge, I.T.C. (in prep). gazeMapper: A tool for automated world-based analysis of wearable eye tracker data<br>
If you use the functionality for automatic determining the data quality (accuracy and precision) of wearable eye tracker recordings,please additionally cite:<br>
[Niehorster, D.C., Hessels, R.S., Benjamins, J.S., Nyström, M. and Hooge, I.T.C. (2023). GlassesValidator:
A data quality tool for eye tracking glasses. Behavior Research Methods. doi: 10.3758/s13428-023-02105-5](https://doi.org/10.3758/s13428-023-02105-5)

![application example](https://raw.githubusercontent.com/dcnieho/gazeMapper/master/.github/images/world_data.png?raw=true)
Example where gazeMapper has been used to map head-centered gaze from two head-worn eye tracker recordings to synchronized world-centered
gaze data of the pair, drawn on an overview video recording with an additional external camera. From Hessels, R.S., Iwabuchi, T., Niehorster, D.C., Funawatari, R., Benjamins, J.S., Kawakami, S.; Nyström, M., Suda, M., Hooge, I.T.C., Sumiya, M., Heijnen, J., Teunisse, M. & Senju, A. (2024). A setup for the crosscultural study of gaze behavior and eye contact in face-to-face collaboration. ECEM 2024

# How to acquire
GazeMapper is available from `https://github.com/dcnieho/gazeMapper`, and supports Python 3.10 and 3.11 on Windows, MacOS and Linux.

For Windows users who wish to use the gazeMapper GUI, the easiest way to acquire gazeMapper is to [download
a standalone executable](https://github.com/dcnieho/gazeMapper/releases/latest). The standalone executable is not
available for MacOS or Linux.

For users on Windows, Mac or Linux who wish to use gazeMapper in their Python code, the easiest way to acquire
gazeMapper is to install it directly into your Python distribution using the command
`python -m pip install gazeMapper`. If you run into problems on MacOS to install the `imgui_bundle` package, you can
try to install it first with the command `SYSTEM_VERSION_COMPAT=0 pip install --only-binary=:all: imgui_bundle`. Note that this repository is pip-installable as well:
`python -m pip install git+https://github.com/dcnieho/gazeMapper.git#egg=gazeMapper`. NB: on some platforms you may have
to replace `python` with `python3` in the above command lines.

Once pip-installed in your Python distribution, there are three ways to run the GUI on any of the supported operating systems:
1. Directly in the terminal of your operating system, type `gazeMapper` and run it.
2. Open a Python console. From such a console, running the GUI requires only the following two lines of code:
    ```python
    import gazeMapper.GUI
    gazeMapper.GUI.set_up()
    gazeMapper.GUI.run()
    gazeMapper.GUI.clean_up()
    ```
3. If you run the gazeMapper's GUI from a script, make sure to wrap your script in `if __name__=="__main__"`. This is required for correct operation from a script because the GUI uses multiprocessing functionality. Do as follows:
    ```python
    if __name__=="__main__":
        import gazeMapper.GUI
        gazeMapper.GUI.set_up()
        gazeMapper.GUI.run()
        gazeMapper.GUI.clean_up()
    ```

# Usage
The gazeMapper xxx. The gazeMapper package includes a graphical user interface (GUI) that can be used to perform all processing. Below we describe an example workflow using the GUI. Advanced users can however opt to call all the GUI's functionality directly from their own Python scripts without making use of the graphical user interface. The interested reader is referred to the [configuration](#configuration) and [API](#api) sections below for further details regarding how to use the gazeMapper functionality directly from their own scripts.

## Workflow and example data
Here we first present an example workflow using the GUI. More detailed information about [using the GUI](#the-gui), or [gazeMapper configuration](#configuration) and [its programming API](#api), are provided below.

**TODO**

## gazeMapper projects
The gazeMapper GUI organizes recordings into a project folder. Each session to be processed is represented by a folder in this project folder, and one or multiple recordings are stored in subfolders of a session folder. After importing recordings, all further processing is done inside these session and recording folders. The source directories containing the original recordings remain
untouched when running gazeMapper. A gazeMapper project folder furthermore contains a folder `config` specifying the configuration
of the project.

When not using the GUI and running gazeMapper using your own scripts, such a project folder organization is not required. Working folders
for a session can be placed anywhere, and a folder for a custom configuration can also be placed anywhere (but its location needs to be provided using the `config_dir` argument of all the functions in `gazeMapper.process`](#gazemapperprocess)). The
`gazeMapper.process`](#gazemapperprocess) functions simply take the path to a session or recording folder.

## Output
During the importing and processing of a recording, a series of files are created in the working folder of a recording. These are the following:
|file|produced<br>by|input<br>for|description|
| --- | --- | --- | --- |

### Coordinate system of data
Gaze data in poster space in the `gazePosterPos.tsv` file of a processed recording has its origin (0,0) at the center of the position
of the fixation target that was indicated to be the center target with the `centerTarget` setting in the [`validationSetup.txt`
configuration file](/src/glassesValidator/config/validationSetup.txt). The positive x-axis points to the right and the positive y-axis
downward, which means that (-,-) coordinates are to the left and above of the poster origin, and (+,+) to the right and below.

Angular accuracy values in the `dataQuality.tsv` file of a processed recording use the same sign-coding as the gaze data in poster space.
That is, for the horizontal component of reported accuracy values, positive means gaze is to the right of the fixation target and
negative to the left. For the vertical component, positive means gaze is below the fixation target, and negative that it is above the
fixation target.


## Eye trackers
gazeMapper supports the following eye trackers:
- AdHawk MindLink
- Pupil Core
- Pupil Invisible
- Pupil Neon
- SeeTrue STONE
- SMI ETG 1 and ETG 2
- Tobii Pro Glasses 2
- Tobii Pro Glasses 3

Pull requests or partial help implementing support for further wearable eye trackers are gladly received. To support a new eye tracker,
implement it in [glassesTools](https://github.com/dcnieho/glassesTools/blob/master/README.md#eye-tracker-support).

### Required preprocessing outside gazeMapper
For some eye trackers, the recording delivered by the eye tracker's recording unit or software can be directly imported into
gazeMapper. Recordings from some other eye trackers however require some steps to be performed in the manufacturer's
software before they can be imported into gazeMapper. These are:
- *Pupil Labs eye trackers*: Recordings should either be preprocessed using Pupil Player (*Pupil Core* and *Pupil Invisible*),
  Neon Player (*Pupil Neon*) or exported from Pupil Cloud (*Pupil Invisible* and *Pupil Neon*).
  - Using Pupil Player (*Pupil Core* and *Pupil Invisible*) or Neon player (*Pupil Neon*): Each recording should 1) be opened
    in Pupil/Neon Player, and 2) an export of the recording (`e` hotkey) should be run from Pupil/Neon Player. Make sure to disable the
    `World Video Exporter` in the `Plugin Manager` before exporting, as the exported video is not used by glassesTools and takes a long time to create. Note that importing a Pupil/Neon Player export of a Pupil Invisibl/Neone recording may require an internet connection. This is used to retrieve the scene camera calibration from Pupil Lab's servers in case the recording does not have
    a `calibration.bin` file.
  - Using Pupil Cloud (*Pupil Invisible* and *Pupil Neon*): Export the recordings using the `Timeseries data + Scene video` action.
  - For the *Pupil Core*, for best results you may wish to do a scene camera calibration yourself, see https://docs.pupil-labs.com/core/software/pupil-capture/#camera-intrinsics-estimation.
    If you do not do so, a generic calibration will be used by Pupil Capture during data recording, by Pupil Player during data
    analysis and by the glassesTools processing functions, which may result in incorrect accuracy values.
- *SMI ETG*: For SMI ETG recordings, access to BeGaze is required and the following steps should be performed:
  - Export gaze data: `Export` -> `Legacy: Export Raw Data to File`.
    - In the `General` tab, make sure you select the following:
      - `Channel`: enable both eyes
      - `Points of Regard (POR)`: enable `Gaze position`, `Eye position`, `Gaze vector`
      - `Binocular`: enable `Gaze position`
      - `Misc Data`: enable `Frame counter`
      - disable everything else
    - In the Details tab, set:
      - `Decimal places` to 4
      - `Decimal separator` to `point`
      - `Separator` to `Tab`
      - enable `Single file output`
    - This will create a text file with a name like `<experiment name>_<participant name>_<number> Samples.txt`
      (e.g. `005-[5b82a133-6901-4e46-90bc-2a5e6f6c6ea9]_005_001 Samples.txt`). Move this file/these files to the
      recordings folder and rename them. If, for instance, the folder contains the files `005-2-recording.avi`,
      `005-2-recording.idf` and `005-2-recording.wav`, amongst others, for the recording you want to process,
      rename the exported samples text file to `005-2-recording.txt`.
  - Export the scene video:
    - On the Dashboard, double click the scene video of the recording you want to export to open it in the scanpath tool.
    - Right click on the video and select settings. Make the following settings in the `Cursor` tab:
      - set `Gaze cursor` to `translucent dot`
      - set `Line width` to 1
      - set `Size` to 1
    - Then export the video, `Export` -> `Export Scan Path Video`. In the options dialogue, make the following settings:
      - set `Video Size` to the maximum (e.g. `(1280,960)` in my case)
      - set `Frames per second` to the framerate of the scene camera (24 in my case)
      - set `Encoder` to `Performance [FFmpeg]`
      - set `Quality` to `High`
      - set `Playback speed` to `100%`
      - disable `Apply watermark`
      - enable `Export stimulus audio`
      - finally, click `Save as`, navigate to the folder containing the recording, and name it in the same format as the
        gaze data export file we created above but replacing `recording` with `export`, e.g. `005-2-export.avi`.

## gazeMapper sessions

## gazeMapper planes

### Validation

## Coding analysis, synchronization and validation episodes

### Automatic coding of analysis and synchronization episodes.

## Synchronization

### Synchronizing eye tracker data and scene camera

### Synchronizing multiple eye tracker or external camera recordings

# The GUI

# Configuration

## Overriding a project's settings for a specific session or recording


# API
All of gazeMapper's functionality is exposed through its API. Below are all functions that are part of the
public API. Many functions share common input arguments. These are documented [here](#common-input-arguments) and linked to in the API
overview below.
gazeMapper makes extensive use of the functionality of [glassesTools](https://github.com/dcnieho/glassesTools) and its functionality for validating the calibration of a recording is a thin wrapper around [glassesValidator](https://github.com/dcnieho/glassesValidator). See the [glassesTools](https://github.com/dcnieho/glassesTools/blob/master/README.md) and [glassesValidator](https://github.com/dcnieho/glassesValidator/blob/master/README.md) documentation for more information about these functions.


### Common input arguments
|argument|description|
| --- | --- |
|`config_dir`|Path to directory containing a gazeMapper configuration setup. If `None`, gazeMapper attempts to find a configuration folder named `config` at the same level as the gazeMapper session folder(s).|
|`source_dir`|Path to directory containing one (or for some eye trackers potentially multiple) eye tracker recording(s), or an external camera recording.|
|`output_dir`|Path to the directory to which recordings will be imported. Each recording will be placed in a subdirectory of the specified path.|
|`working_dir`|Path to a gazeMapper session or recording directory.|
|`rec_info`|Recording info ([`glassesTools.recording.Recording`](https://github.com/dcnieho/glassesTools/blob/master/README.md#recording-info) or `glassesTools.camera_recording.Recording`) or list of recording info specifying what is expected to be found in the specified `source_dir`, so that this does not have to be rediscovered and changes can be made e.g. to the recording name that is used for auto-generating the recording's `working_dir`, or even directly specifying the `working_dir` by filling the `working_directory` field before import.|
