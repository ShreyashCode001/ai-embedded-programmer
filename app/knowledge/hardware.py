"""Hardware safety check: plain rules about the ESP32's pins, no AI.

Reads main.c, works out which GPIO pins the program uses and for what
(output, input, PWM, analog, I2C, UART, touch...), and checks them against
the ESP32 (ESP32-WROOM-32 DevKit) pin rules:

    - GPIO 6-11 are wired to the flash chip              -> error
    - GPIO 34-39 are input-only                          -> error if used as an output
    - GPIO 34-39 have no internal pull-up/pull-down      -> warning
    - ADC2 pins cannot be read while Wi-Fi is on         -> error
    - GPIO 20, 24, 28-31 and >39 do not exist            -> error
    - GPIO 1 and 3 are the USB serial console            -> warning
    - GPIO 12 must be low at boot (strapping pin)        -> warning
    - GPIO 37 and 38 are not on most DevKit boards       -> warning
    - one pin used for two different jobs                -> warning
    - a motor/pump/solenoid/12 V load with no driver     -> warning

The scan is a text scan, not a C compiler. It only reports what it can see
for certain, so it may miss a problem but should not invent one.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

FLASH = {6, 7, 8, 9, 10, 11}
INPUT_ONLY = {34, 35, 36, 37, 38, 39}
NOT_ON_DEVKIT = {37, 38}
ADC2_PINS = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}
EXISTING = set(range(0, 20)) | {21, 22, 23, 25, 26, 27} | set(range(32, 40))
SAFE_PINS = "GPIO 4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32 or 33"

ADC1_CHANNEL_GPIO = {0: 36, 1: 37, 2: 38, 3: 39, 4: 32, 5: 33, 6: 34, 7: 35}
ADC2_CHANNEL_GPIO = {0: 4, 1: 0, 2: 2, 3: 15, 4: 13, 5: 12, 6: 14, 7: 27, 8: 25, 9: 26}
TOUCH_GPIO = {0: 4, 1: 0, 2: 2, 3: 15, 4: 13, 5: 12, 6: 14, 7: 27, 8: 33, 9: 32}

# Roles a pin can have, and whether the pin must be able to DRIVE (output) for it.
DRIVES = {"output", "pwm", "i2c", "uart tx", "spi out", "rmt out", "servo"}
LABEL = {"output": "digital output", "input": "digital input", "pwm": "PWM output", "analog": "analog input (ADC)",
         "i2c": "I2C", "uart tx": "UART TX", "uart rx": "UART RX", "spi out": "SPI", "spi in": "SPI",
         "rmt out": "RMT output (LED strip/IR)", "rmt in": "RMT input", "touch": "touch sensor",
         "counter": "pulse counter input", "servo": "servo / motor PWM", "gpio": "GPIO"}

LOAD_WORDS = re.compile(r"\b(motor|pump|solenoid|fan|heater|12\s?v|24\s?v|9\s?v|lock|valve|peltier|bulb)\b", re.I)
DRIVER_WORDS = re.compile(r"\b(motor[- ]driver|driver (?:board|module|circuit|ic|chip|stage)|h-bridge|transistor|mosfet|"
                          r"l298n?|l293d?|tb6612|uln2003|drv8833|relay module|darlington|bjt|2n2222|irlz44n?|optocoupler)\b", re.I)


@dataclass
class Finding:
    level: str          # "error" | "warning"
    rule: str
    pin: int | None
    line: int
    title: str
    detail: str
    fix: str


@dataclass
class HardwareReport:
    pins: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    wifi: bool = False

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    def as_dict(self) -> dict[str, Any]:
        return {"pins": self.pins, "wifi": self.wifi, "findings": [asdict(f) for f in self.findings],
                "errors": len(self.errors), "warnings": len(self.findings) - len(self.errors)}


# ------------------------------------------------------------------ scanning

def _clean(code: str) -> str:
    """Blank out comments and strings, keeping every newline (so line numbers stay right)."""
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    code = re.sub(r"/\*.*?\*/", blank, code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", blank, code)
    code = re.sub(r'"(?:\\.|[^"\\\n])*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', code)
    return code


_ARG = r"(\(?\s*(?:\(\s*gpio_num_t\s*\)\s*)?[A-Za-z_0-9]+\s*\)?)"


class _Scanner:
    def __init__(self, code: str):
        self.code = _clean(code)
        self.symbols: dict[str, str] = {}
        for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+\(?\s*(?:\(\s*gpio_num_t\s*\)\s*)?(GPIO_NUM_\d+|\d+|[A-Za-z_]\w*)\s*\)?\s*$",
                             self.code, re.MULTILINE):
            self.symbols[m.group(1)] = m.group(2)
        for m in re.finditer(r"(?:static\s+)?(?:const\s+)?(?:gpio_num_t|int|uint8_t|uint32_t|int32_t)\s+(?:const\s+)?"
                             r"([A-Za-z_]\w*)\s*=\s*\(?\s*(?:\(\s*gpio_num_t\s*\)\s*)?(GPIO_NUM_\d+|\d+)\s*\)?\s*;", self.code):
            self.symbols.setdefault(m.group(1), m.group(2))
        self.uses: dict[int, dict[str, Any]] = {}

    def line_of(self, pos: int) -> int:
        return self.code.count("\n", 0, pos) + 1

    def resolve(self, token: str, depth: int = 0) -> int | None:
        token = re.sub(r"\(\s*gpio_num_t\s*\)", "", token).strip(" ()\t")
        m = re.fullmatch(r"GPIO_NUM_(\d+)", token)
        if m:
            return int(m.group(1))
        if re.fullmatch(r"\d+", token):
            return int(token)
        if token in self.symbols and depth < 3:
            return self.resolve(self.symbols[token], depth + 1)
        return None

    def use(self, pin: int | None, role: str, pos: int) -> None:
        if pin is None or pin < 0:
            return
        entry = self.uses.setdefault(pin, {"pin": pin, "roles": [], "lines": []})
        if role not in entry["roles"]:
            entry["roles"].append(role)
        line = self.line_of(pos)
        if line not in entry["lines"]:
            entry["lines"].append(line)

    def scan(self) -> None:
        c = self.code
        for m in re.finditer(r"\bgpio_set_direction\s*\(\s*" + _ARG + r"\s*,\s*(GPIO_MODE_\w+)", c):
            mode = m.group(2)
            self.use(self.resolve(m.group(1)), "output" if "OUTPUT" in mode else "input", m.start())
        for fn, role in (("gpio_set_level", "output"), ("gpio_get_level", "input"), ("gpio_isr_handler_add", "input"),
                         ("gpio_reset_pin", "gpio"), ("gpio_wakeup_enable", "input"), ("rtc_gpio_init", "gpio")):
            for m in re.finditer(r"\b" + fn + r"\s*\(\s*" + _ARG, c):
                self.use(self.resolve(m.group(1)), role, m.start())
        for m in re.finditer(r"\bgpio_set_pull_mode\s*\(\s*" + _ARG + r"\s*,\s*(GPIO_\w+)", c):
            if "FLOATING" not in m.group(2):
                self.use(self.resolve(m.group(1)), "pull", m.start())
        for m in re.finditer(r"\bgpio_(?:pullup|pulldown)_en\s*\(\s*" + _ARG, c):
            self.use(self.resolve(m.group(1)), "pull", m.start())
        # gpio_config_t: pin mask + mode + pulls written near each other
        for m in re.finditer(r"pin_bit_mask\s*=\s*([^;]+?)(?:;|,\s*\n|\n\s*\})", c):
            window = c[max(0, m.start() - 500): m.end() + 500]
            mode = re.search(r"\bmode\s*=\s*(GPIO_MODE_\w+)", window)
            role = "output" if mode and "OUTPUT" in mode.group(1) else "input"
            pulls = re.search(r"pull_(?:up|down)_en\s*=\s*(?:GPIO_PULL(?:UP|DOWN)_ENABLE|1|true)", window)
            for p in re.findall(r"(?:1ULL|1UL|1U|1LL|1)\s*<<\s*" + _ARG, m.group(1)) + \
                    re.findall(r"BIT(?:64)?\s*\(\s*" + _ARG + r"\s*\)", m.group(1)):
                pin = self.resolve(p)
                self.use(pin, role, m.start())
                if pulls:
                    self.use(pin, "pull", m.start())
        # Peripheral pins set in config structs
        fields = {
            "sda_io_num": "i2c", "scl_io_num": "i2c", "tx_io_num": "uart tx", "rx_io_num": "uart rx",
            "mosi_io_num": "spi out", "sclk_io_num": "spi out", "miso_io_num": "spi in", "spics_io_num": "spi out",
            "gen_gpio_num": "servo", "edge_gpio_num": "counter", "level_gpio_num": "counter",
        }
        for m in re.finditer(r"\.(\w+_(?:io|gpio)_num|gpio_num)\s*=\s*" + _ARG, c):
            name = m.group(1)
            if name in fields:
                role = fields[name]
            elif name == "gpio_num":
                before = c[max(0, m.start() - 600): m.start()]
                kinds = {"ledc_channel_config_t": "pwm", "rmt_tx_channel_config_t": "rmt out",
                         "rmt_rx_channel_config_t": "rmt in", "led_strip_config_t": "rmt out"}
                found = [(before.rfind(k), v) for k, v in kinds.items() if k in before]
                role = max(found)[1] if found else "gpio"
            else:
                continue
            self.use(self.resolve(m.group(2)), role, m.start())
        for m in re.finditer(r"\bledc_channel_config_t\s+\w+\s*=\s*\{[^}]*?\bgpio_num\s*=\s*" + _ARG, c):
            self.use(self.resolve(m.group(1)), "pwm", m.start())
        for m in re.finditer(r"\buart_set_pin\s*\(\s*(\w+)\s*,\s*" + _ARG + r"\s*,\s*" + _ARG, c):
            if "PIN_NO_CHANGE" not in m.group(2):
                self.use(self.resolve(m.group(2)), "uart tx", m.start())
            if "PIN_NO_CHANGE" not in m.group(3):
                self.use(self.resolve(m.group(3)), "uart rx", m.start())
        # ADC
        unit2 = bool(re.search(r"\bADC_UNIT_2\b|\badc2_", c))
        for m in re.finditer(r"\b(ADC[12]?)_CHANNEL_(\d+)\b", c):
            ch = int(m.group(2))
            is2 = m.group(1) == "ADC2" or (m.group(1) == "ADC" and unit2 and not re.search(r"\bADC_UNIT_1\b", c))
            table = ADC2_CHANNEL_GPIO if is2 else ADC1_CHANNEL_GPIO
            self.use(table.get(ch), "analog", m.start())
        # Touch pads
        for m in re.finditer(r"\bTOUCH_PAD_NUM(\d+)\b", c):
            self.use(TOUCH_GPIO.get(int(m.group(1))), "touch", m.start())
        for m in re.finditer(r"\btouch_sensor_new_channel\s*\(\s*\w+\s*,\s*(\d+)", c):
            self.use(TOUCH_GPIO.get(int(m.group(1))), "touch", m.start())


# ------------------------------------------------------------------- rules

def check(code: str, request: str = "", reply: str = "") -> HardwareReport:
    report = HardwareReport()
    if not code or not code.strip():
        return report
    s = _Scanner(code)
    s.scan()
    report.wifi = bool(re.search(r"\besp_wifi_(?:start|init)\b", s.code))
    seen: set[tuple[str, int | None]] = set()

    def add(level: str, rule: str, pin: int | None, line: int, title: str, detail: str, fix: str) -> None:
        if (rule, pin) in seen:
            return
        seen.add((rule, pin))
        report.findings.append(Finding(level, rule, pin, line, title, detail, fix))

    for pin, use in sorted(s.uses.items()):
        roles = [r for r in use["roles"] if r != "pull"]
        line = use["lines"][0]
        if pin not in EXISTING:
            add("error", "no_such_pin", pin, line, f"GPIO {pin} does not exist on the ESP32",
                "The ESP32 has GPIO 0-19, 21-23, 25-27 and 32-39.", f"Use {SAFE_PINS}.")
            continue
        if pin in FLASH:
            add("error", "flash_pin", pin, line, f"GPIO {pin} is connected to the flash chip",
                "GPIO 6 to 11 connect the ESP32 to its flash memory. Using them makes the board crash or stop booting.",
                f"Move it to a free pin: {SAFE_PINS}.")
        if pin in INPUT_ONLY and any(r in DRIVES for r in roles):
            jobs = ", ".join(LABEL[r] for r in roles if r in DRIVES)
            add("error", "input_only", pin, line, f"GPIO {pin} is input-only but is used as {jobs}",
                "GPIO 34, 35, 36 and 39 can only read signals. They cannot drive an LED, PWM, a bus or anything else.",
                f"Use an output-capable pin instead, e.g. {SAFE_PINS}.")
        if pin in INPUT_ONLY and "pull" in use["roles"]:
            add("warning", "no_pull", pin, line, f"GPIO {pin} has no internal pull-up or pull-down",
                "Turning on the pull resistor in code does nothing on GPIO 34-39, so the input can float and read random values.",
                "Add an external 10 kΩ resistor: to 3.3 V for a pull-up, or to GND for a pull-down.")
        if pin in NOT_ON_DEVKIT:
            add("warning", "not_on_devkit", pin, line, f"GPIO {pin} is not on most DevKit boards",
                "GPIO 37 and 38 are used inside the module and are usually not brought out to a header pin.",
                "Use GPIO 32, 33, 34, 35, 36 or 39 for an input.")
        if pin in (1, 3) and roles and not set(roles) <= {"uart tx", "uart rx"}:
            add("warning", "console_pin", pin, line, f"GPIO {pin} is the USB serial console",
                "GPIO 1 (TX) and 3 (RX) carry the log messages and flashing. Using them for something else breaks the serial monitor and can stop flashing.",
                f"Use another pin: {SAFE_PINS}.")
        if pin == 12:
            add("warning", "strapping_12", pin, line, "GPIO 12 is a boot (strapping) pin",
                "GPIO 12 must be low when the board starts; if your circuit pulls it high, the board may not boot.",
                "Prefer another pin, or make sure nothing pulls GPIO 12 high at power-on.")
        if report.wifi and pin in ADC2_PINS and "analog" in roles:
            add("error", "adc2_wifi", pin, line, f"GPIO {pin} is an ADC2 pin, which cannot be read while Wi-Fi is on",
                "On the ESP32, Wi-Fi uses the ADC2 unit. analog reads on ADC2 pins fail while Wi-Fi is running.",
                "Use an ADC1 pin instead: GPIO 32, 33, 34, 35, 36 or 39.")
        kinds = set(roles) - {"gpio"}
        peripherals = kinds - {"output", "input"}
        if len(peripherals) > 1 or ("analog" in kinds and "output" in kinds):
            add("warning", "pin_conflict", pin, line, f"GPIO {pin} is used for two different jobs",
                "It is set up as " + " and ".join(LABEL[k] for k in sorted(kinds)) + ". One pin can do only one job at a time.",
                "Give each part its own pin.")

    code_text = re.sub(r"^\s*#\s*include.*$", " ", code, flags=re.MULTILINE)
    if request and LOAD_WORDS.search(request) and not DRIVER_WORDS.search(reply + " " + code_text):
        word = LOAD_WORDS.search(request).group(1)
        add("warning", "needs_driver", None, 1, f"A {word} needs a driver circuit",
            "A GPIO pin gives about 20 mA at 3.3 V. Motors, pumps, solenoids, fans and 12 V loads need far more and can destroy the pin.",
            "Use a motor driver (L298N, TB6612), a logic-level MOSFET or a relay module, with its own power supply.")

    def label(roles: list[str]) -> str:
        jobs = [r for r in roles if r not in ("pull", "gpio")] or ["gpio"]
        return ", ".join(LABEL.get(r, r) for r in jobs) + (" + pull resistor" if "pull" in roles else "")

    report.pins = [s.uses[p] | {"label": label(s.uses[p]["roles"]), "lines": sorted(s.uses[p]["lines"])}
                   for p in sorted(s.uses)]
    report.findings.sort(key=lambda f: (f.level != "error", f.line))
    return report


def fix_text(findings: list[Finding]) -> str:
    """The hardware problems, written for the model's fix request."""
    return "\n".join(f"- line {f.line}: {f.title}. {f.detail} Fix: {f.fix}" for f in findings)
