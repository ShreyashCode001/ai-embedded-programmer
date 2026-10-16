# Decision log – AI-Driven Embedded Programmer

Every important decision, with the reason. Rule for the team: a changed
decision gets a new entry; the old one is marked "Replaced by …", never
silently edited.

**Status:** Decided · Proposed · Open · Replaced

---

## What changed on 16 Oct 2026

The first plan (15 Oct, decisions D-03 to D-29) was a research pipeline: a
fixed benchmark, a rule-based task checker and automatic comparison runs.
The team decided that **version 1 must instead be a finished, interactive
product**: a model that writes ESP32 code which the user can chat with and
change. That earlier work was deleted and v1 was built from scratch in a new
repository. Decisions from the first plan that still apply are kept below
(D-01, D-02, D-04, D-08, D-24); all others are **Replaced by D-30** (some of
their ideas return in v2, see D-45).

---

## Kept from the first plan

### D-01 · We do not train our own model
**Status:** Decided (15 Oct)
We use existing open models as a part of the system. Training a code model
needs data and GPUs we do not have, and it is not where our contribution is.

### D-02 · Not "just RAG"
**Status:** Decided (15 Oct)
Looking up examples (RAG) is one small part. The system also checks the
code against the real ESP-IDF headers, builds it with the real toolchain,
feeds real errors back, keeps versions, and runs the result.

### D-04 · Python
**Status:** Decided (15 Oct)
One language for the server and all tools; the firmware itself is C (ESP-IDF).

### D-08 · ESP-IDF v5.5.5, frozen
**Status:** Decided (15 Oct)
Already installed on the laptop at `C:\Espressif\frameworks\esp-idf-v5.5.5`.
ESP-IDF changes its APIs between versions (ADC, I2C, timers changed in v5), so
one version is used for everything.

### D-24 · VS Code + ESP-IDF extension for hand-written work
**Status:** Decided (15 Oct)
The extension now finds ESP-IDF through `C:\Espressif\tools\eim_idf.json`
(created 15 Oct). The app can open any project in VS Code.

---

## Version 1 decisions (16 Oct 2026)

### D-30 · v1 = an interactive ESP32 code assistant
**Status:** Decided
**What:** chat → complete code → tweak by chatting or by hand → see changes →
versions/undo → build → auto-fix → simulate → flash → serial monitor.
**Why:** this is what the team wants to show and use; it is also the base
that later research features (D-45) plug into.
**Replaces:** D-03, D-05, D-06, D-09 – D-23, D-25 – D-29.

### D-31 · New repository, built from scratch
**Status:** Decided
Repository and folder name: `AI-Driven-Embedded-Programmer` (GitHub does not
allow spaces). Private until the paper is submitted. The older prototype in
`ai-embedded-programmer` is left untouched on the laptop.

### D-32 · Models: GPT-OSS 120B on Groq by default, Qwen2.5-Coder 7B locally
**Status:** Decided
| Role | Model | Why |
|---|---|---|
| Default | `openai/gpt-oss-120b` via Groq free tier | Open-weights, ~120 B parameters, strong at code; free tier ~1,000 requests/day (Oct 2026) |
| Offline | `qwen2.5-coder:7b` via Ollama | Best coding model that fits fully in the laptop's 6 GB RTX 3050 (about 4.4 GB at Q4); bigger ones (14B, Qwen3-Coder 30B) do not fit |
| Also offered | `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` (Groq) | Faster / alternative |
Temperature 0.2 (consistent code, a little flexibility for wording).
Gemini's free tier was not chosen: very low daily limits and free-tier prompts are used by Google.
**To re-check before final results:** model names and free limits change often.

### D-33 · Stay inside Groq's free-tier token limit
**Status:** Decided
Groq's free tier allows about 8,000 tokens per minute and counts the prompt
**plus** the requested reply length. The app estimates the prompt size and
asks for at most the rest; if a request is still too big it retries with fewer
examples and less history. On a rate-limit reply it waits the time Groq asks
for (up to 3 retries) and tells the user. GPT-OSS "thinking" uses medium
effort when there is room, low otherwise.

### D-34 · Give the model matching official ESP-IDF examples
**Status:** Decided
The installed ESP-IDF contains ~450 example projects (306 support the classic
ESP32). For each request the 2 closest are found with BM25 keyword search
(no extra AI model or download) plus a small list of everyday words → ESP-IDF
words ("potentiometer" → ADC oneshot, "servo" → MCPWM). Their code is added to
the prompt so the model copies real v5.5 API usage.

