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
from mat_to_qimage import mat_to_qimage
from magnificator import Magnificator
from structures import (
    ImageProcessingFlags,
    ImageProcessingSettings,
    ThreadStatisticsData,
)

class CaptureWorker(QThread):
    """Lee frames de la cámara y los encola."""

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
        self._stop = False
        self._cap: cv2.VideoCapture | None = None
        self._fps_samples: deque[int] = deque(maxlen=CAPTURE_FPS_STAT_QUEUE_LENGTH)

    def run(self) -> None:
        self._cap = cv2.VideoCapture(self._device_id)
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
            if self._drop and self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._queue.put(frame, timeout=0.5)
            except queue.Full:
                continue
            dt_ms = int((time.perf_counter() - t_loop) * 1000)
            if dt_ms > 0:
                self._fps_samples.append(1000 // dt_ms)
            if n % 30 == 0 and self._fps_samples:
                avg = sum(self._fps_samples) // len(self._fps_samples)
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
        """No emitir señales con el mutex tomado: el GUI puede llamar a update_settings y bloquearse."""
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
            cur = frame[y : y + rh, x : x + rw].copy()

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
