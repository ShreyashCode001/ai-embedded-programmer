# UI design – v2 ("Claude style")

Version 2 of the app looks and behaves like the Claude chat app (claude.ai): calm,
warm, text-first, with the code shown in a side panel the way Claude shows
artifacts. This file records **what we observed** and **how we apply it**, so the
team can check or change the design in one place.

> We copy the *style* (layout, colours, spacing, motion, behaviour). We do **not**
> copy Anthropic's brand: no Claude name, logo or starburst icon, and not their
> paid fonts. The app keeps its own name and icon. Free (SIL OFL) fonts that look
> close are bundled in `web/fonts/` so the app also works offline.

---

## 1. What we observed on claude.ai (21 Oct 2026)

Read from the live site's stylesheet (its design tokens), plus the known layout of
the chat app.

| Token | Light | Dark |
|---|---|---|
| Page background (`bg-100`) | `#F9F9F7` | `#151515` |
| Surface / cards (`bg-000`) | `#FFFFFF` | `#20201F` |
| Sidebar, hover (`bg-200`) | `#F3F3F0` | `#111111` |
| Pressed, user bubble (`bg-300`) | `#F0EFEC` | `#0D0D0D`* |
| Strong fill (`bg-400`) | `#E7E6E1` | `#0B0B0B` |
| Main text (`text-000`) | `#131313` | `#F9F9F7` |
| Secondary text (`text-200`) | `#383835` | `#C3C2B7` |
| Muted text (`text-400`) | `#7B7974` | `#97958D` |
| Borders | main text colour at ~10–15 % opacity | light text at ~10–15 % opacity |
| Brand "clay" | `#D97757` (emphasised `#C6613F`) | same |
| Link / focus accent | `#256ABF` | `#5598E7` |
| Danger | `#D03B3B` | `#E66767` |
| Success | `#009300` | `#0CA30C` |
| Warning | `#A66A00` | `#C98500` |

\* In dark mode Claude lifts the user bubble *above* the page instead; we use `#2A2A28`.

Other tokens: radius base 4 px, cards 12–16 px, the message box ~20 px; shadows are
very soft (`0 4px 20px rgba(0,0,0,.04)`); ease-out curve `cubic-bezier(.215,.61,.355,1)`.

Fonts on claude.ai: *anthropic-sans* for the interface, *anthropic-serif* for
headings and Claude's replies, *anthropic-mono* for code.

Layout and behaviour:
1. **Sidebar** on the left (≈ 280 px): new-chat button with a round clay "+",
   a list of recent chats, account and settings at the bottom; collapses to a thin rail.
2. **Home screen:** large serif greeting with the brand mark, one big message box in
   the middle, suggestion chips under it.
3. **Conversation column** centred, about 720–760 px wide.
   - The user's message sits in a soft rounded bubble (bg-300), right side.
   - Claude's reply has **no bubble**: plain serif text on the page.
   - Work Claude does (searching, running tools, thinking) shows as quiet grey
     step lines with small icons, which fold into one summary line when finished.
   - Hover a reply → small icon buttons (copy, retry).
4. **Message box:** white card, ~20 px radius, hairline border, soft shadow; grows
   with the text; bottom row has "+" on the left, the **model picker** and a clay
   **send button** (arrow up) on the right; while answering it becomes a stop button.
5. **Artifacts:** code or documents open in a **panel on the right** (about half the
   width). In the chat they appear as a small card (icon tile, title, subtitle); the
   panel has a header with title, version picker, Preview/Code switch, copy, download,
   close. Code streams into the panel while it is being written.
6. **Motion:** short (150–300 ms), ease-out; new messages fade and rise a few
   pixels; the panel slides in; nothing bounces.
7. **Dialogs:** centred, rounded 16 px, dimmed background.
8. **Dark mode** follows the system, or can be chosen.

## 2. How we apply it

| Claude | AI-Driven Embedded Programmer |
|---|---|
| Fonts: anthropic-sans / serif / mono | **Inter** (UI), **Source Serif 4** (greeting, AI replies), **JetBrains Mono** (code, logs) |
| Brand mark (starburst) | Our own mark: a small chip with a spark, in clay |
| Recent chats | **Projects** (each project = one firmware with its chat and versions) |
| New chat | **New project** |
| Model picker in the message box | Same: GPT-OSS 120B, GPT-OSS 20B, Qwen 3 32B (Groq) or Qwen2.5-Coder 7B (local) |
| Artifact card | **main.c card**: "main.c · Version 3 · 54 lines · Builds ✓"; click opens the panel at that version |
| Artifact panel | **Code panel** with tabs: **Code · Changes · Pins · Output · Serial**, version picker, copy, download, close. An action bar at the bottom: Build, Fix with AI, Simulate, port, Flash, Monitor |
| Tool-use steps | **Work steps** in the reply: "Read 2 ESP-IDF examples", "Wrote main.c (v3)", "Checked 14 functions", "Built in 8 s", "Fixed 2 errors", "Pin check: 1 problem". Folded into a one-line summary when the turn is done |
| — | **Pins tab** (new in v2): a drawing of the ESP32 DevKit with the pins the program uses; red = error, amber = warning; list of problems with "Ask AI to fix" |
| — | **Did-you-mean card** (new in v2): when the AI used a function that does not exist, the real ones with their exact declarations |

## 3. Components (for whoever edits `web/`)

- `web/css/app.css` – all tokens are CSS variables at the top (`:root` and
  `[data-theme="dark"]`). Change colours there only.
- `web/js/app.js` – one file, no build step. Sections: state · API · theme ·
  sidebar · composer · conversation (turns, steps, cards) · code panel (Monaco,
  diff, pins, output, serial) · dialogs · start-up.
- `web/js/markdown.js` – safe Markdown (escapes first; code blocks, lists, tables,
  bold/italic, inline code, headings).
- `web/js/pins.js` – the ESP32 DevKitC pin layout and the pin-map drawing.

## 4. Rules we keep

- Text first, calm colours; clay is used sparingly (send button, brand, active items).
- One primary action per area; secondary actions are quiet icon buttons with tooltips.
- Every state has a message: empty, loading, streaming, success, error.
- Keyboard: Enter sends, Shift+Enter new line, Ctrl+S saves edits, Ctrl+B builds,
  Ctrl+K new project, Esc closes menus/dialogs, Ctrl+\ toggles the code panel.
- Works from 1024 px wide up; on narrow screens the code panel slides over the chat.
- Respects "reduce motion".
