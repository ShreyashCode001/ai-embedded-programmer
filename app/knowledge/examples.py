"""Search the official ESP-IDF example projects on this computer.

ESP-IDF ships with ~450 example projects (GPIO, LEDC, ADC, I2C, Wi-Fi, ...),
written by Espressif for exactly the installed version. For each user request
we find the closest examples and show them to the model, so it copies real,
working v5.5 code instead of guessing.

Search method: BM25 keyword ranking (the classic search-engine formula) over
each example's README title/summary and the names used in its code. No extra
AI model or download is needed. The index is built once (a few seconds) and
cached in data/cache/.

Only examples whose README lists the classic ESP32 as a supported target are
used.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import CACHE_DIR

INDEX_VERSION = 3
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_LICENSE = re.compile(r"\A\s*/\*.*?\*/\s*", re.DOTALL)

STOPWORDS = frozenset(
    "the a an and or to of in on for with is are be this that it as at by from "
    "esp esp32 idf example examples void int return include define static const "
    "if else while for break case default uint8_t uint32_t int32_t char bool true false null "
    "using use used can will how what which want make please code program write".split()
)

# Words people say -> words that appear in ESP-IDF example code.
SYNONYMS = {
    "led": ["gpio", "blink", "ledc", "led"],
    "blink": ["blink", "gpio"],
    "pwm": ["ledc", "pwm", "mcpwm"],
    "fade": ["ledc", "fade"],
    "dim": ["ledc", "fade"],
    "brightness": ["ledc", "duty"],
    "servo": ["mcpwm", "servo", "ledc"],
    "motor": ["mcpwm", "motor", "ledc"],
    "button": ["gpio", "intr", "isr", "button"],
    "interrupt": ["isr", "intr", "gpio"],
    "potentiometer": ["adc", "oneshot", "adc_oneshot_read"],
    "analog": ["adc", "oneshot", "adc_oneshot_read"],
    "temperature": ["temperature", "sensor", "adc"],
    "sensor": ["sensor", "i2c", "adc"],
    "wifi": ["wifi", "station", "sta", "netif"],
    "internet": ["wifi", "http", "netif"],
    "web": ["http", "server", "httpd"],
    "server": ["httpd", "server"],
    "http": ["http", "client", "server"],
    "mqtt": ["mqtt"],
    "bluetooth": ["bt", "ble", "gatt", "nimble"],
    "ble": ["ble", "gatt", "nimble"],
    "timer": ["gptimer", "esp_timer", "timer"],
    "delay": ["vtaskdelay", "esp_timer"],
    "serial": ["uart", "echo"],
    "uart": ["uart", "echo", "uart_write_bytes"],
    "i2c": ["i2c", "master"],
    "oled": ["i2c", "lcd", "ssd1306"],
    "display": ["lcd", "panel", "ssd1306"],
    "spi": ["spi", "master"],
    "touch": ["touch", "pad"],
    "sleep": ["deep", "sleep", "light"],
    "watchdog": ["wdt", "watchdog"],
    "rgb": ["led_strip", "rmt", "ws2812"],
    "neopixel": ["led_strip", "rmt", "ws2812"],
    "ws2812": ["led_strip", "rmt"],
    "storage": ["nvs", "spiffs", "fatfs"],
    "save": ["nvs"],
    "dac": ["dac"],
    "sound": ["i2s", "dac"],
    "task": ["xtaskcreate", "freertos"],
    "queue": ["queue", "xqueuecreate"],
    "ota": ["ota"],
    "time": ["sntp", "time"],
    "buzzer": ["ledc", "ledc_basic", "frequency"],
    "tone": ["ledc", "frequency"],
    "ultrasonic": ["hc_sr04", "capture"],
    "distance": ["hc_sr04", "capture"],
    "encoder": ["pcnt", "rotary", "encoder"],
    "relay": ["gpio", "generic"],
}


def tokenize(text: str) -> list[str]:
    """Lower-case words; snake_case names also add their parts."""
    out: list[str] = []
    for word in _WORD.findall(text):
        w = word.lower()
        if len(w) < 2 or w in STOPWORDS:
            continue
        out.append(w)
        if "_" in w:
            out.extend(p for p in w.split("_") if len(p) > 1 and p not in STOPWORDS)
    return out


# Everyday words that point to the wrong examples on their own
# ("analog" is in every DAC example). They are replaced by their synonyms.
REPLACE_WITH_SYNONYMS = frozenset({"analog", "potentiometer", "serial", "internet", "save", "buzzer", "tone"})


def expand_query(text: str) -> list[str]:
    tokens = tokenize(text)
    out: list[str] = []
    for t in tokens:
        if t not in REPLACE_WITH_SYNONYMS:
            out.append(t)
        out.extend(SYNONYMS.get(t, []))
    return out


@dataclass
class ExampleHit:
    path: str
    title: str
    score: float
    files: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "title": self.title, "score": round(self.score, 2)}


def _supports_esp32(readme: str) -> bool:
    for line in readme.splitlines():
        if line.strip().startswith("| Supported Targets"):
            cells = [c.strip() for c in line.split("|")]
            return "ESP32" in cells
    return True  # no table -> assume generic


def _title_and_summary(readme: str, fallback: str) -> str:
    title = fallback
    lines = [l.strip() for l in readme.splitlines()]
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    summary = []
    for line in lines:
        if line and not line.startswith(("#", "|", "!", "[", "<", "```", "-", "*")):
            summary.append(line)
        if len(" ".join(summary)) > 300:
            break
    return (title + ". " + " ".join(summary))[:500]


class ExampleIndex:
    """BM25 index over ESP-IDF example projects."""

    k1 = 1.4
    b = 0.75

    def __init__(self, idf_path: Path, docs: list[dict[str, Any]]):
        self.idf_path = Path(idf_path)
        self.docs = docs
        self.avg_len = sum(d["length"] for d in docs) / max(1, len(docs))
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d["tf"].keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    # ---------------------------------------------------------------- build
    @classmethod
    def build(cls, idf_path: Path) -> "ExampleIndex":
        examples = Path(idf_path) / "examples"
        docs: list[dict[str, Any]] = []
        for main_dir in sorted(examples.rglob("main")):
            if not main_dir.is_dir():
                continue
            project = main_dir.parent
            c_files = sorted(p for p in main_dir.glob("*.c"))
            if not c_files:
                continue
            readme_path = project / "README.md"
            readme = readme_path.read_text(encoding="utf-8", errors="replace") if readme_path.exists() else ""
            if not _supports_esp32(readme):
                continue
            rel = project.relative_to(Path(idf_path)).as_posix()
            if "/test" in rel or "test_apps" in rel:
                continue
            code = "\n".join(p.read_text(encoding="utf-8", errors="replace")[:20000] for p in c_files)
            summary = _title_and_summary(readme, project.name.replace("_", " "))
            path_words = " ".join(rel.split("/")[1:])
            # Title and path words count three times: they say what the example is about.
            tokens = tokenize(summary) * 3 + tokenize(path_words) * 5 + tokenize(code)
            tf = Counter(tokens)
            docs.append({
                "path": rel,
                "title": summary.split(". ")[0][:120],
                "files": [p.relative_to(Path(idf_path)).as_posix() for p in c_files],
                "length": len(tokens),
                "tf": dict(tf),
            })
        return cls(idf_path, docs)

    # --------------------------------------------------------------- search
    def search(self, query: str, k: int = 2) -> list[ExampleHit]:
        terms = Counter(expand_query(query))
        scores: list[tuple[float, int]] = []
        for i, d in enumerate(self.docs):
            tf = d["tf"]
            norm = self.k1 * (1 - self.b + self.b * d["length"] / self.avg_len)
            s = 0.0
            for term, qtf in terms.items():
                f = tf.get(term)
                if f:
                    s += self.idf.get(term, 0.0) * (f * (self.k1 + 1)) / (f + norm) * (1 + 0.3 * (qtf - 1))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [
            ExampleHit(self.docs[i]["path"], self.docs[i]["title"], s, self.docs[i]["files"])
            for s, i in scores[:k]
        ]

    def snippet(self, hit: ExampleHit, max_chars: int = 2400) -> str:
        """The example's main C file, without the license header, trimmed to ``max_chars``."""
        parts = []
        for rel in hit.files:
            text = (self.idf_path / rel).read_text(encoding="utf-8", errors="replace")
            text = _LICENSE.sub("", text, count=1)
            parts.append(f"// {rel}\n{text.strip()}")
        joined = "\n\n".join(parts)
        if len(joined) <= max_chars:
            return joined
        # Keep the start (includes, config) and the app_main part if possible.
        main_at = joined.find("void app_main")
        if main_at > max_chars // 2:
            head = joined[: max_chars // 2]
            tail = joined[main_at: main_at + max_chars // 2]
            return head.rstrip() + "\n\n// ... (shortened) ...\n\n" + tail.rstrip() + "\n// ... (shortened)"
        return joined[:max_chars].rstrip() + "\n// ... (shortened)"

    # ---------------------------------------------------------------- cache
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": INDEX_VERSION, "idf_path": str(self.idf_path), "docs": self.docs}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, idf_path: Path) -> "ExampleIndex | None":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != INDEX_VERSION or Path(data.get("idf_path", "")) != Path(idf_path):
            return None
        return cls(idf_path, data["docs"])


_cache: dict[str, ExampleIndex] = {}
_lock = threading.Lock()


def get_index(idf_path: Path, version: str) -> ExampleIndex:
    """Load the cached index for this ESP-IDF, or build it (first start only)."""
    key = str(idf_path)
    with _lock:
        if key in _cache:
            return _cache[key]
        cache_file = CACHE_DIR / f"examples_v{version}.json"
        index = ExampleIndex.load(cache_file, idf_path)
        if index is None:
            index = ExampleIndex.build(idf_path)
            index.save(cache_file)
        _cache[key] = index
        return index
