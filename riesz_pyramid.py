"""Riesz pyramid and phase-based magnification (RieszPyramid.cpp)."""

from __future__ import annotations

import math

import cv2
import numpy as np

CompExpMat = tuple[np.ndarray, np.ndarray]


def _cexp_add(a: CompExpMat, b: CompExpMat) -> CompExpMat:
    return (a[0] + b[0], a[1] + b[1])


def _cexp_mul_pair(a: CompExpMat, b: CompExpMat) -> CompExpMat:
    return (a[0] * b[0], a[1] * b[1])


def _cexp_mul_scalar(ce: CompExpMat, s: np.ndarray | float) -> CompExpMat:
    return (ce[0] * s, ce[1] * s)


def _cexp_div_scalar(ce: CompExpMat, s: np.ndarray) -> CompExpMat:
    return (
        np.divide(ce[0], s, out=np.zeros_like(ce[0]), where=np.abs(s) > 1e-8),
        np.divide(ce[1], s, out=np.zeros_like(ce[1]), where=np.abs(s) > 1e-8),
    )


def _square_pair(p: CompExpMat) -> np.ndarray:
    return p[0] * p[0] + p[1] * p[1]


def _patch_nans(img: np.ndarray, val: float = 0.0) -> None:
    img[np.isnan(img)] = val


def _arc_cos(x: np.ndarray, result: np.ndarray) -> None:
    """
    Elementwise arccos with the input clamped to [-1, 1].

    Vectorised: the original per-pixel Python loop dominated the Riesz path.
    Clamping the argument rather than the result keeps the values continuous at
    the boundary, where the reference C++ instead returns the clamped input
    (-1 or 1) -- which is not an angle at all.
    """
    np.arccos(np.clip(x, -1.0, 1.0), out=result)


def _cos_sin_mag(mag: np.ndarray) -> CompExpMat:
    return (np.cos(mag, dtype=np.float32), np.sin(mag, dtype=np.float32))


LOW_PASS_FILTER = np.array(
    [
        [-0.0001, -0.0007, -0.0023, -0.0046, -0.0057, -0.0046, -0.0023, -0.0007, -0.0001],
        [-0.0007, -0.0030, -0.0047, -0.0025, -0.0003, -0.0025, -0.0047, -0.0030, -0.0007],
        [-0.0023, -0.0047, 0.0054, 0.0272, 0.0387, 0.0272, 0.0054, -0.0047, -0.0023],
        [-0.0046, -0.0025, 0.0272, 0.0706, 0.0910, 0.0706, 0.0272, -0.0025, -0.0046],
        [-0.0057, -0.0003, 0.0387, 0.0910, 0.1138, 0.0910, 0.0387, -0.0003, -0.0057],
        [-0.0046, -0.0025, 0.0272, 0.0706, 0.0910, 0.0706, 0.0272, -0.0025, -0.0046],
        [-0.0023, -0.0047, 0.0054, 0.0272, 0.0387, 0.0272, 0.0054, -0.0047, -0.0023],
        [-0.0007, -0.0030, -0.0047, -0.0025, -0.0003, -0.0025, -0.0047, -0.0030, -0.0007],
        [-0.0001, -0.0007, -0.0023, -0.0046, -0.0057, -0.0046, -0.0023, -0.0007, -0.0001],
    ],
    dtype=np.float32,
)

HIGH_PASS_FILTER = np.array(
    [
        [0.0000, 0.0003, 0.0011, 0.0022, 0.0027, 0.0022, 0.0011, 0.0003, 0.0000],
        [0.0003, 0.0020, 0.0059, 0.0103, 0.0123, 0.0103, 0.0059, 0.0020, 0.0003],
        [0.0011, 0.0059, 0.0151, 0.0249, 0.0292, 0.0249, 0.0151, 0.0059, 0.0011],
        [0.0022, 0.0103, 0.0249, 0.0402, 0.0469, 0.0402, 0.0249, 0.0103, 0.0022],
        [0.0027, 0.0123, 0.0292, 0.0469, -0.9455, 0.0469, 0.0292, 0.0123, 0.0027],
        [0.0022, 0.0103, 0.0249, 0.0402, 0.0469, 0.0402, 0.0249, 0.0103, 0.0022],
        [0.0011, 0.0059, 0.0151, 0.0249, 0.0292, 0.0249, 0.0151, 0.0059, 0.0011],
        [0.0003, 0.0020, 0.0059, 0.0103, 0.0123, 0.0103, 0.0059, 0.0020, 0.0003],
        [0.0000, 0.0003, 0.0011, 0.0022, 0.0027, 0.0022, 0.0011, 0.0003, 0.0000],
    ],
    dtype=np.float32,
)

REAL_K = np.array([[-0.2, -0.48, 0.0, 0.48, 0.2]], dtype=np.float32)
IMAG_K = REAL_K.T


