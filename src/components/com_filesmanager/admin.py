from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.admin.deps import CurrentAdminUser
from src.api.admin.render import admin_render
from src.core.acl import require_admin_permission
from src.core.system_settings import get_runtime_settings
from src.core.templates import make_t
from src.database.base import get_db_session

from . import service
from .models import FileAuditLog, FileShare
from .service import FileManagerError

router = APIRouter(
    prefix="/admin/com_filesmanager",
    tags=["com_filesmanager"],
    dependencies=[Depends(require_admin_permission("filesmanager.view"))],
)

# Veřejný router pro stahování přes sdílecí odkaz (bez admin přihlášení).
public_router = APIRouter(prefix="/filesmanager", tags=["com_filesmanager-public"])

_MANAGE = Depends(require_admin_permission("filesmanager.manage"))
_UPLOAD = Depends(require_admin_permission("filesmanager.upload"))
_DELETE = Depends(require_admin_permission("filesmanager.delete"))
_EDIT = Depends(require_admin_permission("filesmanager.edit"))
_SHARE = Depends(require_admin_permission("filesmanager.share"))


async def _component_t(db: AsyncSession):
    runtime = await get_runtime_settings(db)
    return make_t(runtime.locale, "com_filesmanager")


def _flash(request: Request, flash_type: str, text: str) -> None:
    request.session["flash"] = {"type": flash_type, "text": text}


def _redirect(rel_dir: str | None = None, suffix: str = "") -> RedirectResponse:
    url = "/admin/com_filesmanager"
    if rel_dir:
        url += f"?dir={quote(rel_dir)}"
    url += suffix
    return RedirectResponse(url=url, status_code=303)


