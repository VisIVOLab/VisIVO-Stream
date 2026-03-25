# Architecture Notes

## Active Architecture

VisIVO-Stream is organized around a ParaView-first remote rendering model:

- `pvserver` is the primary rendering backend
- FastAPI manages sessions, state, and orchestration
- `trame` provides the browser-facing interactive viewer
- FITS support is treated as a first-class scientific workflow

FastAPI is not the renderer. It is the control plane.

## Layers

### 1. API And Session Management

Responsibilities:

- health and dataset endpoints
- creation and teardown of interactive sessions
- launch policy for local, attached, and future MPI-backed `pvserver` execution
- future integration point for authentication, authorization, and job control

Key files:

- `backend/app/api/routes.py`
- `backend/app/interactive/session_manager.py`
- `backend/app/interactive/launcher.py`

### 2. Rendering Layer

Responsibilities:

- execute ParaView pipelines server-side
- load CSV and FITS datasets
- serve remote interactive rendering through `pvserver`
- provide non-interactive PNG rendering as fallback/export support

Primary backend:

- ParaView `pvserver`

Fallback backend:

- ParaView `pvpython` driven from `backend/scripts/render_dataset.py`

### 3. Dataset Layer

Responsibilities:

- dataset discovery
- runtime browsing and registration of FITS datasets from a server-side root
- FITS IO through `astropy.io.fits`
- metadata extraction and validation
- conversion from NumPy arrays to `vtkImageData`

Compatibility constraint:

- ParaView 6.0.1 currently requires NumPy 1.x for the FITS programmable pipeline path.
- NumPy 2.x removes APIs still used by `vtkmodules.numpy_interface`, including `numpy.in1d`.
- Any backend or external site-packages exposed to `pvpython` or `pvserver` must therefore stay on `numpy<2` until the ParaView/VTK stack is upgraded.

Key files:

- `backend/app/services/datasets.py`
- `backend/app/datasets/fits_reader.py`
- `backend/app/datasets/fits_metadata.py`
- `backend/app/datasets/fits_to_vtk.py`

### 4. Interactive Viewer Layer

Responsibilities:

- connect the browser viewer to `pvserver`
- expose view controls and representation changes
- allow runtime dataset selection through the API and remote filesystem browser
- drive remote ParaView state through a trame application

Key files:

- `backend/app/interactive/trame_viewer.py`
- `backend/app/interactive/pipeline.py`

## FITS Visualization Policy

Current defaults are intentionally conservative and visibility-oriented:

- FITS 2D: image/slice-oriented display
- FITS 3D: central slice as the default initial representation
- FITS 3D alternatives: volume rendering, outline, and isocontour
- FITS 4D: first frame only for now

Volume rendering uses robust statistics and percentiles so astronomical cubes remain visible without relying only on absolute min/max values.

The active interactive workflow keeps a single authoritative ParaView source for FITS data:

- `Slice` -> `fits_slice`
- `Volume` -> `source`
- `Isocontour` -> `fits_contour`
- `Outline` -> `source`

Runtime dataset loading rebuilds that same pipeline cleanly rather than creating parallel viewer states.

For large FITS datasets, `Volume` can use a temporary memory-safe preview source generated on demand from an early downsampled NumPy array. This preview is isolated to the `Volume` path, can be released explicitly, and does not change the steady-state ParaView model used by `Slice`, `Isocontour`, or `Outline`.

The main runtime workflow is now:

- browse the remote server filesystem under `REMOTE_DATA_ROOT`
- select an existing FITS file
- register/load it through the API
- rebuild the same single-source ParaView pipeline in-place

## Deployment Modes

### Local MVP

- FastAPI and `pvserver` run on the same machine
- trame viewer is launched as a separate local web app
- datasets live under `backend/data/samples`

### Remote Deployment

- FastAPI on an application host
- `pvserver` on a remote visualization host
- viewer exposed separately, still connected to the same ParaView backend

### HPC With MPI

- FastAPI on login/service infrastructure
- `pvserver` launched through `mpiexec` or `srun`
- shared storage for datasets
- future scheduler and policy integration layered on top of the existing session manager

## What Is No Longer Active

The repository no longer treats a separate React frontend scaffold as part of the main architecture.

The old frontend and Docker-based prototype assets were moved under:

- `archive/legacy-web-prototype/`

They are retained only as historical reference and are not part of the active workflow.

## Architectural Rationale

This layout keeps responsibilities clean:

- ParaView handles rendering
- FastAPI handles orchestration
- trame handles remote browser interaction
- FITS support remains modular and can later migrate into a ParaView Python plugin or a C++ plugin if needed

## Next Steps

- persistent interactive sessions
- better process cleanup and fault handling
- scheduler adapters for Slurm-based execution
- authentication and multi-user policy
- optional future embedding of the trame viewer into another web shell, without reintroducing a second active frontend stack
