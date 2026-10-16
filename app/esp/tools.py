"""Build, simulate and flash ESP32 firmware, streaming output line by line.

Every long job is a generator of events:
    ("log", "a line of output")
    ("result", {...})            always the last event

Building happens in one shared ESP-IDF project (data/workspace/fw). Only
main/main.c changes between builds, so after the first build (a few minutes)
rebuilds take seconds. A build only counts as successful if idf.py exited with
0 AND the firmware .bin file exists.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from app.config import FIRMWARE_TEMPLATE, WORKSPACE_DIR
from app.esp.idf import NO_WINDOW, Idf, find_qemu

APP_NAME = "app"
Event = tuple[str, Any]

build_lock = threading.Lock()

_ERROR = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?\.(?:c|h|cpp)):(?P<line>\d+):(?P<col>\d+):\s*(?P<kind>fatal error|error|warning):\s*(?P<msg>.+)$",
    re.MULTILINE,
)
_UNDEFINED = re.compile(r"undefined reference to `([^']+)'")


def workspace() -> Path:
    fw = WORKSPACE_DIR / "fw"
    if not (fw / "CMakeLists.txt").exists():
        fw.mkdir(parents=True, exist_ok=True)
        shutil.copytree(FIRMWARE_TEMPLATE, fw, dirs_exist_ok=True)
    return fw


def run_streaming(cmd: list[str], env: dict[str, str], cwd: Path | None = None,
                  timeout: float = 1200) -> Iterator[Event]:
    """Run a fixed command and yield its output lines; last event is ("exit", code)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
    )
    started = time.monotonic()
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield ("log", line.rstrip("\r\n"))
            if time.monotonic() - started > timeout:
                proc.kill()
                yield ("log", f"Stopped: took longer than {timeout:.0f} s.")
                break
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    yield ("exit", proc.returncode)


# --------------------------------------------------------------------- errors

def plain_hint(message: str) -> str:
    """Explain a common compiler message in simple words."""
    m = message.lower()
    if "implicit declaration of function" in m:
        name = re.search(r"function '([^']+)'", message)
        n = name.group(1) if name else "this function"
        return f"'{n}' is unknown here: it does not exist in ESP-IDF v5.5, or its #include line is missing."
    if "no such file or directory" in m:
        return "This #include file does not exist in ESP-IDF v5.5 (often an Arduino or old-version header)."
    if "undeclared" in m:
        return "A name is used that was never defined: a typo, a missing #include, or an old constant name."
    if "expected" in m and ("';'" in m or "before" in m):
        return "Syntax problem: usually a missing ; or bracket on this line or the line above."
    if "undefined reference" in m:
        return "The function is declared but its code is not linked in: wrong name, or a component is missing."
    if "incompatible" in m or "makes pointer from integer" in m:
        return "A value of the wrong type is passed (for example a number where a pointer is expected)."
    if "has no member named" in m:
        return "This struct field does not exist in ESP-IDF v5.5 (fields often change between versions)."
    if "too few arguments" in m or "too many arguments" in m:
        return "The function is called with the wrong number of arguments for ESP-IDF v5.5."
    return ""


def parse_errors(output: str) -> list[dict[str, Any]]:
    found, seen = [], set()
    for m in _ERROR.finditer(output):
        if m.group("kind") == "warning":
            continue
        key = (Path(m.group("file")).name, m.group("line"), m.group("msg"))
        if key in seen:
            continue
        seen.add(key)
        msg = m.group("msg").strip()
        found.append({"file": Path(m.group("file")).name, "line": int(m.group("line")),
                      "column": int(m.group("col")), "message": msg, "hint": plain_hint(msg)})
    for name in _UNDEFINED.findall(output):
        if ("link", name) not in seen:
            seen.add(("link", name))
            msg = f"undefined reference to '{name}'"
            found.append({"file": "linker", "line": 0, "column": 0, "message": msg, "hint": plain_hint(msg)})
    return found


