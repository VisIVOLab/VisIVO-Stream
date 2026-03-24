import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.pythonpath import bootstrap_external_site_packages

bootstrap_external_site_packages()

from paraview.simple import (
    CSVReader,
    ColorBy,
    CreateView,
    Delaunay3D,
    ExtractSurface,
    GetActiveCamera,
    GetColorTransferFunction,
    GetOpacityTransferFunction,
    Hide,
    ProgrammableSource,
    Render,
    ResetCamera,
    SaveScreenshot,
    Show,
    TableToPoints,
    Threshold,
)


def apply_threshold(source, filter_params):
    if not filter_params.get("enabled") or filter_params.get("kind") != "threshold":
        return source

    scalar_field = filter_params.get("scalar_field", "density")
    threshold = Threshold(Input=source)
    threshold.Scalars = ["POINTS", scalar_field]
    threshold.LowerThreshold = filter_params.get("lower", 0.0)
    threshold.UpperThreshold = filter_params.get("upper", 1.0)
    return threshold


def create_csv_source(dataset_path, parameters, view):
    reader = CSVReader(FileName=[dataset_path])
    points = TableToPoints(Input=reader)
    points.XColumn = "x"
    points.YColumn = "y"
    points.ZColumn = "z"
    source = apply_threshold(points, parameters.get("filter", {}))
    display = Show(source, view, "GeometryRepresentation")
    display.Representation = "Points"
    display.PointSize = parameters.get("point_size", 5.0)
    display.Opacity = parameters.get("opacity", 0.9)
    scalar_name = parameters.get("color_by", "density")
    scalar_range = (0.0, 1.0)
    if source is not points:
        Hide(points, view)
    return source, display, scalar_name, scalar_range


def create_fits_source(dataset_path, view):
    source = ProgrammableSource()
    source.OutputDataSetType = "vtkImageData"
    source.Script = f"""
import sys
project_root = {repr(str(ROOT))}
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from app.core.pythonpath import bootstrap_external_site_packages
bootstrap_external_site_packages()
from app.datasets.fits_reader import SCALAR_NAME, read_fits_data
from app.datasets.fits_to_vtk import numpy_to_vtk_image_data

result = read_fits_data({repr(dataset_path)}, frame_index=0)
vtk_image = numpy_to_vtk_image_data(result.array, scalar_name=SCALAR_NAME)
output = self.GetOutputDataObject(0)
output.ShallowCopy(vtk_image)
"""
    source.UpdatePipeline()
    display = Show(source, view)
    display.Representation = "Slice"
    scalar_name = "FITSImage"

    from app.datasets.fits_metadata import extract_fits_metadata

    metadata = extract_fits_metadata(dataset_path)
    scalar_range = (
        float(metadata.stats.get("min", 0.0)),
        float(metadata.stats.get("max", 1.0)) if float(metadata.stats.get("max", 1.0)) > float(metadata.stats.get("min", 0.0)) else float(metadata.stats.get("min", 0.0)) + 1.0,
    )
    return source, display, scalar_name, scalar_range


def main():
    payload = json.loads(sys.argv[1])
    dataset_path = payload["dataset_path"]
    output_path = payload["output_path"]
    parameters = payload["parameters"]

    view = CreateView("RenderView")
    view.ViewSize = [parameters.get("image_width", 1280), parameters.get("image_height", 720)]
    view.Background = [0.05, 0.08, 0.12]
    view.OrientationAxesVisibility = 1
    view.CameraParallelProjection = 0

    if dataset_path.lower().endswith((".fits", ".fit")):
        source, display, scalar_name, scalar_range = create_fits_source(dataset_path, view)
    else:
        source, display, scalar_name, scalar_range = create_csv_source(dataset_path, parameters, view)

    ColorBy(display, ("POINTS", scalar_name))
    lut = GetColorTransferFunction(scalar_name)
    pwf = GetOpacityTransferFunction(scalar_name)
    lut.ApplyPreset(parameters.get("colormap", "Viridis (matplotlib)"), True)
    lut.RescaleTransferFunction(*scalar_range)
    pwf.RescaleTransferFunction(*scalar_range)

    ResetCamera(view)
    camera = GetActiveCamera()
    camera.Azimuth(parameters.get("camera", {}).get("azimuth", 35.0))
    camera.Elevation(parameters.get("camera", {}).get("elevation", 25.0))
    camera.Zoom(parameters.get("camera", {}).get("zoom", 1.25))

    Render(view)
    SaveScreenshot(output_path, view, ImageResolution=view.ViewSize)
    print(f"Rendered {dataset_path} -> {output_path}")


if __name__ == "__main__":
    main()
