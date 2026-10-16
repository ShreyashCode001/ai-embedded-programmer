"""Find the installed ESP-IDF and prepare the environment its tools need.

Works from any terminal. Lookup order:
1. Paths typed in the app's Settings (idf_path / idf_tools_path / idf_python)
2. Environment variables (inside an ESP-IDF terminal)
3. C:\\Espressif\\tools\\eim_idf.json on Windows (the file VS Code's ESP-IDF
   extension uses), or ~/.espressif/tools/eim_idf.json elsewhere
4. Usual folders: C:\\Espressif\\frameworks\\esp-idf-v*, ~/esp/esp-idf*

The toolchain PATH comes from ESP-IDF's own ``idf_tools.py export``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW: no black console pop-ups


class IdfNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class Idf:
    path: Path
    tools_path: Path
    python: Path
    version: str
    found_via: str
    env: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def idf_py(self) -> list[str]:
        return [str(self.python), str(self.path / "tools" / "idf.py")]


def read_version(idf_path: Path) -> str:
    try:
        text = (Path(idf_path) / "tools" / "cmake" / "version.cmake").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    parts = dict(re.findall(r"set\(IDF_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\)", text))
    return f"{parts.get('MAJOR', '?')}.{parts.get('MINOR', '?')}.{parts.get('PATCH', '?')}"


def _venv_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _find_python(tools: Path, version: str) -> Path | None:
    env_dir = os.environ.get("IDF_PYTHON_ENV_PATH")
    if env_dir and _venv_python(Path(env_dir)).exists():
        return _venv_python(Path(env_dir))
    mm = ".".join(version.split(".")[:2])
    for pattern in (f"idf{mm}_py*_env", "idf*_py*_env"):
        for env in sorted((tools / "python_env").glob(pattern), reverse=True):
            if _venv_python(env).exists():
                return _venv_python(env)
    return None


def _default_tools() -> Path:
    return Path("C:/Espressif") if IS_WINDOWS else Path.home() / ".espressif"


def _candidates(settings: dict[str, Any]) -> list[tuple[str, dict[str, str]]]:
    out: list[tuple[str, dict[str, str]]] = []
    if settings.get("idf_path"):
        out.append(("Settings", {"path": settings["idf_path"], "tools": settings.get("idf_tools_path", ""),
                                 "python": settings.get("idf_python", "")}))
    if os.environ.get("IDF_PATH"):
        out.append(("environment", {"path": os.environ["IDF_PATH"], "tools": os.environ.get("IDF_TOOLS_PATH", ""),
                                    "python": ""}))
    eim = (Path("C:/Espressif/tools/eim_idf.json") if IS_WINDOWS
           else Path.home() / ".espressif" / "tools" / "eim_idf.json")
    if eim.exists():
        try:
            data = json.loads(eim.read_text(encoding="utf-8"))
            installs = sorted(data.get("idfInstalled") or [], key=lambda i: i.get("id") != data.get("idfSelectedId"))
            for inst in installs:
                out.append((str(eim), {"path": inst.get("path", ""), "tools": inst.get("idfToolsPath", ""),
                                       "python": inst.get("python", "")}))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    folders = (sorted(Path("C:/Espressif/frameworks").glob("esp-idf-v*"), reverse=True) if IS_WINDOWS
               else sorted(Path.home().joinpath("esp").glob("esp-idf*"), reverse=True))
    for f in folders:
        out.append(("default folder", {"path": str(f), "tools": "", "python": ""}))
    return out


def locate(settings: dict[str, Any]) -> Idf:
    tried = []
    for source, c in _candidates(settings):
        path = Path(c["path"]) if c["path"] else None
        if not path or not (path / "tools" / "idf.py").exists():
            tried.append(f"{source}: {c['path'] or '(empty)'}")
            continue
        tools = Path(c["tools"]) if c["tools"] else _default_tools()
        version = read_version(path)
        python = Path(c["python"]) if c["python"] else _find_python(tools, version)
        if not python or not python.exists():
            tried.append(f"{source}: {path} (no ESP-IDF Python environment in {tools})")
            continue
        return Idf(path=path, tools_path=tools, python=python, version=version, found_via=source)
    raise IdfNotFound(
        "ESP-IDF was not found. Install ESP-IDF v5.5, or enter its folders in Settings → ESP-IDF.\n"
        "Looked in: " + "; ".join(tried or ["nothing to try"])
    )


_env_cache: dict[tuple, Idf] = {}
_lock = threading.Lock()


def get_idf(settings: dict[str, Any]) -> Idf:
    """Locate ESP-IDF and compute its environment once per process."""
    base = locate(settings)
    key = (str(base.path), str(base.tools_path), str(base.python))
    with _lock:
        if key in _env_cache:
            return _env_cache[key]
        env = dict(os.environ)
        env.update({"IDF_PATH": str(base.path), "IDF_TOOLS_PATH": str(base.tools_path),
                    "IDF_PYTHON_CHECK_CONSTRAINTS": "no", "PYTHONUTF8": "1"})
        proc = subprocess.run(
            [str(base.python), str(base.path / "tools" / "idf_tools.py"), "export", "--format", "key-value"],
            env=env, capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW,
        )
        if proc.returncode != 0:
            raise IdfNotFound(f"ESP-IDF found at {base.path}, but its tools are not installed correctly:\n"
                              + (proc.stderr or proc.stdout)[-800:])
        for line in proc.stdout.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().upper() == "PATH":
                current = os.environ.get("PATH", "")
                v = v.replace("%PATH%", current).replace("$PATH", current)
            env[k.strip()] = v.strip()
        env["IDF_PYTHON_ENV_PATH"] = str(base.python.parent.parent)
        idf = Idf(base.path, base.tools_path, base.python, base.version, base.found_via, env)
        _env_cache[key] = idf
        return idf


def find_qemu(idf: Idf) -> Path | None:
    exe = "qemu-system-xtensa.exe" if IS_WINDOWS else "qemu-system-xtensa"
    for p in sorted((idf.tools_path / "tools" / "qemu-xtensa").glob(f"*/qemu/bin/{exe}"), reverse=True):
        return p
    for d in idf.env.get("PATH", "").split(os.pathsep):
        if d and (Path(d) / exe).exists():
            return Path(d) / exe
    return None


def status(settings: dict[str, Any]) -> dict[str, Any]:
    try:
        idf = get_idf(settings)
    except IdfNotFound as exc:
        return {"found": False, "message": str(exc)}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"found": False, "message": f"Could not start ESP-IDF tools: {exc}"}
    qemu = find_qemu(idf)
    return {"found": True, "version": idf.version, "path": str(idf.path), "found_via": idf.found_via,
            "qemu": str(qemu) if qemu else None}
