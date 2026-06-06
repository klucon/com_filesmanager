from __future__ import annotations

from .models import FileAuditLog, FileCategory, FileShare, ManagedFile

_TABLES_CREATE_ORDER = [
    FileCategory.__table__,
    ManagedFile.__table__,
    FileShare.__table__,
    FileAuditLog.__table__,
]

_TABLES_DROP_ORDER = list(reversed(_TABLES_CREATE_ORDER))


async def upgrade_schema(engine: object) -> None:
    async with engine.begin() as conn:
        for table in _TABLES_CREATE_ORDER:
            await conn.run_sync(lambda c, t=table: t.create(c, checkfirst=True))


async def uninstall_schema(engine: object) -> None:
    async with engine.begin() as conn:
        for table in _TABLES_DROP_ORDER:
            await conn.run_sync(lambda c, t=table: t.drop(c, checkfirst=True))
