"""Filtros temporales: IIR Laplace, ideal (color), Butterworth Riesz."""

from __future__ import annotations

import math

import cv2
import numpy as np
from scipy import signal

# CompExpMat: tupla (cos_mat, sin_mat)
CompExpMat = tuple[np.ndarray, np.ndarray]


def cexp_cos(ce: CompExpMat) -> np.ndarray:
    return ce[0]


def cexp_sin(ce: CompExpMat) -> np.ndarray:
    return ce[1]


def cexp_add(a: CompExpMat, b: CompExpMat) -> CompExpMat:
    return (a[0] + b[0], a[1] + b[1])


def cexp_sub(a: CompExpMat, b: CompExpMat) -> CompExpMat:
    return (a[0] - b[0], a[1] - b[1])


def cexp_mul_pair(a: CompExpMat, b: CompExpMat) -> CompExpMat:
    return (a[0] * b[0], a[1] * b[1])


def cexp_mul_scalar(ce: CompExpMat, s: float | np.ndarray) -> CompExpMat:
    return (ce[0] * s, ce[1] * s)


def cexp_div_scalar(ce: CompExpMat, s: np.ndarray) -> CompExpMat:
    return (cv2.divide(ce[0], s), cv2.divide(ce[1], s))


def iir_filter(
    src: np.ndarray,
    dst: np.ndarray,
    lowpass_hi: np.ndarray,
    lowpass_lo: np.ndarray,
    cutoff_lo: float,
    cutoff_hi: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Filtro paso banda IIR espacial (Euler). Devuelve (dst, lowpass_hi, lowpass_lo)."""
    clo = 0.01 if cutoff_lo == 0 else cutoff_lo
    tmp1 = (1.0 - cutoff_hi) * lowpass_hi + cutoff_hi * src
    tmp2 = (1.0 - clo) * lowpass_lo + clo * src
    lowpass_hi = tmp1
    lowpass_lo = tmp2
    dst = lowpass_hi - lowpass_lo
    return dst, lowpass_hi, lowpass_lo


def _ideal_bandpass_row_fft(
    plane: np.ndarray, cutoff_lo: float, cutoff_hi: float, framerate: float
) -> np.ndarray:
    """Row-wise 1-D FFT; band-pass indices as in createIdealBandpassFilter (C++)."""
    clo = cutoff_lo + 0.01 if cutoff_lo == 0.0 else cutoff_lo
    h, w = plane.shape
    spec = np.fft.rfft(plane.astype(np.float64), axis=1)
    w_eff = spec.shape[1]
    fl = 2.0 * clo * w_eff / framerate
    fh = 2.0 * cutoff_hi * w_eff / framerate
    mask = np.zeros(w_eff, dtype=np.float64)
    hi = int(min(w_eff - 1, max(0, math.floor(fh))))
    lo = int(max(0, math.ceil(fl)))
    if lo <= hi:
        mask[lo : hi + 1] = 1.0
    spec *= mask
    out = np.fft.irfft(spec, n=w, axis=1)
    return out.astype(np.float32)


def ideal_filter(src: np.ndarray, cutoff_lo: float, cutoff_hi: float, framerate: float) -> np.ndarray:
    """Ideal row-wise band-pass filtering (colour magnification)."""
    fps = framerate if framerate > 1e-6 else 30.0
    n_channels = 1 if src.ndim == 2 else src.shape[2]
    if n_channels == 1:
        planes = [src.astype(np.float32)]
    else:
        planes = [np.squeeze(x) for x in cv2.split(src.astype(np.float32))]

    out_planes = [
        _ideal_bandpass_row_fft(p, cutoff_lo, cutoff_hi, fps) for p in planes
    ]
    if n_channels == 1:
        dst = out_planes[0]
    else:
        dst = cv2.merge(out_planes)
    cv2.normalize(dst, dst, 0, 1, cv2.NORM_MINMAX)
    return dst


def img2temp_mat(frame: np.ndarray, dst: np.ndarray | None, max_images: int) -> np.ndarray:
    """Apila frames como columnas (cada columna = un frame aplanado en orden C)."""
    reshaped = frame.astype(np.float32).reshape(-1, 1)
    if dst is None or dst.size == 0:
        out = reshaped.copy()
    else:
        out = np.hstack((dst, reshaped))
    if max_images > 0 and out.shape[1] > max_images:
        out = out[:, 1:].copy()
    return out


def temp_mat2img(
    src: np.ndarray, position: int, frame_size: tuple[int, int], channels: int
) -> np.ndarray:
    """Extrae una columna y la reordena a imagen (width, height, channels)."""
    line = src[:, position]
    w, h = frame_size
    flat = w * h * channels
    vec = line[:flat]
    if channels == 1:
        return vec.reshape(h, w)
    return vec.reshape(h, w, channels)


def butterworth_lowpass_coeffs(order: int, wn: float) -> tuple[np.ndarray, np.ndarray]:
    """Coeficientes digitales; wn normalizado a Nyquist (0..1), como en el C++."""
    if wn <= 0 or wn >= 1:
        wn = min(max(wn, 1e-6), 0.99)
    b, a = signal.butter(order, wn, btype="low", analog=False)
    return b.astype(np.float64), a.astype(np.float64)


class RieszTemporalFilter:
    """Filtro temporal Butterworth sobre fase (Riesz)."""

    def __init__(
        self,
        frq: float,
        fps: float,
        lvl_sizes: list[tuple[int, int]],
    ) -> None:
        self.its_frequency = frq
        self.its_framerate = fps
        self.its_a: list[float] = []
        self.its_b: list[float] = []
        self._num_levels = len(lvl_sizes)
        self._reg0: list[CompExpMat] = []
        self._reg1: list[CompExpMat] = []
        self._phase: list[CompExpMat] = []
        for h, w in lvl_sizes:
            z = np.zeros((h, w), np.float32)
            self._reg0.append((z.copy(), z.copy()))
            self._reg1.append((z.copy(), z.copy()))
            self._phase.append((z.copy(), z.copy()))
        self.compute_coefficients()

    def compute_coefficients(self) -> None:
        fps = self.its_framerate
        wn = 0.0 if fps == 0.0 else self.its_frequency / (fps / 2.0)
        b, a = butterworth_lowpass_coeffs(2, wn)
        self.its_b = b.tolist()
        self.its_a = a.tolist()

    def update_frequency(self, f: float) -> None:
        self.its_frequency = f
        self.compute_coefficients()

    def update_framerate(self, framerate: float) -> None:
        self.its_framerate = framerate
        self.compute_coefficients()

    def reset_mat(self) -> None:
        for i in range(self._num_levels):
            h, w = self._phase[i][0].shape
            z = np.zeros((h, w), np.float32)
            self._reg0[i] = (z.copy(), z.copy())
            self._reg1[i] = (z.copy(), z.copy())
            self._phase[i] = (z.copy(), z.copy())

    def iir_temporal_filter(
        self,
        result_holder: list[CompExpMat],
        phase_diff: CompExpMat,
        lvl: int,
    ) -> None:
        """Direct Form II; writes the result into result_holder[0]."""
        # Normalise by a0 once, on the coefficients. The previous code divided
        # the output AND both state registers by a0 on every level of every
        # frame, which is not the Direct Form II normalisation (the states must
        # not be rescaled) and cost six array divisions per level. scipy always
        # returns a0 == 1, so the old code was a no-op numerically -- but wrong
        # in general and needlessly expensive.
        a0 = self.its_a[0] or 1.0
        b0, b1, b2 = (self.its_b[0] / a0, self.its_b[1] / a0, self.its_b[2] / a0)
        a1, a2 = (self.its_a[1] / a0, self.its_a[2] / a0)
        ph = cexp_add(self._phase[lvl], phase_diff)
        self._phase[lvl] = ph

        r0 = self._reg0[lvl]
        r1 = self._reg1[lvl]

        res_cos = ph[0] * b0 + r0[0]
        res_sin = ph[1] * b0 + r0[1]

        new_r0_cos = ph[0] * b1 + r1[0] - res_cos * a1
        new_r0_sin = ph[1] * b1 + r1[1] - res_sin * a1
        new_r1_cos = ph[0] * b2 - res_cos * a2
        new_r1_sin = ph[1] * b2 - res_sin * a2

        self._reg0[lvl] = (new_r0_cos, new_r0_sin)
        self._reg1[lvl] = (new_r1_cos, new_r1_sin)
        result_holder[0] = (res_cos, res_sin)
