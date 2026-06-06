from __future__ import annotations

import sys
import types
from pathlib import Path

from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


class Base(DeclarativeBase):
    pass


src_module = _ensure_module("src")
src_module.__path__ = [str(ROOT / "src")]

core_module = _ensure_module("src.core")
core_module.__path__ = []

hooks_module = _ensure_module("src.core.hooks")


class _Hooks:
    def on(self, _event: str, _callback: object) -> None:
        return None


hooks_module.hooks = _Hooks()

database_module = _ensure_module("src.database")
database_module.__path__ = []

database_base_module = _ensure_module("src.database.base")
database_base_module.Base = Base
database_base_module.get_db_session = object()
