"""Filesystem service for com_filesmanager.

Veškeré operace jsou uzamčené (sandbox) do jednoho kořenového adresáře.
Žádná cesta nesmí uniknout mimo tento kořen.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

TRASH_DIRNAME = "_trash"
DEFAULT_ROOT = Path("storage/files")

MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB strop pro editor
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ZIP_MEMBERS = 1000
DEFAULT_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_ZIP_RATIO = 250

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
ARCHIVE_EXTENSIONS = {".zip"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".html", ".htm", ".css", ".scss", ".less", ".js", ".ts", ".jsx", ".tsx", ".vue",
    ".py", ".php", ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".sh", ".bash",
    ".sql", ".xml", ".svg", ".gitignore", ".dockerignore",
}
PREVIEW_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}

SORT_KEYS = {"name", "size", "modified", "type"}

try:
    from src.config import get_settings as _get_settings
except ModuleNotFoundError:

    def _get_settings() -> object:
        class _FallbackSettings:
            FILESMANAGER_DIR = DEFAULT_ROOT
            FILESMANAGER_MAX_UPLOAD_BYTES = DEFAULT_MAX_UPLOAD_BYTES
            FILESMANAGER_MAX_ZIP_MEMBERS = DEFAULT_MAX_ZIP_MEMBERS
            FILESMANAGER_MAX_ZIP_TOTAL_BYTES = DEFAULT_MAX_ZIP_TOTAL_BYTES
            FILESMANAGER_MAX_ZIP_RATIO = DEFAULT_MAX_ZIP_RATIO

        return _FallbackSettings()


@dataclass(frozen=True, slots=True)
class FileManagerConfig:
    root: Path
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS
    max_zip_total_bytes: int = DEFAULT_MAX_ZIP_TOTAL_BYTES
    max_zip_ratio: int = DEFAULT_MAX_ZIP_RATIO


class FileManagerError(Exception):
    """Chyba operace správce souborů (bezpečná k zobrazení uživateli)."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


@dataclass(slots=True)
class Entry:
    name: str
    rel_path: str
    is_dir: bool
    size: int
    modified: float
    ext: str = ""
    is_image: bool = False
    is_text: bool = False
    is_archive: bool = False
    can_preview: bool = False
    child_count: int | None = None


@dataclass(slots=True)
class TrashItem:
    trash_id: str
    name: str
    original_rel: str
    deleted_at: str
    is_dir: bool
    size: int


@dataclass(slots=True)
class Listing:
    rel_dir: str
    entries: list[Entry] = field(default_factory=list)
    breadcrumbs: list[dict[str, str]] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0
    dir_count: int = 0


# --------------------------------------------------------------------------- #
# Kořen a bezpečné cesty
# --------------------------------------------------------------------------- #
def get_root() -> Path:
    return get_config().root


