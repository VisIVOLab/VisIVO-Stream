import argparse
import json
import logging
import os
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import paraview.web.venv  # noqa: F401
from paraview import simple

from app.core.logging import configure_logging
from app.core.pythonpath import bootstrap_external_site_packages
from app.interactive.pipeline import RemotePipelineController

bootstrap_external_site_packages()

from trame.app import get_server
from trame.ui.vuetify import SinglePageWithDrawerLayout
from trame.widgets import paraview, vuetify


logger = logging.getLogger(__name__)


def fetch_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API request failed ({exc.code}) for {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(
            f"Unable to reach FastAPI at {url}. Start the backend first with "
            "`uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`."
        ) from exc


def resolve_session(args) -> dict:
    api_base = args.api_base_url.rstrip("/")
    if args.create_session:
        payload = {
            "dataset_id": args.dataset_id,
            "launch_mode": args.launch_mode,
            "host": args.connect_host or "127.0.0.1",
            "port": args.connect_port,
            "nodes": args.nodes,
            "ranks_per_node": args.ranks_per_node,
            "extra_args": args.extra_pvserver_arg,
        }
        return fetch_json("POST", f"{api_base}/interactive/sessions", payload)

    if args.session_id:
        return fetch_json("GET", f"{api_base}/interactive/sessions/{args.session_id}")

    if args.connect_host and args.connect_port and args.dataset_path:
        suffix = Path(args.dataset_path).suffix.lower()
        return {
            "session_id": "ad-hoc",
            "dataset_id": Path(args.dataset_path).stem,
            "dataset_name": Path(args.dataset_path).stem,
            "dataset_path": args.dataset_path,
            "dataset_type": "fits" if suffix in {".fits", ".fit"} else "csv",
            "dataset_metadata": {},
            "launch_mode": "attach",
            "status": "attached",
            "host": args.connect_host,
            "port": args.connect_port,
        }

    raise SystemExit(
        "Provide either --create-session, or --session-id, or --connect-host/--connect-port/--dataset-path."
    )


class PvServerTrameViewer:
    def __init__(self, session_info: dict):
        self.session_info = session_info
        self.server = get_server(client_type="vue2")
        self.state = self.server.state
        self.ctrl = self.server.controller
        self.connected = False
        self.pipeline: RemotePipelineController | None = None
        self.view = None

        self._configure_state()
        self._connect_to_pvserver()
        self._bind_state_callbacks()
        self._build_ui()
        self.ctrl.on_server_ready.add(self._on_server_ready)
        self.ctrl.on_server_ready.add(self._safe_view_update)

    def _configure_state(self) -> None:
        self.state.trame__title = "VisIVO-Stream HPC Viewer"
        self.state.session_id = self.session_info["session_id"]
        self.state.dataset_name = self.session_info["dataset_name"]
        self.state.dataset_type = self.session_info.get("dataset_type", "csv")
        self.state.dataset_path = self.session_info["dataset_path"]
        self.state.pvserver_endpoint = f'{self.session_info["host"]}:{self.session_info["port"]}'
        self.state.representation = "Points"
        self.state.representation_options = []
        self.state.colormap = "Viridis (matplotlib)"
        self.state.colormap_options = [
            {"text": "Viridis", "value": "Viridis (matplotlib)"},
            {"text": "Cool to Warm", "value": "Cool to Warm"},
            {"text": "Inferno", "value": "Inferno (matplotlib)"},
            {"text": "Plasma", "value": "Plasma (matplotlib)"},
        ]
        self.state.volume_preset = "medium"
        self.state.volume_preset_options = ["soft", "medium", "strong"]
        self.state.volume_threshold = 0.0
        self.state.opacity_scale = 1.0
        self.state.iso_value = 0.0
        self.state.iso_min = 0.0
        self.state.iso_max = 1.0
        self.state.status_message = "Connecting to pvserver"

    def _connect_to_pvserver(self) -> None:
        if self.connected:
            return

        host = self.session_info["host"]
        port = int(self.session_info["port"])
        logger.info("Connecting to pvserver at %s:%s", host, port)

        try:
            connection = simple.Connect(host, port)
            logger.info("pvserver connection established: %s", bool(connection))
            self.view = simple.GetActiveViewOrCreate("RenderView")
            self.view.MakeRenderWindowInteractor(True)
            self.view.OrientationAxesVisibility = 1
            self.view.Background = [0.02, 0.05, 0.08]
            simple.Render(self.view)
            logger.info("Render view ready: %s", self.view)
            self.pipeline = RemotePipelineController(self.view)
            self.connected = True
        except Exception:
            logger.exception("Failed to connect to pvserver or create RenderView")
            raise

    def _bind_state_callbacks(self) -> None:
        self.state.change("representation")(self._handle_representation_change)
        self.state.change("colormap")(self._handle_colormap_change)
        self.state.change("volume_preset")(self._handle_volume_preset_change)
        self.state.change("volume_threshold")(self._handle_volume_threshold_change)
        self.state.change("opacity_scale")(self._handle_opacity_scale_change)
        self.state.change("iso_value")(self._handle_iso_value_change)

    def _build_ui(self) -> None:
        assert self.view is not None
        with SinglePageWithDrawerLayout(self.server) as layout:
            layout.title.set_text("VisIVO-Stream HPC Viewer")
            layout.icon.click = self.reset_camera

            with layout.toolbar:
                vuetify.VSpacer()
                vuetify.VBtn("Reload Dataset", click=self.reload_dataset, classes="mr-2")
                vuetify.VBtn("Reset Camera", click=self.reset_camera, outlined=True)

            with layout.drawer:
                layout.drawer.width = 380
                with vuetify.VContainer(fluid=True, classes="pa-4"):
                    vuetify.VCardTitle("Session")
                    vuetify.VAlert("{{ 'Session ' + session_id }}", type="info", dense=True, outlined=True, classes="mb-2")
                    vuetify.VAlert("{{ 'pvserver ' + pvserver_endpoint }}", type="info", dense=True, outlined=True, classes="mb-2")
                    vuetify.VAlert("{{ dataset_name + ' (' + dataset_type + ')' }}", type="success", dense=True, outlined=True, classes="mb-4")
                    vuetify.VSelect(
                        label="Representation",
                        items=("representation_options", []),
                        v_model=("representation", "Points"),
                        hide_details=True,
                        dense=True,
                        outlined=True,
                        classes="mb-4",
                    )
                    vuetify.VSelect(
                        label="Colormap",
                        items=("colormap_options", []),
                        v_model=("colormap", "Viridis (matplotlib)"),
                        item_text="text",
                        item_value="value",
                        hide_details=True,
                        dense=True,
                        outlined=True,
                        classes="mb-4",
                    )
                    vuetify.VSelect(
                        label="Volume Preset",
                        items=("volume_preset_options", []),
                        v_model=("volume_preset", "medium"),
                        hide_details=True,
                        dense=True,
                        outlined=True,
                        classes="mb-4",
                        v_if="representation === 'Volume'",
                    )
                    vuetify.VSlider(
                        label="Volume Threshold",
                        v_model=("volume_threshold", 0.0),
                        min=0.0,
                        max=1.0,
                        step=0.05,
                        hide_details=True,
                        dense=True,
                        classes="mb-4",
                        v_if="representation === 'Volume'",
                    )
                    vuetify.VSlider(
                        label="Opacity Scale",
                        v_model=("opacity_scale", 1.0),
                        min=0.1,
                        max=3.0,
                        step=0.1,
                        hide_details=True,
                        dense=True,
                        classes="mb-4",
                        v_if="representation === 'Volume'",
                    )
                    vuetify.VSlider(
                        label="Iso Value",
                        v_model=("iso_value", 0.0),
                        min=("iso_min", 0.0),
                        max=("iso_max", 1.0),
                        step=0.01,
                        hide_details=True,
                        dense=True,
                        classes="mb-4",
                        v_if="representation === 'Isocontour'",
                    )
                    vuetify.VBtn("Reset Contrast", click=self.reset_contrast, outlined=True, classes="mb-4")
                    vuetify.VAlert("{{ status_message }}", type="info", dense=True, outlined=True)

            with layout.content:
                with vuetify.VContainer(fluid=True, classes="pa-0 fill-height"):
                    logger.info("Initializing trame ParaView remote widget")
                    html_view = paraview.VtkRemoteView(self.view, ref="view", interactive_ratio=1)
                    self.ctrl.view_update = html_view.update
                    self.ctrl.view_reset_camera = html_view.reset_camera

    def _on_server_ready(self, **_kwargs) -> None:
        logger.info("trame server ready, loading dataset into active view")
        self.reload_dataset()

    def _safe_view_update(self, **_kwargs) -> None:
        if hasattr(self.ctrl, "view_update"):
            try:
                self.ctrl.view_update()
                logger.info("Remote view updated")
            except Exception:
                logger.exception("Remote view update failed")

    def reload_dataset(self, *args, **kwargs) -> None:
        del args, kwargs
        if self.pipeline is None:
            logger.warning("reload_dataset called without an initialized pipeline")
            return

        try:
            options = self.pipeline.load_dataset(self.session_info)
            self.state.representation_options = options
            self.state.representation = options[0] if options else "Slice"
            self.state.colormap = "Viridis (matplotlib)"
            self.state.volume_preset = "medium"
            self.pipeline.set_volume_preset("medium")
            if self.pipeline.pipeline is not None:
                self.state.volume_threshold = self.pipeline.pipeline.volume_threshold
                self.state.opacity_scale = self.pipeline.pipeline.opacity_scale
                self.state.iso_value = self.pipeline.pipeline.iso_value
                self.state.iso_min = self.pipeline.pipeline.iso_min
                self.state.iso_max = self.pipeline.pipeline.iso_max
                self.pipeline.set_volume_threshold(self.pipeline.pipeline.volume_threshold)
                self.pipeline.set_opacity_scale(self.pipeline.pipeline.opacity_scale)
            self.state.status_message = f"Loaded {self.state.dataset_name} as {self.state.dataset_type}"
            logger.info(
                "Dataset loaded into remote view: dataset=%s type=%s options=%s",
                self.state.dataset_name,
                self.state.dataset_type,
                options,
            )
            self._safe_view_update()
        except Exception:
            logger.exception("Dataset reload failed")
            self.state.status_message = "Dataset load failed"
            raise

    def reset_camera(self, *args, **kwargs) -> None:
        del args, kwargs
        if self.pipeline is None:
            return
        logger.info("Resetting active camera")
        self.pipeline.reset_camera()
        self.state.status_message = "Camera reset"
        if hasattr(self.ctrl, "view_reset_camera"):
            self.ctrl.view_reset_camera()
        self._safe_view_update()

    def reset_contrast(self, *args, **kwargs) -> None:
        del args, kwargs
        if self.pipeline is None:
            return
        logger.info("Resetting contrast from viewer controls")
        self.pipeline.reset_contrast()
        self.state.status_message = "Contrast reset"
        self._safe_view_update()

    def _handle_representation_change(self, representation, **_kwargs) -> None:
        if self.pipeline is None or not representation:
            return

        previous = self.current_representation_label()
        logger.info("Representation changed from %s to %s", previous, representation)
        logger.info("Applying representation=%s", representation)
        self.pipeline.set_representation(representation)
        self.state.status_message = f"Representation set to {representation}"
        self._safe_view_update()

    def _handle_colormap_change(self, colormap, **_kwargs) -> None:
        if self.pipeline is None or not colormap:
            return
        logger.info("Changing colormap to %s", colormap)
        self.pipeline.apply_colormap(colormap)
        self.state.status_message = f"Colormap set to {colormap}"
        self._safe_view_update()

    def _handle_volume_preset_change(self, volume_preset, **_kwargs) -> None:
        if self.pipeline is None or not volume_preset:
            return
        logger.info("Changing volume preset to %s", volume_preset)
        self.pipeline.set_volume_preset(volume_preset)
        self.state.status_message = f"Volume preset set to {volume_preset}"
        self._safe_view_update()

    def _handle_volume_threshold_change(self, volume_threshold, **_kwargs) -> None:
        if self.pipeline is None:
            return
        logger.info("Changing volume threshold to %s", volume_threshold)
        self.pipeline.set_volume_threshold(float(volume_threshold))
        self.state.status_message = f"Volume threshold set to {volume_threshold:.2f}"
        self._safe_view_update()

    def _handle_opacity_scale_change(self, opacity_scale, **_kwargs) -> None:
        if self.pipeline is None:
            return
        logger.info("Changing opacity scale to %s", opacity_scale)
        self.pipeline.set_opacity_scale(float(opacity_scale))
        self.state.status_message = f"Opacity scale set to {opacity_scale:.2f}"
        self._safe_view_update()

    def _handle_iso_value_change(self, iso_value, **_kwargs) -> None:
        if self.pipeline is None:
            return
        logger.info("Changing iso value to %s", iso_value)
        self.pipeline.set_isocontour_value(float(iso_value))
        self.state.status_message = f"Iso value set to {float(iso_value):.2f}"
        self._safe_view_update()

    def current_representation_label(self) -> str:
        if self.pipeline is None:
            return "unknown"
        return self.pipeline.current_representation_label()


def parse_args():
    parser = argparse.ArgumentParser(description="VisIVO-Stream trame viewer for pvserver sessions")
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1"))
    parser.add_argument("--session-id")
    parser.add_argument("--create-session", action="store_true")
    parser.add_argument("--dataset-id", default=os.getenv("TRAME_DATASET_ID", "galaxy_points"))
    parser.add_argument("--launch-mode", default=os.getenv("PVSERVER_LAUNCH_MODE", "local"))
    parser.add_argument("--connect-host", default=os.getenv("PVSERVER_CONNECT_HOST"))
    parser.add_argument("--connect-port", type=int, default=None)
    parser.add_argument("--dataset-path")
    parser.add_argument("--nodes", type=int, default=int(os.getenv("PVSERVER_NODES", "1")))
    parser.add_argument("--ranks-per-node", type=int, default=int(os.getenv("PVSERVER_RANKS_PER_NODE", "1")))
    parser.add_argument("--extra-pvserver-arg", action="append", default=[])
    return parser.parse_known_args()


def main():
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("trame_server").setLevel(logging.WARNING)
    logging.getLogger("wslink").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    args, unknown = parse_args()
    session_info = resolve_session(args)
    viewer = PvServerTrameViewer(session_info=session_info)
    cli_args = viewer.server.cli.parse_known_args(unknown)[0]
    host = getattr(cli_args, "host", None) or os.getenv("TRAME_HOST", "127.0.0.1")
    port = getattr(cli_args, "port", None) or int(os.getenv("TRAME_PORT", "8081"))
    open_browser = not bool(getattr(cli_args, "server", False))

    logger.info(
        "Starting trame viewer on http://%s:%s for pvserver %s:%s",
        host,
        port,
        session_info["host"],
        session_info["port"],
    )
    viewer.server.start(port=port, open_browser=open_browser, host=host)


if __name__ == "__main__":
    main()