def parse_size(output: str) -> str:
    """Firmware size in KB, from ESP-IDF's 'app.bin binary size 0x23c40 bytes' line."""
    m = re.search(r"binary size (0x[0-9a-fA-F]+) bytes", output)
    if m and "bootloader" not in output[max(0, m.start() - 40):m.start()].lower():
        return f"{int(m.group(1), 16) / 1024:.0f} KB"
    for m in re.finditer(r"(\S+\.bin) binary size (0x[0-9a-fA-F]+) bytes", output):
        if "bootloader" not in m.group(1):
            return f"{int(m.group(2), 16) / 1024:.0f} KB"
    return ""


# ---------------------------------------------------------------------- build

def build(idf: Idf, code: str, timeout: float = 1500) -> Iterator[Event]:
    """Write ``code`` as main.c and run idf.py build."""
    if not build_lock.acquire(blocking=False):
        yield ("result", {"ok": False, "busy": True, "errors": [], "message": "Another build is already running."})
        return
    try:
        fw = workspace()
        (fw / "main" / "main.c").write_text(code, encoding="utf-8")
        binary = fw / "build" / f"{APP_NAME}.bin"
        if binary.exists():
            binary.unlink()  # a failed build must never leave an old firmware behind
        first = not (fw / "build" / "CMakeCache.txt").exists()
        if first:
            yield ("log", "First build: ESP-IDF compiles all its libraries once. This takes a few minutes; later builds take seconds.")
        started = time.monotonic()
        for attempt in (1, 2):
            lines: list[str] = []
            code_exit = None
            for kind, value in run_streaming(idf.idf_py() + ["-C", str(fw), "build"], idf.env, fw, timeout):
                if kind == "log":
                    lines.append(value)
                    yield ("log", value)
                else:
                    code_exit = value
            output = "\n".join(lines)
            if code_exit != 0 and attempt == 1 and _stale_build_dir(output):
                # The app folder was moved or ESP-IDF changed: start the build folder fresh.
                yield ("log", "The build folder belongs to an older location or ESP-IDF install. Starting a clean build…")
                shutil.rmtree(fw / "build", ignore_errors=True)
                continue
            break
        ok = code_exit == 0 and binary.exists()
        yield ("result", {
            "ok": ok,
            "exit_code": code_exit,
            "seconds": round(time.monotonic() - started, 1),
            "errors": [] if ok else parse_errors(output),
            "size": parse_size(output) if ok else "",
            "tail": "" if ok else "\n".join(lines[-40:]),
        })
    finally:
        build_lock.release()


def _stale_build_dir(output: str) -> bool:
    o = output.lower()
    return any(marker in o for marker in (
        "does not match the source", "is different than the directory", "cmakecache.txt directory",
        "was created with a different", "please remove the cmakecache", "idf_path environment variable is different",
        "target 'esp32' does not match", "run 'idf.py fullclean'", "configured for project",
    ))


def built_code() -> str | None:
    """The main.c currently built in the workspace, if the last build succeeded."""
    fw = workspace()
    if (fw / "build" / f"{APP_NAME}.bin").exists():
        return (fw / "main" / "main.c").read_text(encoding="utf-8")
    return None


# ------------------------------------------------------------------- simulate

PANIC_MARKERS = ("Guru Meditation Error", "abort() was called", "ESP_ERROR_CHECK failed",
                 "Stack smashing", "assert failed", "stack overflow")


