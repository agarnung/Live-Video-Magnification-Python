"""
Pipeline instrumentation: counters, fps, latency percentiles.

Design notes carried over from the C++ reference:

* The GUI *polls* a snapshot on a QTimer, it is never signalled per frame.
  Emitting a Qt signal for every frame would put the GUI thread's event loop on
  the critical path of the capture/processing loop, which is exactly the
  coupling the instrumentation is supposed to measure.
* ``processed == captured`` is the invariant that means "zero pipeline drops";
  every place a frame is discarded has to bump a drop counter instead of
  quietly vanishing, otherwise the invariant stops being diagnostic.
* The C++ version pads each counter to its own cache line to avoid false
  sharing between the capture and processing threads.  That is pointless in
  CPython: every counter update is serialised by the GIL anyway, and Python
  ints are heap objects whose memory layout we do not control.  A single lock
  around the whole struct is both simpler and no slower here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Latency histogram geometry: 64 buckets of 5 ms covers 0..320 ms, and the last
# bucket is a catch-all so a pathological frame cannot fall outside the range.
_BUCKETS = 64
_BUCKET_MS = 5.0

# EMA smoothing for fps and drop fraction.  0.3 reacts within a few polls while
# still hiding the sampling jitter of a ~1 Hz GUI timer.
_EMA_ALPHA = 0.3


@dataclass(frozen=True)
class StatsSnapshot:
    """An immutable read of the pipeline health, safe to hand to the GUI."""

    captured: int = 0
    processed: int = 0  # processed == captured  =>  zero pipeline drops
    displayed: int = 0
    display_skipped: int = 0  # cosmetic: frames overwritten before display
    source_drops: int = 0  # must stay 0 on the lossless path
    proc_errors: int = 0  # frames a stage threw on (degraded, not crashed)
    read_errors: int = 0
    queue_depth: int = 0
    fps: float = 0.0  # processed frames/sec, EMA
    latency_mean_ms: float = 0.0  # capture -> processed
    latency_p95_ms: float = 0.0
    drop_fraction: float = 0.0  # EMA of dropped/(dropped+processed)


class Instrumentation:
    """
    Thread-safe pipeline counters.

    Producers (capture / processing threads) call the ``on_*`` mutators; the GUI
    thread calls :meth:`snapshot`, which also closes the fps and drop-fraction
    intervals since the previous call.  All state lives under one lock because
    the contention is negligible (a handful of increments per frame) and the
    alternative -- lock-free counters -- buys nothing under the GIL.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._captured = 0
        self._processed = 0
        self._displayed = 0
        self._display_skipped = 0
        self._source_drops = 0
        self._proc_errors = 0
        self._read_errors = 0
        self._queue_depth = 0

        self._hist = [0] * _BUCKETS
        self._latency_count = 0
        self._latency_sum_ms = 0.0

        # Interval state, only touched inside snapshot() (GUI thread).
        self._last_ts: float | None = None
        self._last_processed = 0
        self._last_source_drops = 0
        self._fps_ema: float | None = None
        self._drop_ema: float | None = None

    # ---- producer side -------------------------------------------------

    def on_captured(self) -> None:
        with self._lock:
            self._captured += 1

    def on_processed(self) -> None:
        with self._lock:
            self._processed += 1

    def on_displayed(self) -> None:
        with self._lock:
            self._displayed += 1

    def add_display_skipped(self, n: int) -> None:
        with self._lock:
            self._display_skipped += n

    def set_queue_depth(self, depth: int) -> None:
        with self._lock:
            self._queue_depth = depth

    def set_source_drops(self, drops: int) -> None:
        """Absolute count, because the capture loop owns its own drop tally."""
        with self._lock:
            self._source_drops = drops

    def on_processing_error(self) -> None:
        with self._lock:
            self._proc_errors += 1

    def on_source_read_error(self) -> None:
        with self._lock:
            self._read_errors += 1

    def record_latency(self, ms: float) -> None:
        """Record a capture->processed latency into the histogram."""
        if ms < 0.0:
            ms = 0.0
        bucket = int(ms / _BUCKET_MS)
        if bucket >= _BUCKETS:
            bucket = _BUCKETS - 1
        with self._lock:
            self._hist[bucket] += 1
            self._latency_count += 1
            self._latency_sum_ms += ms

    # ---- consumer side -------------------------------------------------

    def snapshot(self) -> StatsSnapshot:
        """
        Read every counter and close the current fps / drop-fraction interval.

        Call this from exactly one thread (the GUI's poll timer): it mutates the
        interval bookkeeping, so two concurrent callers would each see half of
        the frames processed since the last poll.
        """
        now = time.perf_counter()
        with self._lock:
            captured = self._captured
            processed = self._processed
            displayed = self._displayed
            display_skipped = self._display_skipped
            source_drops = self._source_drops
            proc_errors = self._proc_errors
            read_errors = self._read_errors
            queue_depth = self._queue_depth

            if self._last_ts is not None:
                dt = now - self._last_ts
                if dt > 0.0:
                    processed_delta = processed - self._last_processed
                    drops_delta = source_drops - self._last_source_drops

                    inst_fps = processed_delta / dt
                    self._fps_ema = (
                        inst_fps
                        if self._fps_ema is None
                        else _EMA_ALPHA * inst_fps + (1.0 - _EMA_ALPHA) * self._fps_ema
                    )

                    resolved = drops_delta + processed_delta
                    if resolved > 0:
                        inst_drop = drops_delta / resolved
                        self._drop_ema = (
                            inst_drop
                            if self._drop_ema is None
                            else _EMA_ALPHA * inst_drop
                            + (1.0 - _EMA_ALPHA) * self._drop_ema
                        )

            self._last_ts = now
            self._last_processed = processed
            self._last_source_drops = source_drops

            mean_ms = 0.0
            p95_ms = 0.0
            if self._latency_count > 0:
                mean_ms = self._latency_sum_ms / self._latency_count
                # ceil(0.95 * n) in integer arithmetic, then walk the CDF and
                # report the containing bucket's centre.
                target = (self._latency_count * 95 + 99) // 100
                acc = 0
                for i, count in enumerate(self._hist):
                    acc += count
                    if acc >= target:
                        p95_ms = (i + 0.5) * _BUCKET_MS
                        break

            return StatsSnapshot(
                captured=captured,
                processed=processed,
                displayed=displayed,
                display_skipped=display_skipped,
                source_drops=source_drops,
                proc_errors=proc_errors,
                read_errors=read_errors,
                queue_depth=queue_depth,
                fps=self._fps_ema or 0.0,
                latency_mean_ms=mean_ms,
                latency_p95_ms=p95_ms,
                drop_fraction=self._drop_ema or 0.0,
            )

    def reset(self) -> None:
        """Clear everything, e.g. when a tab reopens its source."""
        with self._lock:
            self._captured = 0
            self._processed = 0
            self._displayed = 0
            self._display_skipped = 0
            self._source_drops = 0
            self._proc_errors = 0
            self._read_errors = 0
            self._queue_depth = 0
            self._hist = [0] * _BUCKETS
            self._latency_count = 0
            self._latency_sum_ms = 0.0
            self._last_ts = None
            self._last_processed = 0
            self._last_source_drops = 0
            self._fps_ema = None
            self._drop_ema = None
