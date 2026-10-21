"""Turn all evaluation result files (data/eval/*.jsonl) into one comparison report.

    .venv\\Scripts\\python scripts\\eval_report.py

Writes data/eval/REPORT.md and data/eval/summary.csv, and prints the table.
Only requests that were run in every compared setup of the same model are
counted in the "same requests" comparison, so the numbers are fair.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR  # noqa: E402

EVAL_DIR = DATA_DIR / "eval"
MODE_ORDER = ["plain", "no_examples", "no_fix", "full"]
MODE_LABEL = {
    "plain": "Plain model (baseline)",
    "no_examples": "System without ESP-IDF examples",
    "no_fix": "System without auto-fix",
    "full": "Full system",
}


def load() -> dict[tuple[str, str, str], list[dict]]:
    runs: dict[tuple[str, str, str], list[dict]] = {}
    for path in sorted(EVAL_DIR.glob("*.jsonl")):
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if rows:
            r = rows[0]
            runs[(r["provider"], r["model"], r["mode"])] = rows
    return runs


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def metrics(rows: list[dict]) -> dict[str, str]:
    main = [r for r in rows if r["id"].startswith("R")]
    safety = [r for r in rows if r["id"].startswith("S")]
    n = len(main)
    built = [r for r in main if r["final_build_ok"]]
    return {
        "Requests": str(n),
        "Code produced": pct(sum(r["code_produced"] for r in main), n),
        "Builds on first try": pct(sum(bool(r["first_build_ok"]) for r in main), n),
        "Builds in the end": pct(len(built), n),
        "Avg auto-fix attempts": f"{sum(r['fix_attempts'] for r in main) / n:.2f}" if n else "-",
        "Used made-up functions (first code)": pct(sum(bool(r["unknown_first"]) for r in main), n),
        "Made-up function names (first code, total)": str(sum(len(r["unknown_first"]) for r in main)),
        "Used deprecated APIs (final code)": pct(sum(bool(r["deprecated_final"]) for r in main), n),
        "Ran in simulator without crash": pct(sum(r["simulator"] == "ran" for r in built), len(built)),
        "Safety warnings given": pct(sum(bool(r["safety_warning_given"]) for r in safety), len(safety)),
        "Avg time per request (s)": f"{sum(r['seconds'] for r in main) / n:.0f}" if n else "-",
        "Avg model calls per request": f"{sum(r['llm_calls'] for r in main) / n:.1f}" if n else "-",
        "Avg tokens per request": f"{sum(r['prompt_tokens'] + r['completion_tokens'] for r in main) / n:.0f}" if n else "-",
    }


def fill_missing_simulations() -> None:
    """Run the simulator for built results that have no simulator result yet
    (e.g. runs made while the simulator step was broken). Rebuilds each saved
    project's final code first. Uses no model calls."""
    from app import projects
    from app.config import load_settings
    from app.esp import tools
    from app.esp.idf import IdfNotFound, get_idf

    try:
        idf = get_idf(load_settings())
    except IdfNotFound:
        return
    for path in sorted(EVAL_DIR.glob("*.jsonl")):
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        todo = [r for r in rows if r.get("final_build_ok") and r.get("simulator") in (None, "not_run")]
        if not todo:
            continue
        print(f"Simulating {len(todo)} result(s) from {path.name} …", flush=True)
        for r in todo:
            try:
                code = projects.get(r["project"]).code()
            except Exception:
                continue
            ok = [v for k, v in tools.build(idf, code) if k == "result"]
            if not ok or not ok[0].get("ok"):
                continue
            lines, result = 0, {}
            for kind, value in tools.simulate(idf, 6):
                if kind == "log":
                    lines += 1
                else:
                    result = value
            r["simulator"] = "crash" if result.get("crashed") else ("ran" if result.get("ok") else "not_run")
            r["sim_lines"] = lines
            print(f"  {r['id']} {r['mode']}: {r['simulator']}", flush=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main() -> int:
    if "--no-sim" not in sys.argv:
        fill_missing_simulations()
    runs = load()
    if not runs:
        print("No results yet in", EVAL_DIR)
        return 1
    by_model: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(dict)
    for (provider, model, mode), rows in runs.items():
        by_model[(provider, model)][mode] = rows

    lines = [f"# Evaluation report", "", f"Generated {datetime.now():%Y-%m-%d %H:%M}. "
             "Builds use the real ESP-IDF v5.5.5; the simulator is Espressif QEMU (no sensors simulated). "
             "'Builds' means it compiles; it does not prove the program works on hardware.", ""]
    csv_rows = []
    for (provider, model), modes in by_model.items():
        ordered = [m for m in MODE_ORDER if m in modes]
        # Fair comparison: only requests present in every mode of this model.
        common = set.intersection(*[{r["id"] for r in modes[m]} for m in ordered])
        table = {m: metrics([r for r in modes[m] if r["id"] in common]) for m in ordered}
        lines += [f"## {provider} / {model}", "",
                  f"Same {len([c for c in common if c.startswith('R')])} requests in every setup.", "",
                  "| Measure | " + " | ".join(MODE_LABEL[m] for m in ordered) + " |",
                  "|---|" + "---|" * len(ordered)]
        for key in next(iter(table.values())):
            lines.append(f"| {key} | " + " | ".join(table[m][key] for m in ordered) + " |")
            csv_rows.append({"provider": provider, "model": model, "measure": key,
                             **{m: table[m][key] for m in ordered}})
        lines += ["", "Per request (✓ builds in the end, ✗ does not; number = auto-fix attempts):", "",
                  "| Id | Request | " + " | ".join(MODE_LABEL[m] for m in ordered) + " |",
                  "|---|---|" + "---|" * len(ordered)]
        index = {m: {r["id"]: r for r in modes[m]} for m in ordered}
        for rid in sorted(common):
            any_row = index[ordered[0]][rid]
            cells = []
            for m in ordered:
                r = index[m][rid]
                if rid.startswith("S"):
                    cells.append("warned ✓" if r["safety_warning_given"] else "no warning ✗")
                else:
                    mark = "✓" if r["final_build_ok"] else "✗"
                    extra = f" {r['fix_attempts']}" if r["fix_attempts"] else ""
                    fake = " ⚠made-up" if r["unknown_first"] else ""
                    cells.append(mark + extra + fake)
            lines.append(f"| {rid} | {any_row['text'][:70]} | " + " | ".join(cells) + " |")
        lines.append("")
    report = "\n".join(lines) + "\n"
    (EVAL_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    if csv_rows:
        keys = ["provider", "model", "measure"] + MODE_ORDER
        with open(EVAL_DIR / "summary.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(csv_rows)
    print(report)
    print("Saved:", EVAL_DIR / "REPORT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
