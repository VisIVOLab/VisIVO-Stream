from typing import Sequence

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import VTK_FLOAT
from vtkmodules.vtkCommonDataModel import vtkImageData


def numpy_to_vtk_image_data(
    array: np.ndarray,
    scalar_name: str = "FITSImage",
    spacing: Sequence[float] = (1.0, 1.0, 1.0),
    origin: Sequence[float] = (0.0, 0.0, 0.0),
):
    volume = np.asarray(array, dtype=np.float32)
    if volume.ndim == 2:
        dims = (int(volume.shape[1]), int(volume.shape[0]), 1)
    elif volume.ndim == 3:
        dims = (int(volume.shape[2]), int(volume.shape[1]), int(volume.shape[0]))
    else:
        raise ValueError(f"vtkImageData conversion supports 2D or 3D arrays, got shape {volume.shape}")

    vtk_image = vtkImageData()
    vtk_image.SetDimensions(*dims)
    vtk_image.SetExtent(0, dims[0] - 1, 0, dims[1] - 1, 0, dims[2] - 1)
    vtk_image.SetSpacing(*spacing)
    vtk_image.SetOrigin(*origin)

    vtk_array = numpy_to_vtk(np.ravel(volume, order="C"), deep=True, array_type=VTK_FLOAT)
    vtk_array.SetName(scalar_name)
    vtk_image.GetPointData().SetScalars(vtk_array)
    vtk_image.GetPointData().SetActiveScalars(scalar_name)
    vtk_image.Modified()
    return vtk_image
