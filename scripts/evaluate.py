"""Measure the app on 20 typical requests with the model chosen in Settings.

    .venv\\Scripts\\python scripts\\evaluate.py            (Windows)
    .venv\\Scripts\\python scripts\\evaluate.py --only R01,R05
    .venv\\Scripts\\python scripts\\evaluate.py --provider ollama

For each request: one chat turn exactly like the app (examples, model, fake-
function check, real ESP-IDF build, auto-fix up to 3 times), then a short run
in the simulator if it built. Results go to data/eval/<time>.json and a
readable summary to data/eval/<time>.md. Each request runs in its own project
named "eval ...", which you can open in the app afterwards.
"""

from __future__ import annotations

import argparse
import json
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


def run_one(settings: dict, text: str, label: str) -> dict:
    project = projects.create(f"eval {label}")
    assistant = Assistant(settings)
    started = time.monotonic()
    first_unknown, first_build, builds, fixes, error, reply = None, None, [], 0, "", ""
    for ev in assistant.chat_turn(project, text, ""):
        t = ev["type"]
        if t == "version" and first_unknown is None and ev.get("api_check"):
            first_unknown = ev["api_check"]["unknown"]
        elif t == "build_result":
            builds.append(bool(ev.get("ok")))
            if first_build is None:
                first_build = bool(ev.get("ok"))
        elif t == "fix_start":
            fixes += 1
        elif t == "error":
            error = ev["text"]
        elif t == "message_done" and ev["message"].get("kind") == "chat":
            reply = ev["message"]["content"]
        elif t == "status" and "limit" in ev.get("text", ""):
            print("   ", ev["text"])
    seconds = round(time.monotonic() - started, 1)
    sim = None
    if builds and builds[-1] and tools.built_code() == project.code():
        idf = assistant.idf()
        if idf is not None:
            result = [v for k, v in tools.simulate(idf, 6) if k == "result"]
            sim = result[0] if result else None
    return {
        "project": project.id,
        "seconds": seconds,
        "code_produced": bool(project.code()),
        "first_build_ok": first_build,
        "final_build_ok": bool(builds and builds[-1]),
        "fix_attempts": fixes,
        "unknown_functions_first_try": first_unknown or [],
        "simulator": None if sim is None else ("crash" if sim.get("crashed") else "ran"),
        "error": error,
        "reply_start": reply[:400],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated ids, e.g. R01,R05")
    parser.add_argument("--provider", choices=["groq", "ollama"])
    parser.add_argument("--model")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between requests (free-tier limits)")
    args = parser.parse_args()

    settings = load_settings()
    settings["auto_build"] = True
    settings["auto_fix"] = True
    if args.provider:
        settings["provider"] = args.provider
    if args.model:
        settings["groq_model" if settings["provider"] == "groq" else "ollama_model"] = args.model
    model = settings["groq_model"] if settings["provider"] == "groq" else settings["ollama_model"]

    spec = json.loads((ROOT / "scripts" / "eval_requests.json").read_text(encoding="utf-8"))
    wanted = set(args.only.split(",")) if args.only else None
    items = [r for r in spec["requests"] + spec["safety"] if not wanted or r["id"] in wanted]

    print(f"Evaluating {len(items)} requests with {settings['provider']} / {model}\n")
    results = []
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['id']}: {item['text']}")
        r = run_one(settings, item["text"], f"{item['id']} {item['text'][:40]}")
        r.update({"id": item["id"], "text": item["text"]})
        if "must_mention" in item:
            low = r["reply_start"].lower() + " " + (projects.get(r["project"]).chat()[-1]["content"].lower()
                                                     if projects.get(r["project"]).chat() else "")
            r["safety_warning_given"] = any(word in low for word in item["must_mention"])
        status = "BUILD OK" if r["final_build_ok"] else ("NO CODE" if not r["code_produced"] else "BUILD FAILED")
        print(f"      {status} | first try: {r['first_build_ok']} | fixes: {r['fix_attempts']} | "
              f"fake functions: {len(r['unknown_functions_first_try'])} | sim: {r['simulator']} | {r['seconds']} s"
              + (f" | ERROR: {r['error']}" if r["error"] else ""))
        results.append(r)
        if i < len(items):
            time.sleep(args.pause)

    main_set = [r for r in results if r["id"].startswith("R")]
    n = len(main_set) or 1
    summary = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "provider": settings["provider"],
        "model": model,
        "requests": len(main_set),
        "build_ok_first_try": sum(1 for r in main_set if r["first_build_ok"]),
        "build_ok_after_fixes": sum(1 for r in main_set if r["final_build_ok"]),
        "avg_fix_attempts": round(sum(r["fix_attempts"] for r in main_set) / n, 2),
        "requests_with_fake_functions": sum(1 for r in main_set if r["unknown_functions_first_try"]),
        "simulator_crashes": sum(1 for r in main_set if r["simulator"] == "crash"),
        "avg_seconds": round(sum(r["seconds"] for r in main_set) / n, 1),
        "safety_warnings": {r["id"]: r.get("safety_warning_given") for r in results if r["id"].startswith("S")},
    }
    out_dir = DATA_DIR / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    lines = [
        f"# Evaluation {summary['date']}", "",
        f"Model: **{summary['provider']} / {summary['model']}**, ESP-IDF builds are real, simulator = QEMU.", "",
        "| Measure | Result |", "|---|---|",
        f"| Builds on the first try | {summary['build_ok_first_try']}/{summary['requests']} |",
        f"| Builds after auto-fix (max 3) | {summary['build_ok_after_fixes']}/{summary['requests']} |",
        f"| Average auto-fix attempts | {summary['avg_fix_attempts']} |",
        f"| Requests where the first code used made-up functions | {summary['requests_with_fake_functions']} |",
        f"| Crashed in the simulator | {summary['simulator_crashes']} |",
        f"| Average time per request | {summary['avg_seconds']} s |",
        f"| Safety warnings given | {summary['safety_warnings']} |", "",
        "| Id | Request | First try | Final | Fixes | Made-up functions | Simulator | Time |", "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['id']} | {r['text']} | {'✓' if r['first_build_ok'] else '✗'} | {'✓' if r['final_build_ok'] else '✗'} | "
                     f"{r['fix_attempts']} | {', '.join(r['unknown_functions_first_try']) or '-'} | {r['simulator'] or '-'} | {r['seconds']} s |")
    (out_dir / f"{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:12]))
    print(f"\nSaved: {out_dir / (stamp + '.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
