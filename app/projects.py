"""Projects: chat history, every version of the code, and board settings.

Layout of one project (data/projects/<id>/):

    project.json            name, dates, board + connected parts
    chat.json               the conversation
    versions.json           list of versions: number, who made it, note, time
    versions/0001.c ...     the code of every version
    firmware/               a normal ESP-IDF project with the CURRENT code
                            (open it in VS Code, or flash it by hand)

Nothing is ever overwritten: every change - by the AI, by a fix, by the user's
own edits, or by restoring an old version - becomes a new version.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import FIRMWARE_TEMPLATE, PROJECTS_DIR, ensure_dirs

DEFAULT_BOARD = {"name": "ESP32 DevKitC (ESP32-WROOM-32, 4 MB flash)", "parts": [], "notes": ""}
_lock = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class ProjectNotFound(KeyError):
    pass


class Project:
    def __init__(self, folder: Path):
        self.folder = folder
        if not (folder / "project.json").exists():
            raise ProjectNotFound(folder.name)

    @property
    def id(self) -> str:
        return self.folder.name

    # ----------------------------------------------------------------- meta
    def meta(self) -> dict[str, Any]:
        return _read(self.folder / "project.json", {})

    def update_meta(self, **changes: Any) -> dict[str, Any]:
        with _lock:
            meta = self.meta()
            if "name" in changes and str(changes["name"]).strip():
                meta["name"] = str(changes["name"]).strip()[:80]
            if "board" in changes and isinstance(changes["board"], dict):
                board = changes["board"]
                meta["board"] = {
                    "name": str(board.get("name") or DEFAULT_BOARD["name"])[:120],
                    "notes": str(board.get("notes") or "")[:1000],
                    "parts": [
                        {"part": str(p.get("part", ""))[:80], "pins": str(p.get("pins", ""))[:80],
                         "notes": str(p.get("notes", ""))[:200]}
                        for p in (board.get("parts") or [])[:30] if str(p.get("part", "")).strip()
                    ],
                }
            meta["updated"] = _now()
            _write(self.folder / "project.json", meta)
            return meta

    # ----------------------------------------------------------------- chat
    def chat(self) -> list[dict[str, Any]]:
        return _read(self.folder / "chat.json", [])

    def add_message(self, role: str, content: str, kind: str = "chat", **extra: Any) -> dict[str, Any]:
        with _lock:
            chat = self.chat()
            msg = {"id": uuid.uuid4().hex[:10], "role": role, "kind": kind, "content": content, "time": _now(), **extra}
            chat.append(msg)
            _write(self.folder / "chat.json", chat)
            self._touch()
            return msg

    def update_message(self, msg_id: str, **changes: Any) -> None:
        with _lock:
            chat = self.chat()
            for m in chat:
                if m["id"] == msg_id:
                    m.update(changes)
            _write(self.folder / "chat.json", chat)

    def clear_chat(self) -> None:
        with _lock:
            _write(self.folder / "chat.json", [])

    # ------------------------------------------------------------- versions
    def versions(self) -> list[dict[str, Any]]:
        return _read(self.folder / "versions.json", [])

    def code(self, number: int | None = None) -> str:
        versions = self.versions()
        if not versions:
            return ""
        n = number if number is not None else versions[-1]["n"]
        path = self.folder / "versions" / f"{n:04d}.c"
        if not path.exists():
            raise KeyError(f"Version {n} does not exist.")
        return path.read_text(encoding="utf-8")

    def current_version(self) -> int:
        versions = self.versions()
        return versions[-1]["n"] if versions else 0

    def add_version(self, code: str, source: str, note: str = "") -> dict[str, Any] | None:
        """Save ``code`` as a new version. Returns None if it equals the current code."""
        with _lock:
            versions = self.versions()
            if versions and self.code().strip() == code.strip():
                return None
            n = (versions[-1]["n"] + 1) if versions else 1
            (self.folder / "versions").mkdir(exist_ok=True)
            (self.folder / "versions" / f"{n:04d}.c").write_text(code, encoding="utf-8")
            entry = {"n": n, "source": source, "note": note[:200], "time": _now(), "lines": code.count("\n") + 1}
            versions.append(entry)
            _write(self.folder / "versions.json", versions)
            self._sync_firmware(code)
            self._touch()
            return entry

    def set_build_status(self, n: int, ok: bool) -> None:
        with _lock:
            versions = self.versions()
            for v in versions:
                if v["n"] == n:
                    v["build"] = "ok" if ok else "failed"
            _write(self.folder / "versions.json", versions)

    # -------------------------------------------------------------- firmware
    def firmware_dir(self) -> Path:
        return self.folder / "firmware"

    def _sync_firmware(self, code: str) -> None:
        fw = self.firmware_dir()
        if not (fw / "CMakeLists.txt").exists():
            shutil.copytree(FIRMWARE_TEMPLATE, fw, dirs_exist_ok=True)
        (fw / "main" / "main.c").write_text(code, encoding="utf-8")

    def export_zip(self) -> bytes:
        meta = self.meta()
        buf = io.BytesIO()
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", meta.get("name", "project")).strip("_") or "project"
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(self.firmware_dir().rglob("*")):
                rel = path.relative_to(self.firmware_dir())
                if path.is_file() and not rel.parts[0] in ("build",) and rel.name != "sdkconfig":
                    z.write(path, f"{safe}/{rel.as_posix()}")
            z.writestr(f"{safe}/CHAT.md", self.chat_markdown())
        return buf.getvalue()

    def chat_markdown(self) -> str:
        meta = self.meta()
        lines = [f"# {meta.get('name')}", "", f"Exported {_now()} from AI-Driven Embedded Programmer", ""]
        for m in self.chat():
            who = "You" if m["role"] == "user" else "AI"
            lines += [f"## {who} ({m.get('kind', 'chat')}, {m.get('time', '')})", "", m["content"], ""]
        return "\n".join(lines)

    # --------------------------------------------------------------- summary
    def summary(self) -> dict[str, Any]:
        meta = self.meta()
        return {"id": self.id, "name": meta.get("name"), "created": meta.get("created"),
                "updated": meta.get("updated"), "versions": self.current_version()}

    def full(self) -> dict[str, Any]:
        return {**self.summary(), "board": self.meta().get("board", DEFAULT_BOARD),
                "chat": self.chat(), "version_list": self.versions(), "code": self.code(),
                "firmware_dir": str(self.firmware_dir())}

    def _touch(self) -> None:
        meta = self.meta()
        meta["updated"] = _now()
        _write(self.folder / "project.json", meta)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "project"
    return f"{s}-{uuid.uuid4().hex[:6]}"


def create(name: str) -> Project:
    ensure_dirs()
    name = (name or "").strip()[:80] or "Untitled project"
    folder = PROJECTS_DIR / _slug(name)
    folder.mkdir(parents=True)
    (folder / "versions").mkdir()
    _write(folder / "project.json", {"name": name, "created": _now(), "updated": _now(), "board": dict(DEFAULT_BOARD)})
    _write(folder / "chat.json", [])
    _write(folder / "versions.json", [])
    return Project(folder)


def get(project_id: str) -> Project:
    if not re.fullmatch(r"[a-z0-9-]{1,60}", project_id or ""):
        raise ProjectNotFound(project_id)
    return Project(PROJECTS_DIR / project_id)


def list_all() -> list[dict[str, Any]]:
    ensure_dirs()
    out = []
    for folder in PROJECTS_DIR.iterdir():
        if (folder / "project.json").exists():
            out.append(Project(folder).summary())
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def delete(project_id: str) -> None:
    project = get(project_id)
    shutil.rmtree(project.folder)
