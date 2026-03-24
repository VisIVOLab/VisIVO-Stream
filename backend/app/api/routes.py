from fastapi import APIRouter

from app.models.interactive import InteractiveSessionCreateRequest
from app.models.render import CreateSessionRequest, RenderRequest
from app.interactive.session_manager import InteractiveSessionManager
from app.services.paraview_service import ParaViewRenderService


router = APIRouter()
service = ParaViewRenderService()
interactive_sessions = InteractiveSessionManager()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/datasets")
def list_datasets():
    return [
        {
            "id": dataset.id,
            "name": dataset.name,
            "description": dataset.description,
            "file_name": dataset.file_name,
            "dataset_type": dataset.dataset_type,
            "scalar_fields": dataset.scalar_fields,
            "point_count_hint": dataset.point_count_hint,
        }
        for dataset in service.list_datasets()
    ]


@router.get("/datasets/{dataset_id}/metadata")
def get_dataset_metadata(dataset_id: str):
    return service.get_dataset_metadata(dataset_id)


@router.get("/datasets/{dataset_id}/fits-header")
def get_dataset_fits_header(dataset_id: str):
    return service.get_fits_header(dataset_id)


@router.post("/sessions")
def create_session(payload: CreateSessionRequest):
    session = service.create_session(payload.dataset_id)
    return session


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    return service.get_session(session_id)


@router.post("/sessions/{session_id}/render")
def render_session(session_id: str, payload: RenderRequest):
    return service.render(session_id, payload)


@router.get("/interactive/sessions")
def list_interactive_sessions():
    return interactive_sessions.list_sessions()


@router.post("/interactive/sessions")
def create_interactive_session(payload: InteractiveSessionCreateRequest):
    return interactive_sessions.create_session(payload)


@router.get("/interactive/sessions/{session_id}")
def get_interactive_session(session_id: str):
    return interactive_sessions.get_session(session_id)


@router.delete("/interactive/sessions/{session_id}")
def stop_interactive_session(session_id: str):
    return interactive_sessions.stop_session(session_id)
