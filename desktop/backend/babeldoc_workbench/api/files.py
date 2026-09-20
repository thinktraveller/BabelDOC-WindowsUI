"""文件接口：导入（暂存）、列表与删除。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from babeldoc_workbench.services import files as file_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/import", status_code=201)
async def import_files(files: list[UploadFile] = File(...)) -> dict:
    items = []
    try:
        for upload in files:
            items.append(file_store.stash(upload.file, upload.filename or ""))
    except file_store.FileStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        for upload in files:
            await upload.close()
    return {"items": items}


@router.get("")
def list_files() -> dict:
    return {"items": file_store.list_files()}


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: str) -> None:
    try:
        file_store.delete(file_id)
    except file_store.FileStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
