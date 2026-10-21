# User guide

Simple steps for using AI-Driven Embedded Programmer on Windows.

## 1. One-time setup

1. Make sure **ESP-IDF v5.5** is installed (on the team laptop it is, at `C:\Espressif`).
2. For the offline model, install **Ollama** from <https://ollama.com/download>.
3. Double-click **`Setup.bat`** in the project folder. It:
   - installs the Python packages the app needs (in a private `.venv` folder),
   - downloads the code editor,
   - installs the ESP32 simulator (QEMU),
   - asks whether to download the local AI model (about 4.7 GB),
   - builds ESP-IDF once (a few minutes) so later builds take seconds.
4. Get a free Groq key: sign in at <https://console.groq.com/keys> → **Create API key** → copy it.

## 2. Start the app

Double-click **`Start.bat`**. A black window opens (keep it open) and the app
opens in your browser at <http://127.0.0.1:8765>. Close the black window to stop.

The first time, open **Settings**, paste the Groq key, press **Test connection**, then **Save**.
Your key is stored only on this laptop (`data/settings.json`).

## 3. The screen (v2)

- **Left: sidebar** with your projects. **New project** (Ctrl+K) at the top, **Settings** and the theme button
  (same as Windows / light / dark) at the bottom. Test runs from the evaluation are grouped under *Evaluation runs*.
  The ◧ button hides the sidebar.
- **Middle: the chat.** Your messages are in grey bubbles; the AI's answers are plain text.
  What the app did for each answer (examples read, code written, build, fixes, pin check) is listed in small
  grey **work steps**; when the answer is done they fold into one line such as *"Wrote v3 · 1 fix · builds"* –
  click it to see the details.
- **Right: the code panel** (button **Code**, or Ctrl+\). It opens by itself while the AI writes code.
  Tabs: **Code** (editor) · **Changes** (what changed) · **Pins** (board drawing) · **Output** (build log and
  errors) · **Serial** (simulator / board output). The buttons at the bottom build, fix, simulate and flash.

## 4. Make firmware by chatting

1. Type what the ESP32 should do, e.g. *"Read a DHT22 on GPIO 4 every 2 seconds and print temperature and humidity"*, and press **Enter**. On a new project you can also click one of the examples.
2. The answer appears as it is written, and the code streams into the code panel. In the chat it shows as a small **main.c** card; click it to open the code.
3. The app checks the code, then **builds** it automatically:
   - **Made-up functions:** every called function is checked against the real ESP-IDF. If one does not exist, the app finds the real ones it probably meant ("did you mean `gpio_set_level`") and gives them to the AI.
   - **Pin check:** fixed rules check the pins (flash pins 6–11, input-only pins 34–39, ADC2 with Wi-Fi, boot pins, motors without a driver…). A real problem is sent to the AI to fix.
   - **Build errors** are sent back to the AI too. Up to 3 automatic fixes in total. Each fix is shown under the answer as *"Fixed automatically"*, so the explanation always matches the final code.
4. Keep chatting to change it: *"make it every 5 seconds"*, *"also blink an LED on GPIO 2 when it is above 30 °C"*.

Tips:
- Click **Board** (top right, or **+** in the message box) to list what is connected and on which pins (e.g. `DHT22 → DATA on GPIO 4`). The AI will use exactly these pins.
- The **Pins** tab shows the ESP32 DevKit with the pins your program uses (red = error, amber = warning), what each pin does and on which code line. Use it while wiring.
- Above the editor a line says whether every function exists in ESP-IDF.
- **Explain** (above the editor, or in the project menu) gives a simple explanation of the current code.

## 5. Your own edits, versions and undo

- You can type in the editor at any time. Press **Ctrl+S** (or **Save**) to keep your edit as a new version. Your edits are also saved automatically when you send a chat message or press Build, and the AI keeps them.
- Every change is a version (`v1, v2, …`). Click the version line under `main.c` to see all versions; pick one to compare it with the current code, then **Restore** brings it back (as a new version, so nothing is lost).
- **Changes** shows exactly what the last change did (red = removed, green = added). **Undo** goes back one version.

## 6. Run it

- **Simulate** — runs the firmware for 10 s in the ESP32 simulator and shows the output in the **Serial** tab. The simulator cannot press buttons or read sensors; it shows whether the program starts, prints and does not crash.
- **Flash** — plug in the ESP32 with a USB data cable, pick its port (press ↻ if it is missing), press **Flash**. If it cannot connect, hold the board's **BOOT** button while flashing starts.
- **Monitor** — shows what the board prints. You can also send text to it, or press **Reset board**.

## 7. Models

Click the model name in the message box (next to the send button):
- **GPT-OSS 120B** (Groq cloud) — best results, needs internet. Free tier: about 8,000 tokens per minute and a daily limit; if the per-minute limit is reached the app waits and continues.
- **GPT-OSS 20B / Qwen** (Groq cloud) — alternatives with their own daily limits (useful if the 120B limit is used up).
- **qwen2.5-coder:7b** (on this laptop) — runs on the laptop's GPU without internet, but makes more mistakes. Ollama must be running.

## 8. Projects

Click the project name at the top of the chat for: rename, Board & wiring, Explain, download as .zip, open in VS Code (to build/flash it with the ESP-IDF extension), open folder, clear the chat (the code and versions are kept), delete. In the sidebar, the **⋯** next to a project renames or deletes it.

## Troubleshooting

| Problem | Fix |
|---|---|
| "ESP-IDF not found" at the top | Settings → ESP-IDF location: enter `C:\Espressif\frameworks\esp-idf-v5.5.5`, `C:\Espressif`, and the Python path shown as an example |
| "No Groq API key" | Settings → paste the key → Test connection → Save |
| "Groq free-tier limit reached" | Wait; the app retries by itself. If it says *per day*, pick GPT-OSS 20B or the local model |
| "Ollama is not running" | Start the Ollama app from the Start menu |
| First build is slow | Normal: ESP-IDF compiles once (a few minutes). Later builds take seconds |
| Flash: "Could not talk to the ESP32" | Hold BOOT while flashing starts; try another USB cable (some are charge-only) |
| Flash: "port is busy" | Stop the Monitor (here or in another program such as Arduino IDE) |
| Editor does not appear | Run Setup.bat again (it downloads the editor) |
