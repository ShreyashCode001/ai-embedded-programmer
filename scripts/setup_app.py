"""One-time setup, run by Setup.bat (Windows) or setup.sh (Linux/macOS).

Steps (each can be re-run safely):
 1. Download the Monaco code editor (the editor inside VS Code) for offline use
 2. Find ESP-IDF and install its ESP32 simulator (QEMU) if missing
 3. Build the ESP-IDF example index and function list (first-start cache)
 4. Download the local AI model through Ollama (optional, about 4.7 GB)
 5. Pre-build the firmware workspace once, so the first real build is fast

Python packages are installed by Setup.bat before this script runs.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MONACO_VERSION = "0.52.2"
MONACO_URL = f"https://registry.npmjs.org/monaco-editor/-/monaco-editor-{MONACO_VERSION}.tgz"
LOCAL_MODEL = "qwen2.5-coder:7b"


def step(n: int, text: str) -> None:
    print(f"\n[{n}/5] {text}")


def ok(text: str) -> None:
    print(f"      OK  {text}")


def warn(text: str) -> None:
    print(f"      !!  {text}")


def monaco() -> None:
    step(1, "Code editor (Monaco)")
    target = ROOT / "web" / "vendor" / "monaco"
    if (target / "vs" / "loader.js").exists():
        ok("already downloaded")
        return
    try:
        print(f"      downloading {MONACO_URL} …")
        data = urllib.request.urlopen(MONACO_URL, timeout=120).read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name.startswith("package/min/vs/") and m.isfile()]
            target.mkdir(parents=True, exist_ok=True)
            for m in members:
                rel = Path(m.name).relative_to("package/min")
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(out, "wb") as dst:  # type: ignore[union-attr]
                    shutil.copyfileobj(src, dst)
        ok(f"{len(members)} files saved to web/vendor/monaco")
    except Exception as exc:
        warn(f"could not download the editor ({exc}). The app will load it from the internet instead.")


def esp_idf():
    step(2, "ESP-IDF and the ESP32 simulator")
    from app.config import ensure_dirs, load_settings
    from app.esp.idf import IdfNotFound, find_qemu, get_idf

    ensure_dirs()
    try:
        idf = get_idf(load_settings())
    except IdfNotFound as exc:
        warn(str(exc))
        warn("Install ESP-IDF v5.5 (https://dl.espressif.com/dl/esp-idf/) and run Setup again.")
        return None
    ok(f"ESP-IDF v{idf.version} at {idf.path} (found via {idf.found_via})")
    if find_qemu(idf):
        ok("simulator (QEMU) already installed")
    else:
        print("      installing the ESP32 simulator (QEMU), about 30 MB …")
        proc = subprocess.run([str(idf.python), str(idf.path / "tools" / "idf_tools.py"), "install", "qemu-xtensa"],
                              env=idf.env, text=True, capture_output=True)
        print("\n".join("      " + l for l in (proc.stdout + proc.stderr).strip().splitlines()[-4:]))
        from app.esp import idf as idf_mod
        idf_mod._env_cache.clear()
        idf = get_idf(load_settings())
        if find_qemu(idf):
            ok("simulator installed")
        else:
            warn("simulator could not be installed. Everything else still works.")
    return idf


def knowledge(idf) -> None:
    step(3, "ESP-IDF example search and function list")
    if idf is None:
        warn("skipped (ESP-IDF not found)")
        return
    from app.knowledge.api_index import get_api_index
    from app.knowledge.examples import get_index

    t = time.time()
    ex = get_index(idf.path, idf.version)
    api = get_api_index(idf.path, idf.tools_path, idf.version)
    ok(f"{len(ex.docs)} ESP32 examples indexed, {len(api.names)} real function names ({time.time() - t:.0f} s)")


def local_model(ask: bool) -> None:
    step(4, f"Local AI model ({LOCAL_MODEL}, about 4.7 GB, for offline use)")
    ollama = shutil.which("ollama")
    if not ollama:
        warn("Ollama is not installed. Get it from https://ollama.com/download if you want the offline model.")
        return
    listed = subprocess.run([ollama, "list"], capture_output=True, text=True)
    if LOCAL_MODEL.split(":")[0] in listed.stdout and LOCAL_MODEL.split(":")[1] in listed.stdout:
        ok("already downloaded")
        return
    if ask:
        answer = input("      Download it now? It takes a while on slow internet. [Y/n] ").strip().lower()
        if answer.startswith("n"):
            warn(f"skipped. Later: run  ollama pull {LOCAL_MODEL}")
            return
    print("      downloading … (progress below)")
    code = subprocess.call([ollama, "pull", LOCAL_MODEL])
    ok("downloaded") if code == 0 else warn(f"download failed. Later: run  ollama pull {LOCAL_MODEL}")


def prebuild(idf) -> None:
    step(5, "First firmware build (compiles ESP-IDF once, a few minutes)")
    if idf is None:
        warn("skipped (ESP-IDF not found)")
        return
    from app.esp import tools

    code = (ROOT / "firmware_template" / "main" / "main.c").read_text(encoding="utf-8")
    last = ""
    for kind, value in tools.build(idf, code):
        if kind == "log":
            last = value
            if "]" in value and "[" in value[:3]:
                print("\r      " + value[:90].ljust(90), end="", flush=True)
        else:
            print()
            ok(f"build works ({value.get('seconds')} s)") if value.get("ok") else warn(
                "the test build failed: " + (value.get("tail") or last)[-400:])


def main() -> int:
    ask = "--yes" not in sys.argv
    print("AI-Driven Embedded Programmer - setup")
    monaco()
    idf = esp_idf()
    knowledge(idf)
    if "--no-model" not in sys.argv:
        local_model(ask)
    prebuild(idf)
    print("\nSetup finished. Start the app with Start.bat (Windows) or ./start.sh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
