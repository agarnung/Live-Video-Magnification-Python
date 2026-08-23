"""Constants mirroring Config.h from the original C++ project."""

PROCESSING_FPS_STAT_QUEUE_LENGTH = 32
CAPTURE_FPS_STAT_QUEUE_LENGTH = 32

DEFAULT_IMAGE_BUFFER_SIZE = 1
DEFAULT_DROP_FRAMES = False

DEFAULT_COL_MAG_LEVELS = 3
DEFAULT_LAP_MAG_EXAGGERATION = 2.0
DEFAULT_LAP_MAG_LEVELS = 4

DEFAULT_GRAYSCALE = False
DEFAULT_MAGNIFY_TYPE = 0

DEFAULT_CM_AMPLIFICATION = 100
DEFAULT_CM_COWAVELENGTH = 1000
DEFAULT_CM_COLOW = 0.84
DEFAULT_CM_COHIGH = 1.43
DEFAULT_CM_CHROMATTENUATION = 0

# Motion (Laplacian) mode. The cutoffs are HERTZ, like every other mode.
#
# The older reference implementation stored raw IIR blend coefficients here
# (20.0 and 40.0), which are not valid coefficients at all: a blend coefficient
# must lie in [0, 1).  The filter computes (1 - cutoff), so 20.0 and 40.0 give
# gains of -19 and -39; both low-pass states then alternate in sign and grow
# without bound (measured: +-3.5e9 after six iterations).  Their difference --
# the motion signal -- collapses to ~2e-5, so the mode amplifies nothing.
# Upstream fixed this by keeping the UI in Hz and converting to a blend
# coefficient internally; see `motion_hz_to_blend` below.
DEFAULT_MM_AMPLIFICATION = 20
DEFAULT_MM_COWAVELENGTH = 50
DEFAULT_MM_COLOW = 1.0    # Hz
DEFAULT_MM_COHIGH = 2.5   # Hz
DEFAULT_MM_CHROMATTENUATION = 0

# Phase (Riesz) mode.
DEFAULT_PB_AMPLIFICATION = 50
DEFAULT_PB_COWAVELENGTH = 50
DEFAULT_PB_COLOW = 1.0    # Hz
DEFAULT_PB_COHIGH = 5.0   # Hz

DEFAULT_CAPTURE_FPS = 30.0  # fallback when the source reports no frame rate

# Viewer ROI: minimum size in pixels of the source frame (pyramids / OpenCV).
MIN_ROI_SIDE = 96

# Processing resolution divisors offered by the UI (1/1 .. 1/8), as upstream.
PROCESSING_SCALES = (1, 2, 4, 8)

_TWO_PI = 6.283185307179586


def motion_hz_to_blend(hz: float, fps: float) -> float:
    """
    Convert a cutoff in Hz to the IIR blend coefficient of the Laplacian mode.

    The temporal filter is a pair of exponential moving averages, whose cutoff
    relates to the blend coefficient by ``a = 1 - exp(-2*pi*fc/fps)``.  Keeping
    the UI in Hz and converting here means the same number means the same thing
    in every mode, and the coefficient stays strictly inside [0, 1) so the
    recursion cannot diverge.
    """
    if fps <= 0.0:
        fps = DEFAULT_CAPTURE_FPS
    if hz <= 0.0:
        return 0.0
    import math

    a = 1.0 - math.exp(-_TWO_PI * hz / fps)
    # Strictly below 1 so that (1 - a) remains a valid low-pass gain.
    return min(max(a, 0.0), 0.999999)


def motion_blend_to_hz(blend: float, fps: float) -> float:
    """Inverse of :func:`motion_hz_to_blend`, for populating the UI."""
    if fps <= 0.0:
        fps = DEFAULT_CAPTURE_FPS
    blend = min(max(blend, 0.0), 0.999999)
    if blend <= 0.0:
        return 0.0
    import math

    return -(fps / _TWO_PI) * math.log(1.0 - blend)
