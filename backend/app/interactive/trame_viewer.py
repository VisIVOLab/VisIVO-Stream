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
from trame.widgets import html, paraview, vuetify


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
            "launch_mode": args.launch_mode,
            "host": args.connect_host or "127.0.0.1",
            "port": args.connect_port,
            "nodes": args.nodes,
            "ranks_per_node": args.ranks_per_node,
            "extra_args": args.extra_pvserver_arg,
        }
        if args.dataset_id:
            payload["dataset_id"] = args.dataset_id
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
        self.api_base_url = (session_info.get("api_base_url") or os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1")).rstrip("/")
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
        self.state.dataset_name = self.session_info.get("dataset_name") or "No dataset loaded"
        self.state.dataset_type = self.session_info.get("dataset_type") or ""
        self.state.dataset_path = self.session_info.get("dataset_path") or ""
        self.state.pvserver_endpoint = f'{self.session_info["host"]}:{self.session_info["port"]}'
        self.state.representation = "Points"
        self.state.representation_options = []
        self.state.selected_dataset_id = self.session_info.get("dataset_id") or ""
        self.state.dataset_options = []
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
        self.state.slice_axis = "Z"
        self.state.slice_axis_options = ["X", "Y", "Z"]
        self.state.slice_index = 0
        self.state.slice_index_min = 0
        self.state.slice_index_max = 0
        self.state.can_slice_explore = False
        self.state.dataset_dimensions = "-"
        self.state.dataset_scalar_range = "-"
        self.state.dataset_mean = "-"
        self.state.dataset_rms = "-"
        self.state.dataset_origin = "-"
        self.state.dataset_uploaded = False
        self.state.upload_feedback = ""
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
        self.state.change("slice_axis")(self._handle_slice_axis_change)
        self.state.change("slice_index")(self._handle_slice_index_change)
        self.state.change("upload_feedback")(self._handle_upload_feedback)

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
                    vuetify.VAlert("{{ dataset_type ? (dataset_name + ' (' + dataset_type + ')') : dataset_name }}", type="success", dense=True, outlined=True, classes="mb-4")
                    vuetify.VSelect(
                        label="Dataset",
                        items=("dataset_options", []),
                        v_model=("selected_dataset_id", ""),
                        item_text="text",
                        item_value="value",
                        hide_details=True,
                        dense=True,
                        outlined=True,
                        classes="mb-2",
                    )
                    vuetify.VBtn("Load Dataset", click=self.load_selected_dataset, classes="mb-4", block=True)
                    html.Input(id="visivo-upload-feedback", v_model=("upload_feedback", ""), style="display: none;")
                    html.Input(id="visivo-upload-input", type="file", accept=".fits,.fit", style="display: none;")
                    vuetify.VBtn(
                        "Choose FITS File",
                        click="document.getElementById('visivo-upload-input').click()",
                        outlined=True,
                        classes="mb-2",
                        block=True,
                    )
                    vuetify.VBtn(
                        "Upload FITS",
                        click="visivoUploadDataset()",
                        outlined=True,
                        classes="mb-4",
                        block=True,
                    )
                    vuetify.VCardTitle("Metadata")
                    vuetify.VAlert("{{ 'Origin: ' + dataset_origin + (dataset_uploaded ? ' (uploaded)' : '') }}", type="info", dense=True, outlined=True, classes="mb-2")
                    vuetify.VAlert("{{ 'Dimensions: ' + dataset_dimensions }}", type="info", dense=True, outlined=True, classes="mb-2")
                    vuetify.VAlert("{{ 'Scalar range: ' + dataset_scalar_range }}", type="info", dense=True, outlined=True, classes="mb-2")
                    vuetify.VAlert("{{ 'Mean: ' + dataset_mean + ' | RMS: ' + dataset_rms }}", type="info", dense=True, outlined=True, classes="mb-4")
                    vuetify.VAlert("{{ 'Representation: ' + representation }}", type="info", dense=True, outlined=True, classes="mb-4")
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
                        label="Slice Axis",
                        items=("slice_axis_options", []),
                        v_model=("slice_axis", "Z"),
                        hide_details=True,
                        dense=True,
                        outlined=True,
                        classes="mb-4",
                        v_if="representation === 'Slice' && dataset_type === 'fits' && can_slice_explore",
                    )
                    vuetify.VSlider(
                        label="Slice Index",
                        v_model=("slice_index", 0),
                        min=("slice_index_min", 0),
                        max=("slice_index_max", 0),
                        step=1,
                        hide_details=True,
                        dense=True,
                        classes="mb-4",
                        v_if="representation === 'Slice' && dataset_type === 'fits' && can_slice_explore",
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
                    html.Script(
                        f"""
window.visivoUploadDataset = async function() {{
  const fileInput = document.getElementById('visivo-upload-input');
  const feedback = document.getElementById('visivo-upload-feedback');
  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {{
    if (feedback) {{
      feedback.value = JSON.stringify({{ error: 'Select a FITS file first.' }});
      feedback.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
    return;
  }}

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  try {{
    const response = await fetch('{self.api_base_url}/datasets/upload', {{
      method: 'POST',
      body: formData,
    }});
    const data = await response.json();
    if (feedback) {{
      feedback.value = JSON.stringify(data);
      feedback.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
    fileInput.value = '';
  }} catch (error) {{
    if (feedback) {{
      feedback.value = JSON.stringify({{ error: String(error) }});
      feedback.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
  }}
}};
"""
                    )

            with layout.content:
                with vuetify.VContainer(fluid=True, classes="pa-0 fill-height"):
                    logger.info("Initializing trame ParaView remote widget")
                    html_view = paraview.VtkRemoteView(self.view, ref="view", interactive_ratio=1)
                    self.ctrl.view_update = html_view.update
                    self.ctrl.view_reset_camera = html_view.reset_camera

    def _on_server_ready(self, **_kwargs) -> None:
        logger.info("trame server ready, fetching dataset catalog")
        self.refresh_dataset_catalog()
        if self.session_info.get("dataset_id"):
            self.reload_dataset()
        else:
            self.state.status_message = "Connected. Select a dataset to load."
            self._safe_view_update()

    def refresh_dataset_catalog(self) -> None:
        datasets = fetch_json("GET", f"{self.api_base_url}/datasets")
        self.state.dataset_options = [
            {
                "text": f'{item["name"]} ({item["dataset_type"]}, {item.get("origin", "sample")})',
                "value": item["id"],
            }
            for item in datasets
        ]
        if not self.state.selected_dataset_id and datasets:
            self.state.selected_dataset_id = datasets[0]["id"]
        logger.info("Dataset catalog refreshed: count=%s", len(datasets))

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
        if not self.session_info.get("dataset_id"):
            self.refresh_dataset_catalog()
            self.state.status_message = "Select a dataset to load."
            self._safe_view_update()
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
                self.state.slice_axis = self.pipeline.pipeline.slice_axis
                self.state.slice_index = self.pipeline.pipeline.slice_index
                self.state.slice_index_min = self.pipeline.pipeline.slice_index_min
                self.state.slice_index_max = self.pipeline.pipeline.slice_index_max
                self.state.can_slice_explore = int((self.session_info.get("dataset_metadata") or {}).get("naxis") or 0) >= 3
                self.pipeline.set_volume_threshold(self.pipeline.pipeline.volume_threshold)
                self.pipeline.set_opacity_scale(self.pipeline.pipeline.opacity_scale)
            self._update_dataset_metadata_state(self.session_info.get("dataset_metadata") or {})
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

    def load_selected_dataset(self, *args, **kwargs) -> None:
        del args, kwargs
        if self.pipeline is None:
            return
        dataset_id = self.state.selected_dataset_id
        if not dataset_id:
            self.state.status_message = "Select a dataset first."
            self._safe_view_update()
            return
        logger.info("Loading selected dataset from viewer: dataset_id=%s", dataset_id)
        payload = fetch_json("POST", f"{self.api_base_url}/datasets/load", {"dataset_id": dataset_id})
        dataset = payload["dataset"]
        metadata = payload["metadata"]
        self.session_info["dataset_id"] = dataset["id"]
        self.session_info["dataset_name"] = dataset["name"]
        self.session_info["dataset_path"] = metadata["extra"].get("path", "")
        self.session_info["dataset_type"] = dataset["dataset_type"]
        self.session_info["dataset_metadata"] = metadata
        self.state.dataset_name = dataset["name"]
        self.state.dataset_type = dataset["dataset_type"]
        self.state.dataset_path = metadata["extra"].get("path", "")
        self.reload_dataset()

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

    def _handle_slice_axis_change(self, slice_axis, **_kwargs) -> None:
        if self.pipeline is None or not slice_axis:
            return
        logger.info("Changing slice axis to %s", slice_axis)
        self.pipeline.set_slice_axis(slice_axis)
        if self.pipeline.pipeline is not None:
            self.state.slice_index_min = self.pipeline.pipeline.slice_index_min
            self.state.slice_index_max = self.pipeline.pipeline.slice_index_max
            self.state.slice_index = self.pipeline.pipeline.slice_index
        self.state.status_message = f"Slice axis set to {slice_axis}"
        self._safe_view_update()

    def _handle_slice_index_change(self, slice_index, **_kwargs) -> None:
        if self.pipeline is None:
            return
        logger.info("Changing slice index to %s", slice_index)
        self.pipeline.set_slice_index(int(slice_index))
        if self.pipeline.pipeline is not None:
            self.state.slice_index = self.pipeline.pipeline.slice_index
        self.state.status_message = f"Slice index set to {int(slice_index)}"
        self._safe_view_update()

    def _handle_upload_feedback(self, upload_feedback, **_kwargs) -> None:
        if not upload_feedback:
            return
        try:
            payload = json.loads(upload_feedback)
        except json.JSONDecodeError:
            self.state.status_message = "Upload failed: invalid server response"
            self.state.upload_feedback = ""
            return

        if payload.get("error") or payload.get("detail"):
            self.state.status_message = f"Upload failed: {payload.get('error') or payload.get('detail')}"
            self.state.upload_feedback = ""
            self._safe_view_update()
            return

        dataset = payload.get("dataset") or {}
        self.refresh_dataset_catalog()
        if dataset.get("id"):
            self.state.selected_dataset_id = dataset["id"]
        self.state.status_message = f"Dataset uploaded: {dataset.get('name', dataset.get('id', 'dataset'))}"
        logger.info("Dataset uploaded from viewer: dataset_id=%s", dataset.get("id"))
        self.state.upload_feedback = ""
        self._safe_view_update()

    def _update_dataset_metadata_state(self, metadata: dict) -> None:
        stats = metadata.get("stats") or {}
        extra = metadata.get("extra") or {}
        shape = metadata.get("shape") or []
        scalar_min = stats.get("min")
        scalar_max = stats.get("max")
        self.state.dataset_dimensions = " x ".join(str(item) for item in shape) if shape else "-"
        if scalar_min is None or scalar_max is None:
            self.state.dataset_scalar_range = "-"
        else:
            self.state.dataset_scalar_range = f"{float(scalar_min):.4g} .. {float(scalar_max):.4g}"
        self.state.dataset_mean = f'{float(stats.get("mean", 0.0)):.4g}' if stats else "-"
        self.state.dataset_rms = f'{float(stats.get("rms", 0.0)):.4g}' if stats else "-"
        self.state.dataset_origin = str(extra.get("origin", "sample"))
        self.state.dataset_uploaded = bool(extra.get("uploaded", False))
        self.state.can_slice_explore = int(metadata.get("naxis") or 0) >= 3

    def current_representation_label(self) -> str:
        if self.pipeline is None:
            return "unknown"
        return self.pipeline.current_representation_label()


def parse_args():
    parser = argparse.ArgumentParser(description="VisIVO-Stream trame viewer for pvserver sessions")
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1"))
    parser.add_argument("--session-id")
    parser.add_argument("--create-session", action="store_true")
    parser.add_argument("--dataset-id", default=os.getenv("TRAME_DATASET_ID") or None)
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
    session_info["api_base_url"] = args.api_base_url.rstrip("/")
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
