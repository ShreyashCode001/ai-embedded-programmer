"""Automatic tests. No internet, no real AI model and no ESP-IDF needed:
the model is scripted and the ESP-IDF build is replaced by a fake. One extra
test runs a real build when RUN_IDF_TESTS=1 is set (and ESP-IDF is installed)."""

from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test gets its own empty data folder."""
    from app import config, projects

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config.ensure_dirs()
    return tmp_path


GOOD = """#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
static const char *TAG = "x";
void app_main(void) { gpio_set_level(2, 1); vTaskDelay(pdMS_TO_TICKS(10)); }
"""
BAD = GOOD.replace("gpio_set_level", "gpio_write")


# ----------------------------------------------------------------- settings
def test_settings_defaults_and_key_masking():
    from app.config import load_settings, public_settings, save_settings

    s = load_settings()
    assert s["provider"] == "groq" and s["groq_model"] == "openai/gpt-oss-120b"
    save_settings({"groq_api_key": "gsk_secret_1234", "max_fix_attempts": "2", "unknown": 1})
    pub = public_settings()
    assert pub["groq_api_key"].endswith("1234") and "secret" not in pub["groq_api_key"]
    assert pub["groq_api_key_set"] is True
    assert load_settings()["max_fix_attempts"] == 2
    # A masked or empty key from the browser keeps the stored key.
    save_settings({"groq_api_key": pub["groq_api_key"]})
    save_settings({"groq_api_key": ""})
    assert load_settings()["groq_api_key"] == "gsk_secret_1234"


# ------------------------------------------------------------------ prompts
def test_extract_code_takes_the_full_file():
    from app.llm.prompts import extract_code

    assert extract_code("Done.\n```c\n" + GOOD + "```\nWiring: x") == GOOD
    assert extract_code("```cpp\n" + GOOD + "```") == GOOD
    assert extract_code("Just an answer, no code.") is None
    assert extract_code("```c\nint x = 1;\n```") is None  # not a full program
    assert extract_code("Here:\n```c\n" + GOOD) == GOOD     # reply cut before closing fence


def test_messages_contain_rules_board_examples_and_current_code():
    from app.llm.prompts import build_messages

    history = [{"role": "user", "content": "blink", "kind": "chat"},
               {"role": "assistant", "content": "ok\n```c\n" + GOOD + "```", "kind": "chat"},
               {"role": "assistant", "content": "EXPLAIN-REPLY-XYZ", "kind": "explain"}]
    board = {"name": "ESP32 DevKitC", "parts": [{"part": "DHT22", "pins": "DATA -> GPIO 4", "notes": ""}]}
    msgs = build_messages("faster", history, board, [{"path": "examples/get-started/blink", "title": "Blink", "code": "x"}], GOOD)
    text = "\n".join(m["content"] for m in msgs)
    assert "Never Arduino" in msgs[0]["content"]
    assert "DHT22: DATA -> GPIO 4" in text
    assert "examples/get-started/blink" in text
    assert "CURRENT main.c" in text and "gpio_set_level" in text
    assert "EXPLAIN-REPLY-XYZ" not in text      # explain replies are not chat history
    assert msgs[-1] == {"role": "user", "content": "faster"}
    assert "[full main.c from this step" in msgs[-2]["content"]  # old code shortened


def test_fix_request_lists_errors_and_fake_functions():
    from app.llm.prompts import fix_request

    text = fix_request([{"line": 5, "message": "implicit declaration of function 'gpio_write'"}], ["gpio_write"], ["adc1_get_raw"])
    assert "line 5" in text and "gpio_write" in text and "adc1_get_raw" in text


# ---------------------------------------------------------------- providers
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.getvalue().splitlines(keepends=True))


def sse_body(*texts: str) -> bytes:
    lines = [f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n' for t in texts]
    lines.append('data: {"choices":[],"x_groq":{"usage":{"prompt_tokens":10,"completion_tokens":5}}}\n\n')
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def test_groq_streams_text_and_sends_budgeted_request():
    from app.llm.providers import GroqProvider

    sent = {}

    def opener(req, timeout):
        sent["body"] = json.loads(req.data)
        sent["auth"] = req.headers["Authorization"]
        return FakeResponse(sse_body("Hel", "lo"))

    p = GroqProvider(api_key="k", opener=opener)
    events = list(p.stream([{"role": "user", "content": "hi"}]))
    assert "".join(v for k, v in events if k == "text") == "Hello"
    assert ("usage", {"prompt_tokens": 10, "completion_tokens": 5}) in events
    assert sent["auth"] == "Bearer k"
    assert sent["body"]["include_reasoning"] is False
    assert sent["body"]["max_completion_tokens"] <= 6000


def test_groq_waits_on_rate_limit_then_succeeds():
    from app.llm.providers import GroqProvider

    calls = {"n": 0}
    waits = []

    def opener(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"retry-after": "3"}, io.BytesIO(b"{}"))
        return FakeResponse(sse_body("ok"))

    events = list(GroqProvider(api_key="k", opener=opener, sleep=waits.append).stream([{"role": "user", "content": "x"}]))
    assert waits == [3.0]
    assert any(k == "status" and "waiting" in v for k, v in events)
    assert ("text", "ok") in events


def test_groq_errors_are_explained():
    from app.llm.providers import GroqProvider, LLMError, RequestTooLarge

    def bad_key(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"error":{"message":"Invalid API Key"}}'))

    with pytest.raises(LLMError, match="rejected the API key"):
        list(GroqProvider(api_key="k", opener=bad_key).stream([{"role": "user", "content": "x"}]))
    with pytest.raises(LLMError, match="No Groq API key"):
        list(GroqProvider(api_key="").stream([{"role": "user", "content": "x"}]))
    huge = [{"role": "user", "content": "x" * 40000}]
    with pytest.raises(RequestTooLarge):
        list(GroqProvider(api_key="k", opener=lambda r, t: None).stream(huge))


def test_ollama_reports_missing_model():
    from app.llm.providers import LLMError, OllamaProvider

    def opener(req, timeout):
        return FakeResponse(json.dumps({"models": [{"name": "llama3:8b"}]}).encode())

    with pytest.raises(LLMError, match="not downloaded"):
        list(OllamaProvider(opener=opener).stream([{"role": "user", "content": "x"}]))


# -------------------------------------------------------- knowledge (fake IDF)
def make_fake_idf(root: Path) -> Path:
    idf = root / "idf"
    for name, readme, code in [
        ("get-started/blink", "| Supported Targets | ESP32 | ESP32-C3 |\n# Blink Example\nBlinks an LED.", "void app_main(void){ gpio_set_level(2,1); }"),
        ("peripherals/adc/oneshot_read", "| Supported Targets | ESP32 |\n# ADC Oneshot Read\nReads the ADC.", "void app_main(void){ adc_oneshot_read(h, ch, &v); }"),
        ("peripherals/usb/host", "| Supported Targets | ESP32-S3 |\n# USB Host\nUSB.", "void app_main(void){}"),
    ]:
        (idf / "examples" / name / "main").mkdir(parents=True)
        (idf / "examples" / name / "README.md").write_text(readme)
        (idf / "examples" / name / "main" / "main.c").write_text("/* license */\n#include <stdio.h>\n" + code)
    inc = idf / "components" / "esp_driver_gpio" / "include" / "driver"
    inc.mkdir(parents=True)
    (inc / "gpio.h").write_text("esp_err_t gpio_set_level(gpio_num_t gpio_num, uint32_t level);\n#define GPIO_IS_VALID(x) (x)\n")
    dep = idf / "components" / "driver" / "deprecated" / "driver"
    dep.mkdir(parents=True)
    (dep / "adc.h").write_text("int adc1_get_raw(adc1_channel_t channel) __attribute__((deprecated));\n")
    return idf


def test_example_search_finds_relevant_esp32_examples(tmp_path):
    from app.knowledge.examples import ExampleIndex

    index = ExampleIndex.build(make_fake_idf(tmp_path))
    assert [d["path"] for d in index.docs] == ["examples/get-started/blink", "examples/peripherals/adc/oneshot_read"]
    assert index.search("blink an LED")[0].path == "examples/get-started/blink"
    assert index.search("read a potentiometer")[0].path == "examples/peripherals/adc/oneshot_read"
    snippet = index.snippet(index.search("blink")[0])
    assert "license" not in snippet and "gpio_set_level" in snippet


def test_fake_function_check(tmp_path):
    from app.knowledge.api_index import ApiIndex, check_code

    index = ApiIndex.build(make_fake_idf(tmp_path), None)
    code = ('static int helper(int x){ return x; }\n'
            'void app_main(void){ gpio_set_level(2, helper(1)); gpio_write(2,1); adc1_get_raw(0); /* fake(1) */ printf_like("a(b)"); }')
    result = check_code(code, index)
    assert result.unknown == ["gpio_write", "printf_like"]
    assert result.deprecated == ["adc1_get_raw"]


# ------------------------------------------------------------------- build
def test_parse_errors_with_plain_hints():
    from app.esp.tools import parse_errors, parse_size

    out = ("/x/main/main.c:7:5: error: implicit declaration of function 'gpio_write' [-Wimplicit-function-declaration]\n"
           "/x/main/main.c:7:5: error: implicit declaration of function 'gpio_write' [-Wimplicit-function-declaration]\n"
           "/x/main/main.c:9:1: warning: unused variable 'x'\n"
           "/x/main/main.c:3:10: fatal error: Arduino.h: No such file or directory\n")
    errors = parse_errors(out)
    assert [e["line"] for e in errors] == [7, 3]
    assert "does not exist" in errors[0]["hint"] and "Arduino" in errors[1]["hint"]
    assert parse_size("app.bin binary size 0x23c40 bytes.") == "143 KB"


# ---------------------------------------------------------------- projects
def test_projects_versions_never_overwrite():
    from app import projects

    p = projects.create("Weather station")
    assert p.add_version(GOOD, "ai", "first")["n"] == 1
    assert p.add_version(GOOD, "user") is None           # unchanged code: no new version
    assert p.add_version(BAD, "user", "edit")["n"] == 2
    assert p.code(1) == GOOD and p.code() == BAD
    assert (p.firmware_dir() / "main" / "main.c").read_text() == BAD
    p.update_meta(board={"name": "ESP32", "parts": [{"part": "DHT22", "pins": "GPIO 4"}, {"part": " "}]})
    assert p.meta()["board"]["parts"] == [{"part": "DHT22", "pins": "GPIO 4", "notes": ""}]
    import zipfile, io as _io
    names = zipfile.ZipFile(_io.BytesIO(p.export_zip())).namelist()
    assert "Weather_station/main/main.c" in names and "Weather_station/CHAT.md" in names
    with pytest.raises(projects.ProjectNotFound):
        projects.get("../etc")


# --------------------------------------------------- assistant (fake build)
def fake_build(results):
    def build(idf, code, timeout=0):
        ok = "gpio_write" not in code
        results.append(ok)
        yield ("log", "building")
        yield ("result", {"ok": ok, "exit_code": 0 if ok else 2, "seconds": 0.1, "size": "1 KB" if ok else "",
                          "errors": [] if ok else [{"file": "main.c", "line": 5, "column": 1,
                                                    "message": "implicit declaration of function 'gpio_write'", "hint": ""}],
                          "tail": ""})
    return build


class FakeIdf:
    path = Path("/nonexistent")
    tools_path = Path("/nonexistent")
    version = "5.5.5"


def test_chat_turn_generates_builds_and_auto_fixes(monkeypatch):
    from app import projects
    from app.assistant import Assistant
    from app.esp import tools
    from app.llm.providers import ScriptedProvider

    builds = []
    monkeypatch.setattr(tools, "build", fake_build(builds))
    provider = ScriptedProvider(["Blinking.\n```c\n" + BAD + "```", "Fixed.\n```c\n" + GOOD + "```"])
    a = Assistant({"auto_build": True, "auto_fix": True, "max_fix_attempts": 3, "examples_in_prompt": 0},
                  provider=provider, idf=FakeIdf())
    p = projects.create("t")
    events = list(a.chat_turn(p, "blink the led", ""))
    types = [e["type"] for e in events]
    assert types.count("version") == 2 and types.count("build_result") == 2 and "fix_start" in types
    assert builds == [False, True]
    assert [v["source"] for v in p.versions()] == ["ai", "fix"]
    assert [v.get("build") for v in p.versions()] == ["failed", "ok"]
    assert "implicit declaration of function 'gpio_write'" in provider.calls[1][-1]["content"]


def test_user_edits_are_saved_before_the_ai_changes_code(monkeypatch):
    from app import projects
    from app.assistant import Assistant
    from app.llm.providers import ScriptedProvider

    p = projects.create("t")
    p.add_version(GOOD, "ai")
    edited = GOOD.replace("10", "250")
    provider = ScriptedProvider(["Sure, just a question answer."])
    a = Assistant({"auto_build": False}, provider=provider, idf=FakeIdf())
    list(a.chat_turn(p, "what does vTaskDelay do?", edited))
    assert [v["source"] for v in p.versions()] == ["ai", "user"]
    assert "250" in provider.calls[0][1]["content"]   # the model saw the user's edit


def test_model_errors_reach_the_user():
    from app import projects
    from app.assistant import Assistant
    from app.llm.providers import GroqProvider

    a = Assistant({"auto_build": False}, provider=GroqProvider(api_key=""), idf=FakeIdf())
    events = list(a.chat_turn(projects.create("t"), "blink", ""))
    assert events[-1]["type"] == "error" and "Groq API key" in events[-1]["text"]


# --------------------------------------------------------------- web API
def test_api_end_to_end_with_scripted_model(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.assistant import Assistant
    from app.esp import tools
    from app.llm.providers import ScriptedProvider

    monkeypatch.setattr(tools, "build", fake_build([]))
    monkeypatch.setattr(main, "assistant_factory",
                        lambda s: Assistant({**s, "examples_in_prompt": 0}, provider=ScriptedProvider(["Done.\n```c\n" + GOOD + "```"]), idf=FakeIdf()))
    c = TestClient(main.app)
    assert c.get("/").status_code == 200
    pid = c.post("/api/projects", json={"name": "API test"}).json()["id"]
    with c.stream("POST", f"/api/projects/{pid}/chat", json={"message": "blink", "code": ""}) as r:
        events = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "build_result" and e["ok"] for e in events)
    full = c.get(f"/api/projects/{pid}").json()
    assert full["code"] == GOOD and len(full["chat"]) == 2
    assert c.post(f"/api/projects/{pid}/code", json={"code": BAD}).json()["version"]["n"] == 2
    assert c.post(f"/api/projects/{pid}/restore/1").json()["version"]["n"] == 3
    assert c.get(f"/api/projects/{pid}/versions/9").status_code == 404
    s = c.put("/api/settings", json={"groq_api_key": "gsk_abcd9999"}).json()["settings"]
    assert s["groq_api_key_set"] and "abcd" not in s["groq_api_key"]
    assert c.get(f"/api/projects/{pid}/export").headers["content-type"] == "application/zip"
    assert c.delete(f"/api/projects/{pid}").json()["ok"]


# ------------------------------------------------------- real ESP-IDF (opt-in)
@pytest.mark.skipif(os.environ.get("RUN_IDF_TESTS") != "1", reason="set RUN_IDF_TESTS=1 for a real ESP-IDF build")
def test_real_build_and_simulation(monkeypatch):
    from app.config import load_settings
    from app.esp import tools
    from app.esp.idf import get_idf

    settings = load_settings()
    for key in ("idf_path", "idf_tools_path", "idf_python"):
        settings[key] = os.environ.get(key.upper(), settings[key])
    idf = get_idf(settings)
    code = GOOD.replace("void app_main(void) {", 'void app_main(void) { printf("hello-test\\n");').replace(
        '#include "freertos/FreeRTOS.h"', '#include <stdio.h>\n#include "freertos/FreeRTOS.h"')
    result = [v for k, v in tools.build(idf, code) if k == "result"][0]
    assert result["ok"], result
    logs = [v for k, v in tools.simulate(idf, 4) if k == "log"]
    assert any("hello-test" in line for line in logs)
