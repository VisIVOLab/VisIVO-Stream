import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

from app.datasets.fits_metadata import extract_fits_metadata
from app.datasets.fits_reader import FitsReaderError, read_fits_data


class FitsSupportTests(unittest.TestCase):
    def _write_fits(self, array: np.ndarray, suffix: str = ".fits") -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / f"sample{suffix}"
        fits.PrimaryHDU(array).writeto(path)
        return path

    def test_read_fits_2d(self):
        path = self._write_fits(np.arange(9, dtype=np.float32).reshape(3, 3))
        result = read_fits_data(path)
        self.assertEqual(result.naxis, 2)
        self.assertEqual(result.shape, (3, 3))
        self.assertEqual(result.array.dtype, np.float32)

    def test_read_fits_3d(self):
        path = self._write_fits(np.arange(24, dtype=np.float32).reshape(2, 3, 4))
        result = read_fits_data(path)
        self.assertEqual(result.naxis, 3)
        self.assertEqual(result.shape, (2, 3, 4))

    def test_read_fits_4d_uses_first_frame(self):
        array = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
        path = self._write_fits(array)
        result = read_fits_data(path)
        self.assertEqual(result.naxis, 4)
        self.assertEqual(result.selected_frame, 0)
        self.assertEqual(result.shape, (3, 4, 5))
        self.assertTrue(np.array_equal(result.raw_array, array[0].astype(np.float32)))

    def test_metadata_ignores_nan_and_inf(self):
        array = np.array([[1.0, np.nan], [np.inf, 3.0]], dtype=np.float32)
        path = self._write_fits(array)
        metadata = extract_fits_metadata(path)
        self.assertEqual(metadata.stats["min"], 1.0)
        self.assertEqual(metadata.stats["max"], 3.0)
        self.assertEqual(metadata.stats["mean"], 2.0)
        self.assertAlmostEqual(metadata.stats["rms"], np.sqrt(5.0), places=6)

    def test_invalid_naxis_raises(self):
        path = self._write_fits(np.zeros((1, 1, 1, 1, 1), dtype=np.float32))
        with self.assertRaises(FitsReaderError):
            read_fits_data(path)


if __name__ == "__main__":
    unittest.main()
