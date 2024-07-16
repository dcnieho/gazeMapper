import numpy as np
import cv2


border_mask_cache: dict[tuple[int,int,float], np.ndarray] = {}

def detect_blob_HSV(frame: np.ndarray, low: tuple[int], high: tuple[int], edge_cut_fac=9.):
    # for instance:
    # low = (40, 60, 40)
    # high= (70, 255, 255)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    n_rows = frame.shape[0]
    n_cols = frame.shape[1]
    if (n_cols, n_rows) not in border_mask_cache:
        border_pad_row = int(n_rows/edge_cut_fac)
        border_pad_col = int(n_cols/edge_cut_fac / 2)

        border_mask = np.zeros(frame.shape[:2])
        border_mask[border_pad_row:(n_rows - border_pad_row), border_pad_col : (n_cols - border_pad_col)] = 1

        border_mask_cache[(n_cols, n_rows)] = border_mask
    else:
        border_mask = border_mask_cache[(n_cols, n_rows)]


    mask = cv2.inRange(frame, (*low,), (*high,))

    # Consider targets only in center
    mask = mask * border_mask

    # Select largest blob
    output = cv2.connectedComponentsWithStats(mask.astype('uint8'), 8, cv2.CV_32S)
    labels = output[1]
    blob_sizes = output[2][:, -1]
    try:
        labels = labels == np.argsort(blob_sizes)[-2]
        mass_row, mass_col = np.where(labels)
        col = np.mean(mass_col)
        row = np.mean(mass_row)
    except:
        col = row = np.nan

    return col, row
