function [rect, rect_margin] = drawMarker(wpnt, markerTex, center, size, margin)
rect = CenterRectOnPoint([0 0 1 1]*size, center(1), center(2));
rect_margin = CenterRectOnPoint([0 0 1 1]*(size+2*margin), center(1), center(2));
Screen('FillRect', wpnt, 1, rect_margin);
Screen('DrawTexture', wpnt, markerTex, [], rect);
            