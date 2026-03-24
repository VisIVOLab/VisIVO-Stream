import logging
import subprocess
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.config import get_settings
from app.interactive.launcher import PvServerLaunchSpec, PvServerLauncher
from app.models.interactive import (
    InteractiveSessionCreateRequest,
    InteractiveSessionRecord,
    InteractiveSessionStopResponse,
)
from app.services.datasets import DatasetCatalog


logger = logging.getLogger(__name__)


class InteractiveSessionManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.catalog = DatasetCatalog(self.settings.datasets_dir)
        self.launcher = PvServerLauncher()
        self.sessions: dict[str, InteractiveSessionRecord] = {}
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.settings.interactive_logs_dir.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[InteractiveSessionRecord]:
        return list(self.sessions.values())

    def get_session(self, session_id: str) -> InteractiveSessionRecord:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Interactive session '{session_id}' not found.")
        return session

    def create_session(self, payload: InteractiveSessionCreateRequest) -> InteractiveSessionRecord:
        try:
            dataset = self.catalog.get(payload.dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Dataset '{payload.dataset_id}' not found.") from exc

        if not dataset.path.exists():
            raise HTTPException(status_code=500, detail=f"Dataset file missing: {dataset.path}")

        session_id = str(uuid.uuid4())
        port = payload.port or self.launcher.reserve_port()
        record = InteractiveSessionRecord(
            session_id=session_id,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
            dataset_path=str(dataset.path),
            dataset_type=dataset.dataset_type,
            dataset_metadata=self.catalog.metadata(dataset.id).model_dump(mode="json"),
            launch_mode=payload.launch_mode,
            status="created",
            host=payload.host,
            port=port,
            viewer_url_hint=f"http://{self.settings.trame_host}:{self.settings.trame_port}",
            created_at=datetime.now(tz=timezone.utc),
        )

        if payload.launch_mode == "attach":
            record.status = "attached"
            self.sessions[session_id] = record
            return record

        spec = PvServerLaunchSpec(
            mode=payload.launch_mode,
            host=payload.host,
            port=port,
            nodes=payload.nodes,
            ranks_per_node=payload.ranks_per_node,
            extra_args=payload.extra_args,
        )

        process = None
        try:
            process, command = self.launcher.launch(session_id, spec)
            self.launcher.wait_until_ready(payload.host, port)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"Launcher binary not found: {exc.filename}") from exc
        except TimeoutError as exc:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            record.status = "failed"
            record.error = str(exc)
            self.sessions[session_id] = record
            raise HTTPException(status_code=500, detail=record.error) from exc

        record.command = command
        record.process_id = process.pid
        record.status = "running"
        self.sessions[session_id] = record
        self.processes[session_id] = process
        logger.info(
            "Interactive session %s is running on %s:%s using %s",
            session_id,
            record.host,
            record.port,
            record.launch_mode,
        )
        return record

    def stop_session(self, session_id: str) -> InteractiveSessionStopResponse:
        session = self.get_session(session_id)
        process = self.processes.get(session_id)

        if process is not None and process.poll() is None:
            logger.info("Stopping interactive session %s", session_id)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        session.status = "stopped"
        session.stopped_at = datetime.now(tz=timezone.utc)
        return InteractiveSessionStopResponse(session_id=session_id, status=session.status)
