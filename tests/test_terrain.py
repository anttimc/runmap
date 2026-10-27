"""Verify terrain derivatives, smoothing behavior, and missing-data handling."""

import unittest

import numpy as np

from runmap.terrain.derivatives import terrain_gradient, uphill_bearing_degrees
from runmap.terrain.smoothing import (
    PreparedThinPlateSmoother,
    gaussian_smooth,
    nonlinear_diffusion,
    thin_plate_smooth,
)


class TerrainTests(unittest.TestCase):
    def test_tilted_plane_gradient_and_bearing(self):
        resolution = 2.0
        north = np.arange(20, 0, -1)[:, None] * resolution
        east = np.arange(30)[None, :] * resolution
        z = 0.03 * east + 0.04 * north
        gx, gy, magnitude = terrain_gradient(z, resolution, resolution)
        self.assertTrue(np.allclose(gx, 0.03))
        self.assertTrue(np.allclose(gy, 0.04))
        self.assertTrue(np.allclose(magnitude, 0.05))
        expected = np.degrees(np.arctan2(0.03, 0.04))
        self.assertTrue(np.allclose(uphill_bearing_degrees(gx, gy), expected))

    def test_smoothers_preserve_a_plane_away_from_boundaries(self):
        y, x = np.mgrid[0:80, 0:90]
        z = 10 + 0.1 * x + 0.2 * y
        for result in (gaussian_smooth(z, 4, 2),
                       nonlinear_diffusion(z, 2, 10, kappa=0.2, guide_sigma_m=2),
                       thin_plate_smooth(z, 20, 2)):
            self.assertLess(np.max(np.abs(result[12:-12, 12:-12] - z[12:-12, 12:-12])), 1e-5)

    def test_thin_plate_preserves_a_plane_including_boundaries(self):
        y, x = np.mgrid[0:31, 0:40]
        z = 3.0 + 0.12 * x - 0.07 * y
        result = thin_plate_smooth(z, 24, 2)
        self.assertTrue(np.allclose(result, z, atol=1e-12))

    def test_thin_plate_reduces_shorter_wavelengths_more(self):
        size = 128
        x = np.arange(size)
        long_wave = np.cos(2 * np.pi * (x + 0.5) / size)
        short_wave = np.cos(16 * np.pi * (x + 0.5) / size)
        z = np.tile(long_wave + short_wave, (32, 1))
        result = thin_plate_smooth(z, 32, 2)
        long_amplitude = 2 * np.mean(result * long_wave[None, :])
        short_amplitude = 2 * np.mean(result * short_wave[None, :])
        self.assertGreater(long_amplitude, 0.95)
        self.assertAlmostEqual(short_amplitude, 0.5, delta=0.02)

    def test_thin_plate_rejects_missing_cells(self):
        z = np.ones((10, 10))
        z[4, 4] = np.nan
        with self.assertRaisesRegex(ValueError, "complete grid"):
            thin_plate_smooth(z, 20, 2)

    def test_prepared_thin_plate_matches_one_shot_smoothing(self):
        rng = np.random.default_rng(7)
        z = rng.normal(size=(31, 40))
        prepared = PreparedThinPlateSmoother(z, 2)
        for wavelength in (0, 20, 40, 80):
            self.assertTrue(np.allclose(
                prepared.smooth(wavelength), thin_plate_smooth(z, wavelength, 2)
            ))

    def test_invalid_cells_remain_invalid(self):
        z = np.ones((20, 20))
        mask = np.ones_like(z, dtype=bool)
        mask[8:12, 8:12] = False
        for result in (gaussian_smooth(z, 2, 2, mask),
                       nonlinear_diffusion(z, 2, 2, valid=mask)):
            self.assertTrue(np.isnan(result[~mask]).all())
            self.assertTrue(np.allclose(result[mask], 1))


if __name__ == "__main__":
    unittest.main()
