"""Camera enumeration by human-readable name.

Port of the C++ ``CameraEnumerator`` (V4L2 implementation). The point of this
module is that a bare device index is a bad thing to ask a user for: on Linux a
single UVC webcam registers *several* ``/dev/videoN`` nodes and only some of
them deliver images. The others are metadata nodes (``V4L2_CAP_META_CAPTURE``)
or secondary streams that open successfully yet never return a frame, so a user
who types "1" gets a black tab and no explanation. Enumerating names and
filtering to nodes that actually capture removes the guesswork entirely.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import sys
from dataclasses import dataclass

import cv2

# --- V4L2 ioctl plumbing ----------------------------------------------------
# Transcribed from <linux/videodev2.h>. Done by hand rather than via a binding
# so the module has no dependency beyond the standard library and OpenCV.

_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
_V4L2_CAP_DEVICE_CAPS = 0x80000000

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_READ = 2


class _V4l2Capability(ctypes.Structure):
    """``struct v4l2_capability`` as returned by ``VIDIOC_QUERYCAP``."""

    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


def _ior(type_char: str, nr: int, size: int) -> int:
    """Rebuild the kernel's ``_IOR`` macro for the QUERYCAP request code."""
    return (
        (_IOC_READ << (_IOC_NRBITS + _IOC_TYPEBITS + _IOC_SIZEBITS))
        | (size << (_IOC_NRBITS + _IOC_TYPEBITS))
        | (ord(type_char) << _IOC_NRBITS)
        | nr
    )


_VIDIOC_QUERYCAP = _ior("V", 0, ctypes.sizeof(_V4l2Capability))

# UVC devices rarely go past a handful of nodes, but the kernel numbering is
# sparse after hot-plugs, so scan the same range the C++ version does.
_MAX_DEVICE_INDEX = 64

# Number of read() attempts allowed before a node is declared dead. The first
# grab after opening a V4L2 device often fails while the stream starts up, so a
# single failed read is not evidence that the device is unusable.
_PROBE_READ_ATTEMPTS = 3


@dataclass(frozen=True)
class CameraDevice:
    """A camera that answered a capability query *and* delivered a frame."""

    index: int
    name: str


def preferred_capture_apis() -> list[int]:
    """OpenCV backend ids to try, in order, when opening an enumerated device.

    ``CAP_ANY`` is always last so we still work on OpenCV builds compiled
    without the platform-native backend.
    """
    if sys.platform.startswith("linux"):
        return [cv2.CAP_V4L2, cv2.CAP_ANY]
    if sys.platform == "darwin":
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    if sys.platform.startswith("win"):
        return [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]
    return [cv2.CAP_ANY]


def _query_v4l2_name(index: int) -> str | None:
    """Return the ``cap.card`` name of ``/dev/video<index>``, or ``None``.

    ``None`` means "not a video-capture node": either the file does not exist,
    we cannot open it, or the driver reports it as metadata/output only. This is
    the cheap filter -- it discards metadata nodes without touching the sensor.
    """
    path = f"/dev/video{index}"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        # ENOENT: no such node. EACCES/EBUSY: not ours to use.
        if exc.errno not in (errno.ENOENT, errno.EACCES, errno.EBUSY, errno.ENXIO):
            return None
        return None

    try:
        cap = _V4l2Capability()
        try:
            fcntl.ioctl(fd, _VIDIOC_QUERYCAP, cap)
        except OSError:
            return None

        # Prefer the per-node device_caps; older drivers only fill the global
        # capabilities field, in which case every node of a device looks like a
        # capture node and we rely on the frame probe below to sort them out.
        caps = cap.device_caps if cap.capabilities & _V4L2_CAP_DEVICE_CAPS else cap.capabilities
        if not caps & (_V4L2_CAP_VIDEO_CAPTURE | _V4L2_CAP_VIDEO_CAPTURE_MPLANE):
            return None

        name = cap.card.decode("utf-8", "replace").strip()
        return name or f"Camera {index}"
    finally:
        os.close(fd)


def _probe_frame(index: int) -> tuple[int, int] | None:
    """Open ``index`` and return the ``(width, height)`` of a decoded frame.

    Returns ``None`` if the device cannot be opened or never yields an image.
    This second stage is what the capability flags cannot tell us: on many
    laptops a node advertises ``VIDEO_CAPTURE`` and opens cleanly, yet every
    ``read()`` fails because the node belongs to a stream (IR, depth, metadata)
    the driver will not hand over as plain frames.
    """
    for api in preferred_capture_apis():
        cap = cv2.VideoCapture(index, api)
        try:
            if not cap.isOpened():
                continue
            for _ in range(_PROBE_READ_ATTEMPTS):
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    return frame.shape[1], frame.shape[0]
        finally:
            cap.release()
    return None


def _enumerate_linux(verify_frames: bool) -> list[CameraDevice]:
    """V4L2 enumeration: ``/dev/videoN`` ordinals are OpenCV indices verbatim."""
    out: list[CameraDevice] = []
    for index in range(_MAX_DEVICE_INDEX):
        name = _query_v4l2_name(index)
        if name is None:
            continue
        if verify_frames:
            size = _probe_frame(index)
            if size is None:
                continue
            name = f"{name} ({size[0]}x{size[1]})"
        out.append(CameraDevice(index, name))
    return out


def _enumerate_by_probing(max_index: int = 10) -> list[CameraDevice]:
    """Cross-platform fallback: no names available, so probe for live indices."""
    out: list[CameraDevice] = []
    for index in range(max_index):
        size = _probe_frame(index)
        if size is None:
            continue
        out.append(CameraDevice(index, f"Camera {index} ({size[0]}x{size[1]})"))
    return out


def enumerate_cameras(verify_frames: bool = True) -> list[tuple[int, str]]:
    """List usable cameras as ``(opencv_index, display_name)`` pairs.

    ``verify_frames`` actually grabs an image from each candidate. It costs a
    few hundred milliseconds per node but it is the only reliable way to keep
    dead nodes out of the picker, which is the entire purpose of this module.
    Pass ``False`` for a fast, name-only listing.
    """
    if sys.platform.startswith("linux") and os.path.exists("/dev"):
        devices = _enumerate_linux(verify_frames)
        if devices:
            return [(d.index, d.name) for d in devices]
    return [(d.index, d.name) for d in _enumerate_by_probing()]


if __name__ == "__main__":  # manual smoke test
    for idx, label in enumerate_cameras():
        print(f"{idx}: {label}")
