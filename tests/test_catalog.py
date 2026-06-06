from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.components.com_filesmanager import catalog, schema, service
from src.components.com_filesmanager.catalog import CatalogError


@pytest.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    storage_root = tmp_path / "files"
    storage_root.mkdir()
    monkeypatch.setattr(
        service,
        "get_config",
        lambda: service.FileManagerConfig(root=storage_root.resolve()),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await schema.upgrade_schema(engine)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_category_slug_is_generated(db: AsyncSession) -> None:
    payload = catalog.build_category_payload(
        name="Ke stažení",
        slug="",
        description="",
        icon="folder",
        sort_order=10,
        is_active=True,
    )
    category = await catalog.create_category(db, payload)
    assert category.slug == "ke-stazeni"


@pytest.mark.asyncio
async def test_create_file_tracks_category_and_downloads(db: AsyncSession) -> None:
    category = await catalog.create_category(
        db,
        catalog.build_category_payload(
            name="Dokumentace",
            slug="dokumentace",
            description="",
            icon="folder",
            sort_order=0,
            is_active=True,
        ),
    )
    item = await catalog.create_file(
        db,
        catalog.build_file_payload(
            title="Instalace",
            slug="instalace",
            description="PDF navod",
            category_id=category.id,
            is_published=True,
            is_featured=False,
            sort_order=0,
        ),
        filename="manual.pdf",
        data=b"%PDF-1.7",
        created_by=7,
        created_by_name="ondrej",
        content_type="application/pdf",
    )
    await catalog.increment_download(db, item)
    await db.commit()

    stored = await catalog.get_file_by_slug(db, "instalace")
    assert stored is not None
    assert stored.category_id == category.id
    assert stored.download_count == 1
    assert service.resolve(stored.rel_path, must_exist=True).is_file()


@pytest.mark.asyncio
async def test_create_file_rejects_unknown_category(db: AsyncSession) -> None:
    with pytest.raises(CatalogError) as exc:
        await catalog.create_file(
            db,
            catalog.build_file_payload(
                title="Cenik",
                slug="cenik",
                description="",
                category_id=999,
                is_published=True,
                is_featured=False,
                sort_order=0,
            ),
            filename="cenik.pdf",
            data=b"%PDF-1.7",
            created_by=None,
            created_by_name="",
            content_type="application/pdf",
        )
    assert exc.value.key == "com_filesmanager.error.category_not_found"
