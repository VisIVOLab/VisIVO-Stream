import os
import site
from pathlib import Path


def bootstrap_external_site_packages() -> list[str]:
    added_paths: list[str] = []

    explicit_paths = os.getenv("EXTERNAL_PYTHON_SITE_PACKAGES") or os.getenv("TRAME_SITE_PACKAGES")
    if explicit_paths:
        for item in explicit_paths.split(os.pathsep):
            if item and Path(item).exists():
                site.addsitedir(item)
                added_paths.append(item)
        return added_paths

    virtual_env = os.getenv("VIRTUAL_ENV")
    if not virtual_env:
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

    return added_paths
