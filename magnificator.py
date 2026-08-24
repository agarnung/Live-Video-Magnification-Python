"""Colour, Laplacian and Riesz magnification (Magnificator.cpp)."""

from __future__ import annotations

import math
import cv2
import numpy as np

from config import (
    DEFAULT_CAPTURE_FPS,
    DEFAULT_LAP_MAG_EXAGGERATION,
    motion_hz_to_blend,
)
from riesz_pyramid import RieszPyramid
from spatial_filter import (
    build_gauss_pyr_from_img,
    build_img_from_gauss_pyr,
    build_img_from_laplace_pyr,
    build_laplace_pyr_from_img,
)
from structures import ImageProcessingFlags, ImageProcessingSettings
from temporal_filter import (
    RieszTemporalFilter,
    ideal_filter,
    img2temp_mat,
    iir_filter,
    temp_mat2img,
)


class Magnificator:
    """Processes frame buffers and produces magnified frames."""

    def __init__(
        self,
        processing_buffer: list[np.ndarray],
        img_proc_flags: ImageProcessingFlags,
        img_proc_settings: ImageProcessingSettings,
    ) -> None:
        self._processing_buffer = processing_buffer
        self._flags = img_proc_flags
        self._settings = img_proc_settings
        self._levels = 4
        self._exaggeration = 2.0
        self._lambda = 0.0
        self._delta = 0.0
        self._current_frame = 0
        self._magnified_buffer: list[np.ndarray] = []
        self._motion_pyramid: list[np.ndarray] = []
        self._lowpass_hi: list[np.ndarray] = []
        self._lowpass_lo: list[np.ndarray] = []
        self._downsampled_mat: np.ndarray | None = None
        self._old_pyr: RieszPyramid | None = None
        self._cur_pyr: RieszPyramid | None = None
        self._lo_cutoff: RieszTemporalFilter | None = None
        self._hi_cutoff: RieszTemporalFilter | None = None

    def calculate_max_levels(self, width: int, height: int) -> int:
        if width > 5 and height > 5:
            return 1 + self.calculate_max_levels(
                (1 + width) // 2, (1 + height) // 2
            )
        return 0

    def get_optimal_buffer_size(self, fps: float) -> int:
        r = int(max(2 * fps, 16))
        r -= 1
        r |= r >> 1
        r |= r >> 2
        r |= r >> 4
        r |= r >> 8
        r |= r >> 16
        r += 1
        return r

    def clear_buffer(self) -> None:
        self._magnified_buffer.clear()
        self._lowpass_hi.clear()
        self._lowpass_lo.clear()
        self._motion_pyramid.clear()
        self._downsampled_mat = None
        self._current_frame = 0
        self._old_pyr = None
        self._cur_pyr = None
        self._lo_cutoff = None
        self._hi_cutoff = None

    def color_magnify(self) -> None:
        buf = self._processing_buffer
        n = len(buf)
        if self._current_frame >= n:
            return
        self._levels = self._settings.levels
        input_frames: list[np.ndarray] = []
        offset = 0
        p_channels = 0
        ds_size: tuple[int, int] = (0, 0)
        ds_channels = 1

        while self._current_frame < n:
            inp = buf.pop(0)
            p_channels = inp.shape[2] if inp.ndim == 3 else 1
            # "Grayscale" checkbox: when unchecked and the frame is BGR,
            # magnify in colour. The original C++ decides this from FC1/FC3;
            # here we follow the UI instead.
            want_bgr = (not self._flags.grayscale_on) and p_channels >= 3
            if want_bgr:
                inp_f = inp[:, :, :3].astype(np.float32)
            elif inp.ndim == 3 and p_channels >= 3:
                inp_f = cv2.cvtColor(inp, cv2.COLOR_BGR2GRAY).astype(np.float32)
            else:
                inp_f = inp.astype(np.float32)
                if inp_f.ndim == 3:
                    inp_f = cv2.cvtColor(inp_f, cv2.COLOR_BGR2GRAY)
            input_frames.append(inp_f)

            pyr = build_gauss_pyr_from_img(inp_f, self._levels)
            down = pyr[self._levels - 1]
            dh, dw = down.shape[:2]
            ds_channels = 1 if down.ndim == 2 else down.shape[2]
            ds_size = (dw, dh)
            max_img = self.get_optimal_buffer_size(self._settings.framerate or 30.0)
            self._downsampled_mat = img2temp_mat(
                down, self._downsampled_mat, max_img
            )
            self._current_frame += 1
            offset += 1

        fps = self._settings.framerate or 30.0
        filtered = ideal_filter(
            self._downsampled_mat,
            self._settings.co_low,
            self._settings.co_high,
            fps,
        )
        filtered = filtered * float(self._settings.amplification)

        start_col = self._current_frame - offset
        h, w = input_frames[0].shape[:2]
        out_is_bgr = input_frames[0].ndim == 3 and input_frames[0].shape[2] >= 3
        for j in range(offset):
            col_idx = start_col + j
            filt_frame = temp_mat2img(
                filtered, col_idx, ds_size, ds_channels
            )
            color = build_img_from_gauss_pyr(filt_frame, self._levels, (w, h))
            base = input_frames[j].astype(np.float32)
            out = base + color
            out_min, out_max = float(out.min()), float(out.max())
            scale = 255.0 / (out_max - out_min) if out_max > out_min else 1.0
            scaled = ((out - out_min) * scale).clip(0, 255).astype(np.uint8)
            if out_is_bgr:
                self._magnified_buffer.append(scaled)
            else:
                gray = scaled.squeeze() if scaled.ndim == 3 else scaled
                self._magnified_buffer.append(
                    cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                )

    def _amplify_laplacian(self, src: np.ndarray, current_level: int) -> np.ndarray:
        lam = self._lambda
        delta = self._delta
        amp = float(self._settings.amplification)
        exag = self._exaggeration
        curr_alpha = (lam / (delta * 8.0) - 1.0) * exag
        if current_level == self._levels or current_level == 0:
            return src * 0
        return src * min(amp, curr_alpha)

    def _attenuate(self, src: np.ndarray) -> np.ndarray:
        if src.ndim != 3 or src.shape[2] < 3:
            return src
        planes = list(cv2.split(src))
        ca = float(self._settings.chrom_attenuation)
        planes[1] = (planes[1] * ca).astype(np.float32)
        planes[2] = (planes[2] * ca).astype(np.float32)
        return cv2.merge(planes)

    def laplace_magnify(self) -> None:
        buf = self._processing_buffer
        n = len(buf)
        if self._current_frame >= n:
            return
        self._levels = self._settings.levels

        while self._current_frame < n:
            if self._current_frame > 0:
                buf.pop(0)
            inp = buf[0].copy()
            p_channels = inp.shape[2] if inp.ndim == 3 else 1
            gray_mode = self._flags.grayscale_on or p_channels <= 2
            if not gray_mode:
                f = inp.astype(np.float32) / 255.0
                f = cv2.cvtColor(f, cv2.COLOR_BGR2LAB)
                # cv2's float LAB puts L in [0, 100] and a/b roughly in
                # [-127, 127], while every amplification constant here
                # (curr_alpha, delta, lambda) is tuned assuming pixel values in
                # [0, 1] -- the same scale the BGR and grayscale paths use.
                # Left as-is, a motion term of magnitude ~1-4 is a 1-4% change
                # on L's 0-100 scale but was calibrated to read as a 100-400%
                # change on a 0-1 scale, so the visible effect was throttled by
                # roughly two orders of magnitude. Normalising L to [0, 1] here
                # (and undoing it below) restores the intended amplitude.
                f[..., 0] /= 100.0
            else:
                f = inp.astype(np.float32) / 255.0
                if f.ndim == 2:
                    f = f[:, :, np.newaxis]

            pyr = build_laplace_pyr_from_img(
                f.squeeze() if f.shape[2] == 1 else f, self._levels
            )

            if self._current_frame == 0:
                self._lowpass_hi = [x.copy() for x in pyr]
                self._lowpass_lo = [x.copy() for x in pyr]
                self._motion_pyramid = [x.copy() for x in pyr]
            else:
                # co_low/co_high are Hz; the IIR pair consumes blend
                # coefficients, so convert here (see config.motion_hz_to_blend).
                fps = self._settings.framerate or DEFAULT_CAPTURE_FPS
                blend_lo = motion_hz_to_blend(self._settings.co_low, fps)
                blend_hi = motion_hz_to_blend(self._settings.co_high, fps)
                for lev in range(self._levels):
                    dst, lhi, llo = iir_filter(
                        pyr[lev],
                        self._motion_pyramid[lev],
                        self._lowpass_hi[lev],
                        self._lowpass_lo[lev],
                        blend_lo,
                        blend_hi,
                    )
                    self._motion_pyramid[lev] = dst
                    self._lowpass_hi[lev] = lhi
                    self._lowpass_lo[lev] = llo

                h, w = inp.shape[:2]
                self._delta = float(self._settings.co_wavelength) / (
                    8.0 * (1.0 + float(self._settings.amplification))
                )
                self._exaggeration = DEFAULT_LAP_MAG_EXAGGERATION
                lam = math.sqrt(w * w + h * h) / 3.0
                for lev in range(self._levels, -1, -1):
                    self._lambda = lam
                    self._motion_pyramid[lev] = self._amplify_laplacian(
                        self._motion_pyramid[lev], lev
                    )
                    lam /= 2.0

            motion = build_img_from_laplace_pyr(self._motion_pyramid, self._levels)
            motion = self._attenuate(motion)
            f32 = f.astype(np.float32)
            if gray_mode:
                f32 = np.squeeze(f32, axis=-1) if f32.ndim == 3 and f32.shape[2] == 1 else f32
                if motion.ndim == 3 and motion.shape[2] == 1:
                    motion = np.squeeze(motion, axis=-1)
            if self._current_frame > 0:
                out = f32 + motion
            else:
                out = f32

            if not gray_mode:
                # Undo the /100 normalisation applied to L on the way in, so
                # cv2 sees the [0, 100] scale it expects for LAB->BGR.
                out[..., 0] *= 100.0
                out = cv2.cvtColor(out, cv2.COLOR_LAB2BGR)
                out = (out * 255.0).clip(0, 255).astype(np.uint8)
            else:
                out = (np.squeeze(out) * 255.0).clip(0, 255).astype(np.uint8)

            self._magnified_buffer.append(out)
            self._current_frame += 1

    def riesz_magnify(self) -> None:
        buf = self._processing_buffer
        n = len(buf)
        if self._current_frame >= n:
            return
        self._levels = self._settings.levels
        pi_pct = math.pi / 100.0

        while self._current_frame < n:
            if self._current_frame > 0:
                buf.pop(0)
            buffer_in = buf[0].copy()

            p_channels = buffer_in.shape[2] if buffer_in.ndim == 3 else 1
            gray_mode = self._flags.grayscale_on or p_channels <= 2

            if p_channels > 1 and not gray_mode:
                lab = cv2.cvtColor(
                    buffer_in.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB
                )
                l_ch, a_ch, b_ch = cv2.split(lab)
                # See the matching comment in laplace_magnify: cv2's L channel
                # is [0, 100], but the Riesz amplitude/phase machinery and its
                # temporal filters are calibrated for pixel values in [0, 1].
                # Left unnormalised, the recovered motion signal is throttled
                # by two orders of magnitude before it ever reaches amplitude.
                l_ch = l_ch / 100.0
                input_l = l_ch
                channels_lab = (l_ch, a_ch, b_ch)
            elif p_channels > 1:
                input_l = cv2.cvtColor(buffer_in, cv2.COLOR_BGR2GRAY).astype(
                    np.float32
                ) / 255.0
                channels_lab = None
            else:
                input_l = buffer_in.astype(np.float32) / 255.0
                channels_lab = None

            need_reset = (
                self._current_frame == 0
                or self._lo_cutoff is None
                or self._hi_cutoff is None
            )

            if need_reset:
                self._cur_pyr = RieszPyramid()
                self._old_pyr = RieszPyramid()
                self._cur_pyr.init(input_l, self._levels)
                self._old_pyr.init(input_l, self._levels)
                sizes = self._cur_pyr.get_sizes()
                fps = self._settings.framerate or 30.0
                self._lo_cutoff = RieszTemporalFilter(
                    self._settings.co_low, fps, sizes
                )
                self._hi_cutoff = RieszTemporalFilter(
                    self._settings.co_high, fps, sizes
                )
                output = buffer_in.copy()
            else:
                assert self._cur_pyr is not None and self._old_pyr is not None
                assert self._lo_cutoff is not None and self._hi_cutoff is not None
                if self._lo_cutoff.its_frequency != self._settings.co_low:
                    self._lo_cutoff.update_frequency(self._settings.co_low)
                    self._lo_cutoff.reset_mat()
                    self._hi_cutoff.reset_mat()
                    self._old_pyr.build_pyramid(input_l)
                if self._hi_cutoff.its_frequency != self._settings.co_high:
                    self._hi_cutoff.update_frequency(self._settings.co_high)
                    self._hi_cutoff.reset_mat()
                    self._lo_cutoff.reset_mat()
                    self._old_pyr.build_pyramid(input_l)

                self._cur_pyr.build_pyramid(input_l)
                self._cur_pyr.compute_phase_difference_and_amplitude(self._old_pyr)

                for lvl in range(self._cur_pyr.num_levels - 1):
                    pl = self._cur_pyr.pyr_levels[lvl]
                    holder_lo: list = [pl.its_lowpass_iir]
                    self._lo_cutoff.iir_temporal_filter(
                        holder_lo, pl.its_phase_diff, lvl
                    )
                    pl.its_lowpass_iir = holder_lo[0]
                    holder_hi: list = [pl.its_highpass_iir]
                    self._hi_cutoff.iir_temporal_filter(
                        holder_hi, pl.its_phase_diff, lvl
                    )
                    pl.its_highpass_iir = holder_hi[0]

                self._old_pyr.assign_from(self._cur_pyr)

                self._cur_pyr.amplify(
                    float(self._settings.amplification),
                    float(self._settings.co_wavelength) * pi_pct,
                )

                if self._current_frame != 0:
                    magnified = self._cur_pyr.collapse_pyramid()
                else:
                    magnified = input_l

                if channels_lab is not None:
                    # Undo the /100 normalisation applied when input_l was
                    # built, so L is back on cv2's [0, 100] scale to match a/b
                    # before the LAB->BGR conversion.
                    l_out = magnified * 100.0
                    merged = cv2.merge(
                        (
                            l_out,
                            channels_lab[1],
                            channels_lab[2],
                        )
                    )
                    output = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
                    output = (output * 255.0).clip(0, 255).astype(np.uint8)
                else:
                    output = (magnified * 255.0).clip(0, 255).astype(np.uint8)

            self._magnified_buffer.append(output)
            self._current_frame += 1

    def get_frame_last(self) -> np.ndarray:
        img = self._magnified_buffer[-1].copy()
        del self._magnified_buffer[0]
        self._current_frame = len(self._magnified_buffer)
        return img

    def has_frame(self) -> bool:
        return len(self._magnified_buffer) > 0

    def get_buffer_size(self) -> int:
        return len(self._magnified_buffer)
