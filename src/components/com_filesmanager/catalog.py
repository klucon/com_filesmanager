from __future__ import annotations

import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.components.com_filesmanager import service as storage

from .models import FileCategory, ManagedFile


def _slugify(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-")


@dataclass(frozen=True)
class CategoryPayload:
    name: str
    slug: str
    description: str
    icon: str
    sort_order: int
    is_active: bool


@dataclass(frozen=True)
class FilePayload:
    title: str
    slug: str
    description: str
    category_id: int | None
    is_published: bool
    is_featured: bool
    sort_order: int


class CatalogError(ValueError):
    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def build_category_payload(
    *,
    name: str,
    slug: str,
    description: str,
    icon: str,
    sort_order: int,
    is_active: bool,
) -> CategoryPayload:
    clean_name = name.strip()
    if not clean_name:
        raise CatalogError("com_filesmanager.error.category_name_required")
    clean_slug = (slug.strip() or _slugify(clean_name)).strip("-")
    if not clean_slug:
        raise CatalogError("com_filesmanager.error.slug_required")
    if not re.fullmatch(r"[a-z0-9-]+", clean_slug):
        raise CatalogError("com_filesmanager.error.slug_invalid")
    return CategoryPayload(
        name=clean_name,
        slug=clean_slug,
        description=description.strip(),
        icon=icon.strip(),
        sort_order=sort_order,
        is_active=is_active,
    )


def build_file_payload(
    *,
    title: str,
    slug: str,
    description: str,
    category_id: int | None,
    is_published: bool,
    is_featured: bool,
    sort_order: int,
) -> FilePayload:
    clean_title = title.strip()
    if not clean_title:
        raise CatalogError("com_filesmanager.error.file_title_required")
    clean_slug = (slug.strip() or _slugify(clean_title)).strip("-")
    if not clean_slug:
        raise CatalogError("com_filesmanager.error.slug_required")
    if not re.fullmatch(r"[a-z0-9-]+", clean_slug):
        raise CatalogError("com_filesmanager.error.slug_invalid")
    return FilePayload(
        title=clean_title,
        slug=clean_slug,
        description=description.strip(),
        category_id=category_id,
        is_published=is_published,
        is_featured=is_featured,
        sort_order=sort_order,
    )


async def dashboard_stats(db: AsyncSession) -> dict[str, int]:
    files_total = await db.scalar(select(func.count()).select_from(ManagedFile))
    downloads_total = await db.scalar(
        select(func.coalesce(func.sum(ManagedFile.download_count), 0))
    )
    categories_total = await db.scalar(select(func.count()).select_from(FileCategory))
    return {
        "files_total": int(files_total or 0),
        "downloads_total": int(downloads_total or 0),
        "categories_total": int(categories_total or 0),
    }


async def list_categories(db: AsyncSession, *, active_only: bool = False) -> list[FileCategory]:
    query = select(FileCategory).order_by(FileCategory.sort_order, FileCategory.name)
    if active_only:
        query = query.where(FileCategory.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_category(db: AsyncSession, category_id: int) -> FileCategory | None:
    return await db.get(FileCategory, category_id)


async def get_category_by_slug(db: AsyncSession, slug: str) -> FileCategory | None:
    result = await db.execute(select(FileCategory).where(FileCategory.slug == slug))
    return result.scalar_one_or_none()


async def create_category(db: AsyncSession, payload: CategoryPayload) -> FileCategory:
    if await get_category_by_slug(db, payload.slug):
        raise CatalogError("com_filesmanager.error.slug_exists", slug=payload.slug)
    category = FileCategory(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        icon=payload.icon,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(category)
    await db.flush()
    return category


async def update_category(
    db: AsyncSession,
    category: FileCategory,
    payload: CategoryPayload,
) -> None:
    if payload.slug != category.slug:
        existing = await get_category_by_slug(db, payload.slug)
        if existing is not None:
            raise CatalogError("com_filesmanager.error.slug_exists", slug=payload.slug)
    category.name = payload.name
    category.slug = payload.slug
    category.description = payload.description
    category.icon = payload.icon
    category.sort_order = payload.sort_order
    category.is_active = payload.is_active
    await db.flush()


async def delete_category(db: AsyncSession, category_id: int) -> None:
    files_count = await db.scalar(
        select(func.count()).select_from(ManagedFile).where(ManagedFile.category_id == category_id)
    )
    if files_count:
        raise CatalogError("com_filesmanager.error.category_not_empty")
    category = await db.get(FileCategory, category_id)
    if category is not None:
        await db.delete(category)
        await db.flush()


async def list_files(
    db: AsyncSession,
    *,
    category_id: int | None = None,
    published_only: bool = False,
) -> list[ManagedFile]:
    query = select(ManagedFile).order_by(
        ManagedFile.sort_order,
        ManagedFile.is_featured.desc(),
        ManagedFile.created_at.desc(),
    )
    if category_id is not None:
        query = query.where(ManagedFile.category_id == category_id)
    if published_only:
        query = query.where(ManagedFile.is_published.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def recent_files(db: AsyncSession, limit: int = 10) -> list[ManagedFile]:
    result = await db.execute(
        select(ManagedFile).order_by(ManagedFile.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_file(db: AsyncSession, file_id: int) -> ManagedFile | None:
    return await db.get(ManagedFile, file_id)


async def get_file_by_slug(db: AsyncSession, slug: str) -> ManagedFile | None:
    result = await db.execute(select(ManagedFile).where(ManagedFile.slug == slug))
    return result.scalar_one_or_none()


async def _validate_category(db: AsyncSession, category_id: int | None) -> None:
    if category_id is None:
        return
    if await db.get(FileCategory, category_id) is None:
        raise CatalogError("com_filesmanager.error.category_not_found")


async def create_file(
    db: AsyncSession,
    payload: FilePayload,
    *,
    filename: str,
    data: bytes,
    created_by: int | None,
    created_by_name: str,
    content_type: str | None,
) -> ManagedFile:
    if await get_file_by_slug(db, payload.slug):
        raise CatalogError("com_filesmanager.error.slug_exists", slug=payload.slug)
    await _validate_category(db, payload.category_id)
    rel_path = _store_upload(payload, filename, data, content_type=content_type)
    mime_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    extension = Path(filename).suffix.lower()
    item = ManagedFile(
        category_id=payload.category_id,
        title=payload.title,
        slug=payload.slug,
        description=payload.description,
        rel_path=rel_path,
        original_filename=filename,
        mime_type=mime_type,
        extension=extension,
        file_size=len(data),
        is_published=payload.is_published,
        is_featured=payload.is_featured,
        sort_order=payload.sort_order,
        created_by=created_by,
        created_by_name=created_by_name,
    )
    db.add(item)
    await db.flush()
    return item


async def update_file(
    db: AsyncSession,
    item: ManagedFile,
    payload: FilePayload,
    *,
    filename: str | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
) -> None:
    if payload.slug != item.slug:
        existing = await get_file_by_slug(db, payload.slug)
        if existing is not None:
            raise CatalogError("com_filesmanager.error.slug_exists", slug=payload.slug)
    await _validate_category(db, payload.category_id)
    item.category_id = payload.category_id
    item.title = payload.title
    item.slug = payload.slug
    item.description = payload.description
    item.is_published = payload.is_published
    item.is_featured = payload.is_featured
    item.sort_order = payload.sort_order
    if filename and data is not None:
        old_rel_path = item.rel_path
        rel_path = _store_upload(payload, filename, data, content_type=content_type)
        item.rel_path = rel_path
        item.original_filename = filename
        item.mime_type = (
            content_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        item.extension = Path(filename).suffix.lower()
        item.file_size = len(data)
        if old_rel_path != rel_path:
            old_path = storage.resolve(old_rel_path, must_exist=False)
            if old_path.exists() and old_path.is_file():
                old_path.unlink()
    await db.flush()


async def delete_file(db: AsyncSession, item: ManagedFile) -> None:
    storage_path = storage.resolve(item.rel_path, must_exist=False)
    if storage_path.exists():
        if storage_path.is_dir():
            raise CatalogError("com_filesmanager.error.invalid_path")
        storage_path.unlink()
    await db.delete(item)
    await db.flush()


async def increment_download(db: AsyncSession, item: ManagedFile) -> None:
    item.download_count += 1
    await db.flush()


def file_disk_path(item: ManagedFile) -> Path:
    return storage.resolve(item.rel_path, must_exist=True)


def _store_upload(
    payload: FilePayload,
    filename: str,
    data: bytes,
    *,
    content_type: str | None,
) -> str:
    target_dir = "library"
    if payload.category_id is not None:
        target_dir = f"library/category-{payload.category_id}"
    folder = storage.resolve(target_dir, must_exist=False)
    folder.mkdir(parents=True, exist_ok=True)
    return storage.save_upload(
        target_dir,
        filename,
        data,
        content_type=content_type,
    )