class RieszPyramidLevel:
    """A single level of the Riesz pyramid."""

    def __init__(self) -> None:
        self.its_size: tuple[int, int] = (0, 0)
        self.its_lvl = 0
        self.its_lowpass = np.zeros((0, 0), np.float32)
        self.its_riesz: CompExpMat = (
            np.zeros((0, 0), np.float32),
            np.zeros((0, 0), np.float32),
        )
        self.its_phase_diff: CompExpMat = (
            np.zeros((0, 0), np.float32),
            np.zeros((0, 0), np.float32),
        )
        self.its_highpass_iir: CompExpMat = (
            np.zeros((0, 0), np.float32),
            np.zeros((0, 0), np.float32),
        )
        self.its_lowpass_iir: CompExpMat = (
            np.zeros((0, 0), np.float32),
            np.zeros((0, 0), np.float32),
        )
        self.its_amplitude = np.zeros((0, 0), np.float32)
        self.its_amplitude_blurred = np.zeros((0, 0), np.float32)

    def build(self, octave: np.ndarray, lvl: int) -> None:
        self.its_size = (octave.shape[1], octave.shape[0])
        self.its_lvl = lvl
        self.its_lowpass = octave.astype(np.float32)
        rc = cv2.filter2D(
            self.its_lowpass, -1, REAL_K, borderType=cv2.BORDER_REFLECT101
        )
        ic = cv2.filter2D(
            self.its_lowpass, -1, IMAG_K, borderType=cv2.BORDER_REFLECT101
        )
        self.its_riesz = (rc.astype(np.float32), ic.astype(np.float32))

    def compute_phase_difference_and_amplitude(self, prior: RieszPyramidLevel) -> None:
        q_conj_prod_real = (
            self.its_lowpass * prior.its_lowpass
            + self.its_riesz[0] * prior.its_riesz[0]
            + self.its_riesz[1] * prior.its_riesz[1]
        )
        pl = prior.its_lowpass
        q_conj_prod = _cexp_add(
            _cexp_mul_scalar(prior.its_riesz, -self.its_lowpass),
            _cexp_mul_scalar(self.its_riesz, pl),
        )
        q_xy_sq = _square_pair(q_conj_prod)
        q_conj_amp = np.sqrt(
            np.maximum(q_conj_prod_real * q_conj_prod_real + q_xy_sq, 0.0)
        ).astype(np.float32)
        phase_tmp = np.divide(
            q_conj_prod_real,
            q_conj_amp,
            out=np.zeros_like(q_conj_prod_real),
            where=q_conj_amp > 1e-8,
        )
        phase_diff = np.zeros_like(phase_tmp, dtype=np.float32)
        _arc_cos(phase_tmp, phase_diff)
        q_xy_sqrt = np.sqrt(np.maximum(q_xy_sq, 0.0)).astype(np.float32)
        orientation = _cexp_div_scalar(q_conj_prod, q_xy_sqrt)
        self.its_phase_diff = _cexp_mul_pair(orientation, (phase_diff, phase_diff))
        _patch_nans(self.its_phase_diff[0], 0.0)
        _patch_nans(self.its_phase_diff[1], 0.0)
        self.its_amplitude = np.sqrt(np.maximum(q_conj_amp, 0.0)).astype(np.float32)
        self.its_amplitude_blurred = cv2.GaussianBlur(
            self.its_amplitude, (13, 13), 3.0
        )

    def amplify(self, alpha: float, threshold: float) -> None:
        sigma = 3.0
        aperture = int(1.0 + 4.0 * sigma)
        kernel = cv2.getGaussianKernel(aperture, sigma, cv2.CV_32F)
        ch = (
            self.its_highpass_iir[0] - self.its_lowpass_iir[0],
            self.its_highpass_iir[1] - self.its_lowpass_iir[1],
        )
        c = ch[0] * self.its_amplitude
        s = ch[1] * self.its_amplitude
        c = cv2.sepFilter2D(c, -1, kernel, kernel, borderType=cv2.BORDER_REFLECT101)
        s = cv2.sepFilter2D(s, -1, kernel, kernel, borderType=cv2.BORDER_REFLECT101)
        c = np.divide(
            c,
            self.its_amplitude_blurred,
            out=np.zeros_like(c),
            where=self.its_amplitude_blurred > 1e-8,
        )
        s = np.divide(
            s,
            self.its_amplitude_blurred,
            out=np.zeros_like(s),
            where=self.its_amplitude_blurred > 1e-8,
        )
        mag_v = np.sqrt(np.maximum(c * c + s * s, 0.0)).astype(np.float32)
        mag_v2 = np.minimum(mag_v * alpha, threshold)
        pd = _cos_sin_mag(mag_v2)
        pair = self.its_riesz[0] * c + self.its_riesz[1] * s
        pair = np.divide(
            pair,
            mag_v,
            out=np.zeros_like(pair),
            where=np.abs(mag_v) > 1e-8,
        )
        _patch_nans(pair, 0.0)
        self.its_lowpass = (self.its_lowpass * pd[0] - pair * pd[1]).astype(
            np.float32
        )


