"""Conversión OpenCV BGR/gray → QImage (MatToQImage.cpp)."""

from __future__ import annotations

import cv2
import numpy as np
from PyQt6.QtGui import QImage


def mat_to_qimage(mat: np.ndarray) -> QImage:
    """Convierte ndarray uint8 BGR/BGRA/gray o float32 gray normalizado."""
    if mat is None or mat.size == 0:
        return QImage()

    if mat.dtype == np.float32:
        norm = cv2.normalize(mat, None, 0, 255, cv2.NORM_MINMAX)
        norm = norm.astype(np.uint8)
        if norm.ndim == 2:
            h, w = norm.shape
            return QImage(norm.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        bgr = norm
    else:
        bgr = mat

    if bgr.ndim == 2:
        h, w = bgr.shape
        return QImage(bgr.data, w, h, w, QImage.Format.Format_Grayscale8).copy()

    if bgr.shape[2] == 4:
        h, w, _ = bgr.shape
        return QImage(bgr.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()

    h, w, _ = bgr.shape
    return QImage(bgr.data, w, h, w * 3, QImage.Format.Format_BGR888).copy()
