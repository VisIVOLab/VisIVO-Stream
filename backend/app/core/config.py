from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "VisIVO-Stream API"
    app_env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    datasets_dir: Path = Field(default=Path("/app/data/samples"))
    runtime_upload_dir: Path = Field(default=Path("./runtime/uploads"))
    max_upload_size_mb: int = 256
    remote_data_root: Path = Field(default=Path("./data"))
    remote_browser_show_hidden: bool = False
    fits_volume_preview_auto_enable: bool = True
    fits_volume_preview_size_threshold_mb: int = 256
    fits_volume_preview_max_voxels: int = 2_097_152
    fits_volume_preview_max_mb: int = 128
    renders_dir: Path = Field(default=Path("/app/renders"))
    interactive_logs_dir: Path = Field(default=Path("./runtime/interactive"))
    paraview_script: Path = Field(default=Path("/app/scripts/render_dataset.py"))
    pvpython_bin: str = "pvpython"
    pvserver_bin: str = "pvserver"
    mpiexec_bin: str = "mpiexec"
    srun_bin: str = "srun"
    project_root: Path = Field(default=Path("."))
    trame_host: str = "127.0.0.1"
    trame_port: int = 8081
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8080"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
