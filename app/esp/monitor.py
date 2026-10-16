"""Serial monitor: read the board's output live and send text to it.

One monitor can be open at a time (a serial port can only be used by one
program). Flashing closes it automatically.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover - pyserial is in requirements.txt
    serial = None


class SerialMonitor:
    def __init__(self, port: str, baud: int, on_line: Callable[[str], None], on_close: Callable[[str], None]):
        self.port = port
        self.baud = baud
        self.on_line = on_line
        self.on_close = on_close
        self._serial = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def open(self, reset: bool = True) -> None:
        if serial is None:
            raise RuntimeError("pyserial is not installed. Run Setup.bat again.")
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.1)
        except (serial.SerialException, OSError) as exc:
            raise RuntimeError(f"Could not open {self.port}: {exc}. Is another program (or a flash) using it?") from exc
        if reset:
            self.reset_board()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def reset_board(self) -> None:
        """Pulse EN through the USB adapter's RTS line, like the RESET button."""
        if not self._serial:
            return
        self._serial.dtr = False
        self._serial.rts = True
        time.sleep(0.1)
        self._serial.rts = False

    def write(self, text: str) -> None:
        if self._serial:
            self._serial.write(text.encode("utf-8"))

    def _run(self) -> None:
        buffer = b""
        reason = "Monitor closed."
        try:
            while not self._stop.is_set():
                data = self._serial.read(1024)
                if not data:
                    continue
                buffer += data
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    self.on_line(raw.decode("utf-8", errors="replace").rstrip("\r"))
        except (OSError, Exception) as exc:  # unplugged board, etc.
            reason = f"Connection lost: {exc}"
        finally:
            try:
                self._serial.close()
            except Exception:
                pass
            self.on_close(reason)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


_active: SerialMonitor | None = None
_lock = threading.Lock()


def set_active(monitor: SerialMonitor | None) -> None:
    global _active
    with _lock:
        if _active and _active is not monitor:
            _active.close()
        _active = monitor


def close_active() -> None:
    set_active(None)
