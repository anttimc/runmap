"""Replaceable elevation smoothers with explicit physical parameters."""

import numpy as np
from scipy.fft import dctn, idctn
from scipy.ndimage import gaussian_filter

from runmap.terrain.derivatives import terrain_gradient


def _mask(elevation, valid):
    z = np.asarray(elevation, dtype=np.float64)
    mask = np.isfinite(z) if valid is None else np.asarray(valid, dtype=bool) & np.isfinite(z)
    if z.ndim != 2 or mask.shape != z.shape:
        raise ValueError("Expected matching 2D elevation and mask")
    return z, mask


def gaussian_smooth(elevation, sigma_m, resolution, valid=None, truncate=4.0):
    """Normalized Gaussian smoothing that does not treat invalid cells as zero."""
    z, mask = _mask(elevation, valid)
    if sigma_m < 0 or resolution <= 0:
        raise ValueError("sigma must be non-negative and resolution positive")
    if sigma_m == 0:
        return np.where(mask, z, np.nan)
    sigma_px = sigma_m / resolution
    weights = gaussian_filter(mask.astype(float), sigma_px, mode="nearest", truncate=truncate)
    values = gaussian_filter(np.where(mask, z, 0.0), sigma_px, mode="nearest", truncate=truncate)
    result = np.divide(values, weights, out=np.full_like(values, np.nan), where=weights > 1e-12)
    return np.where(mask, result, np.nan)


def thin_plate_smooth(elevation, half_amplitude_wavelength_m, resolution, valid=None):
    """Smooth a complete regular grid with a spectral thin-plate penalty.

    The returned surface minimizes a discrete analogue of data error plus
    ``lambda * integral(curvature**2)``, where

    ``lambda = (half_amplitude_wavelength_m / (2*pi))**4``.

    Thus a sinusoid at the requested wavelength is attenuated to roughly half
    amplitude (the equality is exact for the continuous operator). A DCT and
    the eigenvalues of the discrete Neumann Laplacian avoid periodic wrap at
    opposite raster edges. The best-fitting plane is removed and restored so
    the affine null space of a thin-plate penalty is preserved exactly.

    Missing cells break the spectral diagonalization and are currently
    rejected. Process a filled/complete rectangle or use another smoother.
    """
    return PreparedThinPlateSmoother(elevation, resolution, valid).smooth(
        half_amplitude_wavelength_m
    )


class PreparedThinPlateSmoother:
    """Reusable spectral thin-plate model for interactive parameter changes."""

    def __init__(self, elevation, resolution, valid=None):
        z, mask = _mask(elevation, valid)
        if resolution <= 0:
            raise ValueError("Resolution must be positive")
        if not mask.all():
            raise ValueError("Spectral thin-plate smoothing requires a complete grid")

        rows, cols = z.shape
        x = (np.arange(cols) - (cols - 1) / 2) * resolution
        y = (np.arange(rows) - (rows - 1) / 2) * resolution
        mean = float(np.mean(z))
        slope_x = float(np.sum(z * x[None, :]) / (rows * np.sum(x * x))) if cols > 1 else 0.0
        slope_y = float(np.sum(z * y[:, None]) / (cols * np.sum(y * y))) if rows > 1 else 0.0
        self._trend = mean + slope_x * x[None, :] + slope_y * y[:, None]
        self._source = z.copy()
        self._spectrum = dctn(z - self._trend, norm="ortho")

        eigen_x = 4 * np.sin(np.pi * np.arange(cols) / (2 * cols)) ** 2 / resolution**2
        eigen_y = 4 * np.sin(np.pi * np.arange(rows) / (2 * rows)) ** 2 / resolution**2
        self._biharmonic = (eigen_y[:, None] + eigen_x[None, :]) ** 2

    def smooth(self, half_amplitude_wavelength_m):
        """Return a surface while reusing the prepared DCT and eigenvalues."""
        wavelength = float(half_amplitude_wavelength_m)
        if not np.isfinite(wavelength) or wavelength < 0:
            raise ValueError("Wavelength must be finite and non-negative")
        if wavelength == 0:
            return self._source.copy()
        penalty = (wavelength / (2 * np.pi)) ** 4
        response = 1.0 / (1.0 + penalty * self._biharmonic)
        return idctn(self._spectrum * response, norm="ortho") + self._trend


def nonlinear_diffusion(elevation, resolution, iterations, time_step=0.2,
                        kappa=0.08, guide_sigma_m=2.0, valid=None):
    """Regularized Perona–Malik diffusion using a conservative 4-neighbour flux.

    ``kappa`` is the guide-gradient scale in m/m. ``time_step`` is expressed
    in pixel-time units and must be <= 0.25 for this explicit stencil.
    Invalid cells are impermeable boundaries.
    """
    z, mask = _mask(elevation, valid)
    if resolution <= 0 or iterations < 0 or not (0 < time_step <= 0.25) or kappa <= 0:
        raise ValueError("Invalid nonlinear diffusion parameters")
    u = np.where(mask, z, 0.0).copy()
    for _ in range(iterations):
        guide = gaussian_smooth(u, guide_sigma_m, resolution, mask)
        gx, gy, _ = terrain_gradient(guide, resolution, resolution, mask)
        conduction = 1.0 / (1.0 + (np.hypot(gx, gy) / kappa) ** 2)
        conduction = np.where(mask & np.isfinite(conduction), conduction, 0.0)
        update = np.zeros_like(u)
        # Each pair contributes equal and opposite flux, conserving elevation.
        pair = mask[:, :-1] & mask[:, 1:]
        c = 0.5 * (conduction[:, :-1] + conduction[:, 1:])
        flux = np.where(pair, c * (u[:, 1:] - u[:, :-1]), 0.0)
        update[:, :-1] += flux
        update[:, 1:] -= flux
        pair = mask[:-1, :] & mask[1:, :]
        c = 0.5 * (conduction[:-1, :] + conduction[1:, :])
        flux = np.where(pair, c * (u[1:, :] - u[:-1, :]), 0.0)
        update[:-1, :] += flux
        update[1:, :] -= flux
        u += time_step * update
    return np.where(mask, u, np.nan)
