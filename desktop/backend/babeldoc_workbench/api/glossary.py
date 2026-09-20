"""术语表接口：导入、审核、冲突处理、版本与导出。"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from babeldoc_workbench.services import glossaries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/glossaries", tags=["glossaries"])


class EntryPatch(BaseModel):
    source: str | None = None
    target: str | None = None
    tgt_lng: str | None = None


class BulkStatusPayload(BaseModel):
    entry_ids: list[int]
    status: str


class ResolveConflictPayload(BaseModel):
    entry_id: int
    keep: str


class VersionPayload(BaseModel):
    note: str | None = None


class ImportFromTaskPayload(BaseModel):
    task_id: int
    name: str | None = None


def _error(exc: Exception, status: int = 400) -> HTTPException:
    code = 404 if isinstance(exc, glossaries.GlossaryNotFound) else status
    return HTTPException(status_code=code, detail=str(exc))


@router.get("")
def list_glossaries() -> dict:
    return {"items": glossaries.list_glossaries()}


@router.get("/versions")
def list_versions(
    glossary_id: int | None = Query(default=None),
    tgt_lng: str | None = Query(default=None),
) -> dict:
    return {"items": glossaries.list_versions(glossary_id=glossary_id, tgt_lng=tgt_lng)}


@router.post("/import-task", status_code=201)
def import_from_task(payload: ImportFromTaskPayload) -> dict:
    try:
        return glossaries.import_from_task(payload.task_id, name=payload.name)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.post("/import", status_code=201)
async def import_csv(
    file: UploadFile = File(...),
    name: str = Form(...),
    tgt_lng: str = Form(...),
) -> dict:
    try:
        content = await file.read()
    finally:
        await file.close()
    try:
        import io

        return glossaries.import_from_file(io.BytesIO(content), name=name, tgt_lng=tgt_lng)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.get("/{glossary_id}")
def get_glossary(glossary_id: int) -> dict:
    try:
        glossary = glossaries.get_glossary(glossary_id)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc
    return glossaries._public_glossary(glossary)


@router.get("/{glossary_id}/entries")
def list_entries(
    glossary_id: int,
    tgt_lng: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    try:
        return glossaries.list_entries(
            glossary_id,
            tgt_lng=tgt_lng,
            status=status,
            query=q,
            limit=limit,
            offset=offset,
        )
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: int, payload: EntryPatch) -> dict:
    try:
        return glossaries.update_entry(
            entry_id,
            source=payload.source,
            target=payload.target,
            tgt_lng=payload.tgt_lng,
        )
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int) -> None:
    try:
        glossaries.delete_entry(entry_id)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.post("/{glossary_id}/entries/bulk-status")
def bulk_status(glossary_id: int, payload: BulkStatusPayload) -> dict:
    try:
        glossaries.get_glossary(glossary_id)
        updated = glossaries.set_status(payload.entry_ids, payload.status)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc
    return {"updated": updated, "status": payload.status}


@router.post("/{glossary_id}/resolve-conflict")
def resolve_conflict(glossary_id: int, payload: ResolveConflictPayload) -> dict:
    try:
        entry = glossaries.get_entry(payload.entry_id)
        if entry.glossary_id != glossary_id:
            raise glossaries.GlossaryError("该条目不属于这个术语表")
        return glossaries.resolve_conflict(payload.entry_id, keep=payload.keep)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.post("/{glossary_id}/versions", status_code=201)
def create_version(glossary_id: int, payload: VersionPayload) -> dict:
    try:
        return glossaries.create_version(glossary_id, note=payload.note)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc


@router.get("/{glossary_id}/versions")
def glossary_versions(glossary_id: int) -> dict:
    try:
        glossaries.get_glossary(glossary_id)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc
    return {"items": glossaries.list_versions(glossary_id=glossary_id)}


@router.get("/{glossary_id}/export")
def export_csv(glossary_id: int) -> Response:
    try:
        filename, content = glossaries.export_csv(glossary_id)
    except glossaries.GlossaryError as exc:
        raise _error(exc) from exc
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            # HTTP 头只能放 latin-1；中文文件名用 RFC 5987 的 filename* 传递
            "Content-Disposition": (
                'attachment; filename="glossary.csv"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )
