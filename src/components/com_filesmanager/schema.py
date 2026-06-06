from __future__ import annotations

from .models import FileAuditLog, FileShare

_TABLES_DROP_ORDER = [
    FileAuditLog.__table__,
    FileShare.__table__,
]

_TABLES_CREATE_ORDER = list(reversed(_TABLES_DROP_ORDER))


async def upgrade_schema(engine: object) -> None:
    async with engine.begin() as conn:
        for table in _TABLES_CREATE_ORDER:
            await conn.run_sync(lambda c, t=table: t.create(c, checkfirst=True))


async def uninstall_schema(engine: object) -> None:
    async with engine.begin() as conn:
        for table in _TABLES_DROP_ORDER:
            await conn.run_sync(lambda c, t=table: t.drop(c, checkfirst=True))
