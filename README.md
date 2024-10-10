[![Downloads](https://static.pepy.tech/badge/gazeMapper)](https://pepy.tech/project/gazeMapper)
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
Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. and Hooge, I.T.C. (in prep). gazeMapper: A tool for automated world-based analysis of wearable eye tracker data ([BibTeX](#bibtex))<br>
If you use the functionality for automatic determining the data quality (accuracy and precision) of wearable eye tracker recordings, please additionally cite:<br>
[Niehorster, D.C., Hessels, R.S., Benjamins, J.S., Nyström, M. and Hooge, I.T.C. (2023). GlassesValidator:
A data quality tool for eye tracking glasses. Behavior Research Methods. doi: 10.3758/s13428-023-02105-5](https://doi.org/10.3758/s13428-023-02105-5) ([BibTeX](#bibtex))

## Example
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
    gazeMapper.GUI.run()
    ```
3. If you run the gazeMapper's GUI from a script, make sure to wrap your script in `if __name__=="__main__"`. This is required for correct operation from a script because the GUI uses multiprocessing functionality. Do as follows:
    ```python
    if __name__=="__main__":
        import gazeMapper.GUI
        gazeMapper.GUI.run()
    ```

# Usage
To use gazeMapper, first a recording session needs to be defined for the project. This entails telling gazeMapper which eye tracker and external camera recording(s) to expect, and what planes to map gaze to. The very simplest setup consists of a single eye tracker recording per session and a single plane to map gaze to, but multiple planes and any number of eye tracker and external camera recordings per session is supported. Sessions consisting of only a single external camera recording are not supported, at least one eye tracker recording should be included. Once this setup has been performed, new sessions can be created and recordings imported to the defined eye tracker and external camera recording slots of these new sessions. Once imported, further processing can commence.

The gazeMapper package includes a graphical user interface (GUI) that can be used to perform all configuration and processing. Below we describe an example workflow using the GUI for a simple recording involving two eye trackers and two planes. Not all of gazeMapper's functionality will be used, full a full description of all configuration options, the reader is referred to the [configuration](#configuration) section of this readme.

Besides using the GUI, advanced users can instead opt to call all the GUI's functionality directly from their own Python scripts without making use of the graphical user interface. The interested reader is referred to the [API](#api) section below for further details regarding how to use the gazeMapper functionality directly from their own scripts.

## Workflow and example data
Here we first present an example workflow using the GUI. More detailed information about [using the GUI](#the-gui), or [gazeMapper configuration](#configuration) and [its programming API](#api), are provided below. Example data with which this workflow can be followed is forthcoming.

**TODO** perhaps two or three examples. 1st using glassesValidator example data and just running validator. 2nd single person and overview camera. 3rd teacher student

## gazeMapper projects
The gazeMapper GUI organizes recordings into a project folder. Each session to be processed is represented by a folder in this project folder, and one or multiple recordings are stored in subfolders of a session folder. After importing recordings, all further processing is done inside these session and recording folders. The source directories containing the original recordings remain
untouched when running gazeMapper. A gazeMapper project folder furthermore contains a folder `config` specifying the configuration
of the project. So an example directory structure may look like:
```
my project/
├── config/
│   ├── plane 1/
│   ├── plane 2/
│   └── validation plane/
├── session 01/
│   ├── teacher/
│   ├── student/
│   └── overview camera/
├── session 02/
│   ├── teacher/
│   ├── student/
│   └── overview camera/
...
```
where `session 01` and `session 02` are individual recording session, each made up of a teacher, a student and an overview camera recording. `plane 1` and `plane 2` contain definitions of planes that gazeMapper will map gaze to and `validation plane` is an additional plane used for validation of the eye tracker's calibration using glassesValidator, see [below](#gazemapper-planes) for documentation.

When not using the GUI and running gazeMapper using your own scripts, such a project folder organization is not required. Working folders
for a session can be placed anywhere (though recording folders should be placed inside a session folder), and a folder for a custom configuration can also be placed anywhere (but its location needs to be provided using the `config_dir` argument of all the functions in [`gazeMapper.process`](#gazemapperprocess)). The [`gazeMapper.process`](#gazemapperprocess) functions simply take the path to a session or recording folder.

## Output
During the importing and processing of a eye tracker or camera recording, a series of files are created in the working folder of the recording. These are the following (not all of the files are created for a camera recording, for instance, there is no gaze data associated with such a recording):
|file|location|produced<br>by|description|
| --- | --- | --- | --- |
|`calibration.xml`|recording|[`Session.import_recording`](#gazemappersession)|Camera calibration parameters for the scene camera.|
|`frameTimestamps.tsv`|recording|[`Session.import_recording`](#gazemappersession)|Timestamps for each frame in the scene camera video.|
|`gazeData.tsv`|recording|[`Session.import_recording`](#gazemappersession)|Gaze data cast into the [glassesTools common format](https://github.com/dcnieho/glassesTools/blob/master/README.md#common-data-format) used by gazeMapper.|
|`recording_info.json`|recording|[`Session.import_recording`](#gazemappersession)|Information about the recording.|
|`recording.gazeMapper`|recording|[`Session.import_recording`](#gazemappersession)|JSON file encoding the state of each [recording-level gazeMapper action](#actions).|
|`worldCamera.mp4`|recording|[`Session.import_recording`](#gazemappersession)|Copy of the scene camera video (optional, depends on the `import_do_copy_video` option).|
|||||
|`coding.tsv`|recording|[`process.code_episodes`](#coding-analysis-synchronization-and-validation-episodes)|File denoting the analysis, synchronization and validation episodes to be processed. This is produced with the coding interface included with gazeMapper. Can be manually created or edited to override the coded episodes.|
|`planePose_<plane name>.tsv`|recording|[`process.detect_markers`](#gazemapper-planes)|File with information about plane pose w.r.t. the scene camera for each frame where the plane was detected.|
|`markerPose_<marker ID>.tsv`|recording|[`process.detect_markers`](#gazemapper-planes)|File with information about marker pose w.r.t. the scene camera for each frame where the marker was detected.|
|`planeGaze_<plane name>.tsv`|recording|`process.gaze_to_plane`|File with gaze data projected to the plane/surface.|
|`validate_<plane name>_*`|recording|`process.run_validation`|Series of files with output of the glassesValidator validation procedure. See the [glassesValidator readme](https://github.com/dcnieho/glassesValidator/blob/master/README.md#output) for descriptions.|
|`VOR_sync.tsv`|recording|`process.sync_et_to_cam`|File containing the synchronization offset (s) between eye tracker data and the scene camera.|
|||||
|`session.gazeMapper`|session|[`Session.import_recording`](#gazemappersession)|JSON file encoding the state of each [session-level gazeMapper action](#actions).|
|`ref_sync.tsv`|session|`process.sync_to_ref`|File containing the synchronization offset (s) and other information about sync between multiple recordings.|
|`planeGaze_<recording name>.tsv`|session|`process.export_trials`|File containing the gaze position on one or multiple planes, per recording.|

### Coordinate system of data
gazeMapper produces data in the reference frame of a plane/surface. This 2D data is stored in the `planeGaze_*` files produced when exporting the gazeMapper results, and also in the `planeGaze_*` files stored inside individual recordings' working folders.
The gaze data in these files has its origin (0,0) at a position that is specified in the plane setup (or in the case of a glassesValidator poster at the center of the fixation target that was indicated to be the center target with the `centerTarget` setting in the validation poster's `validationSetup.txt` configuration file). The positive x-axis points to the right and the positive y-axis
downward, which means that (-,-) coordinates are to the left and above of the plane origin, and (+,+) to the right and below.


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
    `World Video Exporter` in the `Plugin Manager` before exporting, as the exported video is not used by glassesTools and takes a long time to create. Note that importing a Pupil/Neon Player export of a Pupil Invisible/Neon recording may require an internet connection. This is used to retrieve the scene camera calibration from Pupil Lab's servers in case the recording does not have
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
### Defining gazeMapper sessions
A gazeMapper session represents a single recording session. It may consist of only a single eye tracker recording, but could also contain multiple (simultaneous) eye tracker and external camera recordings. When setting up a gazeMapper project, one first defines what recordings to expect for a recording session. This is done in the `Session definition` pane in the GUI or by means of a `gazeMapper.session.SessionDefinition` object. A session definition contains a list of expected recordings, which are defined using the `+ new recording` button on the `Session definition` pane in the GUI, or by means of `gazeMapper.session.RecordingDefinition` objects passed to `gazeMapper.session.SessionDefinition.add_recording_def()`. Each recording definition has a name and an eye tracker type (`gazeMapper.session.RecordingType`), where the type can be an eye tracker recording (`gazeMapper.session.RecordingType.Eye_Tracker`) or a (external) camera recording (`gazeMapper.session.RecordingType.Camera`). This session definition is typically stored in a JSON file `session_def.json` in the configuration directory of a gazeMapper project.

### Storage for gazeMapper sessions
As outlined [above](#gazemapper-projects), each gazeMapper session is its own folder inside a gazeMapper project. The name of the session is simply the name of the folder (which we will term the session working folder). You can thus rename a session by renaming its working folder. Similarly, each recording's working folder is stored inside the session working folder, with as folder name the name defined for the corresponding recording in the session definition. You are advised not to manually rename these folders, as folders with a name different than that defined in the session definition are not recognized by gazeMapper.

### Loading gazeMapper sessions
When opening a gazeMapper project folder, each subfolder of the project folder containing a `session.gazeMapper` status file is taken to be a session, regardless of whether it has recording working folders or not. Similarly, recording working folders in a session working folder with names that match the recordings defined in the project's session definition will be loaded automatically.

## gazeMapper planes
The main goal of gazeMapper is to map head-referenced gaze data recording with a wearable eye tracker to one or multiple planes in the world. That means that gazeMapper determines where on the plane a participant looks, regardless of where in their visual field the plane appears, and that gazeMapper's output expresses gaze in the plane's reference frame.
To be able to perform this mapping, gazeMapper needs to be able to determine where the participant is located in space and how they are oriented with respect to the plane (that is, their pose). This is done by placing an array of fiducial markers of known size and known spatial layout on the plane that can be detected through computer vision and used to determine the participant's pose. See the [example](#example) above for what such an array of fiducial markers looks like.

For gazeMapper to be able to do its job, it needs to have precise information about the array of fiducial markers that defines the plane(s). When designing these arrays, it is important to use unique markers (in other words, each marker may only be used once across all planes and other markers that appear in the recording, e.g. for [synchronization](#synchronization) or [automatic trial coding](#automatic-coding-of-analysis-and-synchronization-episodes)). Any dictionary of fiducial markers understood by OpenCV's ArUco module (cv2.aruco, see [`cv::aruco::PREDEFINED_DICTIONARY_NAME`](https://docs.opencv.org/4.10.0/de/d67/group__objdetect__aruco.html#ga4e13135a118f497c6172311d601ce00d)) is supported (i.e. various ArUco marker dictionaries, as well as April tags), the default is `DICT_4X4_250`.

Planes are configured in the `Plane editor` pane in the GUI or by means of `gazeMapper.plane.Definition` objects. There are two types of planes, either a generic 2D plane (`gazeMapper.plane.Type.Plane_2D`), or a glassesValidator plane (`gazeMapper.plane.Type.GlassesValidator`). The configuration of a plane is stored in a subfolder of the project's configuration folder. The name of the plane is given by the name of this folder. For generic 2D planes, two configuration files are needed: a file providing information about which marker is positioned where and how each marker is oriented; and a settings file containing further information about both the markers and the plane. glassesValidator planes have their own settings and are [discussed below](#validation-glassesvalidator-planes). Here we describe the setup for generic 2D planes. It should be noted that a png render of the defined plane is stored in the plane's configuration folder when running any gazeMapper processing action, or by pressing the `TODO` button in the GUI (API: `TODO`). This can be used to check whether your plane definition is correct.

A generic 2D fiducial marker plane is defined by a file with four columns that describes the marker layout on the plane:
| Column | Description |
| --- | --- |
| `ID` |The marker ID. Must match a valid marker ID for the marker dictionary employed, and must be unique throughout the project.|
| `x` |The horizontal location of the marker's center on the plane (mm).|
| `y` |The vertical location of the marker's center on the plane (mm).|
| `rotation_angle` |The rotation of the marker, if any (degree).|

A file with this information should be stored under any name (e.g., `markerPositions.csv`) in the plane's configuration folder inside the project's configuration folder. See the example data for an example of such a file (TODO).

To be able turn the information of the above file into a plane, further settings are needed:
| Setting | Description |
| --- | --- |
|`marker_file`|Name of the file specifying the marker layout on the plane (e.g., `markerPositions.csv`).|
|`marker_size`|Length of the edge of a marker (mm, excluding the white edge, only the black part).|
|`marker_border_bits`|Width of the [black border](https://docs.opencv.org/4.10.0/d5/dae/tutorial_aruco_detection.html) around each marker.|
|`plane_size`|Total size of the plane (mm). Can be larger than the area spanned by fiducial markers.|
|`origin`|The position of the origin of the plane (mm).|
|`unit`|Unit in which sizes and coordinates are expressed. Purely for informational purposes, not used in the software. Should be mm.|
|`aruco_dict`|The ArUco dictionary (see [`cv::aruco::PREDEFINED_DICTIONARY_NAME`](https://docs.opencv.org/4.10.0/de/d67/group__objdetect__aruco.html#ga4e13135a118f497c6172311d601ce00d)) of the markers.|
|`ref_image_size`|The size in pixels of the image that is generated of the plane with fiducial markers.|

These settings are typically stored in a file `plane_def.json` in the plane's configuration folder inside the project's configuration folder.

### Validation (glassesValidator planes)
gazeMapper has built-in support for computing data quality from the gaze data of a participant looking at a validation poster using glassesValidator. To use this functionality, a plane of type GlassesValidator (`gazeMapper.plane.Type.GlassesValidator`) needs to be defined in the project's setup. By default, the default glassesValidator plane is used for a GlassesValidator plane. When the default checkbox is unchecked in the GUI (the `is_default` setting in `plane_def.json` is False), a custom configuration can be used. When unchecking this checkbox in the GUI, files containing the plane setup are deployed to the plane configuration folder, so that the user can edit or replace them. API users are requested to call `glassesValidator.config.deploy_validation_config()` to deploy the glassesValidator configuration files to the plane's configuration folder. The customization options for a glassesValidator plane are [documented here](https://github.com/dcnieho/glassesValidator/blob/master/README.md#customizing-the-poster).

### Individual Markers
Besides planes, gazeMapper can also be configured to detect and report on the appearance of individual markers. This is configured in the `Individual markers editor` pane in the GUI or by means of `gazeMapper.marker.Marker` objects.

## Actions
gazeMapper can perform the following processing actions on a wearable eye tracking data. Some, like detecting the fiducial markers and projecting gaze data to the plane(s), are always available, some other actions are only available when certain settings are enabled. Unavailable actions are not shown in the GUI. Some actions depend on the output of other actions. Such actions whose preconditions have not been met cannot be started from the GUI. Some actions are performed on a gazeMapper session (e.g., `SYNC_TO_REFERENCE` and `MAKE_VIDEO`) whereas others are run on one recording at a time (e.g., `CODE_EPISODES` and `DETECT_MARKERS`). The former will be referred to as session-level actions, the latter as recording-level actions. API use is not gated by such checks, but errors may be raised due to, for instance, missing input files. All available actions (`gazeMapper.process.Action`) are listed in the table below, more details about some of these processing actions are provided in the section below.

| Action | Availability | Level | Description |
| --- | --- | --- | --- |
|`IMPORT`|always|recording|Import a recording from a source directory to a recording working directory, which includes casting the eye tracker-specific data format into the [glassesTools common format](https://github.com/dcnieho/glassesTools/blob/master/README.md#common-data-format) used by gazeMapper.|
|`CODE_EPISODES`|always|recording|[Code analysis, synchronization and validation episodes](#coding-analysis-synchronization-and-validation-episodes) in a recording. Shows a coding GUI.|
|`DETECT_MARKERS`|always|recording|Detect fiducial markers and determine participants pose for one or multiple [planes](#gazemapper-planes) and [individual markers](#individual-markers).|
|`GAZE_TO_PLANE`|always|recording|Mapping head-referenced gaze to one or multiple [planes](#gazemapper-planes).|
|`AUTO_CODE_SYNC`|`auto_code_sync_points` option|recording|[Automatically find sync points](#automatic-coding-of-analysis-and-synchronization-episodes) in the scene/external camera videos. Only makes sense to perform if there are multiple recordings in a session, since otherwise there is nothing to synchronize.|
|`AUTO_CODE_TRIALS`|`auto_code_trial_episodes` option|recording|[Automatically find trial start and ends](#automatic-coding-of-analysis-and-synchronization-episodes) using fiducial markers in the scene camera.|
|`SYNC_ET_TO_CAM`|`get_cam_movement_for_et_sync_method` option|recording|[Synchronize gaze data to the scene camera](#synchronizing-eye-tracker-data-and-scene-camera). Shows a GUI for manually performing this synchronization.|
|`SYNC_TO_REFERENCE`|`sync_ref_recording` option|session|[Synchronize the gaze data and cameras of multiple recordings](#synchronizing-multiple-eye-tracker-or-external-camera-recordings). Only makes sense to perform if there are multiple recordings in a session, since otherwise there is nothing to synchronize.|
|`RUN_VALIDATION`|[plane setup](#gazemapper-planes) and [episode coding setup](#coding-analysis-synchronization-and-validation-episodes)|recording|Run [glassesValidator](#validation-glassesvalidator-planes) to compute data quality from the gaze data of a participant looking at a validation poster.|
|`EXPORT_TRIALS`|always|session|Create file for each recording containing the gaze position on one or multiple planes.|
|`MAKE_VIDEO`|`video_make_which` option|session|Make videos of the eye tracker scene camera or external camera (synchronized if there are multiple) showing gaze on the scene video from the eye tracker, gaze projected to the detected planes, detected plane origins, detected individual markers, and gaze from other eye tracker recordings (if available).|

In the GUI, an overview of what processing actions are enqueued or running for a session or recording are shown in the `Sessions` pane, in the detail view for a specific session, and in the `Processing queue` pane.

## Coding analysis, synchronization and validation episodes
To process a recording, gazeMapper needs to be told where in the recording several events are, such as the trial(s) for which you want to have gaze data mapped to the plane. There are four types of episodes (`glassesTools.annotation.Event`) that can be coded, which are available depends on the set of events that are set to be coded for the project with the `episodes_to_code` setting. Some may not be of use, depending on the configuration of the gazeMapper project:
| Episode | Availability | Description |
| --- | --- | --- |
|`Trial`|always|Denotes an episode for which to map gaze to plane(s). This determines for which segments there will be gaze data when running the [`gazeMapper.process.Action.EXPORT_TRIALS` action](#actions).|
|`Validate`|[plane setup](#gazemapper-planes)|Denotes an episode during which a participant looked at a validation poster, to be used to run [glassesValidator](#validation-glassesvalidator-planes) to compute data quality of the gaze data.|
|`Sync_Camera`|`sync_ref_recording` option|Time point (frame from video) when a synchronization event happened, used for synchronizing different recordings.|
|`Sync_ET_Data`|`get_cam_movement_for_et_sync_method` option|Episode to be used for [synchronization of eye tracker data to scene camera](#synchronizing-eye-tracker-data-and-scene-camera) (e.g. using VOR).|

Furthermore, the `Trial`, `Validate` and `Sync_Camera` episodes need to be associated with one or multiple planes to be detected during these episodes. Which planes to detect per episode is set up with the `planes_per_episode` setting.

Note that when you have multiple recordings, `glassesTools.annotation.Event.Trial` events can only be coded for the reference recording (set by the `sync_ref_recording` setting). The trial coding in the reference recording is automatically propagated to the other recordings once they are [synchronized](#synchronizing-multiple-eye-tracker-or-external-camera-recordings).

The coding is stored in the [`coding.tsv` file](#output) in a recording directory.

### Automatic coding of analysis and synchronization episodes
gazeMapper supports automatically coding synchronization points (`glassesTools.annotation.Event.Sync_Camera`) and trial intervals (`glassesTools.annotation.Event.Trial`) using fiducial markers in the scene video. Specifically, individual markers can be configured to denote such sync points and individual markers or sequences of individual markers can denote the start of a trial or the end of a trial. To do so, first the [individual markers](#individual-markers) to be used for this purpose need to be configured to be detected by the [`gazeMapper.process.Action.DETECT_MARKERS` action](#actions). Then, on the `Project settings` pane, automatic coding of synchronization points can be configured using the `auto_code_sync_points` option, and automatic coding of trial starts and ends using the `auto_code_trial_episodes` option.

Note that when the `gazeMapper.process.Action.AUTO_CODE_SYNC` or `gazeMapper.process.Action.AUTO_CODE_TRIALS` actions are run from the GUI, these reset the `gazeMapper.process.Action.CODE_EPISODES` to not run status. This is done to require that the output of the automatic coding processes is manually checked in the coding interface before further processing is done. Make sure to zoom in on the timeline (using the mouse scroll wheel when hovering the cursor over the timeline) to check that there are not multiple (spurious) events close together.

#### Automatic coding of synchronization timepoints
For automatic coding of synchronization points, the configuration of the `auto_code_sync_points` option works as follows. First, any marker whose appearance should be taken as a sync point should be listed by its ID in `auto_code_sync_points.markers`. Sequences of frames where the marker(s) are detected are taken from the output of the `gazeMapper.process.Action.DETECT_MARKERS` action and processed according to two further settings. Firstly, any gaps between sequences of frames where the marker is detected that are shorter than or equal to `auto_code_sync_points.max_gap_duration` frames will be filled (i.e., the two separate sequences will be merged in to one). This reduces the chance that a single marker presentation is detected as multiple events. Then, any sequences of frames where the marker is detected that are shorter than `auto_code_sync_points.min_duration` frames will be removed. This reduces the chance that a spurious marker detection is picked up as a sync point. For the purposes of this processing, if more than one marker is specified, detections of each of these markers are processed separately.

#### Automatic coding of analysis episodes
For automatic coding of trial starts and ends, the configuration of the `auto_code_trial_episodes` option works as follows. First, the marker whose appearance should be taken as the start of a trial should be listed by its ID in `auto_code_trial_episodes.start_markers` and the marker whose disappearance should be taken as the end of a trial in `auto_code_trial_episodes.end_markers`. Different from the synchronization point coding above, trial starts and ends can be denoted by a sequence of markers, do decrease the change for spurious detections. To make use of this, specify more than one marker in `auto_code_trial_episodes.start_markers` and/or `auto_code_trial_episodes.end_markers` in the order in which they are shown. If this is used, trail starts are coded as the last frame where the last marker in the sequence is detected, and trial ends as the first frame where the first marker of the sequence is detected. Sequences of frames where the marker(s) are detected as delivered by the `gazeMapper.process.Action.DETECT_MARKERS` action are processed as follows. First, detections for individual markers are processed with the same logic for removing spurious detections as for `auto_code_sync_points` above, using the `auto_code_trial_episodes.max_gap_duration` and `auto_code_trial_episodes.min_duration` parameters. If a trial start or end consists of a single marker, that is all that is done. If a trial start/end consists of a sequence of markers, then these sequences are found from the sequence of individual marker detections by looking for detections of the indicated markers in the right order with a gap between them that is no longer than `auto_code_trial_episodes.max_intermarker_gap_duration` frames.

## Synchronization
For eye tracker recordings, the gaze data is not always synchronized correctly to the scene camera. Furthermore, when a gazeMapper session consists of multiple recordings from eye trackers and/or external cameras, these have to be synchronized together. GazeMapper includes methods for solving both these sync problems.

### Synchronizing eye tracker data and scene camera
Synchronization of the eye tracker data to the scene camera is controlled by the `get_cam_movement_for_et_sync_method` setting. If `get_cam_movement_for_et_sync_method` is set to an empty string (default) means that the eye tracker data will not be synchronized to the scene camera. If it is set to another value, the following occurs.
Synchronization of the eye tracker data to the scene camera can be checked and corrected using what we will call the VOR method. In the VOR method, an observer is asked to maintain fixation on a point in the world while slowly shaking their head horizontally (like saying no) and vertically (like saying yes). When performing this task, the eyes are known to counterroll in the head with 0 latency and perfect gain, meaning that gaze is maintained on the point in the world during the head movement. That means that the executed eye and head movement are synchronized perfectly with each other, and this can be exploited to synchronize the eye tracking data and scene camera feeds in a recording. Specifically, the eye movement during the VOR episode is recorded directly by the eye tracker, while the head movement can be extracted from the scene camera video. Aligning these two signals with each other allows checking and removing any temporal offset, thus synchronizing the signals. With gazeMapper, the head movement can be extracted from the scene video using one of two methods, configured with the `get_cam_movement_for_et_sync_method` setting. If `get_cam_movement_for_et_sync_method` is set to `'plane'`, then head movement is represented by the position of the origin of an indicated plane in the scene camera video, as extracted through pose estimation or homography using [gazeMapper planes](#gazemapper-planes). If `get_cam_movement_for_et_sync_method` is set to `'function'`, a user-specified function (configured using the `get_cam_movement_for_et_sync_function` setting) will be called for each frame of the scene video in a `glassesTools.annotation.Event.Sync_ET_Data` episode and is expected to return the location of the target the participant was looking at. For instance, the example function at `gazeMapper.utils.color_blob_localizer` tracks objects of a certain solid color (we have used a solid green disk for this purpose in one experiment).

Once both eye movement and head movement signals have been derived for a `glassesTools.annotation.Event.Sync_ET_Data` episode, these are shown in a GUI where they can checked for any synchronization problem, and be manually aligned to correct for such problems if needed.

### Synchronizing multiple eye tracker or external camera recordings
Recordings in a gazeMapper session can be synchronized by precisely coding when a visual event occurs in the camera feed of each of the recordings. Such an event could for instance be made by flashing a light, using a digital clapperboard, or showing an ArUco marker on a screen that is visible in all cameras. The latter can be used for [automatically finding the synchronization timepoints](#automatic-coding-of-analysis-and-synchronization-episodes). Synchronizing multiple recordings using such synchronization timepoints is done by setting the `sync_ref_recording` setting to the name of the recording to which the other recordings should be synchronized. We will call the recording indicated by the `sync_ref_recording` setting as the reference recording. For instance, if you have two recordings in a session, `et_teacher` and `et_student`, then setting `sync_ref_recording` to `et_teacher` will cause the timestamps of the `et_student` recording to be altered so that they're expressed in the time of the `et_teacher` recording. Using a single synchronization timepoint, offsets in recording start points can be corrected for. By default, when multiple synchronization timepoints have been coded, the average of the offsets between the recordings for all these timepoints is used to synchronize the recordings.

By setting `sync_ref_do_time_stretch` to `True`, multiple time points can however also be used to correct for clock drift. Different video recordings may however undergo clock drift, meaning that time elapses faster or slower in one recording than another. In a particularly bad case, we have seen this amount to several hundred milliseconds for a recording of around half an hour. If clock drift occurs, correcting time for one recording by only a fixed offset is insufficient as that will synchronize the recordings at that specific timepoint in the recording, but significant desynchronization may occur at other timepoints in the recording. When `sync_ref_do_time_stretch` is `True`, clock drift is additionally corrected for by the synchronization procedure by calculating the difference in elapsed time for the two recordings, and stretching the time of either the reference recording (`sync_ref_stretch_which` is set to `'ref'`) or the other recording(s) (`sync_ref_stretch_which` is set to `'other'`). Note that currently only the `'ref'` setting of `sync_ref_stretch_which` is implemented. Finally, there is the setting `sync_ref_average_recordings`. If it is set to a non-empty list, the time stretch factor w.r.t. multiple other recordings (e.g. two identical eye trackers) is used, instead of for individual recordings. This can be useful with `sync_ref_stretch_which='ref'` if the time of the reference recording is deemed unreliable and the other recordings are deemed to be similar. Then the average time stretch factor of these recordings may provide a better estimate of the stretch factor to use than that of individual recordings.

# The GUI
TODO: perhaps put pictures of the GUI where relevant in the other sections of the manual instead.

# Configuration
In this section, a full overview of gazeMapper's settings is given. These settings are stored in a `gazeMapper.config.Study` file, and stored in the `study_def.json` JSON file in a gazeMapper project's configuration directory.

|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
|`Session definition` pane|`session_def`||[gazeMapper session setup](#gazemapper-sessions). Should be a `gazeMapper.session.SessionDefinition` object.|
|`Plane editor` pane|`planes`||[gazeMapper plane setup](#gazemapper-planes). Should be a list of `gazeMapper.plane.Definition` objects.|
|Episodes to code on the `Episode setup` pane|`episodes_to_code`||[gazeMapper coding setup](#coding-analysis-synchronization-and-validation-episodes). Should be a set of `glassesTools.annotation.Event` objects.|
|Planes per episode on the `Episode setup` pane|`planes_per_episode`||[gazeMapper coding setup](#coding-analysis-synchronization-and-validation-episodes), indicating which plane(s) to detect for each episode. Should be a `dict` with `glassesTools.annotation.Event`s as keys, and a set of plane names as value for each key.|
|`Individual marker editor`|`individual_markers`||[gazeMapper individual marker setup](#individual-markers). Should be a list of `gazeMapper.marker.Marker` objects.|
|||||
|Copy video during import?|`import_do_copy_video`|`True`|If `False`, the scene video of an eye tracker recording, or the video of an external camera is not copied to the gazeMapper recording directory during import. Instead, the video will be loaded from the recording's source directory (so do not move it). Ignored when the video must be transcoded to be processed with gazeMapper.|
||`import_source_dir_as_relative_path`|`False`|Specifies whether the path to the source directory stored in the [recording info file](#output) is an absolute path (`False`) or a relative path (`True`). If a relative path is used, the imported recording and the source directory can be moved to another location, and the source directory can still be found as long as the relative path (e.g., one folder up and in the directory `original recordings`: `../original recordings`) doesn't change.|
|||||
||`sync_ref_recording`|`None`|If set to the name of a recording, allows [synchronization](#synchronizing-multiple-eye-tracker-or-external-camera-recordings) of other recordings in a session to the indicated recording.|
||`sync_ref_do_time_stretch`|`None`|If True, multiple sync points are used to calculate a time stretch factor to compensate for clock drift when [synchronizing multiple recordings](#synchronizing-multiple-eye-tracker-or-external-camera-recordings). Should be set if `sync_ref_recording` is set.|
||`sync_ref_stretch_which`|`None`|Which recording(s) should be [corrected for clock drift](#synchronizing-multiple-eye-tracker-or-external-camera-recordings) if `sync_ref_do_time_stretch` is `True`. Possible values are `'ref'` and `'other'`. Should be set if `sync_ref_recording` is set.|
||`sync_ref_average_recordings`|`None`|Whether to average the clock drifts for multiple recordings if `sync_ref_do_time_stretch` is `True`. Should be set if `sync_ref_recording` is set.|
|||||
||`get_cam_movement_for_et_sync_method`|`''`|Method used to derive the head motion for [synchronizing eye tracker data and scene camera](#synchronizing-eye-tracker-data-and-scene-camera). Possible values are `''` (no synchronization), `'plane'` and `'function'`|
||`get_cam_movement_for_et_sync_function`|`None`|Function to use for deriving the head motion when [synchronizing eye tracker data and scene camera](#synchronizing-eye-tracker-data-and-scene-camera) if `get_cam_movement_for_et_sync_method` is set to `'function'`. Should be a [`gazeMapper.config.CamMovementForEtSyncFunction`](#gazemapperconfigcammovementforetsyncfunction) object.|
||`sync_et_to_cam_use_average`|`True`|Whether to use the average offset of multiple sync episodes. If False, the offset for the first sync episode is used, the rest are ignored.|
|||||
||`auto_code_sync_points`|`None`|Setup for [automatic coding of synchronization timepoints](#automatic-coding-of-synchronization-timepoints). Should be a [`gazeMapper.config.AutoCodeSyncPoints`](#gazemapperconfigautocodesyncpoints) object.|
||`auto_code_trial_episodes`|`None`|Setup for [automatic coding of analysis episodes](#automatic-coding-of-analysis-episodes). Should be a [`gazeMapper.config.AutoCodeTrialEpisodes`](#gazemapperconfigautocodetrialepisodes) object.|
|||||
||`export_output3D`|`False`|Determines whether gaze positions on the plane in the scene camera reference frame are exported when invoking the [`gazeMapper.process.Action.EXPORT_TRIALS` action](#actions). See [the glassesTools manual](https://github.com/dcnieho/glassesTools/blob/master/README.md#world-referenced-gaze-data).|
||`export_output2D`|`True`|Determines whether gaze positions on the plane in the plane's reference frame are exported when invoking the [`gazeMapper.process.Action.EXPORT_TRIALS` action](#actions). See [the glassesTools manual](https://github.com/dcnieho/glassesTools/blob/master/README.md#world-referenced-gaze-data).|
||`export_only_code_marker_presence`|`True`|If True, for each marker only a single column is added to the export created by the [`gazeMapper.process.Action.EXPORT_TRIALS` action](#actions), indicating whether the given marker was detected or not on a given frame. If `False`, marker pose information is included in the export.|
|||||
||`validate_do_global_shift`|`True`|glassesValidator setting: if `True`, for each validation interval the mean position will be removed from the gaze data and the targets, removing any overall shift of the data. This improves the matching of fixations to targets when there is a significant overall offset in the data. It may fail (backfire) if there are data samples far outside the range of the validation targets, or if there is no data for some targets.|
||`validate_max_dist_fac`|`.5`|glassesValidator setting: factor for determining distance limit when assigning fixation points to validation targets. If for a given target the closest fixation point is further away than <factor>*[minimum intertarget distance], then no fixation point will be assigned to this target, i.e., it will not be matched to any fixation point. Set to a large value to essentially disable.|
||`validate_dq_types`|`None`|glassesValidator setting: selects the types of data quality you would like to calculate for each of the recordings. When none are selected, a good default is used for each recording. When none of the selected types is available, depending on the `validate_allow_dq_fallback` setting, either an error is thrown or that same default is used instead. Whether a data quality type is available depends on what type of gaze information is available for a recording, as well as whether the camera is calibrated. See the [glassesValidator documentation](https://github.com/dcnieho/glassesValidator/blob/master/README.md#advanced-settings) for more information.|
||`validate_allow_dq_fallback`|`False`|glassesValidator setting: applies if the `validate_dq_types` setting is set. If `False`, an error is raised when the indicated data quality type(s) are not available, if `True`, a sensible default other data type will be used instead.|
||`validate_include_data_loss`|`False`|glassesValidator setting: if `True`, the data quality report will include data loss during the episode selected for each target on the validation poster. This is NOT the data loss of the whole recording and thus not what you want to report in your paper.|
||`validate_I2MC_settings`|`I2MCSettings()`|glassesValidator setting: settings for the [I2MC](https://link.springer.com/article/10.3758/s13428-016-0822-1) fixation classifier used as part of determining the fixation that are assigned to validation targets. Should be a [`gazeMapper.config.I2MCSettings`](#gazemapperconfigi2mcsettings) object.|
|||||
||`video_make_which`|`None`|Indicating one or multiple recordings for which to make videos of the eye tracker scene camera or external camera (synchronized if there are multiple) showing gaze on the scene video from the eye tracker, gaze projected to the detected planes, detected plane origins, detected individual markers, and gaze from other eye tracker recordings (if available, and each depending on the below seeings). Value should be a `set`.|
||`video_recording_colors`|`None`|Color used for drawing each recording's gaze point, scene camera and gaze vector (depending on settings). Each key should be a recording, value in the dict should be a [`gazeMapper.config.RgbColor`](#gazemapperconfigrgbcolor) object.|
||`video_process_planes_for_all_frames`|`False`|If True, shows detection results for all planes for all frames. If False, detection of each plane is only shown during the episode(s) to which it is assigned.|
||`video_process_annotations_for_all_recordings`|`True`|Episode annotations are shown in a bar on the bottom of the screen. If this setting is `True`, annotations for not only the recording for which the video is made, but also for the other recordings are shown in this bar.|
||`video_show_detected_markers`|`True`|If True, known detected markers are indicated in the output video.|
||`video_show_board_axes`|`True`|If Ture, the axes of the detected board |
||`video_process_individual_markers_for_all_frames`|`True`|If `True`, detection results are shown for all frames in the video. If `False`, detection results are only shown during coded episodes of the video.|
||`video_show_individual_marker_axes`|`True`|If True, the pose axis and not only an outline of detected individual markers is shown.|
||`video_show_sync_func_output`|`True`|Applies if the `get_cam_movement_for_et_sync_method` setting is set to `'function'`. If `True`, draw the output of the function on the output video.|
||`video_show_unexpected_markers`|`False`|If `False`, only markers that are part of defined planes or configured individual markers will be drawn on the video. If `True`, also other, unexpected markers will be drawn.|
||`video_show_rejected_markers`|`False`|If `True`, all shapes that potentially are markers but were rejected by OpenCV's ArUco detector are shown. For debug purposes.|
||`video_show_camera_in_ref`|`True`|If `True`, the position of other cameras is marked in the generated video of the reference recording.|
||`video_show_camera_in_other`|`True`|If `True`, the position of other cameras is marked in the generated video of recordings other than the reference recording.|
||`video_show_gaze_vec_in_ref`|`True`|If `True`, a line is drawn for each eye tracker recording between the gaze position and the position of the eye tracker's camera in the generated video of the reference recording.|
||`video_show_gaze_vec_in_other`|`False`|If `True`, a line is drawn for each eye tracker recording between the gaze position and the position of the eye tracker's camera in the generated video of recordings other than the reference recording.|
||`video_gaze_to_plane_margin`|`0.25`|Gaze position more than this factor outside a defined plane will not be drawn.|

## `gazeMapper.config.AutoCodeSyncPoints`
These settings are discussed [here](#automatic-coding-of-synchronization-timepoints).
|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
||`markers`||Set of marker IDs whose appearance indicates a sync points.|
||`max_gap_duration`|`4`|Maximum gap (number of frames) to be filled in sequences of marker detections.|
||`min_duration`|`6`|Minimum length (number of frames) of a sequence of marker detections. Shorter runs are removed.|

## `gazeMapper.config.AutoCodeTrialEpisodes`
These settings are discussed [here](#automatic-coding-of-analysis-episodes).
|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
||`start_markers`||A single marker ID or a sequence (`list`) of marker IDs that indicate the start of a trial.|
||`end_markers`||A single marker ID or a sequence (`list`) of marker IDs that indicate the end of a trial.|
||`max_gap_duration`|`4`|Maximum gap (number of frames) to be filled in sequences of marker detections.|
||`max_intermarker_gap_duration`|`15`|Maximum gap (number of frames) between the detection of two markers in a sequence.|
||`min_duration`|`6`|Minimum length (number of frames) of a sequence of marker detections. Shorter runs are removed.|

## `gazeMapper.config.CamMovementForEtSyncFunction`
These settings are used for when the `get_cam_movement_for_et_sync_method` setting is set to `'function'`, see [here](#synchronizing-eye-tracker-data-and-scene-camera).
|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
||`module_or_file`||Importable module or file (can be a full path) that contains the function to run.|
||`function`||Name of the function to run.|
||`parameters`||`dict` of `kwargs` to pass to the function. The frame to process (`np.ndarray`) is the first (positional) input passed to the function, and should not be specified in this dict.|

## `gazeMapper.config.I2MCSettings`
Settings used when running [I2MC](https://link.springer.com/article/10.3758/s13428-016-0822-1) fixation classifier used as part of determining the fixation that are assigned to validation targets. Used for the [`gazeMapper.process.Action.RUN_VALIDATION`](#actions), see [here](#validation-glassesvalidator-planes).
N.B.: The below fields with `None` as the default value are set by glassesValidator based on the input gaze data. When a value is set for one of these settings, it overrides glassesValidator's dynamic parameter setting.
|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
||`freq`|`None`|Sampling frequency of the eye tracking data.|
||`windowtimeInterp`|`.25`|Maximum duration (s) of gap in the data that is interpolated.|
||`edgeSampInterp`|`2`|Amount of data (number of samples) at edges needed for interpolation.|
||`maxdisp`|`50`|Maximum distance (mm) between the two edges of a gap below which the missing data is interpolated|
||`windowtime`|`.2`|Length of the moving window (s) used by I2MC to calculate 2-means clustering when processing the data.|
||`steptime`|`.02`|Step size (s) by which the moving window is moved.|
||`downsamples`|`None`|Set of integer decimation factors used to downsample the gaze data as part of I2MC processing.|
||`downsampFilter`|`None`|If `True`, a Chebyshev low-pass filter is applied when downsampling.|
||`chebyOrder`|`None`|Order of the Chebyshev low-pass filter.|
||`maxerrors`|`100`|Maximum number of errors before processing of a trial is aborted.|
||`cutoffstd`|`None`|Number of standard deviations above mean k-means weights that will be used as fixation cutoff.|
||`onoffsetThresh`|`3.`|Number of MAD away from median fixation duration. Will be used to walk forward at fixation starts and backward at fixation ends to refine their placement and stop algorithm from eating into saccades.|
||`maxMergeDist`|`20`|Maximum Euclidean distance (mm) between fixations for merging to be possible.|
||`maxMergeTime`|`81`|Maximum time (ms) between fixations for merging to be possible.|
||`minFixDur`|`50`|Minimum fixation duration (ms) after merging, fixations with shorter duration are removed from output.|

## `gazeMapper.config.RgbColor`
|Setting<br>name in GUI|Setting name<br>in settings file|Default<br>value|Description|
| --- | --- | --- | --- |
||`r`|`0`|Value of the red channel (0-255).|
||`g`|`0`|Value of the green channel (0-255).|
||`b`|`0`|Value of the blue channel (0-255).|

## Overriding a project's settings for a specific session or recording
gazeMapper support overriding a subset of the above settings for a specific session or recording. These settings overrides can be set in the GUI on the pane for a specific session, and are stored in JSON files (`study_def_override.json`) in the respective session's or recording's working directory. Programmatically, these settings overrides are handled using `gazeMapper.config.StudyOverride` objects. When using the API, settings can furthermore be overridden by means of keyword arguments to any of the `gazeMapper.process` functions. When overriding subobjects of a `gazeMapper.config.Study` (such as fields in a `dict`), set only the fields you want to override. The other fields will keep their original value.

# API
All of gazeMapper's functionality is exposed through its API. Below are all functions that are part of the
public API. Many functions share common input arguments. These are documented [here](#common-input-arguments) and linked to in the API
overview below.
gazeMapper makes extensive use of the functionality of [glassesTools](https://github.com/dcnieho/glassesTools) and its functionality for validating the calibration of a recording is a thin wrapper around [glassesValidator](https://github.com/dcnieho/glassesValidator). See the [glassesTools](https://github.com/dcnieho/glassesTools/blob/master/README.md) and [glassesValidator](https://github.com/dcnieho/glassesValidator/blob/master/README.md) documentation for more information about these functions.

## `gazeMapper.config`
|function|inputs|description|
guess_config_dir
load_override_and_apply(study: Study, level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None, strict_check=True) -> Study:
load_or_create_override(level: OverrideLevel, override_path: str|pathlib.Path, recording_type: session.RecordingType|None = None) -> StudyOverride:
apply_kwarg_overrides(study: Study, strict_check=True, **kwargs)
read_study_config_with_overrides(config_path: str|pathlib.Path, overrides: dict[OverrideLevel, str|pathlib.Path]=None, recording_type: session.RecordingType|None = None, strict_check=True, **kwargs)

### `gazeMapper.config.Study`
|member function|inputs|description|
__init__: takes all the parameters listed under [configuration](#configuration).
check_valid(self, strict_check
field_problems
store_as_json path: str|pathlib.Path
get_empty (static)
load_from_json(path: str | pathlib.Path, strict_check (static)

### `gazeMapper.config.StudyOverride`
|member function|inputs|description|
__init__(self, level: OverrideLevel, recording_type: session.RecordingType
get_allowed_parameters(level: OverrideLevel, recording_type: session.RecordingType (static)
apply(self, study: Study, strict_check
store_as_json(path: str | pathlib.Path)
load_from_json(level: OverrideLevel, path: str | pathlib.Path, recording_type: session.RecordingType (static)
from_study_diff(config: Study, parent_config: Study, level: OverrideLevel, recording_type: session.RecordingType (static)

## `gazeMapper.episode`
|function|inputs|description|
Episode.__init__(event:annotation.Event, start_frame:int, end_frame:int)
read_list_from_file(fileName: str|pathlib.Path)
write_list_to_file(episodes: list[Episode],fileName: str|pathlib.Path
get_empty_marker_dict(episodes: list[annotation.Event]=None)
list_to_marker_dict(episodes: list[Episode], expected_types: list[annotation.Event]=None)
marker_dict_to_list(episodes: dict[annotation.Event,list[int]|list[list[int]]])
is_in_interval(episodes: dict[annotation.Event,list[int]]|list[Episode], idx: int)

## `gazeMapper.marker`
|function|inputs|description|
Marker.__init__
get_marker_dict_from_list(markers: list[Marker])
load_file(marker: Marker, folder: str|pathlib.Path)
code_marker_for_presence(markers: pd.DataFrame)
fill_gaps_in_marker_detection(markers: pd.DataFrame, fill_value)

## `gazeMapper.plane`
make(path: pathlib.Path, p_type: Type, name: str, **kwargs) -> Definition_GlassesValidator|Definition_Plane_2D:
get_plane_from_path(path: str|pathlib.Path) -> plane.Plane:
get_plane_from_definition(plane_def: Definition, path: str | pathlib.Path) -> plane.Plane:
get_plane_setup(plane_def: Definition)

### `gazeMapper.plane.Type`
Enum, two values: GlassesValidator, Plane_2D
|function|inputs|description|
make(path: pathlib.Path, p_type: Type, name: str, **kwargs)
get_plane_from_path(path: str|pathlib.Path)
get_plane_from_definition(plane_def: Definition, path: str | pathlib.Path)
get_plane_setup(plane_def: Definition)

### `gazeMapper.plane.Definition` and subclasses
`gazeMapper.plane.Definition_GlassesValidator` and `gazeMapper.plane.Definition_Plane_2D`
|member function|inputs|description|
__init__: parameters described above
field_problems(self) -> type_utils.ProblemDict:
fixed_fields(self) -> type_utils.NestedDict:
has_complete_setup(self) -> bool:
store_as_json(self, path: str | pathlib.Path):
load_from_json(path: str | pathlib.Path) (static)

## `gazeMapper.process`
[`gazeMapper.process.Action`](#actions) is described above.

`gazeMapper.process.State` enum:
| Value | Description |
| --- | --- |
|`Not_Run`|Action not run.|
|`Pending`|Action enqueued to be run.|
|`Running`|Action running.|
|`Completed`|Action ran successfully.|
|`Canceled`|Action run cancelled.|
|`Failed`|Action failed.|

NB: the `Pending`, `Canceled` and `Completed` states are only used in the GUI.

action_to_func(action: Action)
is_session_level_action(action: Action) -> bool:
is_action_possible_given_config(action: Action, study_config: 'config.Study')
is_action_possible_for_recording_type(action: Action, rec_type: 'session.RecordingType') -> bool:
get_actions_for_config(study_config: 'config.Study', exclude_session_level: bool=False)
action_update_and_invalidate(action: Action, state: State, study_config: 'config.Study')
get_possible_actions(session_action_states: dict[Action, State], recording_action_states: dict[str,dict[Action, State]], actions_to_check: set[Action], study_config: 'config.Study')

## `gazeMapper.session`
### `gazeMapper.session.RecordingType`
Enumeration
| Value | Description |
| --- | --- |
|`Eye_Tracker`|Recording is an eye tracker recording.|
|`Camera`|Recording is an external camera recording.|

### `gazeMapper.session.RecordingDefinition`
__init__(self, name:str, type:RecordingType):
set_default_cal_file(self, cal_path: str|pathlib.Path, rec_def_path: str|pathlib.Path):
get_default_cal_file(self, rec_def_path: str|pathlib.Path) -> pathlib.Path|None:
remove_default_cal_file(self, rec_def_path: str|pathlib.Path

### `gazeMapper.session.Recording`
__init__(self, definition: RecordingDefinition, info:EyeTrackerRecording|CameraRecording|None=None):
load_action_states(self, create_if_missing: bool)

### `gazeMapper.session.SessionDefinition`
__init__(self, recordings: list[RecordingDefinition]|None=None):
add_recording_def(self, recording: RecordingDefinition):
get_recording_def(self, which: str) -> RecordingDefinition:
is_known_recording(self, which: str) -> bool:
store_as_json(self, path: str | pathlib.Path):
load_from_json(path: str | pathlib.Path) (static)

### `gazeMapper.session.Session`
__init__(self, definition: SessionDefinition, name: str, working_directory: str|pathlib.Path|None = None, recordings: dict[str,Recording]|None = None):
create_working_directory(self, parent_directory: str|pathlib.Path):
import_recording(self, which: str, cam_cal_file: str|pathlib.Path=None, **kwargs):
add_recording_and_import(self, which: str, rec_info: EyeTrackerRecording|CameraRecording, cam_cal_file: str|pathlib.Path=None, load_existing_recordings(self):
load_recording_info(self, which) -> EyeTrackerRecording|CameraRecording:
add_existing_recording(self, which: str) -> Recording:
check_recording_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording):
update_recording_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording):
add_recording_from_info(self, which: str, rec_info: EyeTrackerRecording|CameraRecording) -> Recording:
num_present_recordings(self) -> int:
has_all_recordings(self) -> bool:
missing_recordings(self, rec_type: RecordingType|None=None) -> list[str]:
load_action_states(self, create_if_missing: bool):
is_action_completed(self, action: process.Action) -> bool:
action_completed_num_recordings(self, action: process.Action) -> list[str]:
action_not_completed_recordings(self, action: process.Action) -> list[str]:
from_definition(definition: SessionDefinition|None, path: str | pathlib.Path) (static)

### Free functions
read_recording_info(working_dir: pathlib.Path, rec_type: RecordingType) -> tuple[EyeTrackerRecording|CameraRecording, pathlib.Path]:
get_video_path(rec_info: EyeTrackerRecording|CameraRecording)
get_session_from_directory(path: str|pathlib.Path, session_def: SessionDefinition|None=None) -> Session:
get_sessions_from_project_directory(path: str|pathlib.Path, session_def: SessionDefinition|None=None) -> list[Session]:
get_action_states(working_dir: str|pathlib.Path, for_recording: bool, create_if_missing = False, skip_if_missing=False) -> dict[process.Action, process.State]:
update_action_states(working_dir: str|pathlib.Path, action: process.Action, state: process.State, study_config: 'config.Study', skip_if_missing=False)


### Common input arguments
|argument|description|
| --- | --- |
|`config_dir`|Path to directory containing a gazeMapper configuration setup. If `None`, gazeMapper attempts to find a configuration folder named `config` at the same level as the gazeMapper session folder(s).|
|`source_dir`|Path to directory containing one (or for some eye trackers potentially multiple) eye tracker recording(s), or an external camera recording.|
|`output_dir`|Path to the directory to which recordings will be imported. Each recording will be placed in a subdirectory of the specified path.|
|`working_dir`|Path to a gazeMapper session or recording directory.|
|`rec_info`|Recording info ([`glassesTools.recording.Recording`](https://github.com/dcnieho/glassesTools/blob/master/README.md#recording-info) or `glassesTools.camera_recording.Recording`) or list of recording info specifying what is expected to be found in the specified `source_dir`, so that this does not have to be rediscovered and changes can be made e.g. to the recording name that is used for auto-generating the recording's `working_dir`, or even directly specifying the `working_dir` by filling the `working_directory` field before import.|

# Citation
If you use this tool or any of the code in this repository, please cite:<br>
Niehorster, D.C., Hessels, R.S., Nyström, M., Benjamins, J.S. and Hooge, I.T.C. (in prep). gazeMapper: A tool for automated world-based analysis of wearable eye tracker data<br>
If you use the functionality for automatic determining the data quality (accuracy and precision) of wearable eye tracker recordings, please additionally cite:<br>
[Niehorster, D.C., Hessels, R.S., Benjamins, J.S., Nyström, M. and Hooge, I.T.C. (2023). GlassesValidator:
A data quality tool for eye tracking glasses. Behavior Research Methods. doi: 10.3758/s13428-023-02105-5](https://doi.org/10.3758/s13428-023-02105-5)

## BibTeX
```latex
@article{niehorstergazeMapper,
    Author = {Niehorster, Diederick C. and
              Hessels, R. S. and
              Nystr{\"o}m, Marcus and
              Benjamins, J. S. and
              Hooge, I. T. C.},
    Journal = {},
    Number = {},
    Title = {{gazeMapper}: A tool for automated world-based analysis of wearable eye tracker data},
    Year = {}
}

@article{niehorster2023glassesValidator,
    Author = {Niehorster, Diederick C. and
              Hessels, R. S. and
              Benjamins, J. S. and
              Nystr{\"o}m, Marcus and
              Hooge, I. T. C.},
    Journal = {Behavior Research Methods},
    Number = {},
    Title = {{GlassesValidator}: A data quality tool for eye tracking glasses},
    Year = {2023},
    doi = {10.3758/s13428-023-02105-5}
}
```