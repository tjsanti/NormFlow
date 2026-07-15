"""FastAPI layer — thin adapter over MappingService."""

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

from .mapping_service import (
    BatchImportError,
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


class ImportRecordsResponse(BaseModel):
    auto_committed: int
    review_items: int
    skipped: int
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class ReviewItemResponse(BaseModel):
    id: int
    raw_text: str
    suggested_text: str


class StatusResponse(BaseModel):
    status: str


class IndexBuildResponse(BaseModel):
    entries: int


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
        imported, skipped = service.import_mappings(str(csv_path), source_column, target_column)
        return ImportMappingsResponse(imported=imported, skipped=skipped)


@router.post("/import/records", response_model=ImportRecordsResponse)
async def import_records(
    column: str = Query(...),
    semantic: bool = Query(True),
    llm: bool = Query(True),
    threshold: float = Query(0.85),
    file: UploadFile | None = None,
    service: MappingService = Depends(get_project_service),
) -> ImportRecordsResponse:
    async with _temporary_upload_csv(file) as csv_path:
        try:
            result = service.import_records_for_review(
                str(csv_path), column, semantic=semantic, llm=llm, threshold=threshold
            )
            return ImportRecordsResponse(**result)
        except BatchImportError as error:
            raise HTTPException(status_code=502, detail=str(error))


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
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


_static_dir = Path(__file__).with_name("static")


@router.get("/", include_in_schema=False)
def ui_index() -> FileResponse:
    return FileResponse(_static_dir / "index.html")


def create_app(project: Project) -> FastAPI:
    """Construct an HTTP application bound to one canonical Project."""
    project_app = FastAPI(title="NormFlow", redirect_slashes=False)
    project_app.state.project_service = MappingService(str(project.root))
    project_app.include_router(router)
    project_app.mount(
        "/assets",
        StaticFiles(directory=_static_dir / "assets"),
        name="ui-assets",
    )
    return project_app
