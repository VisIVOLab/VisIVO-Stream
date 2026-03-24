import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paraview import servermanager, simple


logger = logging.getLogger(__name__)


@dataclass
class RemotePipelineState:
    dataset_type: str
    source: object
    full_source: object | None
    preview_source: object | None
    display: object
    active_source: object
    view: object
    scalar_name: str
    scalar_range: tuple[float, float]
    representation_options: list[str]
    dataset_metadata: dict[str, Any] | None = None
    csv_surface: object | None = None
    fits_slice: object | None = None
    fits_contour: object | None = None
    vtk_dimensions: tuple[int, int, int] | None = None
    volume_preset: str = "medium"
    current_colormap: str = "Viridis (matplotlib)"
    volume_threshold: float = 0.0
    opacity_scale: float = 1.0
    iso_value: float = 0.0
    iso_min: float = 0.0
    iso_max: float = 1.0
    preview_factor: int = 1
    is_preview_mode: bool = False
    full_dimensions: tuple[int, int, int] | None = None
    preview_dimensions: tuple[int, int, int] | None = None


class RemotePipelineController:
    def __init__(self, view):
        self.view = view
        self.pipeline: RemotePipelineState | None = None
        self.current_representation = ""

    def current_representation_label(self) -> str:
        return self.current_representation or "unknown"

    def load_dataset(self, session_info: dict[str, Any]) -> list[str]:
        dataset_path = session_info["dataset_path"]
        dataset_type = session_info["dataset_type"]
        metadata = session_info.get("dataset_metadata") or {}

        logger.info("Loading dataset through pvserver: type=%s path=%s", dataset_type, dataset_path)
        self._clear_pipeline()

        if dataset_type == "fits":
            options = self._load_fits(dataset_path, metadata)
        else:
            options = self._load_csv(dataset_path)

        simple.ResetCamera(self.view)
        self.view.CenterOfRotation = self.view.CameraFocalPoint
        simple.Render(self.view)
        return options

    def _load_csv(self, dataset_path: str) -> list[str]:
        reader = simple.CSVReader(FileName=[dataset_path])
        points = simple.TableToPoints(Input=reader)
        points.XColumn = "x"
        points.YColumn = "y"
        points.ZColumn = "z"

        shell = simple.Delaunay3D(Input=points)
        surface = simple.ExtractSurface(Input=shell)

        display = simple.Show(points, self.view, "GeometryRepresentation")
        display.Representation = "Points"
        display.PointSize = 5.0
        display.Opacity = 0.95

        self.pipeline = RemotePipelineState(
            dataset_type="csv",
            source=points,
            full_source=points,
            preview_source=None,
            csv_surface=surface,
            display=display,
            active_source=points,
            view=self.view,
            scalar_name="density",
            scalar_range=(0.0, 1.0),
            representation_options=["Points", "Surface", "Outline"],
        )
        self.current_representation = "Points"
        self.pipeline.current_colormap = "Viridis (matplotlib)"
        self.apply_colormap("Viridis (matplotlib)")
        logger.info("CSV source created with default representation=Points")
        return self.pipeline.representation_options

    def _fits_preview_factor(self, metadata: dict[str, Any]) -> int:
        naxis = int(metadata.get("naxis") or 0)
        if naxis < 3:
            return 1
        # Default to a deterministic stride-4 preview for FITS 3D cubes. This
        # keeps the first remote frame lightweight while preserving structure.
        return max(1, int(os.getenv("FITS_PREVIEW_FACTOR", "4")))

    def _create_fits_programmable_source(self, dataset_path: str, downsample_factor: int) -> tuple[object, tuple[int, int, int]]:
        source = simple.ProgrammableSource()
        source.OutputDataSetType = "vtkImageData"
        source.ScriptRequestInformation = self._build_fits_request_information_script(dataset_path, downsample_factor)
        source.Script = self._build_fits_script(dataset_path, downsample_factor)
        source.UpdatePipeline()
        data_info = source.GetDataInformation()
        extent = data_info.GetExtent()
        dims = (
            int(extent[1] - extent[0] + 1),
            int(extent[3] - extent[2] + 1),
            int(extent[5] - extent[4] + 1),
        )
        return source, dims

    def _load_fits(self, dataset_path: str, metadata: dict[str, Any]) -> list[str]:
        metadata = dict(metadata)
        metadata.setdefault("path", dataset_path)
        naxis = int(metadata.get("naxis") or 0)
        shape = metadata.get("shape") or []
        scalar_name = str(metadata.get("scalar_name") or "FITSImage")
        full_dimensions = self._shape_to_xyz(shape)
        preview_factor = self._fits_preview_factor(metadata)

        if naxis >= 3 and preview_factor > 1:
            preview_source, preview_dimensions = self._create_fits_programmable_source(dataset_path, preview_factor)
            full_source = None
            source = preview_source
            vtk_dimensions = preview_dimensions
            is_preview_mode = True
            logger.info(
                "Preview source created: preview_factor=%s preview_dimensions=%s full_dimensions=%s representation=%s",
                preview_factor,
                preview_dimensions,
                full_dimensions,
                "Slice",
            )
        else:
            source, vtk_dimensions = self._create_fits_programmable_source(dataset_path, 1)
            preview_source = None
            preview_dimensions = None
            full_source = source
            is_preview_mode = False
            logger.info(
                "Full-resolution source ready: full_dimensions=%s preview_dimensions=%s representation=%s",
                full_dimensions,
                preview_dimensions,
                "Slice",
            )

        slice_source = None
        contour_source = None
        default_representation = "Slice"
        representation_options = ["Slice", "Outline"] if naxis == 2 else ["Slice", "Volume", "Isocontour", "Outline"]
        iso_value = 0.0
        iso_min = 0.0
        iso_max = 1.0

        if naxis >= 3:
            slice_source = self._create_central_slice(source, vtk_dimensions)
            slice_source.UpdatePipeline()
            contour_source, iso_value, iso_min, iso_max = self._create_fits_contour(source, metadata, scalar_name)
            display = simple.Show(slice_source, self.view, "GeometryRepresentation")
            display.Representation = "Surface"
            try:
                simple.Hide(source, self.view)
            except Exception:
                pass
            try:
                simple.Hide(contour_source, self.view)
            except Exception:
                pass
        else:
            display = simple.Show(source, self.view)
            display.Representation = "Slice"

        shown_object = slice_source or source
        vtk_details = self._inspect_dataset(shown_object, scalar_name)
        scalar_range = vtk_details["active_range"]
        if slice_source is not None:
            logger.info(
                "Slice input source=%s dims=%s",
                self._fits_source_label(source),
                vtk_dimensions,
            )
            logger.info(
                "Slice VTK diagnostics: bounds=%s arrays=%s active=%s range=%s",
                vtk_details["bounds"],
                vtk_details["point_arrays"],
                vtk_details["active_array"],
                vtk_details["active_range"],
            )

        self.pipeline = RemotePipelineState(
            dataset_type="fits",
            source=source,
            full_source=full_source,
            preview_source=preview_source,
            fits_slice=slice_source,
            display=display,
            active_source=slice_source or source,
            view=self.view,
            scalar_name=scalar_name,
            scalar_range=scalar_range,
            representation_options=representation_options,
            dataset_metadata=metadata,
            vtk_dimensions=vtk_dimensions,
            fits_contour=contour_source,
            iso_value=iso_value,
            iso_min=iso_min,
            iso_max=iso_max,
            preview_factor=preview_factor,
            is_preview_mode=is_preview_mode,
            full_dimensions=full_dimensions,
            preview_dimensions=preview_dimensions,
        )
        self.current_representation = default_representation
        self.pipeline.current_colormap = "Viridis (matplotlib)"
        self.pipeline.volume_threshold, self.pipeline.opacity_scale = self._initial_volume_defaults(metadata)
        logger.info(
            "Initial volume defaults: threshold=%s opacity_scale=%s volume_preset=%s colormap=%s",
            self.pipeline.volume_threshold,
            self.pipeline.opacity_scale,
            self.pipeline.volume_preset,
            self.pipeline.current_colormap,
        )
        self.apply_colormap("Viridis (matplotlib)")
        logger.info(
            "FITS source created: mode=%s source=%s shown=%s vtk_dimensions=%s extent=%s bounds=%s scalar_range=%s default_representation=%s",
            "preview" if is_preview_mode else "full",
            type(source).__name__,
            type(shown_object).__name__,
            vtk_dimensions,
            vtk_details["extent"],
            vtk_details["bounds"],
            scalar_range,
            default_representation,
        )
        self._warn_if_missing_visible_array(vtk_details, scalar_name)
        return representation_options

    def _create_fits_contour(
        self,
        source,
        metadata: dict[str, Any],
        scalar_name: str,
    ) -> tuple[object, float, float, float]:
        stats = metadata.get("stats") or {}
        data_min = float(stats.get("min", 0.0))
        data_max = float(stats.get("max", 1.0))
        mean = float(stats.get("mean", data_min))
        rms = abs(float(stats.get("rms", max(data_max - data_min, 1.0) * 0.1)))
        p50 = float(stats.get("p50", mean))
        p90 = float(stats.get("p90", mean + 3.0 * rms))
        p99 = float(stats.get("p99", data_max))

        iso_min = max(data_min, p50)
        iso_max = min(data_max, p99)
        if iso_max <= iso_min:
            iso_min = data_min
            iso_max = data_max if data_max > data_min else data_min + 1.0

        # Astronomical FITS cubes are often noise-dominated. Use a conservative
        # default that favors real structures over background: max(p90, mean + 3*rms).
        iso_value = max(p90, mean + 3.0 * rms)
        iso_value = min(max(iso_value, iso_min), iso_max)

        contour = simple.Contour(Input=source)
        contour.ContourBy = ["POINTS", scalar_name]
        contour.Isosurfaces = [float(iso_value)]
        contour.PointMergeMethod = "Uniform Binning"
        contour.ComputeScalars = 1
        contour.UpdatePipeline()

        logger.info(
            "Contour created: iso_value=%s iso_range=(%s, %s) scalar_range=(%s, %s) stats=(mean=%s rms=%s p90=%s p99=%s)",
            iso_value,
            iso_min,
            iso_max,
            data_min,
            data_max,
            mean,
            rms,
            p90,
            p99,
        )
        return contour, float(iso_value), float(iso_min), float(iso_max)

    def _initial_volume_defaults(self, metadata: dict[str, Any]) -> tuple[float, float]:
        stats = metadata.get("stats") or {}
        mean = float(stats.get("mean", 0.0))
        rms = abs(float(stats.get("rms", 1.0)))
        p75 = float(stats.get("p75", mean))
        p95 = float(stats.get("p95", mean + 4.0 * rms))
        p99 = float(stats.get("p99", p95))
        dynamic_span = max(p99 - p75, 1e-6)

        # Real FITS cubes such as WALLABY are often low-contrast and noise-dominated.
        # Start slightly above the background and increase opacity when the robust
        # percentile span is narrow so structures are readable without crushing the LUT.
        threshold = 0.15 if p75 >= mean + 0.5 * rms else 0.05
        opacity_scale = 1.4 if dynamic_span < 3.0 * max(rms, 1e-6) else 1.0
        return threshold, opacity_scale

    def _create_central_slice(self, source, dims_xyz: tuple[int, int, int]):
        center = [0.5 * max(dim - 1, 0) for dim in dims_xyz]
        # Keep the default FITS view explicit and stable: a central slice along Z.
        # This makes the initial plane perpendicular to the Z axis, regardless of
        # dataset anisotropy, and keeps future axis switching straightforward.
        z_normal = [0.0, 0.0, 1.0]

        slice_filter = simple.Slice(Input=source)
        slice_filter.SliceType = "Plane"
        slice_filter.SliceOffsetValues = [0.0]
        slice_filter.SliceType.Origin = center
        slice_filter.SliceType.Normal = z_normal
        logger.info(
            "Created central FITS slice along Z: input_source=%s origin=%s normal=%s dims_xyz=%s axis=%s",
            self._fits_source_label(source),
            center,
            z_normal,
            dims_xyz,
            "Z",
        )
        return slice_filter

    def _build_fits_request_information_script(self, dataset_path: str, downsample_factor: int = 1) -> str:
        project_root = os.getenv("PROJECT_ROOT", os.getcwd())
        payload = {
            "dataset_path": str(Path(dataset_path)),
            "project_root": str(Path(project_root)),
            "downsample_factor": max(1, int(downsample_factor)),
        }
        return f"""
import sys
payload = {json.dumps(payload)}
if payload["project_root"] not in sys.path:
    sys.path.insert(0, payload["project_root"])
from app.core.pythonpath import bootstrap_external_site_packages
bootstrap_external_site_packages()
from app.datasets.fits_reader import read_fits_data

result = read_fits_data(payload["dataset_path"], frame_index=0, downsample_factor=payload["downsample_factor"])
shape = result.shape
if len(shape) == 2:
    dims = (int(shape[1]), int(shape[0]), 1)
else:
    dims = (int(shape[2]), int(shape[1]), int(shape[0]))
outInfo = self.GetOutputInformation(0)
outInfo.Set(self.GetExecutive().WHOLE_EXTENT(), 0, dims[0]-1, 0, dims[1]-1, 0, dims[2]-1)
"""

    def _shape_to_xyz(self, shape: list[int]) -> tuple[int, int, int]:
        if len(shape) == 2:
            return (int(shape[1]), int(shape[0]), 1)
        if len(shape) >= 3:
            return (int(shape[2]), int(shape[1]), int(shape[0]))
        return (1, 1, 1)

    def _build_fits_script(self, dataset_path: str, downsample_factor: int = 1) -> str:
        project_root = os.getenv("PROJECT_ROOT", os.getcwd())
        payload = {
            "dataset_path": str(Path(dataset_path)),
            "project_root": str(Path(project_root)),
            "downsample_factor": max(1, int(downsample_factor)),
        }
        return f"""
import sys
payload = {json.dumps(payload)}
if payload["project_root"] not in sys.path:
    sys.path.insert(0, payload["project_root"])
from app.core.pythonpath import bootstrap_external_site_packages
bootstrap_external_site_packages()
from app.datasets.fits_reader import SCALAR_NAME, read_fits_data
from app.datasets.fits_to_vtk import numpy_to_vtk_image_data

fits_result = read_fits_data(payload["dataset_path"], frame_index=0, downsample_factor=payload["downsample_factor"])
vtk_image = numpy_to_vtk_image_data(fits_result.array, scalar_name=SCALAR_NAME)
output = self.GetOutputDataObject(0)
output.ShallowCopy(vtk_image)
"""

    def _rebuild_fits_derived_sources(self, source) -> None:
        assert self.pipeline is not None
        metadata = self.pipeline.dataset_metadata or {}
        dims_xyz = self.pipeline.preview_dimensions if self.pipeline.is_preview_mode else self.pipeline.full_dimensions

        for derived in (self.pipeline.fits_contour, self.pipeline.fits_slice):
            if derived is None:
                continue
            try:
                simple.Hide(derived, self.view)
            except Exception:
                pass
            try:
                simple.Delete(derived)
            except Exception:
                pass

        self.pipeline.fits_slice = None
        self.pipeline.fits_contour = None

        if int(metadata.get("naxis") or 0) < 3:
            return

        if dims_xyz is None:
            dims_xyz = self._source_dimensions(source)
        self.pipeline.fits_slice = self._create_central_slice(source, dims_xyz)
        self.pipeline.fits_slice.UpdatePipeline()
        contour_source, iso_value, iso_min, iso_max = self._create_fits_contour(source, metadata, self.pipeline.scalar_name)
        self.pipeline.fits_contour = contour_source
        self.pipeline.iso_min = iso_min
        self.pipeline.iso_max = iso_max
        self.pipeline.iso_value = max(iso_min, min(iso_max, self.pipeline.iso_value or iso_value))
        self.pipeline.fits_contour.Isosurfaces = [self.pipeline.iso_value]
        self.pipeline.fits_contour.UpdatePipeline()
        slice_details = self._inspect_dataset(self.pipeline.fits_slice, self.pipeline.scalar_name)
        logger.info(
            "Slice VTK diagnostics: input_source=%s bounds=%s arrays=%s active=%s range=%s",
            self._fits_source_label(source),
            slice_details["bounds"],
            slice_details["point_arrays"],
            slice_details["active_array"],
            slice_details["active_range"],
        )

    def load_full_resolution(self) -> bool:
        if self.pipeline is None or self.pipeline.dataset_type != "fits" or not self.pipeline.is_preview_mode:
            return False

        metadata = self.pipeline.dataset_metadata or {}
        dataset_path = str(metadata.get("path") or "")
        if not dataset_path:
            logger.warning("Full-resolution load requested without dataset path in metadata")
            return False

        if self.pipeline.full_source is None:
            self.pipeline.full_source, full_dimensions = self._create_fits_programmable_source(dataset_path, 1)
            self.pipeline.full_dimensions = full_dimensions
            logger.info(
                "Full-resolution source ready: full_dimensions=%s preview_dimensions=%s representation=%s",
                self.pipeline.full_dimensions,
                self.pipeline.preview_dimensions,
                self.current_representation,
            )

        self.pipeline.source = self.pipeline.full_source
        self.pipeline.is_preview_mode = False
        self.pipeline.vtk_dimensions = self.pipeline.full_dimensions
        self._rebuild_fits_derived_sources(self.pipeline.source)

        if self.current_representation == "Slice" and self.pipeline.fits_slice is not None:
            self._swap_display_source(self.pipeline.fits_slice, "Surface", reset_camera=False)
        elif self.current_representation == "Isocontour" and self.pipeline.fits_contour is not None:
            self._swap_display_source(self.pipeline.fits_contour, "Surface", reset_camera=False)
        else:
            source_representation = "Volume" if self.current_representation == "Volume" else "Outline"
            self._swap_display_source(self.pipeline.source, source_representation, reset_camera=False)

        self.apply_colormap(self.pipeline.current_colormap)
        logger.info(
            "Switched from preview to full source: representation=%s preview_dimensions=%s full_dimensions=%s",
            self.current_representation,
            self.pipeline.preview_dimensions,
            self.pipeline.full_dimensions,
        )
        return True

    def apply_colormap(self, preset: str) -> None:
        if self.pipeline is None:
            return

        self.pipeline.current_colormap = preset
        simple.ColorBy(self.pipeline.display, ("POINTS", self.pipeline.scalar_name))
        lut = simple.GetColorTransferFunction(self.pipeline.scalar_name)
        pwf = simple.GetOpacityTransferFunction(self.pipeline.scalar_name)
        lut.ApplyPreset(preset, True)
        active_details = self._inspect_dataset(self.pipeline.active_source, self.pipeline.scalar_name)
        self.pipeline.scalar_range = active_details["active_range"]
        lut.RescaleTransferFunction(*self.pipeline.scalar_range)
        pwf.RescaleTransferFunction(*self.pipeline.scalar_range)
        logger.info(
            "Applied scalar coloring: requested_colormap=%s applied_colormap=%s field=%s range=%s object=%s mode=%s",
            preset,
            self.pipeline.current_colormap,
            self.pipeline.scalar_name,
            self.pipeline.scalar_range,
            "contour"
            if self.pipeline.active_source is self.pipeline.fits_contour
            else "slice"
            if self.pipeline.active_source is self.pipeline.fits_slice
            else "source",
            "preview" if self.pipeline.is_preview_mode else "full",
        )
        if self.pipeline.dataset_type == "fits" and self.current_representation == "Volume":
            self._apply_fits_volume_transfer_functions()
        self._warn_if_missing_visible_array(active_details, self.pipeline.scalar_name)
        simple.Render(self.view)

    def set_representation(self, representation: str) -> None:
        if self.pipeline is None:
            return

        logger.info("Pipeline set_representation called with %s", representation)
        if self.pipeline.dataset_type == "fits":
            self._set_fits_representation(representation)
            return

        self._set_csv_representation(representation)

    def _set_fits_representation(self, representation: str) -> None:
        assert self.pipeline is not None
        normalized = representation if representation in {"Slice", "Volume", "Isocontour", "Outline"} else "Slice"
        logger.info("Applying FITS representation=%s", normalized)

        if normalized == "Slice" and self.pipeline.fits_slice is not None:
            self._swap_display_source(self.pipeline.fits_slice, "Surface")
        elif normalized == "Isocontour":
            self._set_fits_isocontour()
        else:
            source_representation = "Volume" if normalized == "Volume" else "Outline"
            self._swap_display_source(self.pipeline.source, source_representation)

        self.current_representation = normalized
        self.apply_colormap(self.pipeline.current_colormap)
        logger.info(
            "FITS representation set: representation=%s vtk_dimensions=%s",
            normalized,
            self.pipeline.vtk_dimensions,
        )
        if normalized == "Volume":
            self._apply_fits_volume_transfer_functions()
            simple.Render(self.view)

    def _set_csv_representation(self, representation: str) -> None:
        assert self.pipeline is not None
        normalized = representation if representation in {"Points", "Surface", "Outline"} else "Points"
        logger.info("Applying CSV representation=%s", normalized)
        if normalized == self.current_representation:
            self.pipeline.display.Representation = normalized
            simple.Render(self.view)
            return

        next_source = self.pipeline.csv_surface if normalized == "Surface" else self.pipeline.source
        self._swap_display_source(next_source, normalized)
        if normalized == "Points":
            self.pipeline.display.PointSize = 5.0
            self.pipeline.display.Opacity = 0.95
        self.current_representation = normalized
        self.apply_colormap(self.pipeline.current_colormap)
        logger.info("CSV representation set: %s", normalized)

    def _set_fits_isocontour(self) -> None:
        assert self.pipeline is not None
        if self.pipeline.fits_contour is None:
            logger.warning("Isocontour requested but contour source is not available")
            return
        self._swap_display_source(self.pipeline.fits_contour, "Surface")
        logger.info(
            "Applying representation=Isocontour iso_value=%s scalar_range=%s",
            self.pipeline.iso_value,
            self.pipeline.scalar_range,
        )

    def _swap_display_source(self, next_source, representation: str, reset_camera: bool = True) -> None:
        assert self.pipeline is not None
        self._hide_all_fits_objects()

        display = simple.Show(next_source, self.view)
        display.Representation = representation
        if representation != "Volume":
            display.Opacity = 1.0
        self.pipeline.active_source = next_source
        self.pipeline.display = display
        details = self._inspect_dataset(next_source, self.pipeline.scalar_name)
        logger.info(
            "Showing object=%s representation=%s mode=%s active_source=%s bounds=%s arrays(point=%s, cell=%s) active=%s range=%s",
            "contour"
            if next_source is self.pipeline.fits_contour
            else "slice"
            if next_source is self.pipeline.fits_slice
            else "source",
            representation,
            "preview" if self.pipeline.is_preview_mode else "full",
            self._fits_source_label(next_source),
            details["bounds"],
            details["point_arrays"],
            details["cell_arrays"],
            details["active_array"],
            details["active_range"],
        )
        if reset_camera:
            simple.ResetCamera(self.view)
        simple.Render(self.view)

    def set_volume_preset(self, preset: str) -> None:
        if self.pipeline is None or self.pipeline.dataset_type != "fits":
            return
        normalized = preset if preset in {"soft", "medium", "strong"} else "medium"
        self.pipeline.volume_preset = normalized
        logger.info("Volume preset set to %s", normalized)
        if self.current_representation == "Volume":
            self._apply_fits_volume_transfer_functions()
            simple.Render(self.view)

    def set_volume_threshold(self, threshold: float) -> None:
        if self.pipeline is None or self.pipeline.dataset_type != "fits":
            return
        self.pipeline.volume_threshold = max(0.0, min(1.0, float(threshold)))
        logger.info("Volume threshold set to %s", self.pipeline.volume_threshold)
        if self.current_representation == "Volume":
            self._apply_fits_volume_transfer_functions()
            simple.Render(self.view)

    def set_opacity_scale(self, opacity_scale: float) -> None:
        if self.pipeline is None or self.pipeline.dataset_type != "fits":
            return
        self.pipeline.opacity_scale = max(0.05, min(3.0, float(opacity_scale)))
        logger.info("Opacity scale set to %s", self.pipeline.opacity_scale)
        if self.current_representation == "Volume":
            self._apply_fits_volume_transfer_functions()
            simple.Render(self.view)

    def set_isocontour_value(self, iso_value: float) -> None:
        if self.pipeline is None or self.pipeline.dataset_type != "fits" or self.pipeline.fits_contour is None:
            return

        clamped = max(self.pipeline.iso_min, min(self.pipeline.iso_max, float(iso_value)))
        self.pipeline.iso_value = clamped
        self.pipeline.fits_contour.Isosurfaces = [clamped]
        self.pipeline.fits_contour.UpdatePipeline()
        logger.info(
            "Contour updated: iso_value=%s iso_range=(%s, %s)",
            self.pipeline.iso_value,
            self.pipeline.iso_min,
            self.pipeline.iso_max,
        )
        if self.current_representation == "Isocontour":
            self._swap_display_source(self.pipeline.fits_contour, "Surface")
            self.apply_colormap(self.pipeline.current_colormap)
            simple.Render(self.view)

    def reset_contrast(self) -> None:
        if self.pipeline is None:
            return
        logger.info("Resetting contrast for field=%s", self.pipeline.scalar_name)
        self.apply_colormap("Viridis (matplotlib)")
        simple.Render(self.view)

    def reset_camera(self) -> None:
        simple.ResetCamera(self.view)
        self.view.CenterOfRotation = self.view.CameraFocalPoint
        simple.Render(self.view)

    def _clear_pipeline(self) -> None:
        if self.pipeline is None:
            return

        for source in (
            self.pipeline.fits_contour,
            self.pipeline.fits_slice,
            self.pipeline.csv_surface,
            self.pipeline.preview_source,
            self.pipeline.full_source,
            self.pipeline.source,
        ):
            if source is None:
                continue
            try:
                simple.Hide(source, self.view)
            except Exception:
                pass
            try:
                simple.Delete(source)
            except Exception:
                pass
        self.pipeline = None

    def _inspect_dataset(self, source, scalar_name: str) -> dict[str, Any]:
        source.UpdatePipeline()
        vtk_object = servermanager.Fetch(source)
        if vtk_object is None:
            logger.error("Unable to fetch VTK object for diagnostics")
            return {
                "vtk_type": None,
                "extent": None,
                "bounds": None,
                "dimensions": None,
                "point_arrays": [],
                "cell_arrays": [],
                "active_array": None,
                "active_range": (0.0, 1.0),
            }

        point_data = vtk_object.GetPointData()
        cell_data = vtk_object.GetCellData()
        point_arrays = [point_data.GetArrayName(i) for i in range(point_data.GetNumberOfArrays())]
        cell_arrays = [cell_data.GetArrayName(i) for i in range(cell_data.GetNumberOfArrays())]
        active = point_data.GetScalars()
        active_name = active.GetName() if active is not None else None
        active_range = tuple(float(v) for v in active.GetRange()) if active is not None else (0.0, 1.0)

        dimensions = vtk_object.GetDimensions() if hasattr(vtk_object, "GetDimensions") else None
        extent = vtk_object.GetExtent() if hasattr(vtk_object, "GetExtent") else None
        bounds = vtk_object.GetBounds() if hasattr(vtk_object, "GetBounds") else None

        logger.info(
            "VTK diagnostics: type=%s extent=%s dimensions=%s bounds=%s point_arrays=%s cell_arrays=%s active=%s range=%s",
            vtk_object.GetClassName(),
            extent,
            dimensions,
            bounds,
            point_arrays,
            cell_arrays,
            active_name,
            active_range,
        )

        if scalar_name not in point_arrays:
            logger.warning("Expected scalar '%s' not found in PointData arrays: %s", scalar_name, point_arrays)
        if active is None:
            logger.error(
                "Could not determine active scalar range for '%s'. PointData arrays=%s CellData arrays=%s",
                scalar_name,
                point_arrays,
                cell_arrays,
            )

        return {
            "vtk_type": vtk_object.GetClassName(),
            "extent": extent,
            "dimensions": dimensions,
            "bounds": bounds,
            "point_arrays": point_arrays,
            "cell_arrays": cell_arrays,
            "active_array": active_name,
            "active_range": active_range if active is not None else (0.0, 1.0),
        }

    def _source_dimensions(self, source) -> tuple[int, int, int]:
        source.UpdatePipeline()
        data_info = source.GetDataInformation()
        extent = data_info.GetExtent()
        return (
            int(extent[1] - extent[0] + 1),
            int(extent[3] - extent[2] + 1),
            int(extent[5] - extent[4] + 1),
        )

    def _fits_source_label(self, source) -> str:
        if self.pipeline is None:
            return type(source).__name__
        if source is self.pipeline.preview_source:
            return "preview_source"
        if source is self.pipeline.full_source:
            return "full_source"
        if source is self.pipeline.fits_slice:
            return "slice"
        if source is self.pipeline.fits_contour:
            return "contour"
        if source is self.pipeline.source:
            return "source"
        return type(source).__name__

    def _hide_all_fits_objects(self) -> None:
        assert self.pipeline is not None
        if self.pipeline.dataset_type != "fits":
            try:
                simple.Hide(self.pipeline.active_source, self.view)
            except Exception:
                pass
            return

        for obj in (self.pipeline.fits_slice, self.pipeline.fits_contour, self.pipeline.preview_source, self.pipeline.full_source, self.pipeline.source):
            if obj is None:
                continue
            try:
                simple.Hide(obj, self.view)
            except Exception:
                pass

    def _warn_if_missing_visible_array(self, details: dict[str, Any], scalar_name: str) -> None:
        if scalar_name not in details["point_arrays"]:
            logger.warning(
                "Display object does not expose visible PointData scalar '%s'. Available arrays=%s",
                scalar_name,
                details["point_arrays"],
            )

    def _apply_fits_volume_transfer_functions(self) -> None:
        assert self.pipeline is not None
        details = self._inspect_dataset(self.pipeline.source, self.pipeline.scalar_name)
        full_range = details["active_range"]
        robust_low, robust_high = self._compute_robust_volume_range(full_range)

        lut = simple.GetColorTransferFunction(self.pipeline.scalar_name)
        pwf = simple.GetOpacityTransferFunction(self.pipeline.scalar_name)
        lut.ApplyPreset(self.pipeline.current_colormap, True)
        lut.RescaleTransferFunction(robust_low, robust_high)

        opacity_points = self._volume_opacity_points(robust_low, robust_high, self.pipeline.volume_preset)
        pwf_points = []
        for x, y in opacity_points:
            scaled_opacity = max(0.0, min(1.0, float(y) * self.pipeline.opacity_scale))
            pwf_points.extend([float(x), scaled_opacity, 0.5, 0.0])
        pwf.Points = pwf_points
        pwf.ScalarRangeInitialized = 1
        lut.ScalarRangeInitialized = 1
        rgb_points = list(getattr(lut, "RGBPoints", []))

        logger.info(
            "Volume rendering configured: object=%s requested_colormap=%s applied_colormap=%s full_range=%s robust_range=(%s, %s) threshold=%s opacity_scale=%s opacity_points=%s pwf_points=%s rgb_points=%s volume_preset=%s representation=%s",
            type(self.pipeline.source).__name__,
            self.pipeline.current_colormap,
            self.pipeline.current_colormap,
            full_range,
            robust_low,
            robust_high,
            self.pipeline.volume_threshold,
            self.pipeline.opacity_scale,
            opacity_points,
            pwf_points,
            rgb_points,
            self.pipeline.volume_preset,
            self.current_representation,
        )
        if max(point[1] for point in opacity_points) <= 0.0:
            logger.warning("Volume opacity transfer function is effectively zero")

    def _compute_robust_volume_range(self, full_range: tuple[float, float]) -> tuple[float, float]:
        assert self.pipeline is not None
        stats = (self.pipeline.dataset_metadata or {}).get("stats") or {}
        data_min, data_max = full_range
        mean = float(stats.get("mean", data_min))
        rms = abs(float(stats.get("rms", max(data_max - data_min, 1.0) * 0.1)))
        p75 = float(stats.get("p75", mean))
        p95 = float(stats.get("p95", data_max))
        p99 = float(stats.get("p99", data_max))

        # Astronomical FITS cubes are often noise-dominated. Use a lower bound driven
        # by background statistics and a high bound clamped by upper percentiles so
        # the LUT keeps contrast on real structures instead of stretching to outliers.
        robust_low = max(data_min, p75, mean + self.pipeline.volume_threshold * 2.0 * rms)
        robust_high = min(data_max, p99, max(p95, mean + 6.0 * rms))

        if robust_high <= robust_low:
            robust_low = data_min
            robust_high = data_max if data_max > data_min else data_min + 1.0
        logger.info(
            "Computed robust volume range: full_range=%s mean=%s rms=%s p75=%s p95=%s p99=%s threshold=%s robust_range=(%s, %s) lut=%s",
            full_range,
            mean,
            rms,
            p75,
            p95,
            p99,
            self.pipeline.volume_threshold,
            robust_low,
            robust_high,
            self.pipeline.current_colormap,
        )
        return float(robust_low), float(robust_high)

    def _volume_opacity_points(self, low: float, high: float, preset: str) -> list[tuple[float, float]]:
        span = max(high - low, 1e-6)
        strength = {
            "soft": (0.02, 0.12, 0.28),
            "medium": (0.04, 0.22, 0.45),
            "strong": (0.08, 0.35, 0.65),
        }.get(preset, (0.04, 0.22, 0.45))
        return [
            (low - 0.10 * span, 0.0),
            (low, 0.0),
            (low + 0.20 * span, strength[0]),
            (low + 0.55 * span, strength[1]),
            (high, strength[2]),
        ]
