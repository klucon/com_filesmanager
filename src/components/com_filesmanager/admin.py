from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.admin.deps import CurrentAdminUser
from src.api.admin.render import admin_render
from src.core.acl import require_admin_permission
from src.core.system_settings import get_runtime_settings
from src.core.templates import make_t
from src.database.base import get_db_session

from src.components.com_filesmanager import catalog, models

router = APIRouter(
    prefix="/admin/com_filesmanager",
    tags=["com_filesmanager"],
    dependencies=[Depends(require_admin_permission("filesmanager.view"))],
)

_MANAGE = Depends(require_admin_permission("filesmanager.manage"))
_UPLOAD = Depends(require_admin_permission("filesmanager.upload"))
_DELETE = Depends(require_admin_permission("filesmanager.delete"))


async def _ct(db: AsyncSession):
    runtime = await get_runtime_settings(db)
    return make_t(runtime.locale, "com_filesmanager")


def _flash(request: Request, flash_type: str, text: str) -> None:
    request.session["flash"] = {"type": flash_type, "text": text}


def _redirect(path: str = "/admin/com_filesmanager") -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


async def _audit(
    db: AsyncSession,
    user: object,
    action: str,
    target: str,
    detail: str = "",
) -> None:
    db.add(
        models.FileAuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") or "",
            action=action,
            target=target[:1024],
            detail=detail[:2000],
        )
    )


async def _handle_error(request: Request, db: AsyncSession, ct, exc: catalog.CatalogError) -> None:
    await db.rollback()
    _flash(request, "danger", ct(exc.key, **exc.params))


def _to_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "on", "yes"}


