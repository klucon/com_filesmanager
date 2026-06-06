"""File manager component for KLUCON CMS."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.hooks import hooks

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.core.registry import ComponentRegistry
    from starlette.requests import Request

_COMPONENT_DIR = Path(__file__).parent
_manifest: dict = {}
_MENU_HOOK_REGISTERED = False


def _load_manifest() -> dict:
    global _manifest
    if not _manifest:
        try:
            _manifest = json.loads((_COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            _manifest = {}
    return _manifest


async def _menu_item_types(*, request: Request, db: AsyncSession) -> list[dict[str, object]]:
    from src.core.system_settings import get_runtime_settings
    from src.core.templates import make_t

    from src.components.com_filesmanager.catalog import list_categories, list_files

    runtime = await get_runtime_settings(db)
    ct = make_t(runtime.locale, "com_filesmanager")

    category_options = [
        {
            "value": str(category.id),
            "title": category.name,
            "url": f"/filesmanager/category/{category.slug}",
        }
        for category in await list_categories(db, active_only=True)
    ]
    file_options = [
        {
            "value": str(item.id),
            "title": item.title,
            "url": f"/filesmanager/file/{item.slug}",
        }
        for item in await list_files(db, published_only=True)
    ]

    return [
        {
            "key": "com_filesmanager.index",
            "group": ct("com_filesmanager.menu.group"),
            "label": ct("com_filesmanager.menu.index.label"),
            "description": ct("com_filesmanager.menu.index.description"),
            "empty": ct("com_filesmanager.menu.index.empty"),
            "options": [
                {
                    "value": "all",
                    "title": ct("com_filesmanager.menu.index.option"),
                    "url": "/filesmanager",
                }
            ],
            "manual_url": False,
        },
        {
            "key": "com_filesmanager.category",
            "group": ct("com_filesmanager.menu.group"),
            "label": ct("com_filesmanager.menu.category.label"),
            "description": ct("com_filesmanager.menu.category.description"),
            "empty": ct("com_filesmanager.menu.category.empty"),
            "options": category_options,
            "manual_url": False,
        },
        {
            "key": "com_filesmanager.file",
            "group": ct("com_filesmanager.menu.group"),
            "label": ct("com_filesmanager.menu.file.label"),
            "description": ct("com_filesmanager.menu.file.description"),
            "empty": ct("com_filesmanager.menu.file.empty"),
            "options": file_options,
            "manual_url": False,
        },
    ]


def setup(reg: ComponentRegistry) -> None:
    from src.i18n.translator import translator

    from src.components.com_filesmanager import admin, web

    global _MENU_HOOK_REGISTERED

    manifest = _load_manifest()

    reg.register("com_filesmanager", "src.components.com_filesmanager")
    reg.register_display_name(
        "com_filesmanager",
        manifest.get("display_name_key", "extensions.name.com_filesmanager"),
    )
    reg.register_admin_url(
        "com_filesmanager",
        manifest.get("admin_url", "/admin/com_filesmanager"),
    )
    reg.register_router(admin.router)
    reg.register_router(web.router)

    translator.load_domain("com_filesmanager", _COMPONENT_DIR / "i18n")
    if not _MENU_HOOK_REGISTERED:
        hooks.on("menu.item.types", _menu_item_types)
        _MENU_HOOK_REGISTERED = True


async def upgrade_schema(engine: object) -> None:
    from src.components.com_filesmanager.schema import upgrade_schema as _up

    await _up(engine)


async def uninstall_schema(engine: object) -> None:
    from src.components.com_filesmanager.schema import uninstall_schema as _down

    await _down(engine)
