"""One chat turn, end to end, as a stream of events for the browser.

    user request
      -> save the user's own edits as a version (if the editor changed)
      -> find matching ESP-IDF examples
      -> ask the model (streamed, token by token)
      -> take the complete main.c out of the reply -> new version
      -> check every called function against the real ESP-IDF headers
      -> build with ESP-IDF  -> if it fails: send the errors back to the
         model and rebuild, up to N times (auto-fix)

Every event is a small dict with a "type", sent to the browser as it happens.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

from app.esp import tools
from app.esp.idf import Idf, IdfNotFound, get_idf
from app.knowledge import hardware
from app.knowledge.api_index import check_code, get_api_index, suggestions
from app.knowledge.examples import get_index
from app.llm.prompts import (EXPLAIN_PROMPT, SYSTEM_PROMPT, build_messages, build_plain_messages,
                              extract_code, fix_request)
from app.llm.providers import LLMError, RequestTooLarge, make_provider
from app.projects import Project

Event = dict[str, Any]


def _first_sentence(text: str, limit: int = 110) -> str:
    plain = re.sub(r"```.*?```", "", text or "", flags=re.DOTALL).strip()
    plain = re.sub(r"[*`#>]", "", plain)
    m = re.match(r"(.+?[.!?])(\s|$)", plain, re.DOTALL)
    s = (m.group(1) if m else plain).replace("\n", " ").strip()
    return s[:limit]


class Assistant:
    def __init__(self, settings: dict[str, Any], provider: Any = None, idf: Idf | None = None):
        self.settings = settings
        self.provider = provider or make_provider(settings)
        self._idf = idf
        self._idf_error = ""
        self._request = ""

    # ------------------------------------------------------------ helpers
    def idf(self) -> Idf | None:
        if self._idf is None and not self._idf_error:
            try:
                self._idf = get_idf(self.settings)
            except IdfNotFound as exc:
                self._idf_error = str(exc)
            except Exception as exc:  # broken install, etc.
                self._idf_error = f"ESP-IDF problem: {exc}"
        return self._idf

    def _examples(self, query: str, count: int) -> list[dict[str, str]]:
        idf = self.idf()
        if idf is None or count <= 0:
            return []
        try:
            index = get_index(idf.path, idf.version)
        except Exception:
            return []
        hits = index.search(query, k=count)
        size = 2400 if count <= 2 else 1600
        return [{"path": h.path, "title": h.title, "code": index.snippet(h, size)} for h in hits]

    def _api(self):
        idf = self.idf()
        if idf is None:
            return None
        try:
            return get_api_index(idf.path, idf.tools_path, idf.version)
        except Exception:
            return None

    def _suggest(self, unknown: list[str]) -> dict[str, list[dict[str, str]]]:
        idf, api = self.idf(), self._api()
        if not unknown or idf is None or api is None or not self.settings.get("function_hints", True):
            return {}
        try:
            return suggestions(unknown, api, idf.path)
        except Exception:
            return {}

    def check(self, code: str, request: str = "", reply: str = "") -> dict[str, Any]:
        """Everything the app can tell about a main.c without building it."""
        api = self._api()
        api_check = check_code(code, api) if (api and code.strip()) else None
        return {
            "api_check": api_check.as_dict() if api_check else None,
            "suggestions": self._suggest(api_check.unknown) if api_check else {},
            "hardware": hardware.check(code, request, reply).as_dict(),
        }

    def _stream_reply(self, messages_for: Any, kind: str) -> Iterator[Event]:
        """Call the model; on 'too large', retry with less context. Yields events; last is ('reply', text)."""
        wanted = int(self.settings.get("examples_in_prompt", 2))
        attempts = [dict(examples=min(wanted, n), history=h) for n, h in ((wanted, 6), (1, 4), (0, 2), (0, 0))]
        last_error: Exception | None = None
        for i, plan in enumerate(attempts):
            messages, examples = messages_for(plan["examples"], plan["history"])
            if i == 0 and examples:
                yield {"type": "context", "examples": [{"path": e["path"], "title": e["title"]} for e in examples]}
            if i > 0:
                yield {"type": "status", "text": "Request was too big for the free tier, retrying with less context…"}
            parts: list[str] = []
            started = time.monotonic()
            try:
                for kind_, value in self.provider.stream(messages):
                    if kind_ == "text":
                        parts.append(value)
                        yield {"type": "token", "text": value, "kind": kind}
                    elif kind_ == "status":
                        yield {"type": "status", "text": value}
                    elif kind_ == "usage":
                        yield {"type": "usage", **value}
            except RequestTooLarge as exc:
                if parts:
                    raise
                last_error = exc
                continue
            yield {"type": "reply", "text": "".join(parts), "seconds": round(time.monotonic() - started, 1)}
            return
        raise last_error or LLMError("The request could not be sent.")

    # ------------------------------------------------------------ chat turn
    def chat_turn(self, project: Project, request: str, editor_code: str | None) -> Iterator[Event]:
        request = (request or "").strip()
        if not request:
            yield {"type": "error", "text": "Type what you want the ESP32 to do."}
            return
        if editor_code is not None and editor_code.strip() and editor_code.strip() != project.code().strip():
            entry = project.add_version(editor_code, "user", "Your manual edits")
            if entry:
                yield {"type": "version", "version": entry, "code": editor_code}
        self._request = request
        user_msg = project.add_message("user", request)
        yield {"type": "user_saved", "message": user_msg}

        current = project.code()
        board = project.meta().get("board")
        history = project.chat()[:-1]
        query = request + " " + " ".join(m["content"] for m in history[-2:] if m["role"] == "user")

        def messages_for(n_examples: int, n_history: int):
            if self.settings.get("plain_prompt"):
                # Baseline for evaluation: the bare model, no rules, examples or board info.
                return build_plain_messages(request, history, current, max_history=n_history), []
            examples = self._examples(query, n_examples)
            return build_messages(request, history, board, examples, current, max_history=n_history), examples

        yield {"type": "status", "text": f"Asking {self.provider.name} · {self.provider.model}…"}
        reply, seconds = "", 0.0
        try:
            for event in self._stream_reply(messages_for, "chat"):
                if event["type"] == "reply":
                    reply, seconds = event["text"], event["seconds"]
                else:
                    yield event
        except LLMError as exc:
            yield {"type": "error", "text": str(exc)}
            return
        except GeneratorExit:
            raise
        yield from self._handle_reply(project, reply, seconds, kind="chat")

    def _handle_reply(self, project: Project, reply: str, seconds: float, kind: str) -> Iterator[Event]:
        code = extract_code(reply)
        msg = project.add_message("assistant", reply, kind=kind, model=f"{self.provider.name}:{self.provider.model}",
                                  seconds=seconds)
        yield {"type": "message_done", "message": msg}
        if not code:
            if "```" in reply:
                yield {"type": "status", "text": "The reply had code but not a complete main.c, so the editor was not changed."}
            return
        previous = project.code()
        entry = project.add_version(code, "ai" if kind == "chat" else "fix", _first_sentence(reply))
        if entry is None:
            yield {"type": "status", "text": "The AI returned the same code as before."}
            return
        project.update_message(msg["id"], version=entry["n"])
        info = self.check(code, self._request, reply)
        yield {"type": "version", "version": entry, "code": code, "previous": previous, "message_id": msg["id"],
               "api_check": info["api_check"], "suggestions": info["suggestions"], "hardware": info["hardware"]}
        if kind == "chat" and self.settings.get("auto_build", True):
            yield from self.build_and_fix(project, auto_fix=bool(self.settings.get("auto_fix", True)))

    # ------------------------------------------------------- build + fix
    def build_and_fix(self, project: Project, auto_fix: bool = True) -> Iterator[Event]:
        idf = self.idf()
        if idf is None:
            yield {"type": "build_result", "ok": False, "errors": [], "message": self._idf_error or "ESP-IDF not found."}
            return
        max_attempts = int(self.settings.get("max_fix_attempts", 3)) if auto_fix else 0
        attempt = 0
        while True:
            n = project.current_version()
            code = project.code()
            if not code.strip():
                yield {"type": "build_result", "ok": False, "errors": [], "message": "There is no code to build yet."}
                return
            yield {"type": "build_start", "version": n}
            result: dict[str, Any] = {}
            for kind, value in tools.build(idf, code):
                if kind == "log":
                    yield {"type": "build_log", "line": value}
                else:
                    result = value
            if result.get("busy"):
                yield {"type": "build_result", "version": n, **result}
                return
            project.set_build_status(n, bool(result.get("ok")))
            yield {"type": "build_result", "version": n, **result}
            hw_errors = []
            if result.get("ok") and self.settings.get("hardware_fix", True):
                hw_errors = hardware.check(code, self._request).errors
            if (result.get("ok") and not hw_errors) or attempt >= max_attempts:
                if not result.get("ok") and max_attempts:
                    yield {"type": "status", "text": f"Still failing after {attempt} automatic fix(es). Try describing the problem in the chat, or edit the code."}
                elif hw_errors and max_attempts:
                    yield {"type": "status", "text": "It builds, but the hardware check still finds a pin problem. See the Pins tab."}
                return
            attempt += 1
            api = self._api()
            check = check_code(code, api) if api else None
            unknown = check.unknown if check else []
            request = fix_request(result.get("errors", []), unknown, check.deprecated if check else [],
                                  result.get("tail", ""), suggestions=self._suggest(unknown),
                                  hardware=hardware.fix_text(hw_errors), build_failed=not result.get("ok"))
            count = len(result.get("errors", [])) + len(hw_errors)
            project.add_message("user", request, kind="fix", attempt=attempt)
            yield {"type": "fix_start", "attempt": attempt, "max": max_attempts, "errors": count,
                   "reason": "hardware" if result.get("ok") else "build"}
            board = project.meta().get("board")
            history = [m for m in project.chat()[:-1] if m.get("kind") == "chat"]

            def messages_for(n_examples: int, n_history: int, _req=request, _code=code):
                return build_messages(_req, history, board, [], _code, max_history=min(n_history, 2)), []

            try:
                reply, seconds = "", 0.0
                for event in self._stream_reply(messages_for, "fix"):
                    if event["type"] == "reply":
                        reply, seconds = event["text"], event["seconds"]
                    else:
                        yield event
            except LLMError as exc:
                yield {"type": "error", "text": f"Auto-fix stopped: {exc}"}
                return
            before = project.current_version()
            yield from self._handle_reply(project, reply, seconds, kind="fix")
            if project.current_version() == before:
                yield {"type": "status", "text": "The AI did not return a changed main.c, so auto-fix stopped."}
                return

    # ------------------------------------------------------------ explain
    def explain(self, project: Project, code: str) -> Iterator[Event]:
        code = code if (code or "").strip() else project.code()
        if not code.strip():
            yield {"type": "error", "text": "There is no code to explain yet."}
            return
        project.add_message("user", "Explain this code.", kind="explain")
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": EXPLAIN_PROMPT + "\n\n```c\n" + code + "\n```"}]
        parts: list[str] = []
        try:
            for kind, value in self.provider.stream(messages):
                if kind == "text":
                    parts.append(value)
                    yield {"type": "token", "text": value, "kind": "explain"}
                elif kind == "status":
                    yield {"type": "status", "text": value}
        except LLMError as exc:
            yield {"type": "error", "text": str(exc)}
            return
        msg = project.add_message("assistant", "".join(parts), kind="explain",
                                  model=f"{self.provider.name}:{self.provider.model}")
        yield {"type": "message_done", "message": msg}

    # ----------------------------------------------------------- simulate
    def simulate(self, project: Project) -> Iterator[Event]:
        idf = self.idf()
        if idf is None:
            yield {"type": "sim_result", "ok": False, "message": self._idf_error}
            return
        code = project.code()
        if tools.built_code() != code:
            yield from self.build_and_fix(project, auto_fix=False)
            if tools.built_code() != project.code():
                yield {"type": "sim_result", "ok": False, "message": "The code must build before it can be simulated."}
                return
        for kind, value in tools.simulate(idf, int(self.settings.get("sim_seconds", 10))):
            yield {"type": "sim_log", "line": value} if kind == "log" else {"type": "sim_result", **value}

    # -------------------------------------------------------------- flash
    def flash(self, project: Project, port: str) -> Iterator[Event]:
        from app.esp.monitor import close_active

        idf = self.idf()
        if idf is None:
            yield {"type": "flash_result", "ok": False, "message": self._idf_error}
            return
        close_active()  # the port can only be used by one program
        if tools.built_code() != project.code():
            yield from self.build_and_fix(project, auto_fix=False)
            if tools.built_code() != project.code():
                yield {"type": "flash_result", "ok": False, "message": "The code must build before it can be flashed."}
                return
        for kind, value in tools.flash(idf, port, int(self.settings.get("flash_baud", 460800))):
            yield {"type": "flash_log", "line": value} if kind == "log" else {"type": "flash_result", **value}