@router.get("", response_class=HTMLResponse)
async def overview(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    stats = await catalog.dashboard_stats(db)
    recent = await catalog.recent_files(db)
    categories = await catalog.list_categories(db)
    return await admin_render(
        "admin/com_filesmanager/overview.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        stats=stats,
        recent=recent,
        categories=categories,
        flash=request.session.pop("flash", None),
    )


@router.get("/categories", response_class=HTMLResponse)
async def categories(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    rows = await catalog.list_categories(db)
    return await admin_render(
        "admin/com_filesmanager/categories.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        rows=rows,
        flash=request.session.pop("flash", None),
    )


@router.get("/categories/new", response_class=HTMLResponse)
async def category_new(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    return await admin_render(
        "admin/com_filesmanager/category_form.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        item=None,
        flash=request.session.pop("flash", None),
    )


@router.post("/categories/new", dependencies=[_MANAGE])
async def category_create(
    request: Request,
    user: CurrentAdminUser,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    icon: str = Form("folder"),
    sort_order: int = Form(0),
    is_active: str = Form("on"),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    try:
        payload = catalog.build_category_payload(
            name=name,
            slug=slug,
            description=description,
            icon=icon,
            sort_order=sort_order,
            is_active=_to_bool(is_active),
        )
        category = await catalog.create_category(db, payload)
        await _audit(db, user, "category_create", category.slug)
        await db.commit()
        _flash(
            request,
            "success",
            ct("com_filesmanager.success.category_created", name=category.name),
        )
        return _redirect("/admin/com_filesmanager/categories")
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
        return _redirect("/admin/com_filesmanager/categories/new")


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
async def category_edit(
    category_id: int,
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    item = await catalog.get_category(db, category_id)
    if item is None:
        _flash(request, "danger", ct("com_filesmanager.error.not_found", name=category_id))
        return _redirect("/admin/com_filesmanager/categories")
    return await admin_render(
        "admin/com_filesmanager/category_form.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        item=item,
        flash=request.session.pop("flash", None),
    )


@router.post("/categories/{category_id}/edit", dependencies=[_MANAGE])
async def category_update(
    category_id: int,
    request: Request,
    user: CurrentAdminUser,
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    icon: str = Form("folder"),
    sort_order: int = Form(0),
    is_active: str = Form("on"),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    item = await catalog.get_category(db, category_id)
    if item is None:
        _flash(request, "danger", ct("com_filesmanager.error.not_found", name=category_id))
        return _redirect("/admin/com_filesmanager/categories")
    try:
        payload = catalog.build_category_payload(
            name=name,
            slug=slug,
            description=description,
            icon=icon,
            sort_order=sort_order,
            is_active=_to_bool(is_active),
        )
        await catalog.update_category(db, item, payload)
        await _audit(db, user, "category_update", item.slug)
        await db.commit()
        _flash(request, "success", ct("com_filesmanager.success.category_updated", name=item.name))
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
    return _redirect("/admin/com_filesmanager/categories")


@router.post("/categories/{category_id}/delete", dependencies=[_DELETE])
async def category_delete(
    category_id: int,
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    try:
        await catalog.delete_category(db, category_id)
        await _audit(db, user, "category_delete", str(category_id))
        await db.commit()
        _flash(request, "success", ct("com_filesmanager.success.category_deleted"))
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
    return _redirect("/admin/com_filesmanager/categories")


@router.get("/files", response_class=HTMLResponse)
async def files(
    request: Request,
    user: CurrentAdminUser,
    category_id: int | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    rows = await catalog.list_files(db, category_id=category_id)
    categories = await catalog.list_categories(db)
    return await admin_render(
        "admin/com_filesmanager/files.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        rows=rows,
        categories=categories,
        selected_category_id=category_id,
        human_size=storage_human_size,
        flash=request.session.pop("flash", None),
    )


def storage_human_size(num: int) -> str:
    from src.components.com_filesmanager.service import human_size

    return human_size(num)


@router.get("/files/new", response_class=HTMLResponse)
async def file_new(
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    categories = await catalog.list_categories(db)
    return await admin_render(
        "admin/com_filesmanager/file_form.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        item=None,
        categories=categories,
        flash=request.session.pop("flash", None),
    )


@router.post("/files/new", dependencies=[_UPLOAD])
async def file_create(
    request: Request,
    user: CurrentAdminUser,
    title: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    category_id: str = Form(""),
    is_published: str = Form("on"),
    is_featured: str = Form(""),
    sort_order: int = Form(0),
    upload: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    try:
        payload = catalog.build_file_payload(
            title=title,
            slug=slug,
            description=description,
            category_id=int(category_id) if category_id.strip().isdigit() else None,
            is_published=_to_bool(is_published),
            is_featured=_to_bool(is_featured),
            sort_order=sort_order,
        )
        data = await upload.read()
        item = await catalog.create_file(
            db,
            payload,
            filename=upload.filename or "file.bin",
            data=data,
            created_by=getattr(user, "id", None),
            created_by_name=getattr(user, "username", "") or "",
            content_type=upload.content_type,
        )
        await _audit(db, user, "file_create", item.slug)
        await db.commit()
        _flash(request, "success", ct("com_filesmanager.success.file_created", name=item.title))
        return _redirect("/admin/com_filesmanager/files")
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
        return _redirect("/admin/com_filesmanager/files/new")


@router.get("/files/{file_id}/edit", response_class=HTMLResponse)
async def file_edit(
    file_id: int,
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ct = await _ct(db)
    item = await catalog.get_file(db, file_id)
    if item is None:
        _flash(request, "danger", ct("com_filesmanager.error.not_found", name=file_id))
        return _redirect("/admin/com_filesmanager/files")
    categories = await catalog.list_categories(db)
    return await admin_render(
        "admin/com_filesmanager/file_form.html",
        request=request,
        db=db,
        user=user,
        ct=ct,
        item=item,
        categories=categories,
        flash=request.session.pop("flash", None),
    )


@router.post("/files/{file_id}/edit", dependencies=[_MANAGE])
async def file_update(
    file_id: int,
    request: Request,
    user: CurrentAdminUser,
    title: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    category_id: str = Form(""),
    is_published: str = Form("on"),
    is_featured: str = Form(""),
    sort_order: int = Form(0),
    upload: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    item = await catalog.get_file(db, file_id)
    if item is None:
        _flash(request, "danger", ct("com_filesmanager.error.not_found", name=file_id))
        return _redirect("/admin/com_filesmanager/files")
    try:
        payload = catalog.build_file_payload(
            title=title,
            slug=slug,
            description=description,
            category_id=int(category_id) if category_id.strip().isdigit() else None,
            is_published=_to_bool(is_published),
            is_featured=_to_bool(is_featured),
            sort_order=sort_order,
        )
        filename = None
        data = None
        content_type = None
        if upload is not None and upload.filename:
            filename = upload.filename
            data = await upload.read()
            content_type = upload.content_type
        await catalog.update_file(
            db,
            item,
            payload,
            filename=filename,
            data=data,
            content_type=content_type,
        )
        await _audit(db, user, "file_update", item.slug)
        await db.commit()
        _flash(request, "success", ct("com_filesmanager.success.file_updated", name=item.title))
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
    return _redirect("/admin/com_filesmanager/files")


@router.post("/files/{file_id}/delete", dependencies=[_DELETE])
async def file_delete(
    file_id: int,
    request: Request,
    user: CurrentAdminUser,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    ct = await _ct(db)
    item = await catalog.get_file(db, file_id)
    if item is None:
        _flash(request, "danger", ct("com_filesmanager.error.not_found", name=file_id))
        return _redirect("/admin/com_filesmanager/files")
    try:
        await catalog.delete_file(db, item)
        await _audit(db, user, "file_delete", item.slug)
        await db.commit()
        _flash(request, "success", ct("com_filesmanager.success.file_deleted"))
    except catalog.CatalogError as exc:
        await _handle_error(request, db, ct, exc)
    return _redirect("/admin/com_filesmanager/files")
