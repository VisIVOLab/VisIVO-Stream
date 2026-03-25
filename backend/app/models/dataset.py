from typing import Any, Literal

from pathlib import Path

from pydantic import BaseModel, Field


DatasetType = Literal["csv", "fits"]
DatasetOrigin = Literal["sample", "upload"]


class DatasetSummary(BaseModel):
    id: str
    name: str
    description: str
    file_name: str
    dataset_type: DatasetType = "csv"
    origin: DatasetOrigin = "sample"
    uploaded: bool = False
    scalar_fields: list[str] = Field(default_factory=list)
    point_count_hint: int


class DatasetRecord(DatasetSummary):
    path: Path


class DatasetMetadata(BaseModel):
    dataset_id: str
    dataset_type: DatasetType
    scalar_name: str | None = None
    shape: list[int] = Field(default_factory=list)
    naxis: int | None = None
    dtype_original: str | None = None
    stats: dict[str, float] = Field(default_factory=dict)
    header: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class DatasetLoadRequest(BaseModel):
    dataset_id: str


class DatasetLoadResponse(BaseModel):
    dataset: DatasetSummary
    metadata: DatasetMetadata


class DatasetLoadPathRequest(BaseModel):
    relative_path: str


class FileBrowserEntry(BaseModel):
    name: str
    relative_path: str
    entry_type: Literal["directory", "fits", "other"]
    is_dir: bool = False


class FileBrowserResponse(BaseModel):
    current_path: str
    parent_path: str | None = None
    entries: list[FileBrowserEntry] = Field(default_factory=list)
