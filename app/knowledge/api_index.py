"""Catch made-up functions in generated code.

All function and function-like macro names declared in the installed ESP-IDF
headers (and the C library headers of its compiler) are collected once and
cached. Every name the generated code *calls* is looked up: a name that is
neither in that list nor defined in the file itself does not exist - the
model invented it. Names from deprecated/legacy headers are reported too,
because they are outdated ways of doing things in ESP-IDF v5.

The header scan is a simple text scan, not a C parser. It errs on the side of
accepting names, so a real function is not flagged by mistake.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import CACHE_DIR

INDEX_VERSION = 2

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_MACRO_FN = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\(", re.MULTILINE)
_SPLIT = re.compile(r"[;{}]")
_DECL = re.compile(r"^[A-Za-z_][\w\s\*]*?[\s\*]([A-Za-z_]\w*)\s*\(")
_ATTRS = re.compile(r"__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)")

C_KEYWORDS = frozenset(
    "if for while switch return sizeof do else case typedef struct union enum defined "
    "__attribute__ __asm__ asm _Static_assert static_assert typeof __typeof__ alignof "
    "_Alignof __extension__ _Generic va_arg va_start va_end va_copy offsetof "
    "__builtin_expect __builtin_unreachable".split()
)


def scan_header(text: str) -> tuple[set[str], set[str], set[str]]:
    """(functions, macros, deprecated names) declared in one header's text."""
    text = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", text)).replace("\\\n", " ")
    macros = set(_MACRO_FN.findall(text))
    body = re.sub(r"^\s*#.*$", " ", text, flags=re.MULTILINE)
    functions: set[str] = set()
    deprecated: set[str] = set()
    for raw in _SPLIT.split(body):
        stmt = " ".join(raw.split())
        if "(" not in stmt or len(stmt) > 2000:
            continue
        is_deprecated = "deprecated" in stmt.lower()
        stmt = _ATTRS.sub(" ", stmt).strip()
        for prefix in ('extern "C"', "extern", "static", "inline", "__inline", "FORCE_INLINE_ATTR", "IRAM_ATTR"):
            if stmt.startswith(prefix + " "):
                stmt = stmt[len(prefix) + 1:].lstrip()
        m = _DECL.match(stmt)
        if m and m.group(1) not in C_KEYWORDS:
            functions.add(m.group(1))
            if is_deprecated:
                deprecated.add(m.group(1))
    return functions, macros, deprecated


def _headers(idf_path: Path, tools_path: Path | None) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for h in (Path(idf_path) / "components").rglob("*.h"):
        rel = h.relative_to(idf_path).as_posix()
        if ("/include" not in rel and "/deprecated/" not in rel) or "private_include" in rel:
            continue
        if any(x in rel for x in ("/test", "/host_test", "/linux/")):
            continue
        found.append((rel, h))
    if tools_path:
        for inc in sorted(Path(tools_path).glob("tools/xtensa-esp-elf/*/xtensa-esp-elf/xtensa-esp-elf/include"))[-1:]:
            for h in inc.rglob("*.h"):
                rel = h.relative_to(inc).as_posix()
                if not rel.startswith("c++/"):
                    found.append(("libc:" + rel, h))
    return found


@dataclass
class ApiIndex:
    names: dict[str, int]          # name -> 1 if deprecated else 0
    count_headers: int = 0

    @classmethod
    def build(cls, idf_path: Path, tools_path: Path | None) -> "ApiIndex":
        names: dict[str, int] = {}
        headers = _headers(idf_path, tools_path)
        for rel, path in headers:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            functions, macros, deprecated = scan_header(text)
            legacy = "deprecated" in rel or "legacy" in rel
            for name in functions | macros:
                flag = 1 if (name in deprecated or legacy) else 0
                names[name] = min(names.get(name, flag), flag)  # public declaration wins
        return cls(names=names, count_headers=len(headers))

    def save(self, path: Path, idf_path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": INDEX_VERSION, "idf_path": str(idf_path),
                                    "headers": self.count_headers, "names": self.names}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, idf_path: Path) -> "ApiIndex | None":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != INDEX_VERSION or Path(data.get("idf_path", "")) != Path(idf_path):
            return None
        return cls(names=data["names"], count_headers=data.get("headers", 0))


# ------------------------------------------------------------------ checking

_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"')
_CHAR = re.compile(r"'(?:\\.|[^'\\\n])+'")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_LOCAL_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)", re.MULTILINE)
_LOCAL_FUNC = re.compile(
    r"^[ \t]*(?:static\s+|inline\s+|IRAM_ATTR\s+)*[A-Za-z_][\w \t\*]*?[\s\*]([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{",
    re.MULTILINE,
)
_LOCAL_PROTO = re.compile(
    r"^[ \t]*(?:static\s+|extern\s+)*[A-Za-z_][\w \t\*]*?[\s\*]([A-Za-z_]\w*)\s*\([^;{}]*\)\s*;",
    re.MULTILINE,
)
_FUNC_PTR_VAR = re.compile(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)")


@dataclass
class ApiCheck:
    called: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"called": len(self.called), "unknown": self.unknown, "deprecated": self.deprecated}


def check_code(code: str, index: ApiIndex) -> ApiCheck:
    clean = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", code))
    clean = _CHAR.sub("' '", _STRING.sub('""', clean))
    local = (set(_LOCAL_DEFINE.findall(clean)) | set(_LOCAL_FUNC.findall(clean))
             | set(_LOCAL_PROTO.findall(clean)) | set(_FUNC_PTR_VAR.findall(clean)))
    body = re.sub(r"^\s*#.*$", " ", clean, flags=re.MULTILINE)
    called: list[str] = []
    for name in _CALL.findall(body):
        if name not in C_KEYWORDS and name not in called:
            called.append(name)
    unknown = [n for n in called if n not in local and n not in index.names]
    deprecated = [n for n in called if n not in local and index.names.get(n) == 1]
    return ApiCheck(called=called, unknown=unknown, deprecated=deprecated)


_cache: dict[str, ApiIndex] = {}
_lock = threading.Lock()


def get_api_index(idf_path: Path, tools_path: Path | None, version: str) -> ApiIndex:
    key = str(idf_path)
    with _lock:
        if key in _cache:
            return _cache[key]
        cache_file = CACHE_DIR / f"api_names_v{version}.json"
        index = ApiIndex.load(cache_file, idf_path)
        if index is None:
            index = ApiIndex.build(idf_path, tools_path)
            index.save(cache_file, idf_path)
        _cache[key] = index
        return index
