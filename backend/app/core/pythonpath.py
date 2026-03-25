import logging
import os
import site
import sys
from pathlib import Path


logger = logging.getLogger(__name__)


def _prepend_site_package(path: str) -> None:
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
    # Keep .pth processing, but restore precedence afterward because addsitedir
    # appends paths near the end and ParaView's embedded stdlib can otherwise win.
    site.addsitedir(path)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


def bootstrap_external_site_packages() -> list[str]:
    added_paths: list[str] = []

    explicit_paths = os.getenv("EXTERNAL_PYTHON_SITE_PACKAGES") or os.getenv("TRAME_SITE_PACKAGES")
    if explicit_paths:
        for item in explicit_paths.split(os.pathsep):
            if item and Path(item).exists():
                _prepend_site_package(item)
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
            candidate_str = str(candidate)
            _prepend_site_package(candidate_str)
            added_paths.append(candidate_str)

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
