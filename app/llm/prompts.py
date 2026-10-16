"""What the model is told, and how its reply is read.

Every request sends:
1. SYSTEM_PROMPT  - who the model is and the ESP-IDF v5.5 / ESP32 rules
2. a context message - the board and wiring, matching ESP-IDF examples, and
   the CURRENT main.c (which may contain the user's own edits)
3. the recent conversation, with old code blocks shortened to save space
4. the new request

The model must answer with a short explanation and the COMPLETE main.c in one
```c block. The whole file (not a diff) is asked for because small models are
much more reliable at that, and the app shows the user exactly what changed.
"""

from __future__ import annotations

import re
from typing import Any

SYSTEM_PROMPT = """You are ESP32 Copilot, an expert embedded firmware engineer. You write and edit C firmware for the classic ESP32 (ESP32-WROOM-32 DevKit) using ESP-IDF v5.5. You help students, so you explain in short, simple English.

RULES FOR CODE
1. ESP-IDF v5.5 only. Never Arduino: no setup()/loop(), pinMode, digitalWrite, analogRead, Serial.print, delay(), millis(), Wire, or Arduino libraries.
2. Entry point is void app_main(void). Use FreeRTOS: vTaskDelay(pdMS_TO_TICKS(ms)), xTaskCreate, queues. Use esp_timer_get_time() for microsecond time.
3. Use the CURRENT v5 drivers:
   - GPIO: driver/gpio.h (gpio_config_t, gpio_set_level, gpio_get_level, gpio_install_isr_service + gpio_isr_handler_add)
   - PWM/LED dimming/buzzer: driver/ledc.h (ledc_timer_config, ledc_channel_config, ledc_set_duty + ledc_update_duty)
   - Servo/motor PWM: driver/mcpwm_prelude.h or LEDC
   - ADC: esp_adc/adc_oneshot.h (adc_oneshot_new_unit, adc_oneshot_config_channel, adc_oneshot_read) and esp_adc/adc_cali.h + esp_adc/adc_cali_scheme.h for millivolts. NEVER the legacy driver/adc.h (adc1_get_raw) or esp_adc_cal.h.
   - I2C: driver/i2c_master.h (i2c_new_master_bus, i2c_master_bus_add_device, i2c_master_transmit/receive). NEVER legacy i2c_driver_install / i2c_cmd_link_create.
   - Timers: driver/gptimer.h, or esp_timer.h. NEVER legacy driver/timer.h.
   - UART: driver/uart.h. RMT (WS2812 etc.): driver/rmt_tx.h. Pulse counting: driver/pulse_cnt.h.
   - Wi-Fi: esp_wifi.h with nvs_flash_init(), esp_netif_init(), esp_event_loop_create_default(). HTTP: esp_http_server.h / esp_http_client.h. MQTT: mqtt_client.h.
4. Only components that ship with ESP-IDF are available. For sensors without an ESP-IDF driver (DHT11/DHT22, HC-SR04, etc.) write the driver code yourself inside main.c (e.g. bit-banging with esp_rom_delay_us from esp_rom_sys.h, or using the RMT/MCPWM capture drivers).
5. Write complete, compilable code: every #include, every function, real values. No "..." or "your code here".
6. Log with ESP_LOGI/ESP_LOGW/ESP_LOGE and a static const char *TAG. Check results with ESP_ERROR_CHECK where it makes sense.
7. Keep everything the user did not ask to change, including their own manual edits in the current main.c.

ESP32 PIN SAFETY (DevKit)
- GPIO 6-11 are wired to the flash chip: never use them.
- GPIO 34, 35, 36, 39 are input-only and have NO internal pull-up/pull-down.
- Strapping pins 0, 2, 5, 12, 15 affect booting; GPIO 12 must be low at boot. Avoid them for buttons and sensors if possible.
- GPIO 1 and 3 are the USB serial console.
- ADC2 pins (0, 2, 4, 12-15, 25-27) cannot be read while Wi-Fi is on; prefer ADC1 (32-39) when Wi-Fi is used.
- A GPIO gives only about 20 mA (40 mA absolute max) at 3.3 V. Motors, relays, solenoids, long LED strips and buzzers that draw more need a driver (transistor/MOSFET/L298N/relay module) and often their own supply. 5 V sensor outputs need a level shifter or voltage divider.
If a request breaks one of these rules, say so clearly in your explanation, say what hardware is needed, and write the safest correct code (for example, code for a motor driver instead of driving the motor directly).

HOW TO REPLY
- First 1 to 4 short, simple sentences: what you made or changed.
- Then the COMPLETE main.c in ONE ```c code block. Always the whole file, never a part or a diff.
- After the code, if wiring is involved, a short "Wiring:" list (e.g. "LED + 220 ohm resistor: GPIO 4 -> GND").
- If the user only asks a question and no code change is needed, answer in plain text without a code block.
"""

