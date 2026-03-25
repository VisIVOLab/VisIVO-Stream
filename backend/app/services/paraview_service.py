import json
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.core.config import get_settings
from app.models.render import RenderParameters, RenderSession
from app.services.datasets import get_dataset_catalog


logger = logging.getLogger(__name__)


class ParaViewRenderService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.catalog = get_dataset_catalog()
        self.sessions: dict[str, RenderSession] = {}
        self.settings.renders_dir.mkdir(parents=True, exist_ok=True)

    def list_datasets(self):
        return self.catalog.list()

    def get_dataset_metadata(self, dataset_id: str):
        try:
            return self.catalog.metadata(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def get_fits_header(self, dataset_id: str):
        try:
            return self.catalog.fits_header(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def create_session(self, dataset_id: str) -> RenderSession:
        try:
            dataset = self.catalog.get(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.") from exc

        session_id = str(uuid.uuid4())
        session = RenderSession(
            session_id=session_id,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> RenderSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
        return session

    def render(self, session_id: str, parameters: RenderParameters) -> RenderSession:
        session = self.get_session(session_id)
        dataset = self.catalog.get(session.dataset_id)

        if not dataset.path.exists():
            raise HTTPException(status_code=500, detail=f"Dataset file missing: {dataset.path}")

        output_file = f"{session_id}.png"
        output_path = self.settings.renders_dir / output_file
        payload = {
            "dataset_path": str(dataset.path),
            "output_path": str(output_path),
            "parameters": parameters.model_dump(mode="json"),
        }

        command = [
            self.settings.pvpython_bin,
            str(self.settings.paraview_script),
            json.dumps(payload),
        ]
        logger.info("Rendering session %s with dataset %s", session_id, dataset.id)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="pvpython not available. Use the Docker setup or install ParaView locally.",
            ) from exc
        except subprocess.CalledProcessError as exc:
            logger.error("ParaView render failed: %s", exc.stderr.strip())
            session.status = "failed"
            session.error = exc.stderr.strip() or exc.stdout.strip() or "Unknown render failure"
            raise HTTPException(status_code=500, detail=session.error) from exc

        logger.info("ParaView render completed: %s", result.stdout.strip())
        session.parameters = parameters
        session.status = "rendered"
        session.image_path = str(output_path)
        session.image_url = f"/renders/{output_file}?ts={int(datetime.now(tz=timezone.utc).timestamp())}"
        session.rendered_at = datetime.now(tz=timezone.utc)
        session.error = None
        return session
