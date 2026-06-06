"""File manager component for KLUCON CMS."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.registry import ComponentRegistry

_COMPONENT_DIR = Path(__file__).parent
_manifest: dict = {}


async def upgrade_schema(engine: object) -> None:
    from src.components.com_filesmanager.schema import upgrade_schema as _up

    await _up(engine)


async def uninstall_schema(engine: object) -> None:
    from src.components.com_filesmanager.schema import uninstall_schema as _down

    await _down(engine)


def _load_manifest() -> dict:
    global _manifest
    if not _manifest:
        try:
            _manifest = json.loads((_COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            _manifest = {}
    return _manifest


def setup(reg: ComponentRegistry) -> None:
    from src.i18n.translator import translator

    from src.components.com_filesmanager import admin

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
    reg.register_router(admin.public_router)

    translator.load_domain("com_filesmanager", _COMPONENT_DIR / "i18n")
