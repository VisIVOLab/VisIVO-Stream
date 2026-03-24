from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


InteractiveLaunchMode = Literal["local", "mpiexec", "srun", "attach"]
InteractiveSessionStatus = Literal["created", "running", "attached", "stopped", "failed"]


class InteractiveSessionCreateRequest(BaseModel):
    dataset_id: str
    launch_mode: InteractiveLaunchMode = "local"
    host: str = "127.0.0.1"
    port: int | None = None
    nodes: int = Field(default=1, ge=1)
    ranks_per_node: int = Field(default=1, ge=1)
    extra_args: list[str] = Field(default_factory=list)


class InteractiveSessionRecord(BaseModel):
    session_id: str
    dataset_id: str
    dataset_name: str
    dataset_path: str
    dataset_type: str
    dataset_metadata: dict[str, Any] | None = None
    launch_mode: InteractiveLaunchMode
    status: InteractiveSessionStatus
    host: str
    port: int
    viewer_url_hint: str | None = None
    process_id: int | None = None
    command: list[str] = Field(default_factory=list)
    created_at: datetime
    stopped_at: datetime | None = None
    error: str | None = None


class InteractiveSessionStopResponse(BaseModel):
    session_id: str
    status: InteractiveSessionStatus
