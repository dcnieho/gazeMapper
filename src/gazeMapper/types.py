import typing
import cv2

ProblemDict = dict[str,typing.Union[None,str,'ProblemDict']]

NestedDict = dict[str,typing.Union[None,'NestedDict']]



aruco_dicts_to_str = {getattr(cv2.aruco,k):k for k in ['DICT_4X4_50', 'DICT_4X4_100', 'DICT_4X4_250', 'DICT_4X4_1000', 'DICT_5X5_50', 'DICT_5X5_100', 'DICT_5X5_250', 'DICT_5X5_1000', 'DICT_6X6_50', 'DICT_6X6_100', 'DICT_6X6_250', 'DICT_6X6_1000', 'DICT_7X7_50', 'DICT_7X7_100', 'DICT_7X7_250', 'DICT_7X7_1000', 'DICT_ARUCO_ORIGINAL', 'DICT_APRILTAG_16H5', 'DICT_APRILTAG_25H9', 'DICT_APRILTAG_36H10', 'DICT_APRILTAG_36H11', 'DICT_ARUCO_MIP_36H12']}
ArucoDictType = typing.Literal[tuple(aruco_dicts_to_str.keys())]