from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
MANIFEST = ROOT / "src" / "components" / "com_filesmanager" / "manifest.json"
NOTES_FILE = ROOT / "RELEASE_NOTES.md"

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def latest_semver_tag() -> str:
    tags = run_git("tag", "--list", "v[0-9]*", "--sort=-v:refname")
    for tag in tags.splitlines():
        if SEMVER_RE.match(tag):
            return tag
    return ""


def commits_since(tag: str) -> list[str]:
    revision = f"{tag}..HEAD" if tag else "HEAD"
    raw = run_git("log", "--format=%s%n%b%n---END---", revision)
    if not raw:
        return []
    return [
        item.strip()
        for item in raw.split("---END---")
        if item.strip() and "[skip release]" not in item
    ]


def detect_bump(commits: list[str], manual: str) -> str:
    if manual in {"major", "minor", "patch"}:
        return manual
    if not commits:
        return "patch"
    joined = "\n".join(commits)
    if "BREAKING CHANGE" in joined or re.search(r"^[a-zA-Z]+(?:\([^)]+\))?!:", joined, re.M):
        return "major"
    if re.search(r"^feat(?:\([^)]+\))?:", joined, re.M):
        return "minor"
    return "patch"


def current_version() -> str:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = str(payload.get("version") or "")
    match = SEMVER_RE.match(version)
    if not match:
        raise SystemExit(f"Neplatná verze v manifest.json: {version}")
    return ".".join(match.groups())


def bump_version(version: str, bump: str) -> str:
    match = SEMVER_RE.match(version)
    if not match:
        raise SystemExit(f"Neplatná SemVer verze: {version}")
    major, minor, patch = (int(part) for part in match.groups())
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def update_files(version: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["version"] = version
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pyproject = PYPROJECT.read_text(encoding="utf-8")
    pyproject = re.sub(
        r'^version = "[^"]+"$',
        f'version = "{version}"',
        pyproject,
        flags=re.MULTILINE,
    )
    PYPROJECT.write_text(pyproject, encoding="utf-8")


def release_notes(version: str, previous_tag: str, commits: list[str]) -> None:
    lines = [f"# v{version}", ""]
    if previous_tag:
        lines.append(f"Changes since `{previous_tag}`:")
    else:
        lines.append("Initial automated release.")
    lines.append("")
    for commit in commits:
        lines.append(f"- {commit.splitlines()[0]}")
    if not commits:
        lines.append("- Maintenance release.")
    NOTES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def github_output(**values: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        for key, value in values.items():
            print(f"{key}={value}")
        return
    with Path(output).open("a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bump", default="", choices=["", "major", "minor", "patch"])
    args = parser.parse_args()

    previous_tag = latest_semver_tag()
    base_version = previous_tag.lstrip("v") if previous_tag else current_version()
    commits = commits_since(previous_tag)
    bump = detect_bump(commits, args.bump)
    version = bump_version(base_version, bump)

    update_files(version)
    release_notes(version, previous_tag, commits)
    github_output(version=version, tag=f"v{version}", previous_tag=previous_tag, bump=bump)


if __name__ == "__main__":
    main()
