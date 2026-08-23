"""Estructuras de datos compartidas (equivalente a Structures.h)."""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QRect


@dataclass
class ImageProcessingSettings:
    """Magnification and video parameters."""

    amplification: float = 0.0
    co_wavelength: float = 0.0
    co_low: float = 0.1
    co_high: float = 0.4
    chrom_attenuation: float = 0.0
    frame_width: int = 0
    frame_height: int = 0
    framerate: float = 0.0
    levels: int = 4


@dataclass
class ImageProcessingFlags:
    """Modo de procesamiento activo."""

    grayscale_on: bool = False
    color_magnify_on: bool = False
    laplace_magnify_on: bool = False
    riesz_magnify_on: bool = False


@dataclass
class MouseData:
    """Mouse state over the viewer."""

    selection_box: QRect = field(default_factory=QRect)
    left_button_release: bool = False
    right_button_release: bool = False


@dataclass
class ThreadStatisticsData:
    """Statistics reported to the GUI."""

    average_fps: int = 0
    n_frames_processed: float = 0.0
    average_vid_processing_fps: float = 0.0
