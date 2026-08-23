"""Hilos de captura y procesamiento (CaptureThread / ProcessingThread)."""

from __future__ import annotations

import queue
import time
from collections import deque
import cv2
import numpy as np
from PyQt6.QtCore import QMutex, QThread, pyqtSignal

from config import (
    CAPTURE_FPS_STAT_QUEUE_LENGTH,
    PROCESSING_FPS_STAT_QUEUE_LENGTH,
)
from camera_enumerator import preferred_capture_apis
from mat_to_qimage import mat_to_qimage
from magnificator import Magnificator
from structures import (
    ImageProcessingFlags,
    ImageProcessingSettings,
    ThreadStatisticsData,
)

class CaptureWorker(QThread):
    """Reads frames from the camera and pushes them onto the queue."""

    frame_ready = pyqtSignal(np.ndarray)
    stats = pyqtSignal(object)
    capture_fps = pyqtSignal(float)

    def __init__(
        self,
        device_id: int,
        width: int,
        height: int,
        fps: int,
        drop_if_full: bool,
        frame_queue: queue.Queue,
        max_queue: int,
    ) -> None:
        super().__init__()
        self._device_id = device_id
        self._width = width
        self._height = height
        self._fps = fps
        self._drop = drop_if_full
        self._queue = frame_queue
        self._max_queue = max_queue
        # Frames evicted by the latest-wins policy. Should stay at 0 for files;
        # for cameras it is the honest measure of how far behind we are.
        self._drops = 0
        self._stop = False
        self._cap: cv2.VideoCapture | None = None
        self._fps_samples: deque[int] = deque(maxlen=CAPTURE_FPS_STAT_QUEUE_LENGTH)

    def run(self) -> None:
        # Try the platform-native backend first and fall back to CAP_ANY: the
        # indices reported by the enumerator are V4L2 ordinals, so opening with
        # the same backend guarantees index and device stay in agreement.
        for api in preferred_capture_apis():
            cap = cv2.VideoCapture(self._device_id, api)
            if cap.isOpened():
                self._cap = cap
                break
            cap.release()
        if self._cap is None:
            return

        if self._width > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        if self._height > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        if self._fps > 0:
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        t0 = time.perf_counter()
        n = 0
        while not self._stop and self._cap.isOpened():
            t_loop = time.perf_counter()
            ok, frame = self._cap.read()
            if not ok:
                break
            n += 1
            if self._drop:
                # Latest-wins: evict the oldest frame so the grab loop never
                # stalls. Dropping is a deliberate policy here, and is counted.
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                        self._drops += 1
                    except queue.Empty:
                        pass
                self._queue.put(frame)
            else:
                # Lossless: block until the consumer catches up. The previous
                # code used put(timeout=0.5) and swallowed queue.Full, which
                # silently dropped frames even with dropping disabled -- that
                # breaks the continuity the temporal filters depend on.
                self._queue.put(frame)

            dt = time.perf_counter() - t_loop
            if dt > 0:
                # Float division: the old `1000 // dt_ms` quantised the rate so
                # coarsely that 60 fps could only ever read as 62 or 76.
                self._fps_samples.append(1.0 / dt)
            if n % 30 == 0 and self._fps_samples:
                avg = sum(self._fps_samples) / len(self._fps_samples)
                real_fps = self._cap.get(cv2.CAP_PROP_FPS)
                self.capture_fps.emit(float(real_fps or avg))

            st = ThreadStatisticsData()
            st.average_fps = sum(self._fps_samples) // max(
                1, len(self._fps_samples)
            )
            self.stats.emit(st)

        if self._cap:
            self._cap.release()


