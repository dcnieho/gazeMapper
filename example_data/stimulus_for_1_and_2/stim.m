sync_marker_size    = 400;
sync_marker_margin  = 40;
sync_marker_dur     = .5;
sync_marker_qshow   = false;
trial_marker_size   = 400;
trial_marker_margin = 40;
trial_marker_dur    = .5;
plane_marker_size   = 150;
plane_marker_margin = 20;
plane_which         = 1;
bgColor             = .5;
inter_trial_interval= 1;

try
    PsychDefaultSetup(2);

    [wpnt, wrect] = PsychImaging('OpenWindow',  max(Screen('Screens')), bgColor);
    HideCursor;
    [sx, sy] = RectSize(wrect);
    [cx, cy] = RectCenter(wrect);

    % load all images, etc
    [poster, pSz]   = uploadImages('poster.png', wpnt);
    [stimuli, sSz]  = uploadImages('images', wpnt);
    sync_marker     = uploadImages('sync_marker', wpnt);
    trial_markers   = uploadImages('trial_markers', wpnt);
    plane_markers   = uploadImages(sprintf('plane_markers_%d',plane_which), wpnt);

    % position plane markers
    assert(length(plane_markers)==8)
    off = plane_marker_size/2+plane_marker_margin;
    plane_marker_pos = [
        off, off
        cx, off
        sx-off, off
        off, cy
        sx-off, cy
        off, sy-off
        cx, sy-off
        sx-off, sy-off
        ];
    stimRect = [off*2 off*2 sx-off*2 sy-off*2];
    stimSz   = [stimRect(3)-stimRect(1) stimRect(4)-stimRect(2)];

    % show sync marker after keypress
    if sync_marker_qshow
        KbWait([], 2);
        drawMarker(wpnt, sync_marker, [cx cy], sync_marker_size, sync_marker_margin);
        t_next = Screen('Flip', wpnt) + sync_marker_dur;
    else
        t_next = 0;
    end

    % clear screen, wait for key press to start showing validation poster
    Screen('Flip', wpnt, t_next);
    KbWait([], 2);
    % show validation poster until keypress
    Screen('DrawTexture', wpnt, poster, [], CenterRectOnPoint([0 0 pSz*min([sx sy]./pSz)], cx, cy));
    Screen('Flip', wpnt);
    KbWait([], 2);

    % clear screen, wait for key press to start trials
    Screen('Flip', wpnt);
    t_next = KbWait([], 2);

    % trial loop: for each stimulus:
    % 1. show trial start marker sequence
    % 2. show stimulus until key press
    % 3. show trial end marker sequence
    % 4. blank screen for inter-trial interval
    for s=1:length(stimuli)
        % show trial start marker sequence
        for t=1:length(trial_markers)
            drawMarker(wpnt, trial_markers(t), [cx cy], trial_marker_size, trial_marker_margin);
            t_next = Screen('Flip', wpnt, t_next) + trial_marker_dur;
        end

        % show stimulus
        % draw ArUco frame
        for p=1:length(plane_markers)
            drawMarker(wpnt, plane_markers(p), plane_marker_pos(p,:), plane_marker_size, plane_marker_margin);
        end
        % draw stimulus
        Screen('DrawTexture', wpnt, stimuli(s), [], CenterRectOnPoint([0 0 sSz(s,:)*min(stimSz./sSz(s,:))], cx, cy));
        Screen('Flip', wpnt, t_next);
        
        t_next = KbWait([], 2);

        % show trial start marker sequence
        for t=length(trial_markers):-1:1
            drawMarker(wpnt, trial_markers(t), [cx cy], trial_marker_size, trial_marker_margin);
            t_next = Screen('Flip', wpnt, t_next) + trial_marker_dur;
        end
        Screen('Flip', wpnt, t_next);

        if s<length(stimuli)
            t_next = Screen('Flip', wpnt, t_next+inter_trial_interval);
        end
    end

    % show sync marker, after keypress
    if sync_marker_qshow
        KbWait([], 2);
        drawMarker(wpnt, sync_marker, [cx cy], sync_marker_size, sync_marker_margin);
        t_next = Screen('Flip', wpnt) + sync_marker_dur;
        Screen('Flip', wpnt, t_next); % clear screen after shown for wanted duration
    end

catch ME
    sca;
    rethrow(ME);
end
sca
