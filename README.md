# VisIVO-Stream

VisIVO-Stream is a ParaView-centered remote visualization platform for scientific 3D data.

The active architecture is built around:

- FastAPI as API, session, and orchestration layer
- ParaView `pvserver` as the main interactive rendering backend
- `trame` as the browser-facing remote viewer
- FITS support as a primary scientific data path
- a local-to-HPC evolution path, including future MPI-aware deployment

## Architecture Decision

**Chosen stack**

- API and orchestration: FastAPI + Uvicorn
- Interactive rendering backend: ParaView `pvserver`
- Interactive web viewer: `trame` + ParaView `VtkRemoteView`
- Dataset support: CSV samples and FITS via `astropy.io.fits`
- Fallback non-interactive rendering: `pvpython` + PNG preview/export

**Core components**

- `backend/app/api/`: REST API for health, dataset catalog, fallback preview sessions, and interactive session orchestration
- `backend/app/core/`: configuration, logging, Python path bootstrap helpers
- `backend/app/datasets/`: FITS reading, metadata extraction, and NumPy-to-VTK conversion
- `backend/app/interactive/`: `pvserver` launch/attach logic, session management, ParaView pipeline control, trame viewer
- `backend/app/models/`: request/response and dataset models
- `backend/app/services/`: dataset catalog and PNG fallback rendering service
- `backend/scripts/`: local entrypoints for preview/export and interactive viewing

**Request flow**

Interactive mode:

1. FastAPI creates or attaches a `pvserver` session.
2. The session manager returns session metadata, host, and port.
3. The trame viewer connects to `pvserver`.
4. ParaView executes the remote pipeline.
5. The browser receives an interactive remote render view.

Fallback preview mode:

1. A client calls FastAPI preview endpoints.
2. FastAPI invokes `pvpython`.
3. A PNG preview is generated and served from `/renders`.

**Why this architecture**

- `pvserver` is the right primary backend for remote and future MPI/HPC execution.
- FastAPI stays out of the rendering hot path and focuses on orchestration.
- `trame` provides an idiomatic ParaView web client without reintroducing a separate frontend stack.
- PNG rendering remains useful as preview, export, and operational fallback, but it is no longer the main interaction model.

## Repository Structure

```text
VisIVO-Stream/
├── README.md
├── TODO.md
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── requirements-interactive.txt
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── datasets/
│   │   ├── interactive/
│   │   ├── models/
│   │   ├── services/
│   │   └── main.py
│   ├── data/
│   │   └── samples/
│   ├── renders/
│   ├── runtime/
│   │   └── interactive/
│   ├── scripts/
│   └── tests/
├── docs/
│   ├── api.md
│   ├── architecture.md
│   └── repository_cleanup.md
└── archive/
    └── legacy-web-prototype/
```

`archive/legacy-web-prototype/` contains the old React/Docker scaffold from the prototype phase. It is not part of the active architecture.

## Active Modes

### Interactive Mode

This is the primary workflow.

- backend FastAPI on `localhost:8000`
- `pvserver` launched locally or attached remotely
- trame viewer on `localhost:8081`
- CSV and FITS datasets available from `backend/data/samples`

### Fallback Preview Mode

This is a secondary workflow kept for utility.

- non-interactive PNG generation through `pvpython`
- useful for quick preview, export, diagnostics, and environments where a full interactive viewer is not desired

## Deployment Targets

### Local MVP

- FastAPI and `pvserver` on the same workstation
- local sample datasets
- trame viewer launched separately

### Remote Deployment

- FastAPI on an application or service host
- `pvserver` on a remote visualization node
- trame viewer exposed through a browser-accessible endpoint

### HPC with MPI

- FastAPI on login/service infrastructure
- `pvserver` launched via `mpiexec` or `srun`
- shared filesystem or HPC data path
- future extension for scheduler integration, auth, and policy control

## Local Prerequisites

- Python 3.11+
- ParaView installed locally with:
  - `pvpython`
  - `pvserver`
- Python dependencies installed in the backend virtualenv
- `trame` available to the Python runtime used by the interactive viewer
- `astropy` available for FITS handling
- NumPy 1.x for ParaView 6.0.1 compatibility

Quick checks:

```bash
pvpython --version
pvserver --version
python3 --version
```

Compatibility note:

- ParaView 6.0.1 is not compatible with NumPy 2.x in the FITS programmable pipeline because VTK Python code still relies on APIs such as `numpy.in1d`.
- Keep backend and ParaView-exposed site-packages on `numpy<2`.
- The project requirements now pin NumPy accordingly, and runtime bootstrap emits a warning if it detects NumPy 2.x with ParaView 6.0.1.

## Local Setup

### 1. Backend API

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-interactive.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

If your ParaView Python runtime does not ship with `pip`, keep `trame` and `astropy` in the backend `.venv` and let the bootstrap logic expose those packages to `pvpython`.

When using ParaView 6.0.1, make sure the exposed environment also resolves to `numpy<2`. A mixed setup with `pvpython` plus external `site-packages` from a NumPy 2.x environment will break FITS programmable sources.

### 2. Interactive Viewer

With FastAPI already running:

```bash
cd backend
source .venv/bin/activate
./scripts/run_interactive_viewer.sh --create-session --dataset-id galaxy_points
```

The viewer will be available at:

- `http://127.0.0.1:8081`

### 3. Fallback Preview Rendering

The fallback mode remains available through the API and ParaView render script:

```bash
cd backend
source .venv/bin/activate
python scripts/render_dataset.py --help
```

## FITS Support

Supported semantics:

- FITS 2D: image/slice visualization
- FITS 3D: central slice by default, with volume and isocontour available on the same authoritative ParaView source
- FITS 4D: first frame of the fourth axis is used for now

The FITS pipeline extracts:

- `shape`
- `naxis`
- original dtype
- selected FITS header fields
- finite-only statistics
- robust percentiles for viewer defaults

For FITS 3D, the interactive pipeline uses a single authoritative ParaView source. Optional input downsampling can still be enabled before source creation via `FITS_PREVIEW_FACTOR`; the default is `1`, which keeps the source at full resolution.

## Main API Endpoints

Base path: `/api/v1`

- `GET /health`
- `GET /datasets`
- `GET /datasets/{dataset_id}/metadata`
- `GET /datasets/{dataset_id}/fits-header`
- `POST /sessions`
- `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/render`
- `GET /interactive/sessions`
- `POST /interactive/sessions`
- `GET /interactive/sessions/{session_id}`
- `DELETE /interactive/sessions/{session_id}`

See [docs/api.md](/Users/fvitello/Documents/GitHub/VisIVO-Stream/docs/api.md) for details.

## Current Priorities

- keep the interactive `pvserver` workflow as the main path
- keep PNG rendering only as fallback/export support
- keep the repository compact and easy to understand
- preserve a clear migration path from local workstation to remote/HPC deployment

## Cleanup Status

Repository cleanup notes are documented in [docs/repository_cleanup.md](/Users/fvitello/Documents/GitHub/VisIVO-Stream/docs/repository_cleanup.md).
