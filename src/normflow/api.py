"""FastAPI layer — thin adapter over MappingService."""

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

from .mapping_service import (
    BulkAcceptError,
    BulkAcceptPersistenceError,
    BulkAcceptStaleItemsError,
    MappingService,
    ReviewItemNotFoundError,
)

app = FastAPI(title="NormFlow", redirect_slashes=False)


class BulkAcceptRequest(BaseModel):
    review_item_ids: list[StrictInt]


class BulkAcceptResponse(BaseModel):
    accepted: int


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


def _get_workspace(workspace: str = Header(alias="X-Normflow-Workspace")) -> MappingService:
    """Extract workspace from X-Normflow-Workspace header."""
    try:
        return MappingService(workspace)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/workspace/info")
def workspace_info(ms: MappingService = Depends(_get_workspace)):
    return ms.workspace_info()


@app.post("/import/mappings")
async def import_mappings(
    source_column: str = Query(...),
    target_column: str = Query(...),
    file: UploadFile = None,
    ms: MappingService = Depends(_get_workspace),
):
    async with _temporary_upload_csv(file) as csv_path:
        imported, skipped = ms.import_mappings(str(csv_path), source_column, target_column)
        return {"imported": imported, "skipped": skipped}


@app.post("/import/records")
async def import_records(
    column: str = Query(...),
    semantic: bool = Query(True),
    llm: bool = Query(True),
    threshold: float = Query(0.85),
    file: UploadFile = None,
    ms: MappingService = Depends(_get_workspace),
):
    async with _temporary_upload_csv(file) as csv_path:
        return ms.import_records_for_review(
            str(csv_path), column, semantic=semantic, llm=llm, threshold=threshold
        )


@app.get("/review-items")
def list_review_items(ms: MappingService = Depends(_get_workspace)):
    return ms.list_review_items()


@app.post("/review-items/bulk-accept", response_model=BulkAcceptResponse)
def bulk_accept_review_items(
    request: BulkAcceptRequest,
    ms: MappingService = Depends(_get_workspace),
):
    try:
        result = ms.accept_review_items(request.review_item_ids)
        return BulkAcceptResponse(accepted=result.accepted)
    except BulkAcceptStaleItemsError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except BulkAcceptPersistenceError as error:
        raise HTTPException(status_code=500, detail=str(error))
    except BulkAcceptError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.post("/review-items/{record_id}/accept")
def accept_review_item(record_id: int, ms: MappingService = Depends(_get_workspace)):
    try:
        ms.accept_review_item(record_id)
        return {"status": "accepted"}
    except ReviewItemNotFoundError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/review-items/{record_id}/edit-and-accept")
def edit_and_accept_review_item(
    record_id: int,
    normalized_text: str = Query(...),
    ms: MappingService = Depends(_get_workspace),
):
    try:
        ms.edit_and_accept_review_item(record_id, normalized_text)
        return {"status": "accepted"}
    except ReviewItemNotFoundError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/export")
def export_normalized(
    source_column: str = Query("raw_text"),
    output_column: str = Query("normalized_text"),
    ms: MappingService = Depends(_get_workspace),
):
    try:
        csv_content = ms.export_normalized_csv(source_column, output_column)
        return PlainTextResponse(content=csv_content, media_type="text/csv")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/index/build")
def build_index(ms: MappingService = Depends(_get_workspace)):
    try:
        count = ms.build_index()
        return {"entries": count}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


_static_dir = Path(__file__).with_name("static")


@app.get("/", include_in_schema=False)
def ui_index():
    return FileResponse(_static_dir / "index.html")


app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="ui-assets")
