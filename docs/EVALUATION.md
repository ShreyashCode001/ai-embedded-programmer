# Evaluation results

Method: decision D-43 (20 typical requests + 2 safety requests, `scripts/eval_requests.json`).
Run with: `.venv\Scripts\python scripts\evaluate.py --provider groq` (or `--provider ollama`).

## Status

**Not run yet with a real model.** The results below will be filled in from
`data/eval/<date>.md` after the first run on the team laptop.

| Model | Builds first try | Builds after auto-fix | Avg fixes | Made-up functions (first try) | Sim crashes |
|---|---|---|---|---|---|
| Groq GPT-OSS 120B | – | – | – | – | – |
| Local Qwen2.5-Coder 7B | – | – | – | – | – |

## What was already verified (16 Oct 2026, without a real model)

- 17 automatic tests pass (scripted model, fake and real builds).
- Real ESP-IDF v5.5.5 build + QEMU run from a clean start: first build ~90 s, later builds ~8 s.
- Full flow in a real browser (scripted model): code with a made-up function → flagged → build fails → auto-fix → builds → follow-up change → builds → simulator shows the LED toggling every 200 ms.
