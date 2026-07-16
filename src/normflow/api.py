"""FastAPI layer — thin adapter over MappingService."""

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

from .batch_import import BatchImportRunNotFoundError, ProjectBusyError, RunStatus
from .embedding_model import EmbeddingModelUnavailableError

from .mapping_service import (
    BulkAcceptError,
    BulkAcceptPersistenceError,
    BulkAcceptStaleItemsError,
    MappingService,
    ReviewItemNotFoundError,
)
from .project import Project
from .semantic_index import SemanticIndexStatus

router = APIRouter()


class BulkAcceptRequest(BaseModel):
    review_item_ids: list[StrictInt]


class BulkAcceptResponse(BaseModel):
    accepted: int


class AcceptReviewItemRequest(BaseModel):
    normalized_text: str | None = None


class ProjectInfoResponse(BaseModel):
    project: str
    database: str
    mappings: int
    review_items: int
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class ImportMappingsResponse(BaseModel):
    imported: int
    skipped: int


class ReviewItemResponse(BaseModel):
    id: int
    raw_text: str
    suggested_text: str


class StatusResponse(BaseModel):
    status: str


class IndexBuildResponse(BaseModel):
    entries: int


class BatchImportResultResponse(BaseModel):
    auto_committed: int
    review_items: int
    skipped: int
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class BatchImportRunResponse(BaseModel):
    id: str
    status: RunStatus
    input_name: str
    input_fingerprint: str
    created_at: str
    started_at: str
    updated_at: str
    terminal_at: str | None
    result: BatchImportResultResponse | None
    error: str | None
    replacement_run_id: str | None


@asynccontextmanager
async def _temporary_upload_csv(file: UploadFile | None):
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
        f.write(await file.read())
        csv_path = Path(f.name)

    try:
        yield csv_path
    finally:
        csv_path.unlink(missing_ok=True)


def get_project_service(request: Request) -> MappingService:
    """Return the canonical Project service bound to this application."""
    return request.app.state.project_service


def _project_busy_response(request: Request, error: ProjectBusyError) -> JSONResponse:
    """Translate the shared Project writer conflict for every HTTP adapter."""
    active = error.active_run
    if active is None:
        active = get_project_service(request).active_batch_import_run()
    detail: str | dict = str(error)
    headers = None
    if active:
        detail = {"message": str(error), "active_run": active}
        headers = {"Location": f"/batch-import-runs/{active['id']}"}
    return JSONResponse(status_code=409, content={"detail": detail}, headers=headers)


@router.get("/project/info", response_model=ProjectInfoResponse)
def project_info(
    service: MappingService = Depends(get_project_service),
) -> ProjectInfoResponse:
    return ProjectInfoResponse(**service.project_info())


@router.post("/import/mappings", response_model=ImportMappingsResponse)
async def import_mappings(
    source_column: str = Query(...),
    target_column: str = Query(...),
    file: UploadFile | None = None,
    service: MappingService = Depends(get_project_service),
) -> ImportMappingsResponse:
    async with _temporary_upload_csv(file) as csv_path:
        try:
            imported, skipped = service.import_mappings(
                str(csv_path), source_column, target_column
            )
            return ImportMappingsResponse(imported=imported, skipped=skipped)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/import/records", include_in_schema=False)
def legacy_import_records(request: Request) -> RedirectResponse:
    """Redirect the former Batch endpoint to the durable canonical resource."""
    column = request.query_params.get("column", "")
    target = f"/batch-import-runs?{urlencode({'column': column})}"
    return RedirectResponse(target, status_code=307)


@router.post(
    "/batch-import-runs",
    status_code=202,
    response_model=BatchImportRunResponse,
)
async def start_batch_import_run(
    response: Response,
    column: str = Query(...),
    file: UploadFile | None = None,
    service: MappingService = Depends(get_project_service),
) -> BatchImportRunResponse:
    async with _temporary_upload_csv(file) as csv_path:
        try:
            run = service.start_batch_import(csv_path, column)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    response.headers["Location"] = f"/batch-import-runs/{run['id']}"
    return BatchImportRunResponse(**run)


