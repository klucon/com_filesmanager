from __future__ import annotations

from pathlib import Path

import pytest

from src.components.com_filesmanager import service
from src.components.com_filesmanager.service import FileManagerError


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "files"
    base.mkdir()
    monkeypatch.setattr(service, "get_root", lambda: base.resolve())
    return base.resolve()


def test_create_and_list_dir(root: Path) -> None:
    rel = service.create_dir(None, "docs")
    assert rel == "docs"
    assert (root / "docs").is_dir()
    listing = service.list_dir(None)
    assert listing.dir_count == 1
    assert listing.entries[0].name == "docs"
    assert listing.entries[0].is_dir


def test_upload_dedupe(root: Path) -> None:
    first = service.save_upload(None, "a.txt", b"one")
    second = service.save_upload(None, "a.txt", b"two")
    assert first == "a.txt"
    assert second == "a (2).txt"
    assert (root / "a.txt").read_bytes() == b"one"
    assert (root / "a (2).txt").read_bytes() == b"two"


def test_rename_move_copy(root: Path) -> None:
    service.create_dir(None, "src")
    service.create_dir(None, "dst")
    service.save_upload("src", "f.txt", b"x")
    renamed = service.rename("src/f.txt", "g.txt")
    assert renamed == "src/g.txt"
    moved = service.move("src/g.txt", "dst")
    assert moved == "dst/g.txt"
    copied = service.copy("dst/g.txt", "src")
    assert copied == "src/g.txt"
    assert (root / "src/g.txt").exists()
    assert (root / "dst/g.txt").exists()


def test_trash_roundtrip(root: Path) -> None:
    service.save_upload(None, "del.txt", b"bye")
    trash_id = service.delete_to_trash("del.txt")
    assert not (root / "del.txt").exists()
    items = service.list_trash()
    assert len(items) == 1
    assert items[0].trash_id == trash_id
    restored = service.restore_from_trash(trash_id)
    assert restored == "del.txt"
    assert (root / "del.txt").exists()
    assert service.list_trash() == []


def test_empty_trash(root: Path) -> None:
    service.save_upload(None, "a.txt", b"a")
    service.save_upload(None, "b.txt", b"b")
    service.delete_to_trash("a.txt")
    service.delete_to_trash("b.txt")
    assert service.empty_trash() == 2
    assert service.list_trash() == []


def test_search(root: Path) -> None:
    service.create_dir(None, "deep")
    service.save_upload("deep", "report-2026.pdf", b"%PDF")
    service.save_upload(None, "notes.txt", b"hi")
    results = service.search("report")
    assert len(results) == 1
    assert results[0].name == "report-2026.pdf"


def test_text_editor(root: Path) -> None:
    service.save_upload(None, "config.ini", b"[a]\n")
    assert service.read_text_file("config.ini") == "[a]\n"
    service.write_text_file("config.ini", "[b]\n")
    assert (root / "config.ini").read_text() == "[b]\n"


def test_zip_and_extract(root: Path) -> None:
    service.create_dir(None, "bundle")
    service.save_upload("bundle", "one.txt", b"1")
    service.save_upload("bundle", "two.txt", b"2")
    archive = service.make_zip(["bundle"], None, "out.zip")
    assert archive == "out.zip"
    assert (root / "out.zip").exists()
    extracted = service.extract_zip("out.zip")
    assert (root / extracted).is_dir()


def test_sandbox_escape_blocked(root: Path) -> None:
    with pytest.raises(FileManagerError):
        service.resolve("../escape")
    with pytest.raises(FileManagerError):
        service.create_dir(None, "../escape")


def test_invalid_name_blocked(root: Path) -> None:
    with pytest.raises(FileManagerError):
        service.safe_name("a/b")
    with pytest.raises(FileManagerError):
        service.safe_name("..")