def simulate(idf: Idf, seconds: int = 10) -> Iterator[Event]:
    """Run the last built firmware in QEMU and stream its serial output."""
    qemu = find_qemu(idf)
    if qemu is None:
        yield ("result", {"ok": False, "message": "The ESP32 simulator (QEMU) is not installed. Run Setup.bat again."})
        return
    fw = workspace()
    build_dir = fw / "build"
    if not (build_dir / f"{APP_NAME}.bin").exists():
        yield ("result", {"ok": False, "message": "Build the code first (no firmware to run)."})
        return
    flash = build_dir / "qemu_flash.bin"
    merge = subprocess.run(
        [str(idf.python), "-m", "esptool", "--chip", "esp32", "merge-bin", "--fill-flash-size", "4MB",
         "-o", str(flash), "@flash_args"],
        cwd=build_dir, env=idf.env, capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW,
    )
    if merge.returncode != 0:
        yield ("result", {"ok": False, "message": "Could not prepare the simulator image: " + (merge.stderr or merge.stdout)[-400:]})
        return
    yield ("log", f"--- ESP32 simulator (QEMU) running for {seconds} s. Sensors and buttons are not simulated. ---")
    serial_log = build_dir / "qemu_serial.log"
    if serial_log.exists():
        serial_log.unlink()
    cmd = [str(qemu), "-machine", "esp32", "-nographic", "-monitor", "none",
           "-serial", f"file:{serial_log.name}", "-nic", "none",
           "-drive", f"file={flash.name},if=mtd,format=raw"]
    proc = subprocess.Popen(cmd, cwd=build_dir, env=idf.env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    stop_at = time.monotonic() + seconds
    panic = ""
    position = 0
    pending = ""
    try:
        while True:
            finished = time.monotonic() > stop_at or proc.poll() is not None
            if serial_log.exists():
                with serial_log.open("rb") as fh:
                    fh.seek(position)
                    data = fh.read()
                    position += len(data)
                pending += data.decode("utf-8", errors="replace")
                *lines, pending = pending.split("\n")
                for raw in lines:
                    line = re.sub(r"\x1b\[[0-9;]*m", "", raw.rstrip("\r"))
                    yield ("log", line)
                    if not panic and any(p in line for p in PANIC_MARKERS):
                        panic = line.strip()
            if finished:
                break
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    if pending.strip():
        yield ("log", pending)
    yield ("result", {"ok": not panic, "crashed": bool(panic), "panic": panic,
                      "message": f"Firmware crashed: {panic}" if panic else f"Ran for {seconds} s without crashing."})


# ---------------------------------------------------------------------- flash

def list_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    ports = []
    for p in lp.comports():
        desc = p.description or ""
        likely = any(k in (desc + (p.manufacturer or "")).lower()
                     for k in ("cp210", "ch340", "ch910", "usb serial", "uart", "ftdi", "silicon labs", "esp"))
        ports.append({"device": p.device, "description": desc, "likely_esp32": likely})
    ports.sort(key=lambda p: not p["likely_esp32"])
    return ports


def flash(idf: Idf, port: str, baud: int = 460800, timeout: float = 600) -> Iterator[Event]:
    if not re.fullmatch(r"(COM\d{1,3}|/dev/[\w./-]+)", port or ""):
        yield ("result", {"ok": False, "message": "Choose the board's serial port first."})
        return
    if not build_lock.acquire(blocking=False):
        yield ("result", {"ok": False, "message": "A build is running. Try again when it finishes."})
        return
    try:
        fw = workspace()
        if not (fw / "build" / f"{APP_NAME}.bin").exists():
            yield ("result", {"ok": False, "message": "Build the code first."})
            return
        code_exit = None
        tail: list[str] = []
        for kind, value in run_streaming(idf.idf_py() + ["-C", str(fw), "-p", port, "-b", str(baud), "flash"],
                                         idf.env, fw, timeout):
            if kind == "log":
                tail = (tail + [value])[-30:]
                yield ("log", value)
            else:
                code_exit = value
        ok = code_exit == 0
        message = "Flashed successfully." if ok else _flash_hint("\n".join(tail))
        yield ("result", {"ok": ok, "exit_code": code_exit, "message": message})
    finally:
        build_lock.release()


def _flash_hint(tail: str) -> str:
    t = tail.lower()
    if "failed to connect" in t or "wrong boot mode" in t:
        return "Could not talk to the ESP32. Hold the BOOT button while flashing starts, or try a different USB cable."
    if "could not open port" in t or "permission" in t or "access is denied" in t:
        return "The port is busy. Close the serial monitor (here or in another app) and try again."
    return "Flashing failed. See the log for details."
