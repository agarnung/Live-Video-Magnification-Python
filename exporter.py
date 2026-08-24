"""
Offline video export: re-process a file with a fresh chain and encode it.

Port of the C++ ``export/`` package (ExportTypes.hpp, Exporter.cpp,
FileExportFrameSource.cpp).

The central design point is that the exporter never reuses the live preview's
Magnificator. The magnification algorithms are *temporal*: every output frame
depends on the per-pixel history of the frames fed before it. Sharing state with
a preview that skips frames, or that the user is scrubbing, would make the
exported file depend on what happened to be on screen. So the worker owns its
own Magnificator and feeds it strictly in decode order, from the first frame of
the requested range.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from config import DEFAULT_CAPTURE_FPS
from magnificator import Magnificator
from structures import ImageProcessingFlags, ImageProcessingSettings


class SplitMode(Enum):
    """How the output frame is composed."""

    NONE = "none"
    LEFT_RIGHT = "left_right"
    TOP_BOTTOM = "top_bottom"


class ExportFormat(Enum):
    """Output container plus codec. FFV1 is mathematically lossless (large files)."""

    MP4_H264 = "mp4_h264"
    AVI_MJPG = "avi_mjpg"
    MKV_FFV1 = "mkv_ffv1"

    @property
    def extension(self) -> str:
        """Extension without the leading dot."""
        return {
            ExportFormat.MP4_H264: "mp4",
            ExportFormat.AVI_MJPG: "avi",
            ExportFormat.MKV_FFV1: "mkv",
        }[self]

    @property
    def label(self) -> str:
        return {
            ExportFormat.MP4_H264: "MP4 / H.264",
            ExportFormat.AVI_MJPG: "AVI / Motion JPEG",
            ExportFormat.MKV_FFV1: "MKV / FFV1 (lossless)",
        }[self]


class ExportPhase(Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    FINALIZING = "finalizing"
    DONE = "done"
    ABORTED = "aborted"
    ERROR = "error"


#: fourcc cascade per format. Which encoders an OpenCV build actually has is a
#: build-time question, so each format lists its preferences and the caller
#: falls through until a writer opens; the last entry of every list is the
#: universally available Motion JPEG in an .avi container.
_FOURCC_CASCADE: dict[ExportFormat, tuple[tuple[str, str | None], ...]] = {
    # (fourcc, extension override or None to keep the requested one)
    ExportFormat.MP4_H264: (("avc1", None), ("mp4v", None), ("MJPG", "avi")),
    ExportFormat.AVI_MJPG: (("MJPG", None),),
    ExportFormat.MKV_FFV1: (("FFV1", None), ("MJPG", "avi")),
}


@dataclass
class ExportRequest:
    """
    Everything the exporter needs.

    ``capture_fps`` is the rate the *algorithm* assumes (it sets the temporal
    filter cutoffs, so it must match the source's true rate); ``file_fps`` is the
    cadence written into the output container. Keeping them separate is what
    lets a 1000 fps high-speed clip be analysed at 1000 fps but written as a
    watchable 30 fps file.
    """

    output_path: str
    source_path: str
    settings: ImageProcessingSettings = field(default_factory=ImageProcessingSettings)
    flags: ImageProcessingFlags = field(default_factory=ImageProcessingFlags)
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)
    downscale: int = 1
    capture_fps: float = DEFAULT_CAPTURE_FPS
    file_fps: float = 0.0  # 0 -> follow capture_fps
    split: SplitMode = SplitMode.NONE
    text_overlay: bool = False
    fmt: ExportFormat = ExportFormat.MP4_H264
    start_frame: int = 0  # inclusive
    end_frame: int = -1  # exclusive; -1 = to the end


@dataclass
class ExportProgress:
    """Snapshot polled by the GUI on a timer."""

    phase: ExportPhase = ExportPhase.IDLE
    frames_done: int = 0
    frames_total: int = -1  # -1 = unknown
    error: str = ""
    codec_used: str = ""
    output_path: str = ""
    frame_size: tuple[int, int] = (0, 0)  # (width, height) actually written


class FileExportFrameSource:
    """
    Trimmed frame source over a video file.

    The in-point is resolved with ``CAP_PROP_POS_FRAMES``; the out-point is
    counted in software because a seek lands on the nearest keyframe in some
    containers and cannot be trusted to define an exact end.
    """

    def __init__(self, path: str, start_frame: int = 0, end_frame: int = -1) -> None:
        self._path = path
        self._start = max(0, int(start_frame))
        self._end = int(end_frame)
        self._cap: cv2.VideoCapture | None = None
        self._delivered = 0
        self._frame_count = -1
        self._size = (0, 0)

    def open(self) -> bool:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            return False
        self._cap = cap
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        end = self._end
        if total > 0:
            if end < 0 or end > total:
                end = total
            start = min(self._start, total)
            self._frame_count = max(0, end - start)
        else:
            # Unknown total: deliver to natural EOF and report indeterminate
            # progress rather than inventing a denominator.
            self._frame_count = -1
        self._end = end

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if w <= 0 or h <= 0:
            ok, probe = cap.read()
            if not ok:
                return False
            h, w = probe.shape[:2]
        self._size = (w, h)
        if self._start > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(self._start))
        else:
            # A probe read above would otherwise swallow the first frame.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
        self._delivered = 0
        return True

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def next(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        if self._end >= 0 and self._start + self._delivered >= self._end:
            return None  # out-point
        ok, frame = self._cap.read()
        if not ok:
            return None  # natural EOF
        self._delivered += 1
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


def _to_bgr(mat: np.ndarray) -> np.ndarray:
    """A frame's pixels as 3-channel BGR8 (grayscale expanded, floats clipped)."""
    if mat.dtype != np.uint8:
        mat = np.clip(mat, 0, 255).astype(np.uint8)
    if mat.ndim == 2:
        return cv2.cvtColor(mat, cv2.COLOR_GRAY2BGR)
    if mat.shape[2] == 4:
        return cv2.cvtColor(mat, cv2.COLOR_BGRA2BGR)
    return mat


def _draw_label(canvas: np.ndarray, text: str, x: int, y: int, scale: float) -> None:
    """Burn a label onto a darkened plate so it stays legible over any content."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = max(1, int(round(scale)))
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    pad = max(2, int(round(scale * 4)))
    x0, y0 = max(0, x), max(0, y)
    x1 = min(canvas.shape[1], x0 + tw + 2 * pad)
    y1 = min(canvas.shape[0], y0 + th + baseline + 2 * pad)
    if x1 <= x0 or y1 <= y0:
        return
    roi = canvas[y0:y1, x0:x1]
    cv2.addWeighted(roi, 0.35, np.zeros_like(roi), 0.65, 0.0, roi)
    cv2.putText(
        canvas,
        text,
        (x0 + pad, y0 + pad + th),
        font,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def compose(
    original: np.ndarray | None,
    processed: np.ndarray,
    split: SplitMode,
    overlay: bool,
) -> np.ndarray | None:
    """
    Build the output frame.

    Panes are cropped to common EVEN dimensions because H.264 and FFV1 both
    require even width and height (chroma subsampling / plane alignment); an odd
    size makes the writer fail to open or silently produce a corrupt file.
    """
    p = _to_bgr(processed)
    if split is SplitMode.NONE:
        w = p.shape[1] & ~1
        h = p.shape[0] & ~1
        if w <= 0 or h <= 0:
            return None
        return p[:h, :w].copy()

    o = _to_bgr(original) if original is not None and original.size else p
    w = min(o.shape[1], p.shape[1]) & ~1
    h = min(o.shape[0], p.shape[0]) & ~1
    if w <= 0 or h <= 0:
        return None
    oc = o[:h, :w]
    pc = p[:h, :w]
    scale = min(1.5, max(0.4, w / 800.0))

    if split is SplitMode.LEFT_RIGHT:
        canvas = np.empty((h, 2 * w, 3), dtype=np.uint8)
        canvas[:, :w] = oc
        canvas[:, w:] = pc
        if overlay:
            _draw_label(canvas, "Original", 6, 6, scale)
            _draw_label(canvas, "Processed", w + 6, 6, scale)
    else:
        canvas = np.empty((2 * h, w, 3), dtype=np.uint8)
        canvas[:h] = oc
        canvas[h:] = pc
        if overlay:
            _draw_label(canvas, "Original", 6, 6, scale)
            _draw_label(canvas, "Processed", 6, h + 6, scale)
    return canvas


def _open_writer(
    fmt: ExportFormat, path: str, fps: float, size: tuple[int, int]
) -> tuple[cv2.VideoWriter | None, str, str]:
    """
    Try the format's fourcc cascade.

    Returns ``(writer, codec_name, actual_path)``; the path may differ from the
    request when a fallback had to change container. cv2.VideoWriter reports
    failure through ``isOpened()`` rather than an exception, and it also happily
    "opens" some combinations it cannot encode -- hence the isOpened() check on
    every attempt.
    """
    for fourcc_str, ext_override in _FOURCC_CASCADE[fmt]:
        target = path
        if ext_override is not None:
            target = str(Path(path).with_suffix("." + ext_override))
        writer = cv2.VideoWriter(
            target, cv2.VideoWriter_fourcc(*fourcc_str), fps, size, True
        )
        if writer.isOpened():
            name = fourcc_str
            if ext_override is not None:
                name = f"{fourcc_str} (fallback .{ext_override})"
            return writer, name, target
        writer.release()
    return None, "", path


class Exporter:
    """
    Runs one export on its own thread.

    Deliberately a plain ``threading.Thread`` rather than a QThread: nothing here
    touches Qt, and the GUI polls :meth:`progress` on a timer instead of
    receiving signals. That keeps the encoder independent of the Qt event loop,
    so a busy GUI cannot stall the write.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._progress = ExportProgress()

    # -------------------------------------------------------------- lifecycle

    def start(self, request: ExportRequest) -> None:
        self.join()
        self._abort.clear()
        with self._lock:
            self._progress = ExportProgress(
                phase=ExportPhase.PROCESSING, output_path=request.output_path
            )
        self._thread = threading.Thread(
            target=self._run, args=(request,), name="exporter", daemon=True
        )
        self._thread.start()

    def abort(self) -> None:
        """Idempotent: setting the flag twice is harmless and never blocks."""
        self._abort.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def progress(self) -> ExportProgress:
        with self._lock:
            return ExportProgress(**vars(self._progress))

    # ----------------------------------------------------------------- worker

    def _set(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._progress, k, v)

    def _fail(self, message: str) -> None:
        self._set(error=message, phase=ExportPhase.ERROR)

    def _run(self, request: ExportRequest) -> None:
        """
        Body of the worker thread.

        Everything is wrapped: an exception escaping a thread would be printed
        and the GUI would sit on a progress dialog that never completes, so the
        failure is turned into an ERROR phase the dialog can report.
        """
        source = FileExportFrameSource(
            request.source_path, request.start_frame, request.end_frame
        )
        writer: cv2.VideoWriter | None = None
        out_path = request.output_path
        wrote_file = False
        try:
            if not source.open():
                self._fail("Could not open the export source.")
                return

            capture_fps = (
                request.capture_fps if request.capture_fps > 0.0 else DEFAULT_CAPTURE_FPS
            )
            file_fps = request.file_fps if request.file_fps > 0.0 else capture_fps
            self._set(frames_total=source.frame_count)

            # Fresh chain: its own buffer and its own Magnificator, so the
            # temporal state starts empty and only ever sees this range's frames
            # in order.
            buffer: list[np.ndarray] = []
            settings = ImageProcessingSettings(**vars(request.settings))
            settings.framerate = capture_fps
            flags = ImageProcessingFlags(**vars(request.flags))
            magnificator = Magnificator(buffer, flags, settings)
            magnify = (
                flags.color_magnify_on
                or flags.laplace_magnify_on
                or flags.riesz_magnify_on
            )

            out_size: tuple[int, int] | None = None
            while not self._abort.is_set():
                raw = source.next()
                if raw is None:
                    break
                if raw.size == 0:
                    continue

                cur = self._preprocess(raw, request, flags)
                original = cur.copy()

                out = cur
                if magnify:
                    buffer.append(cur)
                    if len(buffer) == 2:
                        if flags.color_magnify_on:
                            magnificator.color_magnify()
                        elif flags.laplace_magnify_on:
                            magnificator.laplace_magnify()
                        else:
                            magnificator.riesz_magnify()
                        if magnificator.has_frame():
                            out = magnificator.get_frame_last()

                canvas = compose(
                    original, out, request.split, request.text_overlay
                )
                if canvas is None:
                    continue

                if writer is None:
                    out_size = (canvas.shape[1], canvas.shape[0])
                    writer, codec, out_path = _open_writer(
                        request.fmt, out_path, file_fps, out_size
                    )
                    if writer is None:
                        self._fail(
                            "Could not open a video writer for the chosen format."
                        )
                        return
                    wrote_file = True
                    self._set(
                        codec_used=codec, output_path=out_path, frame_size=out_size
                    )
                if out_size is not None and (
                    canvas.shape[1],
                    canvas.shape[0],
                ) != out_size:
                    # Defensive: the geometry is fixed for the whole run, but a
                    # single odd frame would otherwise be dropped by the encoder.
                    canvas = cv2.resize(canvas, out_size)
                writer.write(canvas)
                with self._lock:
                    self._progress.frames_done += 1

            self._set(phase=ExportPhase.FINALIZING)
            # Release BEFORE any partial-file removal: on Windows an open file
            # cannot be deleted, and the release is also what flushes the
            # container's index (an unreleased .mp4 has no moov atom).
            if writer is not None:
                writer.release()
                writer = None

            if not wrote_file and not self._abort.is_set():
                # Surface an empty range instead of reporting a 0-frame success.
                self._fail("No frames to export (empty range?).")
                return

            if self._abort.is_set():
                if wrote_file:
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                self._set(phase=ExportPhase.ABORTED)
            else:
                self._set(phase=ExportPhase.DONE)
        except Exception as exc:  # noqa: BLE001 - must not escape the thread
            self._fail(f"Export failed: {exc}")
        finally:
            if writer is not None:
                writer.release()
            source.close()

    @staticmethod
    def _preprocess(
        raw: np.ndarray, request: ExportRequest, flags: ImageProcessingFlags
    ) -> np.ndarray:
        """
        ROI crop, downscale and optional grayscale -- the "original" tap stage.

        Kept identical to ProcessingWorker's first stage so what is exported
        matches what the preview showed.
        """
        fh, fw = raw.shape[:2]
        x, y, rw, rh = request.roi
        if rw <= 0 or rh <= 0:
            x, y, rw, rh = 0, 0, fw, fh
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        rw = max(1, min(rw, fw - x))
        rh = max(1, min(rh, fh - y))
        cur = raw[y : y + rh, x : x + rw].copy()

        divisor = request.downscale if request.downscale in (1, 2, 4, 8) else 1
        if divisor > 1:
            dh = max(1, cur.shape[0] // divisor)
            dw = max(1, cur.shape[1] // divisor)
            cur = cv2.resize(cur, (dw, dh), interpolation=cv2.INTER_AREA)

        if flags.grayscale_on and cur.ndim == 3:
            cur = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
        return cur
