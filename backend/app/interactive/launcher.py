import logging
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.models.interactive import InteractiveLaunchMode


logger = logging.getLogger(__name__)


@dataclass
class PvServerLaunchSpec:
    mode: InteractiveLaunchMode
    host: str
    port: int
    nodes: int = 1
    ranks_per_node: int = 1
    extra_args: list[str] = field(default_factory=list)


class PvServerLauncher:
    def __init__(self) -> None:
        self.settings = get_settings()

    def reserve_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    def build_command(self, spec: PvServerLaunchSpec) -> list[str]:
        pvserver_cmd = [
            self.settings.pvserver_bin,
            f"--server-port={spec.port}",
            "--force-offscreen-rendering",
            *spec.extra_args,
        ]

        if spec.mode == "local":
            return pvserver_cmd

        total_ranks = spec.nodes * spec.ranks_per_node
        if spec.mode == "mpiexec":
            return [
                self.settings.mpiexec_bin,
                "-np",
                str(total_ranks),
                *pvserver_cmd,
            ]

        if spec.mode == "srun":
            return [
                self.settings.srun_bin,
                "--nodes",
                str(spec.nodes),
                "--ntasks-per-node",
                str(spec.ranks_per_node),
                *pvserver_cmd,
            ]

        raise ValueError(f"Unsupported launch mode for command construction: {spec.mode}")

    def launch(self, session_id: str, spec: PvServerLaunchSpec) -> tuple[subprocess.Popen[str], list[str]]:
        command = self.build_command(spec)
        log_dir = self.settings.interactive_logs_dir / session_id
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = (log_dir / "pvserver.stdout.log").open("w", encoding="utf-8")
        stderr_log = (log_dir / "pvserver.stderr.log").open("w", encoding="utf-8")

        logger.info("Launching pvserver session %s: %s", session_id, " ".join(command))
        process = subprocess.Popen(
            command,
            stdout=stdout_log,
            stderr=stderr_log,
            text=True,
            cwd=Path(self.settings.project_root),
        )
        return process, command

    def wait_until_ready(self, host: str, port: int, timeout_seconds: float = 15.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                try:
                    sock.connect((host, port))
                    return
                except OSError:
                    time.sleep(0.5)
        raise TimeoutError(f"pvserver did not become reachable on {host}:{port} within {timeout_seconds}s")