async def _audit(
    db: AsyncSession,
    user: object,
    action: str,
    target: str = "",
    detail: str = "",
) -> None:
    db.add(
        FileAuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") or "",
            action=action,
            target=target[:1024],
            detail=detail[:2000],
        )
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# Procházení a hledání
# --------------------------------------------------------------------------- #
@router.get("", response_class=HTMLResponse)
async def index(
    request: Request,
    user: CurrentAdminUser,
    dir: str = "",
    sort: str = "name",
    desc: bool = False,
    q: str = "",
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _component_t(db)
    search_results = None
    listing = None
    try:
        if q.strip():
            search_results = service.search(q, dir or None)
            listing = service.Listing(rel_dir=service._normalize_rel(dir), breadcrumbs=service.build_breadcrumbs(dir))
        else:
            listing = service.list_dir(dir or None, sort=sort, desc=desc)
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
        listing = service.list_dir(None, sort=sort, desc=desc)

    return await admin_render(
        "admin/com_filesmanager/index.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        listing=listing,
        search_results=search_results,
        query=q,
        sort=sort,
        desc=desc,
        human_size=service.human_size,
        flash=request.session.pop("flash", None),
    )


# --------------------------------------------------------------------------- #
# Vytváření, upload
# --------------------------------------------------------------------------- #
@router.post("/folder", dependencies=[_MANAGE])
async def create_folder(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    name: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        rel = service.create_dir(dir or None, name)
        await _audit(db, user, "create_folder", rel)
        _flash(request, "success", ct("com_filesmanager.success.folder_created", name=name))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


@router.post("/upload", dependencies=[_UPLOAD])
async def upload(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    saved = 0
    try:
        for upload_file in files:
            if not upload_file.filename:
                continue
            data = await upload_file.read()
            service.save_upload(dir or None, upload_file.filename, data)
            saved += 1
        if saved:
            await _audit(db, user, "upload", dir, f"{saved} soubor(ů)")
            _flash(request, "success", ct("com_filesmanager.success.uploaded", count=saved))
        else:
            _flash(request, "warning", ct("com_filesmanager.error.nothing_selected"))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


# --------------------------------------------------------------------------- #
# Přejmenování, přesun, kopie
# --------------------------------------------------------------------------- #
@router.post("/rename", dependencies=[_MANAGE])
async def rename_entry(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    path: str = Form(...),
    new_name: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        rel = service.rename(path, new_name)
        await _audit(db, user, "rename", rel, f"z {path}")
        _flash(request, "success", ct("com_filesmanager.success.renamed", name=new_name))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


@router.post("/move", dependencies=[_MANAGE])
async def move_entries(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    paths: list[str] = Form(default=[]),
    dest: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    moved = 0
    try:
        for path in paths:
            service.move(path, dest or None)
            moved += 1
        await _audit(db, user, "move", dest, f"{moved} položek")
        _flash(request, "success", ct("com_filesmanager.success.moved", count=moved))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


@router.post("/copy", dependencies=[_MANAGE])
async def copy_entries(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    paths: list[str] = Form(default=[]),
    dest: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    copied = 0
    try:
        for path in paths:
            service.copy(path, dest or None)
            copied += 1
        await _audit(db, user, "copy", dest, f"{copied} položek")
        _flash(request, "success", ct("com_filesmanager.success.copied", count=copied))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


# --------------------------------------------------------------------------- #
# Mazání do koše
# --------------------------------------------------------------------------- #
@router.post("/delete", dependencies=[_DELETE])
async def delete_entries(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    paths: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    deleted = 0
    try:
        for path in paths:
            service.delete_to_trash(path)
            deleted += 1
        await _audit(db, user, "delete", dir, f"{deleted} položek do koše")
        _flash(request, "success", ct("com_filesmanager.success.deleted", count=deleted))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


# --------------------------------------------------------------------------- #
# Stahování a náhledy
# --------------------------------------------------------------------------- #
@router.get("/download")
async def download(path: str) -> Response:
    try:
        file_path, is_temp = service.resolve_download(path)
    except FileManagerError:
        return Response(status_code=404)
    filename = file_path.name if not is_temp else file_path.name
    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")


@router.get("/preview")
async def preview(path: str) -> Response:
    try:
        target = service.resolve(path, must_exist=True)
    except FileManagerError:
        return Response(status_code=404)
    if not target.is_file() or not service._classify(target)["can_preview"]:
        return Response(status_code=404)
    return FileResponse(target)


# --------------------------------------------------------------------------- #
# Editor textových souborů
# --------------------------------------------------------------------------- #
@router.get("/editor", response_class=HTMLResponse)
async def editor(
    request: Request,
    user: CurrentAdminUser,
    path: str,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    ct = await _component_t(db)
    try:
        content = service.read_text_file(path)
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return _redirect(parent)
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return await admin_render(
        "admin/com_filesmanager/editor.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        path=path,
        parent=parent,
        content=content,
        flash=request.session.pop("flash", None),
    )


@router.post("/editor", dependencies=[_EDIT])
async def editor_save(
    request: Request,
    user: CurrentAdminUser,
    path: str = Form(...),
    content: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    try:
        service.write_text_file(path, content)
        await _audit(db, user, "edit", path)
        _flash(request, "success", ct("com_filesmanager.success.saved", name=path))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(parent)


# --------------------------------------------------------------------------- #
# ZIP / rozbalení
# --------------------------------------------------------------------------- #
@router.post("/zip", dependencies=[_MANAGE])
async def zip_entries(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    paths: list[str] = Form(default=[]),
    archive_name: str = Form("archiv.zip"),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        rel = service.make_zip(paths, dir or None, archive_name)
        await _audit(db, user, "zip", rel, f"{len(paths)} položek")
        _flash(request, "success", ct("com_filesmanager.success.zipped", name=rel))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


@router.post("/unzip", dependencies=[_MANAGE])
async def unzip_entry(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    path: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        rel = service.extract_zip(path)
        await _audit(db, user, "unzip", rel, f"z {path}")
        _flash(request, "success", ct("com_filesmanager.success.unzipped", name=rel))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


# --------------------------------------------------------------------------- #
# Koš
# --------------------------------------------------------------------------- #
@router.get("/trash", response_class=HTMLResponse)
async def trash(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _component_t(db)
    return await admin_render(
        "admin/com_filesmanager/trash.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        items=service.list_trash(),
        human_size=service.human_size,
        flash=request.session.pop("flash", None),
    )


@router.post("/trash/restore", dependencies=[_DELETE])
async def trash_restore(
    request: Request,
    user: CurrentAdminUser,
    trash_id: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        rel = service.restore_from_trash(trash_id)
        await _audit(db, user, "restore", rel)
        _flash(request, "success", ct("com_filesmanager.success.restored", name=rel))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(suffix="/trash")


@router.post("/trash/delete", dependencies=[_DELETE])
async def trash_delete(
    request: Request,
    user: CurrentAdminUser,
    trash_id: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    service.delete_trash_item(trash_id)
    await _audit(db, user, "purge", trash_id)
    _flash(request, "success", ct("com_filesmanager.success.purged"))
    return _redirect(suffix="/trash")


@router.post("/trash/empty", dependencies=[_DELETE])
async def trash_empty(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    count = service.empty_trash()
    await _audit(db, user, "empty_trash", "", f"{count} položek")
    _flash(request, "success", ct("com_filesmanager.success.trash_emptied", count=count))
    return _redirect(suffix="/trash")


# --------------------------------------------------------------------------- #
# Sdílecí odkazy
# --------------------------------------------------------------------------- #
@router.get("/shares", response_class=HTMLResponse)
async def shares(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _component_t(db)
    rows = (await db.execute(select(FileShare).order_by(FileShare.created_at.desc()))).scalars().all()
    return await admin_render(
        "admin/com_filesmanager/shares.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        shares=rows,
        now=datetime.now(UTC),
        flash=request.session.pop("flash", None),
    )


@router.post("/shares/create", dependencies=[_SHARE])
async def share_create(
    request: Request,
    user: CurrentAdminUser,
    dir: str = Form(""),
    path: str = Form(...),
    expires_days: int = Form(0),
    max_downloads: int = Form(0),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    try:
        target = service.resolve(path, must_exist=True)
        if not target.is_file():
            raise FileManagerError("com_filesmanager.error.share_file_only", name=path)
        expires_at = None
        if expires_days > 0:
            expires_at = datetime.now(UTC) + timedelta(days=expires_days)
        share = FileShare(
            token=service.new_share_token(),
            rel_path=path,
            note=note[:255],
            created_by=getattr(user, "id", None),
            created_by_name=getattr(user, "username", "") or "",
            expires_at=expires_at,
            max_downloads=max_downloads if max_downloads > 0 else None,
        )
        db.add(share)
        await db.commit()
        await _audit(db, user, "share_create", path)
        _flash(request, "success", ct("com_filesmanager.success.share_created", name=path))
    except FileManagerError as exc:
        _flash(request, "danger", ct(exc.key, **exc.params))
    return _redirect(dir)


@router.post("/shares/revoke", dependencies=[_SHARE])
async def share_revoke(
    request: Request,
    user: CurrentAdminUser,
    share_id: int = Form(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _component_t(db)
    share = (await db.execute(select(FileShare).where(FileShare.id == share_id))).scalar_one_or_none()
    if share is not None:
        share.revoked = True
        await db.commit()
        await _audit(db, user, "share_revoke", share.rel_path)
        _flash(request, "success", ct("com_filesmanager.success.share_revoked"))
    return _redirect(suffix="/shares")


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _component_t(db)
    rows = (
        await db.execute(select(FileAuditLog).order_by(FileAuditLog.created_at.desc()).limit(300))
    ).scalars().all()
    return await admin_render(
        "admin/com_filesmanager/audit.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        rows=rows,
        flash=request.session.pop("flash", None),
    )


# --------------------------------------------------------------------------- #
# Veřejné stahování přes sdílecí odkaz
# --------------------------------------------------------------------------- #
@public_router.get("/share/{token}")
async def public_share(
    token: str,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    share = (await db.execute(select(FileShare).where(FileShare.token == token))).scalar_one_or_none()
    if share is None or share.revoked:
        return Response(status_code=404)
    if share.expires_at is not None and share.expires_at < datetime.now(UTC):
        return Response(status_code=410)
    if share.max_downloads is not None and share.download_count >= share.max_downloads:
        return Response(status_code=410)
    try:
        target = service.resolve(share.rel_path, must_exist=True)
    except FileManagerError:
        return Response(status_code=404)
    if not target.is_file():
        return Response(status_code=404)
    share.download_count += 1
    await db.commit()
    return FileResponse(target, filename=target.name)
