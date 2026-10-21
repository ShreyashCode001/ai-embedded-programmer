"""Evaluate the app on fixed requests, in several setups ("modes"), with any model.

Modes (what is switched on):
    full         our complete system: ESP-IDF rules + pin safety + matching official
                 examples + made-up-function check + build + auto-fix (max 3)
    no_examples  full system, but no ESP-IDF examples in the prompt      (ablation)
    no_fix       full system, but no auto-fix after a failed build       (ablation)
    plain        the bare model: a one-line instruction, no rules, no examples,
                 no auto-fix (baseline, "general LLM code")

Every mode builds the result with the real ESP-IDF and runs it in the simulator.

Examples (Windows):
    .venv\\Scripts\\python scripts\\evaluate.py --provider groq --mode full
    .venv\\Scripts\\python scripts\\evaluate.py --provider ollama --mode all
    .venv\\Scripts\\python scripts\\evaluate.py --provider groq --mode plain --only R01,R05

Results: data/eval/<provider>__<model>__<mode>.jsonl (one line per request).
Re-running the same command skips requests already done (resume), so a run
stopped by Groq's daily limit can simply be started again the next day.
Use --fresh to start a mode again from zero.
Progress is also written to data/eval/progress.log.
Make the comparison table with:  python scripts/eval_report.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import projects  # noqa: E402
from app.assistant import Assistant  # noqa: E402
from app.config import DATA_DIR, load_settings  # noqa: E402
from app.esp import tools  # noqa: E402
from app.knowledge.api_index import check_code  # noqa: E402

MODES = {
    "full": {"examples_in_prompt": 2, "auto_fix": True, "plain_prompt": False},
    "no_examples": {"examples_in_prompt": 0, "auto_fix": True, "plain_prompt": False},
    "no_fix": {"examples_in_prompt": 2, "auto_fix": False, "plain_prompt": False},
    "plain": {"examples_in_prompt": 0, "auto_fix": False, "plain_prompt": True},
}
EVAL_DIR = DATA_DIR / "eval"
NETWORK = re.compile(r"Lost the connection|did not answer in time|Could not reach", re.IGNORECASE)
DAILY_LIMIT = re.compile(r"per day|tokens per day|TPD|requests per day|RPD|daily", re.IGNORECASE)


def log(text: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}"
    print(line, flush=True)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVAL_DIR / "progress.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def keep_awake(on: bool) -> None:
    """Stop Windows from sleeping while the evaluation runs (like a video player does)."""
    if sys.platform.startswith("win"):
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")


def run_one(settings: dict, item: dict, mode: str) -> dict:
    project = projects.create(f"eval {mode} {item['id']} {item['text'][:30]}")
    assistant = Assistant(settings)
    started = time.monotonic()
    rec = {
        "id": item["id"], "text": item["text"], "mode": mode,
        "provider": settings["provider"],
        "model": settings["groq_model"] if settings["provider"] == "groq" else settings["ollama_model"],
        "project": project.id, "code_produced": False, "first_build_ok": None, "final_build_ok": False,
        "fix_attempts": 0, "first_errors": 0, "unknown_first": [], "unknown_final": [],
        "deprecated_first": [], "deprecated_final": [], "examples_used": [],
        "llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "error": "",
        "simulator": None, "sim_lines": 0, "safety_warning_given": None, "reply_start": "",
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    builds = []
    for ev in assistant.chat_turn(project, item["text"], ""):
        t = ev["type"]
        if t == "context":
            rec["examples_used"] = [e["path"] for e in ev["examples"]]
        elif t == "usage":
            rec["prompt_tokens"] += ev.get("prompt_tokens") or 0
            rec["completion_tokens"] += ev.get("completion_tokens") or 0
        elif t == "message_done":
            rec["llm_calls"] += 1
            if ev["message"].get("kind") == "chat":
                rec["reply_start"] = ev["message"]["content"][:600]
        elif t == "version" and ev.get("api_check") and not builds:
            rec["unknown_first"] = ev["api_check"]["unknown"]
            rec["deprecated_first"] = ev["api_check"]["deprecated"]
        elif t == "build_result":
            builds.append(bool(ev.get("ok")))
            if len(builds) == 1:
                rec["first_errors"] = len(ev.get("errors") or [])
        elif t == "fix_start":
            rec["fix_attempts"] += 1
        elif t == "error":
            rec["error"] = ev["text"]
        elif t == "status" and ("limit" in ev.get("text", "") or "waiting" in ev.get("text", "")):
            log(f"      {ev['text']}")
    rec["seconds"] = round(time.monotonic() - started, 1)
    code = project.code()
    rec["code_produced"] = bool(code.strip())
    rec["first_build_ok"] = builds[0] if builds else None
    rec["final_build_ok"] = bool(builds and builds[-1])
    api = assistant._api()
    if code.strip() and api is not None:
        final = check_code(code, api)
        rec["unknown_final"], rec["deprecated_final"] = final.unknown, final.deprecated
    if rec["final_build_ok"] and tools.built_code() == code:
        idf = assistant.idf()
        if idf is not None:
            lines, result = 0, {}
            for kind, value in tools.simulate(idf, 6):
                if kind == "log":
                    lines += 1
                else:
                    result = value
            rec["simulator"] = "crash" if result.get("crashed") else ("ran" if result.get("ok") else "not_run")
            rec["sim_lines"] = lines
    if "must_mention" in item:
        text = " ".join(m["content"] for m in project.chat() if m["role"] == "assistant").lower()
        rec["safety_warning_given"] = any(w in text for w in item["must_mention"])
    return rec


def run_mode(base: dict, mode: str, items: list[dict], fresh: bool, pause: float) -> str:
    settings = dict(base)
    settings.update(MODES[mode])
    settings["auto_build"] = True
    model = settings["groq_model"] if settings["provider"] == "groq" else settings["ollama_model"]
    out = EVAL_DIR / f"{settings['provider']}__{safe_name(model)}__{mode}.jsonl"
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if fresh and out.exists():
        out.unlink()
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                pass
    todo = [i for i in items if i["id"] not in done]
    log(f"=== {settings['provider']} / {model} / mode={mode}: {len(todo)} to do, {len(done)} already done ===")
    for n, item in enumerate(todo, 1):
        log(f"[{n}/{len(todo)}] {item['id']}: {item['text']}")
        try:
            rec = run_one(settings, item, mode)
        except Exception as exc:  # never let one request stop the whole evaluation
            log(f"      SKIPPED: unexpected problem ({type(exc).__name__}: {str(exc)[:160]}). It will be retried on the next run.")
            continue
        if rec["error"] and DAILY_LIMIT.search(rec["error"]):
            log(f"      STOPPED: daily model limit reached ({rec['error'][:160]}). Run the same command again tomorrow to continue.")
            projects.delete(rec["project"])
            return "daily_limit"
        if rec["error"] and not rec["code_produced"] and ("Could not reach" in rec["error"] or "not running" in rec["error"]
                                                         or "API key" in rec["error"]):
            log(f"      STOPPED: {rec['error']}")
            projects.delete(rec["project"])
            return "error"
        if rec["error"] and NETWORK.search(rec["error"]):
            # A dropped connection says nothing about the model, so it is not recorded.
            log(f"      SKIPPED: network problem ({rec['error'][:160]}). It will be retried on the next run.")
            projects.delete(rec["project"])
            time.sleep(pause)
            continue
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        status = "BUILD OK" if rec["final_build_ok"] else ("NO CODE" if not rec["code_produced"] else "BUILD FAILED")
        log(f"      {status} | first try {rec['first_build_ok']} | fixes {rec['fix_attempts']} | made-up {rec['unknown_first']}"
            f" | sim {rec['simulator']} | {rec['seconds']} s | tokens {rec['prompt_tokens']}+{rec['completion_tokens']}"
            + (f" | safety warning: {rec['safety_warning_given']}" if rec["safety_warning_given"] is not None else "")
            + (f" | ERROR {rec['error'][:120]}" if rec["error"] else ""))
        if n < len(todo):
            time.sleep(pause)
    log(f"=== finished mode={mode} ===")
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["groq", "ollama"])
    parser.add_argument("--model")
    parser.add_argument("--mode", default="full", help="full, no_examples, no_fix, plain, or all (comma list allowed)")
    parser.add_argument("--only", help="comma-separated request ids, e.g. R01,R05,S01")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between requests")
    parser.add_argument("--fresh", action="store_true", help="start the mode(s) again from zero")
    args = parser.parse_args()

    base = load_settings()
    if args.provider:
        base["provider"] = args.provider
    if args.model:
        base["groq_model" if base["provider"] == "groq" else "ollama_model"] = args.model

    spec = json.loads((ROOT / "scripts" / "eval_requests.json").read_text(encoding="utf-8"))
    wanted = set(args.only.split(",")) if args.only else None
    items = [r for r in spec["requests"] + spec["safety"] if not wanted or r["id"] in wanted]
    modes = list(MODES) if args.mode == "all" else [m.strip() for m in args.mode.split(",")]
    for m in modes:
        if m not in MODES:
            parser.error(f"unknown mode {m!r}")

    keep_awake(True)
    try:
        for mode in modes:
            result = run_mode(base, mode, items, args.fresh, args.pause)
            if result != "ok":
                return 3 if result == "daily_limit" else 2
    finally:
        keep_awake(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
