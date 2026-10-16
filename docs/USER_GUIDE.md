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

## 3. Make firmware by chatting

1. Type what the ESP32 should do, e.g. *"Read a DHT22 on GPIO 4 every 2 seconds and print temperature and humidity"*, and press **Enter**.
2. The reply appears as it is written. The complete code goes into the editor on the right.
3. The app then **builds** it automatically. If the build fails, the errors are sent back to the AI and it tries again (up to 3 times). The **Problems** tab explains each error in simple words; click one to jump to that line.
4. Keep chatting to change it: *"make it every 5 seconds"*, *"also blink an LED on GPIO 2 when it is above 30 °C"*.

Tips:
- Click **Board** to list what is connected and on which pins (e.g. `DHT22 → DATA on GPIO 4`). The AI will use exactly these pins.
- The badge next to `main.c` shows whether every function used exists in ESP-IDF (`✓ 12 functions checked`), or which ones are made up.
- **Explain** gives a simple explanation of the current code.

## 4. Your own edits, versions and undo

- You can type in the editor at any time. Press **Ctrl+S** (or **Save**) to keep your edit as a new version. Your edits are also saved automatically when you send a chat message or press Build, and the AI keeps them.
- Every change is a version (`v1, v2, …`). Pick one from the version list to compare it with the current code; **Restore** brings it back (as a new version, so nothing is lost).
- **Changes** shows exactly what the last AI change did (red = removed, green = added). **Undo** goes back one version.

## 5. Run it

- **Simulate** — runs the firmware for 10 s in the ESP32 simulator and shows the output in the **Serial** tab. The simulator cannot press buttons or read sensors; it shows whether the program starts, prints and does not crash.
- **Flash** — plug in the ESP32 with a USB data cable, pick its port (e.g. `COM5 · ESP32?`; press ↻ if it is missing), press **Flash**. If it cannot connect, hold the board's **BOOT** button while flashing starts.
- **Monitor** — shows what the board prints. You can also send text to it, or press **Reset board**.

## 6. Models

Use the **Model** menu at the top:
- **☁ GPT-OSS 120B** (Groq) — best results, needs internet. Free tier: about 1,000 requests per day and 8,000 tokens per minute; if the limit is reached the app waits a few seconds and continues.
- **💻 Local: qwen2.5-coder:7b** — runs on the laptop's GPU without internet. Faster to start, but makes more mistakes with less common ESP-IDF features. Ollama must be running.

## 7. Projects

The project menu (**⋯** next to the project name) can rename, delete, download as .zip, open the project in VS Code (to build/flash it with the ESP-IDF extension), or clear the chat (the code and versions are kept).

## Troubleshooting

| Problem | Fix |
|---|---|
| "ESP-IDF not found" at the top | Settings → ESP-IDF location: enter `C:\Espressif\frameworks\esp-idf-v5.5.5`, `C:\Espressif`, and the Python path shown as an example |
| "No Groq API key" | Settings → paste the key → Test connection → Save |
| "Groq free-tier limit reached" | Wait; the app retries by itself. Or switch to the local model |
| "Ollama is not running" | Start the Ollama app from the Start menu |
| First build is slow | Normal: ESP-IDF compiles once (a few minutes). Later builds take seconds |
| Flash: "Could not talk to the ESP32" | Hold BOOT while flashing starts; try another USB cable (some are charge-only) |
| Flash: "port is busy" | Stop the Monitor (here or in another program such as Arduino IDE) |
| Editor does not appear | Run Setup.bat again (it downloads the editor) |