def get_config() -> FileManagerConfig:
    settings = _get_settings()
    root = Path(getattr(settings, "FILESMANAGER_DIR", DEFAULT_ROOT)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return FileManagerConfig(
        root=root,
        max_upload_bytes=max(
            int(getattr(settings, "FILESMANAGER_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)),
            1,
        ),
        max_zip_members=max(
            int(getattr(settings, "FILESMANAGER_MAX_ZIP_MEMBERS", DEFAULT_MAX_ZIP_MEMBERS)),
            1,
        ),
        max_zip_total_bytes=max(
            int(getattr(settings, "FILESMANAGER_MAX_ZIP_TOTAL_BYTES", DEFAULT_MAX_ZIP_TOTAL_BYTES)),
            1,
        ),
        max_zip_ratio=max(
            int(getattr(settings, "FILESMANAGER_MAX_ZIP_RATIO", DEFAULT_MAX_ZIP_RATIO)),
            1,
        ),
    )


def _trash_root() -> Path:
    trash = get_root() / TRASH_DIRNAME
    trash.mkdir(parents=True, exist_ok=True)
    return trash


def _normalize_rel(rel: str | None) -> str:
    """Vrátí normalizovanou relativní cestu (POSIX) bez úniku z kořene."""
    raw = (rel or "").strip().strip("/")
    if not raw:
        return ""
    pure = PurePosixPath(raw)
    parts = [p for p in pure.parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise FileManagerError("com_filesmanager.error.invalid_path")
    return "/".join(parts)


def resolve(rel: str | None, *, must_exist: bool = False) -> Path:
    """Přeloží relativní cestu na absolutní uvnitř kořene a ověří sandbox."""
    root = get_root()
    norm = _normalize_rel(rel)
    target = (root / norm).resolve() if norm else root
    if target != root and root not in target.parents:
        raise FileManagerError("com_filesmanager.error.invalid_path")
    if must_exist and not target.exists():
        raise FileManagerError("com_filesmanager.error.not_found", name=norm or "/")
    return target


def _to_rel(path: Path) -> str:
    return path.resolve().relative_to(get_root()).as_posix()


def safe_name(name: str) -> str:
    """Ověří jméno souboru/složky. Oddělovače cest a `..` jsou odmítnuty."""
    cleaned = name.strip().replace("\x00", "")
    if (
        cleaned in ("", ".", "..")
        or "/" in cleaned
        or "\\" in cleaned
        or Path(cleaned).name != cleaned
    ):
        raise FileManagerError("com_filesmanager.error.invalid_name", name=name)
    return cleaned


# --------------------------------------------------------------------------- #
# Klasifikace
# --------------------------------------------------------------------------- #
def _classify(path: Path) -> dict[str, object]:
    ext = path.suffix.lower()
    return {
        "ext": ext,
        "is_image": ext in IMAGE_EXTENSIONS,
        "is_text": ext in TEXT_EXTENSIONS,
        "is_archive": ext in ARCHIVE_EXTENSIONS,
        "can_preview": ext in PREVIEW_EXTENSIONS,
    }


def _classify_name(name: str) -> dict[str, object]:
    return _classify(Path(name))


def _entry_from_path(path: Path) -> Entry:
    stat = path.stat()
    is_dir = path.is_dir()
    info = _classify(path)
    child_count = None
    if is_dir:
        try:
            child_count = sum(1 for _ in path.iterdir())
        except OSError:
            child_count = None
    return Entry(
        name=path.name,
        rel_path=_to_rel(path),
        is_dir=is_dir,
        size=0 if is_dir else stat.st_size,
        modified=stat.st_mtime,
        ext="" if is_dir else str(info["ext"]),
        is_image=False if is_dir else bool(info["is_image"]),
        is_text=False if is_dir else bool(info["is_text"]),
        is_archive=False if is_dir else bool(info["is_archive"]),
        can_preview=False if is_dir else bool(info["can_preview"]),
        child_count=child_count,
    )


def _is_hidden_root_entry(path: Path) -> bool:
    return path.name == TRASH_DIRNAME and path.parent == get_root()


# --------------------------------------------------------------------------- #
# Procházení
# --------------------------------------------------------------------------- #
def build_breadcrumbs(rel_dir: str) -> list[dict[str, str]]:
    crumbs = [{"name": "/", "rel": ""}]
    acc: list[str] = []
    for part in _normalize_rel(rel_dir).split("/"):
        if not part:
            continue
        acc.append(part)
        crumbs.append({"name": part, "rel": "/".join(acc)})
    return crumbs


def list_dir(rel_dir: str | None, *, sort: str = "name", desc: bool = False) -> Listing:
    target = resolve(rel_dir, must_exist=True)
    if not target.is_dir():
        raise FileManagerError("com_filesmanager.error.not_a_dir", name=rel_dir or "/")
    if sort not in SORT_KEYS:
        sort = "name"

    listing = Listing(rel_dir=_normalize_rel(rel_dir), breadcrumbs=build_breadcrumbs(rel_dir or ""))
    for child in target.iterdir():
        if _is_hidden_root_entry(child):
            continue
        try:
            entry = _entry_from_path(child)
        except OSError:
            continue
        listing.entries.append(entry)
        if entry.is_dir:
            listing.dir_count += 1
        else:
            listing.file_count += 1
            listing.total_size += entry.size

    listing.entries.sort(key=_sort_key(sort), reverse=desc)
    return listing


def _sort_key(sort: str):
    def key(e: Entry):
        # Složky vždy nahoře, pak dle zvoleného kritéria.
        if sort == "size":
            return (not e.is_dir, e.size)
        if sort == "modified":
            return (not e.is_dir, e.modified)
        if sort == "type":
            return (not e.is_dir, e.ext, e.name.lower())
        return (not e.is_dir, e.name.lower())

    return key


# --------------------------------------------------------------------------- #
# Operace se soubory/složkami
# --------------------------------------------------------------------------- #
def create_dir(rel_dir: str | None, name: str) -> str:
    parent = resolve(rel_dir, must_exist=True)
    new_dir = parent / safe_name(name)
    if new_dir.exists():
        raise FileManagerError("com_filesmanager.error.exists", name=new_dir.name)
    new_dir.mkdir()
    return _to_rel(new_dir)


def save_upload(
    rel_dir: str | None,
    filename: str,
    data: bytes,
    *,
    overwrite: bool = False,
    content_type: str | None = None,
) -> str:
    parent = resolve(rel_dir, must_exist=True)
    if not parent.is_dir():
        raise FileManagerError("com_filesmanager.error.not_a_dir", name=rel_dir or "/")
    validate_upload(filename, data, content_type=content_type)
    target = parent / safe_name(filename)
    if target.exists() and not overwrite:
        target = _dedupe(target)
    target.write_bytes(data)
    return _to_rel(target)


def validate_upload(filename: str, data: bytes, *, content_type: str | None = None) -> None:
    config = get_config()
    if len(data) > config.max_upload_bytes:
        raise FileManagerError(
            "com_filesmanager.error.upload_too_large",
            name=filename,
            size=human_size(config.max_upload_bytes),
        )

    guessed = _guess_upload_types(filename)
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized or normalized == "application/octet-stream":
        return
    if guessed and normalized not in guessed:
        raise FileManagerError(
            "com_filesmanager.error.invalid_content_type",
            name=filename,
            content_type=normalized,
        )


def _guess_upload_types(filename: str) -> set[str]:
    info = _classify_name(filename)
    ext = str(info["ext"])
    guessed: set[str] = set()
    if ext == ".zip":
        guessed.update({"application/zip", "application/x-zip-compressed"})
    elif ext == ".pdf":
        guessed.add("application/pdf")
    elif bool(info["is_image"]):
        mime, _ = mimetypes.guess_type(filename)
        if mime:
            guessed.add(mime)
    elif bool(info["is_text"]):
        mime, _ = mimetypes.guess_type(filename)
        if mime:
            guessed.add(mime)
        guessed.update(
            {
                "text/plain",
                "application/json",
                "application/xml",
                "application/x-yaml",
                "text/yaml",
                "application/toml",
            }
        )
    return guessed


def _dedupe(path: Path) -> Path:
    """Najde volné jméno: name (2).ext, name (3).ext, ..."""
    stem, suffix = path.stem, path.suffix
    counter = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        counter += 1
    return candidate


def rename(rel_path: str, new_name: str) -> str:
    source = resolve(rel_path, must_exist=True)
    if source == get_root():
        raise FileManagerError("com_filesmanager.error.invalid_path")
    target = source.with_name(safe_name(new_name))
    if target.exists():
        raise FileManagerError("com_filesmanager.error.exists", name=target.name)
    source.rename(target)
    return _to_rel(target)


def move(rel_path: str, dest_dir: str | None, *, overwrite: bool = False) -> str:
    source = resolve(rel_path, must_exist=True)
    dest = resolve(dest_dir, must_exist=True)
    if not dest.is_dir():
        raise FileManagerError("com_filesmanager.error.not_a_dir", name=dest_dir or "/")
    if source == get_root():
        raise FileManagerError("com_filesmanager.error.invalid_path")
    if source == dest or dest == source or _is_subpath(source, dest):
        raise FileManagerError("com_filesmanager.error.move_into_self", name=source.name)
    target = dest / source.name
    if target.exists():
        if not overwrite:
            raise FileManagerError("com_filesmanager.error.exists", name=target.name)
        _remove(target)
    shutil.move(str(source), str(target))
    return _to_rel(target)


def copy(rel_path: str, dest_dir: str | None, *, overwrite: bool = False) -> str:
    source = resolve(rel_path, must_exist=True)
    dest = resolve(dest_dir, must_exist=True)
    if not dest.is_dir():
        raise FileManagerError("com_filesmanager.error.not_a_dir", name=dest_dir or "/")
    if source.is_dir() and _is_subpath(source, dest):
        raise FileManagerError("com_filesmanager.error.move_into_self", name=source.name)
    target = dest / source.name
    if target.exists() and not overwrite:
        target = _dedupe(target)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    return _to_rel(target)


def _is_subpath(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


# --------------------------------------------------------------------------- #
# Koš (trash)
# --------------------------------------------------------------------------- #
def delete_to_trash(rel_path: str) -> str:
    source = resolve(rel_path, must_exist=True)
    if source == get_root() or _is_hidden_root_entry(source):
        raise FileManagerError("com_filesmanager.error.invalid_path")
    trash_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
    bucket = _trash_root() / trash_id
    bucket.mkdir(parents=True)
    meta = {
        "original_rel": _to_rel(source),
        "name": source.name,
        "deleted_at": datetime.now(UTC).isoformat(),
        "is_dir": source.is_dir(),
    }
    (bucket / ".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    shutil.move(str(source), str(bucket / source.name))
    return trash_id


def list_trash() -> list[TrashItem]:
    items: list[TrashItem] = []
    for bucket in sorted(_trash_root().iterdir(), reverse=True):
        if not bucket.is_dir():
            continue
        meta_file = bucket / ".meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = bucket / meta["name"]
        items.append(
            TrashItem(
                trash_id=bucket.name,
                name=meta["name"],
                original_rel=meta["original_rel"],
                deleted_at=meta["deleted_at"],
                is_dir=bool(meta.get("is_dir")),
                size=_path_size(payload),
            )
        )
    return items


def restore_from_trash(trash_id: str) -> str:
    bucket = _trash_root() / safe_name(trash_id)
    meta_file = bucket / ".meta.json"
    if not meta_file.exists():
        raise FileManagerError("com_filesmanager.error.not_found", name=trash_id)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    payload = bucket / meta["name"]
    target = resolve(meta["original_rel"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = _dedupe(target)
    shutil.move(str(payload), str(target))
    shutil.rmtree(bucket)
    return _to_rel(target)


def delete_trash_item(trash_id: str) -> None:
    bucket = _trash_root() / safe_name(trash_id)
    if bucket.exists():
        shutil.rmtree(bucket)


def empty_trash() -> int:
    count = 0
    for bucket in _trash_root().iterdir():
        if bucket.is_dir():
            shutil.rmtree(bucket)
            count += 1
    return count


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


# --------------------------------------------------------------------------- #
# Hledání
# --------------------------------------------------------------------------- #
def search(query: str, rel_dir: str | None = None, *, limit: int = 500) -> list[Entry]:
    needle = query.strip().lower()
    if not needle:
        return []
    base = resolve(rel_dir, must_exist=True)
    results: list[Entry] = []
    for path in base.rglob("*"):
        if TRASH_DIRNAME in path.relative_to(get_root()).parts:
            continue
        if needle in path.name.lower():
            try:
                results.append(_entry_from_path(path))
            except OSError:
                continue
        if len(results) >= limit:
            break
    results.sort(key=_sort_key("name"))
    return results


# --------------------------------------------------------------------------- #
# Textový / kódový editor
# --------------------------------------------------------------------------- #
def read_text_file(rel_path: str) -> str:
    source = resolve(rel_path, must_exist=True)
    if not source.is_file():
        raise FileManagerError("com_filesmanager.error.not_found", name=rel_path)
    if source.stat().st_size > MAX_EDIT_BYTES:
        raise FileManagerError("com_filesmanager.error.too_large_to_edit", name=source.name)
    if source.suffix.lower() not in TEXT_EXTENSIONS:
        raise FileManagerError("com_filesmanager.error.not_editable", name=source.name)
    try:
        return source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FileManagerError("com_filesmanager.error.not_editable", name=source.name) from exc


def write_text_file(rel_path: str, content: str) -> str:
    source = resolve(rel_path, must_exist=True)
    if not source.is_file():
        raise FileManagerError("com_filesmanager.error.not_found", name=rel_path)
    if source.suffix.lower() not in TEXT_EXTENSIONS:
        raise FileManagerError("com_filesmanager.error.not_editable", name=source.name)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_EDIT_BYTES:
        raise FileManagerError("com_filesmanager.error.too_large_to_edit", name=source.name)
    source.write_bytes(encoded)
    return _to_rel(source)


# --------------------------------------------------------------------------- #
# ZIP / rozbalení
# --------------------------------------------------------------------------- #
def make_zip(rel_paths: Iterable[str], rel_dir: str | None, archive_name: str) -> str:
    dest = resolve(rel_dir, must_exist=True)
    name = safe_name(archive_name)
    if not name.lower().endswith(".zip"):
        name += ".zip"
    archive = _dedupe(dest / name)
    sources = [resolve(p, must_exist=True) for p in rel_paths]
    if not sources:
        raise FileManagerError("com_filesmanager.error.nothing_selected")
    with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
        for source in sources:
            if source.is_dir():
                for child in source.rglob("*"):
                    if child.is_file():
                        zf.write(child, source.name + "/" + child.relative_to(source).as_posix())
            else:
                zf.write(source, source.name)
    return _to_rel(archive)


def extract_zip(rel_path: str, *, into_subdir: bool = True) -> str:
    source = resolve(rel_path, must_exist=True)
    if source.suffix.lower() != ".zip":
        raise FileManagerError("com_filesmanager.error.not_archive", name=source.name)
    target_dir = source.with_suffix("") if into_subdir else source.parent
    if into_subdir:
        target_dir = _dedupe(target_dir)
        target_dir.mkdir()
    resolved_target = target_dir.resolve()
    try:
        with ZipFile(source) as zf:
            _validate_zip_members(zf, resolved_target)
            zf.extractall(resolved_target)
    except BadZipFile as exc:
        raise FileManagerError("com_filesmanager.error.bad_archive", name=source.name) from exc
    return _to_rel(target_dir)


def _validate_zip_members(zf: ZipFile, resolved_target: Path) -> None:
    config = get_config()
    members = zf.infolist()
    file_members = [member for member in members if not member.is_dir()]
    if len(file_members) > config.max_zip_members:
        raise FileManagerError(
            "com_filesmanager.error.archive_too_large",
            count=len(file_members),
            size=human_size(config.max_zip_total_bytes),
        )

    total_size = 0
    for member in file_members:
        _validate_zip_destination(member, resolved_target)
        total_size += member.file_size
        if total_size > config.max_zip_total_bytes:
            raise FileManagerError(
                "com_filesmanager.error.archive_too_large",
                count=len(file_members),
                size=human_size(config.max_zip_total_bytes),
            )
        if (
            member.compress_size > 0
            and member.file_size / member.compress_size > config.max_zip_ratio
        ):
            raise FileManagerError("com_filesmanager.error.unsafe_archive", name=member.filename)


def _validate_zip_destination(member: ZipInfo, resolved_target: Path) -> None:
    dest = (resolved_target / member.filename).resolve()
    if dest != resolved_target and resolved_target not in dest.parents:
        raise FileManagerError("com_filesmanager.error.unsafe_archive", name=member.filename)
    mode = (member.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise FileManagerError("com_filesmanager.error.unsafe_archive", name=member.filename)


# --------------------------------------------------------------------------- #
# Stahování
# --------------------------------------------------------------------------- #
def resolve_download(rel_path: str) -> tuple[Path, bool]:
    """Vrátí (cesta, je_dočasný_zip). Složka se zabalí do dočasného zipu."""
    source = resolve(rel_path, must_exist=True)
    if source.is_file():
        return source, False
    tmp = Path(shutil.make_archive(str(_temp_base(source.name)), "zip", root_dir=source))
    return tmp, True


def _temp_base(name: str) -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / f"fm-{secrets.token_hex(6)}-{safe_name(name)}"


# --------------------------------------------------------------------------- #
# Sdílecí tokeny
# --------------------------------------------------------------------------- #
def new_share_token() -> str:
    return secrets.token_urlsafe(24)


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
