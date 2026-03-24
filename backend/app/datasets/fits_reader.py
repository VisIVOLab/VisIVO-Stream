import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.pythonpath import bootstrap_external_site_packages

bootstrap_external_site_packages()

from astropy.io import fits


logger = logging.getLogger(__name__)

SCALAR_NAME = "FITSImage"


class FitsReaderError(ValueError):
    pass


@dataclass
class FitsReadResult:
    path: Path
    array: np.ndarray
    raw_array: np.ndarray
    original_shape: tuple[int, ...]
    shape: tuple[int, ...]
    naxis: int
    selected_frame: int | None
    dtype_original: str
    header: dict[str, int | float | str | bool | None]


def _header_subset(header) -> dict[str, int | float | str | bool | None]:
    values: dict[str, int | float | str | bool | None] = {}
    for key in ("BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "NAXIS4"):
        if key in header:
            values[key] = header[key]
    return values


def read_fits_data(path: str | Path, frame_index: int = 0) -> FitsReadResult:
    file_path = Path(path)
    logger.info("Opening FITS file: %s", file_path)

    if file_path.suffix.lower() not in {".fits", ".fit"}:
        raise FitsReaderError(f"Unsupported FITS extension for '{file_path.name}'")

    try:
        with fits.open(file_path, memmap=False) as hdul:
            data = hdul[0].data
            header = hdul[0].header.copy()
    except OSError as exc:
        raise FitsReaderError(f"Failed to open FITS file '{file_path}': {exc}") from exc

    if data is None:
        raise FitsReaderError(f"FITS dataset '{file_path.name}' is empty")

    original = np.asarray(data)
    naxis = int(original.ndim)
    if naxis not in {2, 3, 4}:
        raise FitsReaderError(f"Unsupported FITS NAXIS={naxis} for '{file_path.name}'. Supported: 2, 3, 4.")

    selected_frame = None
    if naxis == 4:
        if not 0 <= frame_index < original.shape[0]:
            raise FitsReaderError(f"Frame index {frame_index} out of range for shape {original.shape}")
        logger.info("Using FITS frame %s from 4D dataset", frame_index)
        selected = original[frame_index]
        selected_frame = frame_index
    else:
        selected = original

    if selected.size == 0:
        raise FitsReaderError(f"FITS dataset '{file_path.name}' contains no samples")

    raw_array = np.asarray(selected, dtype=np.float32)
    sanitized = np.nan_to_num(raw_array, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)

    logger.info(
        "Loaded FITS dataset %s with naxis=%s original_shape=%s selected_shape=%s frame=%s",
        file_path.name,
        naxis,
        tuple(int(v) for v in original.shape),
        tuple(int(v) for v in sanitized.shape),
        selected_frame,
    )

    return FitsReadResult(
        path=file_path,
        array=sanitized,
        raw_array=raw_array,
        original_shape=tuple(int(v) for v in original.shape),
        shape=tuple(int(v) for v in sanitized.shape),
        naxis=naxis,
        selected_frame=selected_frame,
        dtype_original=str(original.dtype),
        header=_header_subset(header),
    )
