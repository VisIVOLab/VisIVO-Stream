from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CameraParameters(BaseModel):
    azimuth: float = 35.0
    elevation: float = 25.0
    zoom: float = 1.25


class FilterParameters(BaseModel):
    kind: Literal["none", "threshold"] = "none"
    enabled: bool = False
    scalar_field: str = "density"
    lower: float = 0.0
    upper: float = 1.0


class RenderParameters(BaseModel):
    color_by: str = "density"
    colormap: str = "Viridis (matplotlib)"
    opacity: float = Field(default=0.9, ge=0.05, le=1.0)
    point_size: float = Field(default=5.0, ge=1.0, le=20.0)
    image_width: int = Field(default=1280, ge=320, le=4096)
    image_height: int = Field(default=720, ge=240, le=2160)
    camera: CameraParameters = Field(default_factory=CameraParameters)
    filter: FilterParameters = Field(default_factory=FilterParameters)


class CreateSessionRequest(BaseModel):
    dataset_id: str


class RenderRequest(RenderParameters):
    pass


class RenderSession(BaseModel):
    session_id: str
    dataset_id: str
    dataset_name: str
    status: Literal["created", "rendered", "failed"] = "created"
    image_url: str | None = None
    image_path: str | None = None
    parameters: RenderParameters = Field(default_factory=RenderParameters)
    rendered_at: datetime | None = None
    error: str | None = None

