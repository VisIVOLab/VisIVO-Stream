import logging
import os
import site
from pathlib import Path


logger = logging.getLogger(__name__)


def bootstrap_external_site_packages() -> list[str]:
    added_paths: list[str] = []

    explicit_paths = os.getenv("EXTERNAL_PYTHON_SITE_PACKAGES") or os.getenv("TRAME_SITE_PACKAGES")
    if explicit_paths:
        for item in explicit_paths.split(os.pathsep):
            if item and Path(item).exists():
                site.addsitedir(item)
                added_paths.append(item)
        warn_if_numpy_paraview_incompatible()
        return added_paths

    virtual_env = os.getenv("VIRTUAL_ENV")
    if not virtual_env:
        warn_if_numpy_paraview_incompatible()
        return added_paths

    venv_path = Path(virtual_env)
    lib_dir = venv_path / "lib"
    candidates = []
    if lib_dir.exists():
        candidates.extend(sorted(lib_dir.glob("python*/site-packages")))
    candidates.append(venv_path / "Lib" / "site-packages")

    for candidate in candidates:
        if candidate.exists():
            site.addsitedir(str(candidate))
            added_paths.append(str(candidate))

    warn_if_numpy_paraview_incompatible()
    return added_paths


def warn_if_numpy_paraview_incompatible() -> None:
    try:
        import numpy as np
    except Exception:
        return

    major = int(str(np.__version__).split(".", 1)[0])
    if major < 2:
        return

    paraview_version = os.getenv("PARAVIEW_VERSION", "").strip()
    if not paraview_version:
        try:
            import paraview  # type: ignore

            paraview_version = str(getattr(paraview, "__version__", "") or "")
        except Exception:
            paraview_version = ""

    if paraview_version.startswith("6.0.1"):
        logger.warning(
            "Detected NumPy %s with ParaView %s. ParaView 6.0.1 and vtkmodules.numpy_interface "
            "still expect NumPy 1.x APIs such as numpy.in1d. Use numpy<2 in the backend and any "
            "external site-packages exposed to pvpython/pvserver.",
            np.__version__,
            paraview_version,
        )