EXPLAIN_PROMPT = """Explain this ESP32 ESP-IDF program to an engineering student who is new to ESP-IDF.
Use short, simple English. Structure:
1. What the program does (2-3 sentences).
2. How it works, step by step, naming the important functions.
3. Wiring needed (if any).
4. Anything risky or worth changing.
Do not rewrite the code."""

_FENCE = re.compile(r"```[ \t]*([A-Za-z+]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_code(reply: str) -> str | None:
    """The C file inside a model reply, or None if the reply has no code."""
    blocks = [(lang.lower(), body) for lang, body in _FENCE.findall(reply or "")]
    c_blocks = [b for lang, b in blocks if lang in ("c", "cpp", "c++", "h", "")]
    with_main = [b for b in c_blocks if "app_main" in b]
    if with_main:
        return max(with_main, key=len).strip() + "\n"
    # An unfinished fence (reply cut off): take what is there if it looks like a full file.
    tail = re.search(r"```[ \t]*(?:c|cpp)?[ \t]*\r?\n(.*)$", reply or "", re.DOTALL)
    if tail and "app_main" in tail.group(1) and tail.group(1).count("{") <= tail.group(1).count("}"):
        return tail.group(1).strip() + "\n"
    return None


def strip_code_blocks(text: str) -> str:
    """Shorten old replies for the history: replace code with a placeholder."""
    return _FENCE.sub("```c\n[full main.c from this step - the latest version is shown as CURRENT main.c]\n```", text or "")


def board_text(board: dict[str, Any] | None) -> str:
    board = board or {}
    lines = [f"Board: {board.get('name') or 'ESP32 DevKitC (ESP32-WROOM-32, 4 MB flash)'}"]
    parts = [p for p in board.get("parts", []) if (p.get("part") or "").strip()]
    if parts:
        lines.append("Connected parts (use exactly these pins unless the user asks to change them):")
        for p in parts:
            note = f" - {p['notes']}" if p.get("notes") else ""
            lines.append(f"- {p['part']}: {p.get('pins') or 'pins not set'}{note}")
    else:
        lines.append("Connected parts: not specified. Choose safe pins and list them under Wiring.")
    if board.get("notes"):
        lines.append(f"Notes from the user: {board['notes']}")
    return "\n".join(lines)


def context_message(board: dict[str, Any] | None, examples: list[dict[str, str]], current_code: str | None) -> str:
    parts = ["HARDWARE\n" + board_text(board)]
    if examples:
        ex = "\n\n".join(
            f"Example '{e['path']}' ({e['title']}):\n```c\n{e['code']}\n```" for e in examples
        )
        parts.append(
            "OFFICIAL ESP-IDF v5.5 EXAMPLES that may be relevant (real, working code from the installed "
            "ESP-IDF - copy their API usage, ignore parts that do not apply):\n" + ex
        )
    if current_code and current_code.strip():
        parts.append(
            "CURRENT main.c (the latest version; it may include the user's own manual edits - keep them "
            "unless asked otherwise):\n```c\n" + current_code.strip() + "\n```"
        )
    else:
        parts.append("CURRENT main.c: none yet (new project).")
    return "\n\n".join(parts)


def build_messages(
    request: str,
    history: list[dict[str, Any]],
    board: dict[str, Any] | None,
    examples: list[dict[str, str]],
    current_code: str | None,
    max_history: int = 6,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context_message(board, examples, current_code)},
                {"role": "assistant", "content": "Understood. I will follow the rules and work from the current main.c."}]
    recent = [m for m in history if m.get("role") in ("user", "assistant") and m.get("kind", "chat") in ("chat", "fix")]
    for m in recent[-max_history:]:
        content = m["content"] if m["role"] == "user" else strip_code_blocks(m["content"])
        messages.append({"role": m["role"], "content": content})
    messages.append({"role": "user", "content": request})
    return messages


def fix_request(errors: list[dict[str, Any]], unknown: list[str], deprecated: list[str], tail: str = "") -> str:
    lines = ["The ESP-IDF v5.5 build of the CURRENT main.c failed."]
    if errors:
        lines.append("Compiler errors:")
        lines += [f"- line {e['line']}: {e['message']}" for e in errors[:15]]
    elif tail:
        lines.append("Build output (end):\n" + tail[-1500:])
    if unknown:
        lines.append("These called names do not exist in ESP-IDF v5.5 (checked against the real headers): "
                     + ", ".join(unknown) + ". Replace them with real ESP-IDF v5.5 functions.")
    if deprecated:
        lines.append("These are deprecated legacy APIs, use the v5 drivers instead: " + ", ".join(deprecated) + ".")
    lines.append("Fix the code. Keep the same behaviour and pins. Reply with one short sentence and the complete corrected main.c.")
    return "\n".join(lines)


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Rough token count (about 3.5 characters per token for code and English)."""
    return int(sum(len(m["content"]) for m in messages) / 3.5) + 20 * len(messages)
