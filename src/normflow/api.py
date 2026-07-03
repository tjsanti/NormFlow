"""FastAPI layer — thin adapter over MappingService."""

from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .mapping_service import MappingService

app = FastAPI(title="NormFlow", redirect_slashes=False)


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


@app.get("/suggestions")
def list_suggestions(ms: MappingService = Depends(_get_workspace)):
    return ms.list_pending_suggestions()


@app.post("/suggestions/{record_id}/accept")
def accept_suggestion(record_id: int, ms: MappingService = Depends(_get_workspace)):
    try:
        ms.accept_suggestion(record_id)
        return {"status": "accepted"}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/suggestions/{record_id}/edit")
def edit_suggestion(
    record_id: int,
    normalized_text: str = Query(...),
    ms: MappingService = Depends(_get_workspace),
):
    try:
        ms.edit_suggestion(record_id, normalized_text)
        return {"status": "accepted"}
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
