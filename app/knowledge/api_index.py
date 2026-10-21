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


# ------------------------------------------------------- real-function lookup
# When the model calls a function that does not exist, the most useful help is
# the exact form of the REAL functions it probably meant. These are found by
# name (same prefix, similar spelling) and read straight from the headers.

_VERBS = frozenset("init deinit create new del delete start stop begin end setup get set read write "
                   "config configure install uninstall enable disable add remove open close".split())


# Words models use (often from Arduino) -> the word ESP-IDF uses for the same thing.
_SYNONYMS = {"write": "set", "read": "get", "init": "new", "create": "new", "begin": "start", "level": "level",
             "value": "level", "output": "level", "mode": "direction", "pin": "gpio", "digital": "gpio",
             "analog": "adc", "pwm": "ledc", "duty": "duty", "freq": "freq", "frequency": "freq"}


# Arduino functions models often slip in, and the ESP-IDF functions that do the same job.
_ARDUINO = {
    "digitalWrite": ["gpio_set_level"], "digitalRead": ["gpio_get_level"], "pinMode": ["gpio_set_direction", "gpio_config"],
    "analogRead": ["adc_oneshot_read", "adc_oneshot_new_unit", "adc_oneshot_config_channel"],
    "analogWrite": ["ledc_set_duty", "ledc_update_duty"], "delay": ["vTaskDelay"], "delayMicroseconds": ["esp_rom_delay_us"],
    "millis": ["esp_timer_get_time"], "micros": ["esp_timer_get_time"], "attachInterrupt": ["gpio_isr_handler_add", "gpio_install_isr_service"],
    "tone": ["ledc_set_freq", "ledc_set_duty"],
}


def _tokens(name: str) -> set[str]:
    words = {t.lower() for t in re.split(r"_|(?<=[a-z])(?=[A-Z])", name) if len(t) > 1}
    return words | {_SYNONYMS[w] for w in words if w in _SYNONYMS}


def similar_names(name: str, index: ApiIndex, limit: int = 5) -> list[str]:
    """Real, non-deprecated ESP-IDF names that look like ``name`` (best first).

    Two ways to be a candidate: the same leading words (mcpwm_generator_set_*),
    or all the same "content" words in any order (i2c_master_bus_init ->
    i2c_new_master_bus). A candidate must share at least two words, so random
    look-alikes (set_pixel -> set_errno) are not suggested."""
    import difflib

    if name in _ARDUINO:
        return [n for n in _ARDUINO[name] if n in index.names][:limit]
    words = _tokens(name)
    content = words - _VERBS
    parts = name.split("_")
    pool: set[str] = set()
    for k in range(len(parts) - 1, 0 if len(parts) == 2 else 1, -1):
        prefix = "_".join(parts[:k]) + "_"
        hits = [n for n, dep in index.names.items() if n.startswith(prefix) and not dep and n != name]
        pool.update(hits)
        if len(pool) >= 6:
            break
    if not pool:
        pool.update(difflib.get_close_matches(name, [n for n, dep in index.names.items() if not dep], n=6, cutoff=0.8))
    if len(content) >= 2:
        pool.update(n for n, dep in index.names.items()
                    if not dep and n != name and content <= _tokens(n) and len(n) < len(name) + 25)

    def score(n: str) -> float:
        shared = len(words & _tokens(n))
        return difflib.SequenceMatcher(None, name.lower(), n.lower()).ratio() + 0.2 * shared

    good = [n for n in pool if len(words & _tokens(n)) >= 2 and not n.startswith(("_", "hal_", "ll_", "trace"))
            and "_ll_" not in n and "_hal_" not in n]
    good.sort(key=score, reverse=True)
    return good[:limit]


_header_cache: dict[str, list[tuple[str, Path]]] = {}


def declarations(idf_path: Path, names: list[str]) -> dict[str, dict[str, str]]:
    """{name: {"signature": "...", "header": "driver/gptimer.h"}} for the given real names."""
    wanted = set(names)
    found: dict[str, dict[str, str]] = {}
    if not wanted:
        return found
    key = str(idf_path)
    if key not in _header_cache:
        _header_cache[key] = _headers(Path(idf_path), None)
    for rel, path in _header_cache[key]:
        if len(found) == len(wanted):
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(n in text for n in wanted - found.keys()):
            continue
        text = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", text)).replace("\\\n", " ")
        include = rel.split("/include/", 1)[-1]
        for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)\(([^)]*)\)", text, flags=re.MULTILINE):
            if m.group(1) in wanted and m.group(1) not in found:
                found[m.group(1)] = {"signature": f"{m.group(1)}({' '.join(m.group(2).split())})  /* macro */",
                                     "header": include}
        text = re.sub(r"^\s*#.*$", " ", text, flags=re.MULTILINE)
        for raw in _SPLIT.split(text):
            stmt = _ATTRS.sub(" ", " ".join(raw.split())).strip()
            for prefix in ('extern "C"', "extern", "static", "inline", "__inline", "FORCE_INLINE_ATTR", "IRAM_ATTR"):
                if stmt.startswith(prefix + " "):
                    stmt = stmt[len(prefix) + 1:].lstrip()
            m = _DECL.match(stmt)
            if m and m.group(1) in wanted and m.group(1) not in found and len(stmt) < 400:
                found[m.group(1)] = {"signature": stmt + ";", "header": include}
    return found


def suggestions(unknown: list[str], index: ApiIndex, idf_path: Path, per_name: int = 4) -> dict[str, list[dict[str, str]]]:
    """For each made-up name: the real functions it probably meant, with their exact declarations."""
    similar = {u: similar_names(u, index, per_name) for u in unknown[:6]}
    decls = declarations(idf_path, [n for names in similar.values() for n in names])
    return {u: [{"name": n, **decls.get(n, {"signature": n + "(...)", "header": ""})} for n in names]
            for u, names in similar.items() if names}