class ProcessingWorker(QThread):
    """Magnifica frames de la cola y emite QImage."""

    new_frame = pyqtSignal(object)
    stats = pyqtSignal(object)
    max_levels = pyqtSignal(int)

    def __init__(self, frame_queue: queue.Queue) -> None:
        super().__init__()
        # Processing-resolution divisor (1, 2, 4 or 8); see PROCESSING_SCALES.
        self._downscale = 1
        self._queue = frame_queue
        self._stop = False
        self._mutex = QMutex()
        self._flags = ImageProcessingFlags()
        self._settings = ImageProcessingSettings()
        self._roi = (0, 0, 0, 0)
        self._need_max_levels_for_full = True
        self._processing_buffer: list[np.ndarray] = []
        self._buffer_len = 2
        self._magnificator = Magnificator(
            self._processing_buffer, self._flags, self._settings
        )
        self._proc_times: deque[int] = deque(maxlen=PROCESSING_FPS_STAT_QUEUE_LENGTH)
        self._sample_n = 0
        self._fps_sum = 0

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        """Do not emit signals while holding the mutex: the GUI may call update_settings and deadlock."""
        self._mutex.lock()
        self._roi = (x, y, w, h)
        self._processing_buffer.clear()
        self._magnificator.clear_buffer()
        emit_lv: int | None = None
        if w > 0 and h > 0:
            emit_lv = self._magnificator.calculate_max_levels(w, h)
            self._need_max_levels_for_full = False
        else:
            self._need_max_levels_for_full = True
        self._mutex.unlock()
        if emit_lv is not None:
            self.max_levels.emit(emit_lv)

    def set_downscale(self, divisor: int) -> None:
        """
        Set the processing-resolution divisor (1, 2, 4 or 8).

        Changing it alters the frame geometry, so the temporal state is
        invalidated: the magnification filters hold per-pixel history that is
        meaningless at a different resolution.
        """
        divisor = int(divisor) if divisor in (1, 2, 4, 8) else 1
        self._mutex.lock()
        changed = divisor != self._downscale
        self._downscale = divisor
        if changed:
            self._magnificator.clear_buffer()
            self._need_max_levels_for_full = True
        self._mutex.unlock()

    def update_flags(self, f: ImageProcessingFlags) -> None:
        self._mutex.lock()
        self._flags.grayscale_on = f.grayscale_on
        self._flags.color_magnify_on = f.color_magnify_on
        self._flags.laplace_magnify_on = f.laplace_magnify_on
        self._flags.riesz_magnify_on = f.riesz_magnify_on
        self._processing_buffer.clear()
        self._magnificator.clear_buffer()
        self._mutex.unlock()

    def update_settings(self, s: ImageProcessingSettings) -> None:
        self._mutex.lock()
        old_lv = self._settings.levels
        self._settings.amplification = s.amplification
        self._settings.co_wavelength = s.co_wavelength
        self._settings.co_low = s.co_low
        self._settings.co_high = s.co_high
        self._settings.chrom_attenuation = s.chrom_attenuation
        self._settings.levels = s.levels
        self._settings.framerate = s.framerate
        if old_lv != s.levels:
            self._processing_buffer.clear()
            self._magnificator.clear_buffer()
        self._mutex.unlock()

    def update_framerate(self, fps: float) -> None:
        self._mutex.lock()
        self._settings.framerate = fps
        self._mutex.unlock()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        while not self._stop:
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            t0 = time.perf_counter()
            self._mutex.lock()
            x, y, rw, rh = self._roi
            emit_lv_full: int | None = None
            if rw <= 0 or rh <= 0:
                fh, fw = frame.shape[:2]
                x, y, rw, rh = 0, 0, fw, fh
                if self._need_max_levels_for_full:
                    self._need_max_levels_for_full = False
                    emit_lv_full = self._magnificator.calculate_max_levels(rw, rh)
            # Clamp the ROI to the frame: a stale ROI kept after the source
            # changed resolution would otherwise index out of bounds.
            fh, fw = frame.shape[:2]
            x = max(0, min(x, fw - 1))
            y = max(0, min(y, fh - 1))
            rw = max(1, min(rw, fw - x))
            rh = max(1, min(rh, fh - y))
            cur = frame[y : y + rh, x : x + rw].copy()

            # Processing resolution: dividing each side by `downscale` cuts the
            # pyramid cost quadratically, which is the single most effective
            # performance control (1/4 is ~16x less work). INTER_AREA is the
            # right filter for shrinking.
            if self._downscale > 1:
                dh = max(1, cur.shape[0] // self._downscale)
                dw = max(1, cur.shape[1] // self._downscale)
                cur = cv2.resize(cur, (dw, dh), interpolation=cv2.INTER_AREA)

            if self._flags.grayscale_on and cur.ndim == 3:
                cur = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)

            self._processing_buffer.append(cur)

            out = cur
            if len(self._processing_buffer) == self._buffer_len:
                if self._flags.color_magnify_on:
                    self._magnificator.color_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                elif self._flags.laplace_magnify_on:
                    self._magnificator.laplace_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                elif self._flags.riesz_magnify_on:
                    self._magnificator.riesz_magnify()
                    if self._magnificator.has_frame():
                        out = self._magnificator.get_frame_last()
                else:
                    self._processing_buffer.pop(0)

            if out.ndim == 2:
                out_disp = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
            else:
                out_disp = out

            self._mutex.unlock()

            if emit_lv_full is not None:
                self.max_levels.emit(emit_lv_full)

            dt_ms = int((time.perf_counter() - t0) * 1000)
            self._update_fps_stats(dt_ms)
            self.new_frame.emit(mat_to_qimage(out_disp))

            st = ThreadStatisticsData()
            st.n_frames_processed = 1
            if self._proc_times:
                st.average_fps = sum(self._proc_times) // len(self._proc_times)
            self.stats.emit(st)

    def _update_fps_stats(self, elapsed_ms: int) -> None:
        if elapsed_ms > 0:
            self._proc_times.append(1000 // elapsed_ms)
            self._sample_n += 1
        if (
            len(self._proc_times) == PROCESSING_FPS_STAT_QUEUE_LENGTH
            and self._sample_n >= PROCESSING_FPS_STAT_QUEUE_LENGTH
        ):
            self._fps_sum = sum(self._proc_times)
            self._settings.framerate = self._fps_sum // PROCESSING_FPS_STAT_QUEUE_LENGTH
            self._proc_times.clear()
            self._sample_n = 0
            self._fps_sum = 0
