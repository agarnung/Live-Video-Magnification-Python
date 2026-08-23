"""Pirámides Gauss/Laplace y utilidades espaciales (SpatialFilter.cpp)."""

from __future__ import annotations

import cv2
import numpy as np


def build_gauss_pyr_from_img(img: np.ndarray, levels: int) -> list[np.ndarray]:
    """Construye pirámide Gaussiana; el último elemento es la más pequeña."""
    pyr: list[np.ndarray] = []
    current = img
    for _ in range(levels):
        down = cv2.pyrDown(current)
        pyr.append(down)
        current = down
    return pyr


def build_laplace_pyr_from_img(img: np.ndarray, levels: int) -> list[np.ndarray]:
    """Pirámide Laplaciana; último nivel es la base (no diferencia)."""
    pyr: list[np.ndarray] = []
    current = img
    for _ in range(levels):
        down = cv2.pyrDown(current)
        up = cv2.pyrUp(down, dstsize=(current.shape[1], current.shape[0]))
        laplace = current.astype(np.float32) - up.astype(np.float32)
        pyr.append(laplace.astype(np.float32))
        current = down
    pyr.append(current.astype(np.float32))
    return pyr


def build_img_from_gauss_pyr(pyr_smallest: np.ndarray, levels: int, size: tuple[int, int]) -> np.ndarray:
    """Reconstruye desde el nivel más pequeño de una pirámide Gaussiana."""
    current = pyr_smallest.astype(np.float32)
    for _ in range(levels):
        current = cv2.pyrUp(current)
    return cv2.resize(current, (size[0], size[1]))


def build_img_from_laplace_pyr(pyr: list[np.ndarray], levels: int) -> np.ndarray:
    """Colapsa pirámide Laplaciana."""
    current = pyr[levels].astype(np.float32)
    for level in range(levels - 1, -1, -1):
        up = cv2.pyrUp(current, dstsize=(pyr[level].shape[1], pyr[level].shape[0]))
        current = up + pyr[level]
    return current
