"""Run the app with a SCRIPTED model (no real AI) for UI tests and demos.

    python -m tests.demo_server            -> http://127.0.0.1:8770

The scripted model answers a few fixed requests. The first answer for a blink
request deliberately calls a function that does not exist (gpio_write), so the
fake-function check, the build error and the auto-fix can be seen working.
Everything else (ESP-IDF builds, simulator, versions) is real.
"""

from __future__ import annotations

import os

import uvicorn

from app import main
from app.assistant import Assistant
from app.llm.providers import ScriptedProvider

BLINK = """#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_log.h"

#define LED_GPIO GPIO_NUM_2
#define BLINK_MS {ms}

static const char *TAG = "blink";

void app_main(void)
{{
    gpio_reset_pin(LED_GPIO);
    gpio_set_direction(LED_GPIO, GPIO_MODE_OUTPUT);

    int on = 0;
    while (1) {{
        on = !on;
        {setter}(LED_GPIO, on);
        ESP_LOGI(TAG, "LED %s", on ? "ON" : "OFF");
        vTaskDelay(pdMS_TO_TICKS(BLINK_MS));
    }}
}}
"""


def reply_for(messages):
    last = messages[-1]["content"].lower()
    if "breaks the esp32 hardware rules" in last:
        return ("GPIO 34 can only read signals, so I moved the LED to **GPIO 4**, which can drive an output.\n\n```c\n"
                + BLINK.format(ms=500, setter="gpio_set_level").replace("GPIO_NUM_2", "GPIO_NUM_4") + "```\n"
                "Wiring: LED + 220 Ω resistor from GPIO 4 to GND.")
    if "gpio 34" in last and "led" in last:
        return ("Here is a program that blinks an LED on GPIO 34 every 500 ms.\n\n```c\n"
                + BLINK.format(ms=500, setter="gpio_set_level").replace("GPIO_NUM_2", "GPIO_NUM_34") + "```\n"
                "Wiring:\n- LED + 220 Ω resistor: GPIO 34 → GND")
    if "build of the current main.c failed" in last:
        return ("I replaced `gpio_write`, which does not exist in ESP-IDF, with `gpio_set_level`.\n\n```c\n"
                + BLINK.format(ms=500, setter="gpio_set_level") + "```")
    if "faster" in last or "200" in last:
        return ("Done: the LED now blinks every 200 ms.\n\n```c\n" + BLINK.format(ms=200, setter="gpio_set_level")
                + "```\nWiring: onboard LED on GPIO 2.")
    if "explain" in last:
        return ("**What it does:** blinks the onboard LED.\n\n1. `gpio_reset_pin` and `gpio_set_direction` make GPIO 2 an output.\n"
                "2. The loop flips the LED with `gpio_set_level` and waits with `vTaskDelay`.\n\nWiring: none, the LED is on the board.")
    if "blink" in last:
        return ("I wrote a program that blinks the onboard LED (GPIO 2) every 500 ms and logs each change.\n\n```c\n"
                + BLINK.format(ms=500, setter="gpio_write") + "```\n"
                "| Part | Pin | Note |\n|---|---|---|\n| Onboard LED | GPIO 2 | already on most DevKit boards |\n\n"
                "**Wiring:** nothing to connect, the LED is on the board.")
    return "This demo model only knows blink requests. Connect a real model in Settings."


class SlowScripted(ScriptedProvider):
    """Types the scripted reply out a little at a time, like a real model."""

    def stream(self, messages):
        import time
        for kind, value in super().stream(messages):
            time.sleep(float(os.environ.get("ADEP_DEMO_DELAY", "0.03")))
            yield kind, value


def factory(settings):
    return Assistant(settings, provider=SlowScripted(reply_for, model="scripted-demo"))


if __name__ == "__main__":
    main.assistant_factory = factory
    uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ.get("ADEP_PORT", "8770")), log_level="warning")
