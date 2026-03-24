import csv
from pathlib import Path

from app.datasets.fits_metadata import extract_fits_header, extract_fits_metadata
from app.models.dataset import DatasetMetadata, DatasetRecord


class DatasetCatalog:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._datasets = self._build_catalog()

    def _build_catalog(self) -> dict[str, DatasetRecord]:
        datasets = {
            "galaxy_points": DatasetRecord(
                id="galaxy_points",
                name="Galaxy Points",
                description="Synthetic point cloud with density values inspired by galaxy distributions.",
                file_name="galaxy_points.csv",
                path=self.base_dir / "galaxy_points.csv",
                dataset_type="csv",
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
                scalar_fields=["density"],
                point_count_hint=25,
            ),
        }

        for path in sorted(self.base_dir.glob("*")):
            if path.suffix.lower() not in {".fits", ".fit"}:
                continue
            dataset_id = path.stem
            datasets[dataset_id] = DatasetRecord(
                id=dataset_id,
                name=dataset_id.replace("_", " ").title(),
                description="FITS scientific dataset loaded through astropy and exposed as vtkImageData.",
                file_name=path.name,
                path=path,
                dataset_type="fits",
                scalar_fields=["FITSImage"],
                point_count_hint=0,
            )
        return datasets

    def list(self) -> list[DatasetRecord]:
        return list(self._datasets.values())

    def get(self, dataset_id: str) -> DatasetRecord:
        if dataset_id not in self._datasets:
            raise KeyError(dataset_id)
        return self._datasets[dataset_id]

    def metadata(self, dataset_id: str) -> DatasetMetadata:
        dataset = self.get(dataset_id)
        if dataset.dataset_type == "fits":
            metadata = extract_fits_metadata(dataset.path)
            metadata.dataset_id = dataset.id
            return metadata
        return self._csv_metadata(dataset)

    def fits_header(self, dataset_id: str) -> dict:
        dataset = self.get(dataset_id)
        if dataset.dataset_type != "fits":
            raise ValueError(f"Dataset '{dataset_id}' is not a FITS dataset")
        return extract_fits_header(dataset.path)

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
            extra={"columns": columns},
        )
