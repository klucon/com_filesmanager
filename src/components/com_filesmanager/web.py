from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.templates import make_t
from src.database.base import get_db_session

try:
    from src.core.web_render import web_render
except ImportError:
    from src.api.web.render import web_render  # type: ignore[no-redef]

from . import catalog, models

router = APIRouter(prefix="/filesmanager", tags=["com_filesmanager_web"])


async def _ct(locale: str):
    return make_t(locale, "com_filesmanager")


def _locale(request: Request) -> str:
    return getattr(request.state, "locale", "cs_CZ")


@router.get("", response_class=HTMLResponse)
async def file_index(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    locale = _locale(request)
    ct = await _ct(locale)
    categories = await catalog.list_categories(db, active_only=True)
    files = await catalog.list_files(db, published_only=True)
    return await web_render(
        "com_filesmanager/index.html",
        request=request,
        db=db,
        locale=locale,
        ct=ct,
        categories=categories,
        files=files,
        current_category=None,
        human_size=_human_size,
    )


@router.get("/category/{slug}", response_class=HTMLResponse)
async def file_category(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse | Response:
    locale = _locale(request)
    ct = await _ct(locale)
    category = await catalog.get_category_by_slug(db, slug)
    if category is None or not category.is_active:
        return RedirectResponse("/filesmanager", status_code=302)
    categories = await catalog.list_categories(db, active_only=True)
    files = await catalog.list_files(db, category_id=category.id, published_only=True)
    return await web_render(
        "com_filesmanager/index.html",
        request=request,
        db=db,
        locale=locale,
        ct=ct,
        categories=categories,
        files=files,
        current_category=category,
        human_size=_human_size,
    )


@router.get("/file/{slug}", response_class=HTMLResponse)
async def file_detail(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse | Response:
    locale = _locale(request)
    ct = await _ct(locale)
    item = await catalog.get_file_by_slug(db, slug)
    if item is None or not item.is_published:
        return RedirectResponse("/filesmanager", status_code=302)
    category = await catalog.get_category(db, item.category_id) if item.category_id else None
    return await web_render(
        "com_filesmanager/detail.html",
        request=request,
        db=db,
        locale=locale,
        ct=ct,
        item=item,
        category=category,
        human_size=_human_size,
    )


@router.get("/download/{slug}")
async def file_download(
    slug: str,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    item = await catalog.get_file_by_slug(db, slug)
    if item is None or not item.is_published:
        return RedirectResponse("/filesmanager", status_code=302)
    try:
        path = catalog.file_disk_path(item)
    except Exception:
        return RedirectResponse(f"/filesmanager/file/{slug}", status_code=302)
    await catalog.increment_download(db, item)
    db.add(
        models.FileAuditLog(
            user_id=None,
            username="",
            action="download",
            target=item.slug,
            detail=item.rel_path,
        )
    )
    await db.commit()
    return FileResponse(path, filename=item.original_filename, media_type=item.mime_type)


def _human_size(num: int) -> str:
    from src.components.com_filesmanager.service import human_size

    return human_size(num)
