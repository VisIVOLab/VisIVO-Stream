import logging
from pathlib import Path

import numpy as np

from app.datasets.fits_reader import SCALAR_NAME, FitsReadResult, read_fits_data
from app.models.dataset import DatasetMetadata


logger = logging.getLogger(__name__)


def _compute_stats(result: FitsReadResult) -> dict[str, float]:
    finite_mask = np.isfinite(result.raw_array)
    finite_values = result.raw_array[finite_mask]
    if finite_values.size == 0:
        raise ValueError(f"FITS dataset '{result.path.name}' contains no finite values")

    stats = {
        "min": float(np.min(finite_values)),
        "max": float(np.max(finite_values)),
        "mean": float(np.mean(finite_values)),
        "rms": float(np.sqrt(np.mean(np.square(finite_values)))),
        "p50": float(np.percentile(finite_values, 50)),
        "p75": float(np.percentile(finite_values, 75)),
        "p90": float(np.percentile(finite_values, 90)),
        "p95": float(np.percentile(finite_values, 95)),
        "p99": float(np.percentile(finite_values, 99)),
    }
    logger.info("Computed FITS stats for %s: %s", result.path.name, stats)
    return stats


def extract_fits_metadata(path: str | Path, frame_index: int = 0) -> DatasetMetadata:
    result = read_fits_data(path, frame_index=frame_index)
    metadata = DatasetMetadata(
        dataset_id=result.path.stem,
        dataset_type="fits",
        scalar_name=SCALAR_NAME,
        shape=list(result.shape),
        naxis=result.naxis,
        dtype_original=result.dtype_original,
        stats=_compute_stats(result),
        header=result.header,
        extra={"selected_frame": result.selected_frame},
    )
    return metadata


def extract_fits_header(path: str | Path, frame_index: int = 0) -> dict[str, int | float | str | bool | None]:
    return extract_fits_metadata(path, frame_index=frame_index).header
