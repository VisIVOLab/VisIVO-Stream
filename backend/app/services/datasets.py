import csv
import logging
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings
from app.datasets.fits_metadata import extract_fits_header, extract_fits_metadata
from app.models.dataset import (
    DatasetLoadResponse,
    DatasetMetadata,
    DatasetRecord,
    FileBrowserEntry,
    FileBrowserResponse,
)


logger = logging.getLogger(__name__)


class DatasetCatalog:
    SUPPORTED_RUNTIME_SUFFIXES = {".fits", ".fit"}

    def __init__(
        self,
        base_dir: Path,
        runtime_upload_dir: Path,
        max_upload_size_mb: int,
        remote_data_root: Path,
        remote_browser_show_hidden: bool,
    ) -> None:
        self.base_dir = base_dir
        self.runtime_upload_dir = runtime_upload_dir
        self.max_upload_size_bytes = max_upload_size_mb * 1024 * 1024
        self.remote_data_root = remote_data_root.resolve()
        self.remote_browser_show_hidden = remote_browser_show_hidden
        self._registered_runtime_paths: dict[str, Path] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_upload_dir.mkdir(parents=True, exist_ok=True)
        self.remote_data_root.mkdir(parents=True, exist_ok=True)
        self._datasets: dict[str, DatasetRecord] = {}
        self.refresh()

    def refresh(self) -> None:
        self._datasets = self._build_catalog()

    def _build_catalog(self) -> dict[str, DatasetRecord]:
        datasets: dict[str, DatasetRecord] = {
            "galaxy_points": DatasetRecord(
                id="galaxy_points",
                name="Galaxy Points",
                description="Synthetic point cloud with density values inspired by galaxy distributions.",
                file_name="galaxy_points.csv",
                path=self.base_dir / "galaxy_points.csv",
                dataset_type="csv",
                origin="sample",
                uploaded=False,
                scalar_fields=["density"],
                point_count_hint=16,
            ),
            "wave_points": DatasetRecord(
                id="wave_points",
                name="Wave Points",
                description="Structured scientific point cloud with scalar amplitude sampled over a wave field.",
                file_name="wave_points.csv",
                path=self.base_dir / "wave_points.csv",
                dataset_type="csv",
                origin="sample",
                uploaded=False,
                scalar_fields=["density"],
                point_count_hint=25,
            ),
        }

        self._append_fits_from_dir(datasets, self.base_dir, origin="sample")
        self._append_fits_from_dir(datasets, self.runtime_upload_dir, origin="upload")
        self._append_registered_runtime_paths(datasets)
        return datasets

    def _append_registered_runtime_paths(self, datasets: dict[str, DatasetRecord]) -> None:
        for dataset_id, path in sorted(self._registered_runtime_paths.items()):
            if not path.exists() or path.suffix.lower() not in self.SUPPORTED_RUNTIME_SUFFIXES:
                continue
            datasets[dataset_id] = DatasetRecord(
                id=dataset_id,
                name=path.stem.replace("_", " ").title(),
                description="FITS scientific dataset selected from the remote server filesystem.",
                file_name=path.name,
                path=path,
                dataset_type="fits",
                origin="upload",
                uploaded=False,
                scalar_fields=["FITSImage"],
                point_count_hint=0,
            )

    def _append_fits_from_dir(self, datasets: dict[str, DatasetRecord], directory: Path, origin: str) -> None:
        for path in sorted(directory.glob("*")):
            if path.suffix.lower() not in self.SUPPORTED_RUNTIME_SUFFIXES:
                continue
            dataset_id = self._unique_dataset_id(path.stem, datasets)
            datasets[dataset_id] = DatasetRecord(
                id=dataset_id,
                name=path.stem.replace("_", " ").title(),
                description="FITS scientific dataset loaded through astropy and exposed as vtkImageData.",
                file_name=path.name,
                path=path,
                dataset_type="fits",
                origin="upload" if origin == "upload" else "sample",
                uploaded=origin == "upload",
                scalar_fields=["FITSImage"],
                point_count_hint=0,
            )

    def _unique_dataset_id(self, base_name: str, datasets: dict[str, DatasetRecord]) -> str:
        candidate = self._sanitize_dataset_id(base_name)
        if candidate not in datasets:
            return candidate
        suffix = 2
        while f"{candidate}_{suffix}" in datasets:
            suffix += 1
        return f"{candidate}_{suffix}"

    def _sanitize_dataset_id(self, value: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
        return sanitized or "dataset"

    def list(self) -> list[DatasetRecord]:
        self.refresh()
        return list(self._datasets.values())

    def get(self, dataset_id: str) -> DatasetRecord:
        self.refresh()
        if dataset_id not in self._datasets:
            raise KeyError(dataset_id)
        return self._datasets[dataset_id]

    def metadata(self, dataset_id: str) -> DatasetMetadata:
        dataset = self.get(dataset_id)
        if dataset.dataset_type == "fits":
            metadata = extract_fits_metadata(dataset.path)
            metadata.dataset_id = dataset.id
            metadata.extra.update(
                {
                    "origin": dataset.origin,
                    "uploaded": dataset.uploaded,
                    "path": str(dataset.path),
                    "file_name": dataset.file_name,
                }
            )
            return metadata
        return self._csv_metadata(dataset)

    def fits_header(self, dataset_id: str) -> dict:
        dataset = self.get(dataset_id)
        if dataset.dataset_type != "fits":
            raise ValueError(f"Dataset '{dataset_id}' is not a FITS dataset")
        return extract_fits_header(dataset.path)

    def load(self, dataset_id: str) -> DatasetLoadResponse:
        dataset = self.get(dataset_id)
        return DatasetLoadResponse(dataset=dataset, metadata=self.metadata(dataset_id))

    def load_remote_path(self, relative_path: str) -> DatasetLoadResponse:
        resolved = self.resolve_remote_path(relative_path)
        if resolved.suffix.lower() not in self.SUPPORTED_RUNTIME_SUFFIXES:
            raise HTTPException(status_code=400, detail="Selected remote file is not a FITS dataset.")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="Selected remote path is not a file.")

        existing = next((dataset_id for dataset_id, path in self._registered_runtime_paths.items() if path == resolved), None)
        dataset_id = existing or self._unique_dataset_id(resolved.stem, self._datasets | {})
        self._registered_runtime_paths[dataset_id] = resolved
        self.refresh()
        logger.info("Loaded remote dataset path: dataset_id=%s path=%s", dataset_id, resolved)
        return self.load(dataset_id)

    def browse_remote(self, relative_path: str = "") -> FileBrowserResponse:
        current_path = self.resolve_remote_path(relative_path)
        if not current_path.exists():
            raise HTTPException(status_code=404, detail=f"Remote path '{relative_path}' not found.")
        if not current_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Remote path '{relative_path}' is not a directory.")

        entries: list[FileBrowserEntry] = []
        for item in sorted(current_path.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
            if not self.remote_browser_show_hidden and item.name.startswith("."):
                continue
            entry_type = "directory" if item.is_dir() else "fits" if item.suffix.lower() in self.SUPPORTED_RUNTIME_SUFFIXES else "other"
            rel = item.relative_to(self.remote_data_root).as_posix()
            entries.append(
                FileBrowserEntry(
                    name=item.name,
                    relative_path="" if rel == "." else rel,
                    entry_type=entry_type,
                    is_dir=item.is_dir(),
                )
            )

        rel_current = current_path.relative_to(self.remote_data_root).as_posix()
        rel_current = "" if rel_current == "." else rel_current
        parent = None
        if current_path != self.remote_data_root:
            parent_rel = current_path.parent.relative_to(self.remote_data_root).as_posix()
            parent = "" if parent_rel == "." else parent_rel

        logger.info("Browsed remote path: path=%s entries=%s", rel_current or "/", len(entries))
        return FileBrowserResponse(current_path=rel_current, parent_path=parent, entries=entries)

    def resolve_remote_path(self, relative_path: str) -> Path:
        relative = (relative_path or "").strip().lstrip("/")
        candidate = (self.remote_data_root / relative).resolve()
        try:
            candidate.relative_to(self.remote_data_root)
        except ValueError as exc:
            logger.warning("Rejected remote path outside root: requested=%s resolved=%s", relative_path, candidate)
            raise HTTPException(status_code=400, detail="Requested path is outside REMOTE_DATA_ROOT.") from exc
        return candidate

    def upload_fits(self, file: UploadFile) -> DatasetLoadResponse:
        file_name = file.filename or "dataset.fits"
        suffix = Path(file_name).suffix.lower()
        if suffix not in self.SUPPORTED_RUNTIME_SUFFIXES:
            raise HTTPException(status_code=400, detail="Only .fits and .fit files are supported.")

        target_name = self._build_runtime_file_name(file_name)
        target_path = self.runtime_upload_dir / target_name
        bytes_written = 0

        try:
            with target_path.open("wb") as stream:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > self.max_upload_size_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload exceeds configured limit of {self.max_upload_size_bytes // (1024 * 1024)} MB.",
                        )
                    stream.write(chunk)
        except HTTPException:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to store upload '{file_name}': {exc}") from exc
        finally:
            file.file.close()

        self.refresh()
        dataset_id = next(
            (item.id for item in self._datasets.values() if item.path == target_path),
            None,
        )
        if dataset_id is None:
            self.refresh()
            dataset_id = next((item.id for item in self._datasets.values() if item.path == target_path), None)
        if dataset_id is None:
            raise HTTPException(status_code=500, detail="Uploaded dataset could not be registered.")

        logger.info("Dataset uploaded: dataset_id=%s path=%s size_bytes=%s", dataset_id, target_path, bytes_written)
        return self.load(dataset_id)

    def _build_runtime_file_name(self, file_name: str) -> str:
        candidate = Path(file_name).name
        target = self.runtime_upload_dir / candidate
        if not target.exists():
            return candidate

        stem = Path(candidate).stem
        suffix = Path(candidate).suffix
        counter = 2
        while True:
            candidate = f"{stem}_{counter}{suffix}"
            if not (self.runtime_upload_dir / candidate).exists():
                return candidate
            counter += 1

    def _csv_metadata(self, dataset: DatasetRecord) -> DatasetMetadata:
        with dataset.path.open("r", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)

        columns = reader.fieldnames or []
        return DatasetMetadata(
            dataset_id=dataset.id,
            dataset_type="csv",
            scalar_name="density" if "density" in columns else None,
            shape=[len(rows)],
            naxis=1,
            dtype_original="csv",
            stats={},
            header={},
            extra={
                "columns": columns,
                "origin": dataset.origin,
                "uploaded": dataset.uploaded,
                "path": str(dataset.path),
                "file_name": dataset.file_name,
            },
        )


@lru_cache
def get_dataset_catalog() -> DatasetCatalog:
    settings = get_settings()
    return DatasetCatalog(
        base_dir=settings.datasets_dir,
        runtime_upload_dir=settings.runtime_upload_dir,
        max_upload_size_mb=settings.max_upload_size_mb,
        remote_data_root=settings.remote_data_root,
        remote_browser_show_hidden=settings.remote_browser_show_hidden,
    )
