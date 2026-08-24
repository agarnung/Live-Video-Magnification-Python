"""Shared data structures (equivalent to Structures.h)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage


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
    """Active processing mode."""

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


class ViewMode(Enum):
    """
    How the viewer composes the {original, processed} pair it is handed.

    Mirrors the C++ DisplayWidget::ViewMode. ORIGINAL additionally lets the
    processing worker skip magnification entirely, since nobody looks at it.
    """

    PROCESSED = 0
    ORIGINAL = 1
    SIDE_BY_SIDE = 2
    STACKED = 3


@dataclass(frozen=True)
class DisplayFrame:
    """
    Processed frame paired with its matching pre-magnification frame.

    Published as ONE object rather than two signals: if the two travelled
    separately, the side-by-side panes could latch frames from different
    instants (the queues and the event loop give no ordering guarantee), and the
    comparison would silently show a one-frame offset. Coupling them makes the
    lockstep a property of the data, not of delivery timing.

    `original` is the output of the FIRST pipeline stage -- after ROI crop and
    downscale but BEFORE magnification -- so both panes share geometry and can
    be blitted side by side without rescaling.
    """

    processed: QImage
    original: QImage | None = None