@router.get("/batch-import-runs", response_model=BatchImportRunResponse)
def latest_batch_import_run_status(
    service: MappingService = Depends(get_project_service),
) -> BatchImportRunResponse:
    try:
        return BatchImportRunResponse(**service.batch_import_status())
    except BatchImportRunNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/batch-import-runs/{run_id}/retry",
    status_code=202,
    response_model=BatchImportRunResponse,
)
async def retry_batch_import_run(
    run_id: str,
    response: Response,
    column: str = Query(...),
    file: UploadFile | None = None,
    service: MappingService = Depends(get_project_service),
) -> BatchImportRunResponse:
    async with _temporary_upload_csv(file) as csv_path:
        try:
            run = service.start_batch_import_retry(run_id, csv_path, column)
        except BatchImportRunNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    response.headers["Location"] = f"/batch-import-runs/{run['id']}"
    return BatchImportRunResponse(**run)


@router.get(
    "/batch-import-runs/{run_id}",
    response_model=BatchImportRunResponse,
)
def batch_import_run_status(
    run_id: str,
    service: MappingService = Depends(get_project_service),
) -> BatchImportRunResponse:
    try:
        return BatchImportRunResponse(**service.batch_import_status(run_id))
    except BatchImportRunNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/review-items", response_model=list[ReviewItemResponse])
def list_review_items(
    service: MappingService = Depends(get_project_service),
) -> list[ReviewItemResponse]:
    return [ReviewItemResponse(**item) for item in service.list_review_items()]


@router.post("/review-items/bulk-accept", response_model=BulkAcceptResponse)
def bulk_accept_review_items(
    request: BulkAcceptRequest,
    service: MappingService = Depends(get_project_service),
) -> BulkAcceptResponse:
    try:
        result = service.accept_review_items(request.review_item_ids)
        return BulkAcceptResponse(accepted=result.accepted)
    except BulkAcceptStaleItemsError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except BulkAcceptPersistenceError as error:
        raise HTTPException(status_code=500, detail=str(error))
    except BulkAcceptError as error:
        raise HTTPException(status_code=422, detail=str(error))


@router.post(
    "/review-items/{review_item_id}/accept",
    response_model=StatusResponse,
)
def accept_review_item(
    review_item_id: int,
    request: AcceptReviewItemRequest | None = None,
    service: MappingService = Depends(get_project_service),
) -> StatusResponse:
    try:
        normalized_text = request.normalized_text if request else None
        service.accept_review_item(review_item_id, normalized_text)
        return StatusResponse(status="accepted")
    except ReviewItemNotFoundError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

@router.post("/export")
def export_normalized(
    source_column: str = Query("raw_text"),
    output_column: str = Query("normalized_text"),
    service: MappingService = Depends(get_project_service),
) -> PlainTextResponse:
    try:
        csv_content = service.export_normalized_csv(source_column, output_column)
        return PlainTextResponse(content=csv_content, media_type="text/csv")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/index/build", response_model=IndexBuildResponse)
def build_index(
    service: MappingService = Depends(get_project_service),
) -> IndexBuildResponse:
    try:
        count = service.build_index()
        return IndexBuildResponse(entries=count)
    except EmbeddingModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@router.post("/index/clear", response_model=StatusResponse)
def clear_index(
    service: MappingService = Depends(get_project_service),
) -> StatusResponse:
    service.clear_index()
    return StatusResponse(status="cleared")


_static_dir = Path(__file__).with_name("static")


@router.get("/", include_in_schema=False)
def ui_index() -> FileResponse:
    return FileResponse(_static_dir / "index.html")


def create_app(project: Project) -> FastAPI:
    """Construct an HTTP application bound to one canonical Project."""
    project_app = FastAPI(title="NormFlow", redirect_slashes=False)
    project_app.add_exception_handler(ProjectBusyError, _project_busy_response)
    project_app.state.project_service = MappingService(str(project.root))
    project_app.include_router(router)
    project_app.mount(
        "/assets",
        StaticFiles(directory=_static_dir / "assets"),
        name="ui-assets",
    )
    return project_app
