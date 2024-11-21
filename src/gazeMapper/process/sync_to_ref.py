import pathlib
import pandas as pd
import polars as pl

from glassesTools import annotation, gaze_headref, naming, timestamps


from . import _utils
from .. import config, process, session, synchronization


def run(working_dir: str|pathlib.Path, config_dir: str|pathlib.Path = None, **study_settings):
    working_dir = pathlib.Path(working_dir) # working directory of a session, not of a recording
    if config_dir is None:
        config_dir = config.guess_config_dir(working_dir)
    config_dir  = pathlib.Path(config_dir)
    print(f'processing: {working_dir.name}')

    # get settings for the study
    study_config = config.read_study_config_with_overrides(config_dir, {config.OverrideLevel.Session: working_dir}, **study_settings)

    # check there is a sync setup
    if not study_config.sync_ref_recording:
        raise ValueError('Synchronization to a reference recording is not defined, should not run this function')
    if annotation.Event.Sync_Camera not in study_config.episodes_to_code:
        raise ValueError('Camera sync points are not set up to be coded, nothing to do here')

    # documentation for some settings in the json file:
    # 1. sync_ref_recording. Name of one of the recordings that is part of the session, the one w.r.t. which
    #    the other recordings are synced.
    # 2. If do_time_stretch is True, time is offset, and stretched by a linear scale factor based on
    #    differences in elapsed time for the reference and the other camera. Requires at least two sync
    #    points to determine the stretch factor. If there are more, piecewise linear scaling is done for
    #    timestamps falling between each pair of sync points. If False, one single offset is applied. If
    #    there are multiple sync points, the average is used.
    # 3. stretch_which='ref' means that the time of the reference is judged to be unreliable and will be
    #    stretched. You may wish to do this if you have an overview camera as ref and are syncing one or two
    #    eye trackers to it, so that the timing of your eye tracker events does not change (and the timing of
    #    some webcam recording may indeed be unreliable). If 'other', the other signals get stretched.
    # 4. sync_average_recordings. If a non-empty list, the stretch_fac w.r.t. multiple others (e.g. two
    #    identical eye trackers) is used, instead of for individual recordings. This can be useful with
    #    stretch_which='ref' if the ref is deemed unreliable and the other sources are deemed similar. Then
    #    the average may provide a better estimate of the stretch factor to use.

    # get session info
    session_info = session.Session.from_definition(study_config.session_def, working_dir)

    # get info from reference recording
    ref_vid_ts_file = working_dir / study_config.sync_ref_recording / naming.frame_timestamps_fname
    video_ts_ref = timestamps.VideoTimestamps(ref_vid_ts_file)

    # prep for sync info
    recs = [r for r in session_info.recordings if r!=study_config.sync_ref_recording]
    sync = synchronization.get_sync_for_recs(working_dir, recs, study_config.sync_ref_recording, study_config.sync_ref_do_time_stretch, study_config.sync_ref_average_recordings)

    # store sync info
    sync.to_csv(working_dir / 'ref_sync.tsv', sep='\t', na_rep='nan', float_format="%.16f")

    # now that we have determined how to sync, apply
    for r in recs:
        rec_def = study_config.session_def.get_recording_def(r)
        has_gaze_data = rec_def.type==session.RecordingType.Eye_Tracker

        # just read whole gaze dataframe so we can apply things vectorized
        if has_gaze_data:
            df = pd.read_csv(working_dir / r / naming.gaze_data_fname, delimiter='\t', index_col=False)
            ts_col = 'timestamp_VOR' if 'timestamp_VOR' in df else 'timestamp'
        else:
            # stretch video timestamps instead
            ts_file = working_dir / r / naming.frame_timestamps_fname
            df = pd.read_csv(ts_file, delimiter='\t', index_col='frame_idx')
            ts_col = 'timestamp'
        # get gaze timestamps and camera frame numbers _in reference video timeline_
        ts_ref, ref_vid_ts, fr_ref = synchronization.apply_sync(r, sync, df[ts_col].to_numpy(), video_ts_ref.timestamps,
                                                                study_config.sync_ref_do_time_stretch, study_config.sync_ref_stretch_which)

        # make and store new video time signal
        if study_config.sync_ref_do_time_stretch and study_config.sync_ref_stretch_which=='ref':
            vid_ts_df = pd.read_csv(ref_vid_ts_file, delimiter='\t', index_col='frame_idx')
            should_store = False
            if 'timestamp_stretched' not in vid_ts_df.columns:
                # doesn't exist, insert
                vid_ts_df.insert(1,'timestamp_stretched', ref_vid_ts)
                should_store = True
            elif max(vid_ts_df['timestamp_stretched'].to_numpy()-ref_vid_ts)<10e-5:
                # exists but what we just computed is different, update
                vid_ts_df['timestamp_stretched'] = ref_vid_ts
                should_store = True
            if should_store:
                vid_ts_df.to_csv(ref_vid_ts_file, sep='\t', float_format="%.8f")

        # write into df (use polars as that library saves to file waaay faster)
        if has_gaze_data:
            df = _utils.insert_ts_fridx_in_df(df, gaze_headref.Gaze, 'ref', ts_ref, fr_ref)
            df = pl.from_pandas(df)
            df.write_csv(working_dir / r / naming.gaze_data_fname, separator='\t', null_value='nan', float_precision=8)
        else:
            if 'timestamp_ref' not in df.columns:
                # doesn't exist, insert
                df.insert(1,'timestamp_ref', ts_ref)
            else:
                df['timestamp_ref'] = ts_ref
            df.to_csv(ts_file, sep='\t', float_format="%.8f")

    # update state
    session.update_action_states(working_dir, process.Action.SYNC_TO_REFERENCE, process.State.Completed, study_config)