class RieszPyramid:
    """The complete pyramid."""

    def __init__(self) -> None:
        self.num_levels = 0
        self.pyr_levels: list[RieszPyramidLevel] = []
        self._low_pass = LOW_PASS_FILTER
        self._high_pass = HIGH_PASS_FILTER

    @staticmethod
    def _subsample(img: np.ndarray) -> np.ndarray:
        """Keep every other row and column (vectorised stride, was a double loop)."""
        return np.ascontiguousarray(img[::2, ::2], dtype=np.float32)

    @staticmethod
    def _inject_zeros_even(img: np.ndarray) -> np.ndarray:
        """Zero out the odd rows and columns (vectorised, was a double loop)."""
        tmp = np.zeros(img.shape[:2], dtype=np.float32)
        tmp[::2, ::2] = img[::2, ::2]
        return tmp

    def build_pyramid(self, frame: np.ndarray) -> None:
        max_idx = self.num_levels - 1
        if max_idx < 0:
            return
        octave = frame.astype(np.float32)
        for i in range(max_idx):
            hp = cv2.filter2D(
                octave, cv2.CV_32F, self._high_pass, borderType=cv2.BORDER_REFLECT101
            )
            self.pyr_levels[i].build(hp, i)
            lp = cv2.filter2D(
                octave,
                cv2.CV_32F,
                2.0 * self._low_pass,
                borderType=cv2.BORDER_REFLECT101,
            )
            octave = self._subsample(lp)
        self.pyr_levels[max_idx].build(octave, max_idx)

    def compute_phase_difference_and_amplitude(self, prior: RieszPyramid) -> None:
        for i in range(len(self.pyr_levels) - 1):
            self.pyr_levels[i].compute_phase_difference_and_amplitude(
                prior.pyr_levels[i]
            )

    def amplify(self, alpha: float, threshold: float) -> None:
        for i in range(self.num_levels - 2, -1, -1):
            self.pyr_levels[i].amplify(alpha, threshold)

    def collapse_pyramid(self) -> np.ndarray:
        count = len(self.pyr_levels) - 1
        result = self.pyr_levels[count].its_lowpass.copy()
        for i in range(count - 1, -1, -1):
            octave = self.pyr_levels[i].its_lowpass
            up = cv2.resize(
                result,
                (octave.shape[1], octave.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
            up_zero = self._inject_zeros_even(up)
            lp = cv2.filter2D(
                up_zero,
                cv2.CV_32F,
                2.0 * self._low_pass,
                borderType=cv2.BORDER_REFLECT101,
            )
            hp = cv2.filter2D(
                octave,
                cv2.CV_32F,
                self._high_pass,
                borderType=cv2.BORDER_REFLECT101,
            )
            result = lp + hp
        return result.astype(np.float32)

    def get_sizes(self) -> list[tuple[int, int]]:
        return [(pl.its_size[1], pl.its_size[0]) for pl in self.pyr_levels]

    def init(self, frame: np.ndarray, levels: int) -> None:
        self.num_levels = levels
        self.pyr_levels = [RieszPyramidLevel() for _ in range(levels)]
        self.build_pyramid(frame)
        for i in range(self.num_levels):
            rpl = self.pyr_levels[i]
            h, w = rpl.its_lowpass.shape[:2]
            z = np.zeros((h, w), np.float32)
            rpl.its_riesz = (z.copy(), z.copy())
            rpl.its_phase_diff = (z.copy(), z.copy())
            rpl.its_lowpass_iir = (z.copy(), z.copy())
            rpl.its_highpass_iir = (z.copy(), z.copy())
            rpl.its_amplitude = z.copy()
            rpl.its_amplitude_blurred = z.copy()

    def assign_from(self, other: RieszPyramid) -> None:
        self.num_levels = other.num_levels
        self.pyr_levels = []
        for ol in other.pyr_levels:
            nl = RieszPyramidLevel()
            nl.its_size = ol.its_size
            nl.its_lvl = ol.its_lvl
            nl.its_lowpass = ol.its_lowpass.copy()
            nl.its_riesz = (ol.its_riesz[0].copy(), ol.its_riesz[1].copy())
            nl.its_phase_diff = (
                ol.its_phase_diff[0].copy(),
                ol.its_phase_diff[1].copy(),
            )
            nl.its_highpass_iir = (
                ol.its_highpass_iir[0].copy(),
                ol.its_highpass_iir[1].copy(),
            )
            nl.its_lowpass_iir = (
                ol.its_lowpass_iir[0].copy(),
                ol.its_lowpass_iir[1].copy(),
            )
            nl.its_amplitude = ol.its_amplitude.copy()
            nl.its_amplitude_blurred = ol.its_amplitude_blurred.copy()
            self.pyr_levels.append(nl)