### D-35 · Check every called function against the real headers
**Status:** Decided
~30,000 function and macro names are read from the installed ESP-IDF headers
and the compiler's C library. A called name that is not there and not defined
in the file is "made up" and shown to the user; names from deprecated headers
(e.g. `adc1_get_raw`) are flagged as old APIs. Both go into the auto-fix prompt.

### D-36 · The model always returns the whole main.c
**Status:** Decided
Small models are far more reliable at rewriting a whole short file than at
producing patches. The app shows the user exactly what changed (Monaco diff
view), saves every change as a version and never overwrites anything.
The user's manual edits are saved as a version before the next AI request
and the model is told to keep them.

### D-37 · Real builds, auto-fix up to 3 times, one shared build folder
**Status:** Decided
Builds use ESP-IDF's own `idf.py build`; success = exit code 0 **and** the
firmware file exists (the old file is deleted first). Failed builds send the
compiler errors (with plain-English hints) back to the model, up to 3 times.
All builds share one ESP-IDF project folder, so only `main.c` changes:
measured ~90 s for the first build and **~8 s** for later ones (cloud test
machine). A moved or stale build folder is detected and rebuilt automatically.

### D-38 · ESP32 simulator: Espressif QEMU, output read from a file
**Status:** Decided
QEMU comes with ESP-IDF and runs the real firmware with accurate timers.
Its serial output is written to a file that the app reads live (writing to
the console did not work reliably). Limits, tested: no simulated sensors or
button presses, and analog (ADC) reads freeze the simulated CPU.

### D-39 · FastAPI server + browser app + Monaco editor
**Status:** Decided (replaces the Streamlit idea from the 16 Oct plan)
FastAPI streams the AI's reply, build logs and simulator output as they
happen (Server-Sent Events) and the serial monitor over a WebSocket. The
page is plain JavaScript (no build step) with Monaco, the editor inside VS
Code. Streamlit was dropped because it redraws the whole page on each action,
its editors are limited, and live serial output is awkward in it.

### D-40 · Runs only on the user's computer; data stays local
**Status:** Decided
The server listens on 127.0.0.1 only. Settings (including the Groq key),
projects, caches and the build folder live in `data/`, which is git-ignored.
The key is never shown back to the page in full and never logged.

### D-41 · One-click Setup.bat and Start.bat
**Status:** Decided
No commands to type: Setup installs packages, the editor, the simulator,
optionally the local model, and does the first ESP-IDF build. ESP-IDF is
found automatically (Settings → `eim_idf.json` → usual folders), so no special
ESP-IDF terminal is needed.

### D-42 · Pin-safety rules in the model's instructions (light hardware awareness)
**Status:** Decided
The system prompt states the ESP32 pin rules (flash pins 6–11, input-only
34–39, strapping pins, ADC2 + Wi-Fi, GPIO current limit, 5 V signals) and
asks the model to warn and write the safe version. The user can also list
the wiring (Board dialog) and the model must use those pins. A full
rule-based checker that blocks impossible requests is planned for v2 (D-45).

### D-43 · How v1 is measured
**Status:** Decided
20 typical requests (GPIO, PWM, ADC, DHT22, HC-SR04, servo, buzzer, WS2812,
LM35, Wi-Fi, web server, UART, I2C, OLED, deep sleep, timer, FreeRTOS queue,
NVS, touch) + 2 safety requests, written before any model ran on them
(`scripts/eval_requests.json`). Measured per model: builds first try, builds
after auto-fix, fix attempts, made-up functions, simulator crashes, time.
Target for GPT-OSS 120B: at least 18/20 build after auto-fix. Results are
reported as measured in `docs/EVALUATION.md`. "Builds" ≠ "works on hardware".

### D-44 · Only the classic ESP32 and single-file projects in v1
**Status:** Decided
Keeps prompts small (free-tier limit) and builds fast. Multi-file projects,
extra components and other chips come later.

### D-45 · Planned for v2 (research part)
**Status:** Proposed
- Rule-based hardware feasibility checker that rejects impossible requests
  before generation (current limits, pull-ups, ADC pins, voltages).
- Benchmark and comparison: plain LLM vs. our system vs. human code.
- Testing on the real board (serial output checks).
- More boards (ESP32-S3/C3) and multi-file projects.

---

## Open questions
- [ ] Run the 20-request evaluation with GPT-OSS 120B and with local Qwen (needs the Groq key) → `docs/EVALUATION.md`
- [ ] Buy an ESP32 DevKit + basic parts to test Flash / Monitor on real hardware
- [ ] Who has a Claude plan for Claude Code (D-07 from the first plan)
- [ ] Team members review the code and the prompts
