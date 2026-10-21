/* ESP32 DevKitC (38-pin, ESP32-WROOM-32) pin map drawing for the Pins tab.
   Pins the program uses are highlighted; red = error, amber = warning. */
(function () {
  "use strict";

  // Header pins from the top (antenna end) to the USB end. [label, gpio or null]
  var LEFT = [["3V3", null], ["EN", null], ["VP 36", 36], ["VN 39", 39], ["34", 34], ["35", 35], ["32", 32], ["33", 33],
    ["25", 25], ["26", 26], ["27", 27], ["14", 14], ["12", 12], ["GND", null], ["13", 13], ["D2 9", 9], ["D3 10", 10],
    ["CMD 11", 11], ["5V", null]];
  var RIGHT = [["GND", null], ["23", 23], ["22", 22], ["TX 1", 1], ["RX 3", 3], ["21", 21], ["GND", null], ["19", 19],
    ["18", 18], ["5", 5], ["17", 17], ["16", 16], ["4", 4], ["0", 0], ["2", 2], ["15", 15], ["D1 8", 8], ["D0 7", 7],
    ["CLK 6", 6]];

  var NOTE = {
    6: "flash", 7: "flash", 8: "flash", 9: "flash", 10: "flash", 11: "flash",
    34: "input only", 35: "input only", 36: "input only", 39: "input only",
    0: "boot pin", 2: "boot pin · onboard LED on many boards", 5: "boot pin", 12: "boot pin (keep low)", 15: "boot pin",
    1: "USB serial TX", 3: "USB serial RX",
  };

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function render(container, report, onSelect) {
    report = report || { pins: [], findings: [] };
    var used = {};
    (report.pins || []).forEach(function (p) { used[p.pin] = p; });
    var level = {};
    (report.findings || []).forEach(function (f) {
      if (f.pin == null) return;
      if (f.level === "error" || !level[f.pin]) level[f.pin] = f.level;
    });

    var W = 340, top = 150, step = 21.5, rows = LEFT.length;
    var H = top + rows * step + 70;
    var bx = 112, bw = 116, hx1 = bx + 12, hx2 = bx + bw - 12;
    var out = [];
    out.push('<svg class="board-svg" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="ESP32 DevKit pin map">');
    // board, module, antenna
    out.push('<rect class="pcb" x="' + bx + '" y="20" width="' + bw + '" height="' + (H - 40) + '" rx="8"/>');
    out.push('<rect class="module" x="' + (bx + 14) + '" y="26" width="' + (bw - 28) + '" height="108" rx="3"/>');
    out.push('<path class="antenna" d="M' + (bx + 24) + " 36 h16 v12 h-10 v8 h18 v-20 h14 v20 h14 v-20 h14 v12" + '"/>');
    out.push('<rect class="shield" x="' + (bx + 20) + '" y="66" width="' + (bw - 40) + '" height="62" rx="2"/>');
    out.push('<text class="mod-text" x="' + (bx + bw / 2) + '" y="94" text-anchor="middle">ESP32</text>');
    out.push('<text class="mod-text" x="' + (bx + bw / 2) + '" y="107" text-anchor="middle" style="font-weight:500;font-size:7.5px">WROOM-32</text>');
    var by = top + rows * step + 2;
    out.push('<rect class="btn-sq" x="' + (bx + 14) + '" y="' + (by - 6) + '" width="14" height="10" rx="2"/>');
    out.push('<rect class="btn-sq" x="' + (bx + bw - 28) + '" y="' + (by - 6) + '" width="14" height="10" rx="2"/>');
    out.push('<rect class="usb" x="' + (bx + bw / 2 - 16) + '" y="' + (H - 34) + '" width="32" height="18" rx="3"/>');
    out.push('<text class="board-title" x="' + (bx + bw / 2) + '" y="' + (top + rows * step / 2) + '" text-anchor="middle">DevKitC</text>');
    out.push('<text class="board-sub" x="' + (bx + bw / 2) + '" y="' + (top + rows * step / 2 + 13) + '" text-anchor="middle">38 pins</text>');

    function pin(side, idx, label, gpio) {
      var y = top + idx * step;
      var hx = side === "L" ? hx1 : hx2;
      var cls = "pin-hit";
      var isUsed = gpio != null && used[gpio];
      if (isUsed) cls += " used";
      if (gpio != null && level[gpio] === "error") cls += " p-err";
      else if (gpio != null && level[gpio] === "warning") cls += " p-warn";
      if (gpio != null && gpio >= 6 && gpio <= 11) cls += " flash";
      var labelW = 64;
      var lx = side === "L" ? bx - 6 - labelW : bx + bw + 6;
      var tip = (gpio != null ? "GPIO " + gpio : label) + (NOTE[gpio] ? " – " + NOTE[gpio] : "") +
        (isUsed ? "\nUsed as: " + used[gpio].label : "");
      out.push('<g class="' + cls + '"' + (gpio != null ? ' data-pin="' + gpio + '"' : "") + "><title>" + esc(tip) + "</title>");
      out.push('<rect class="pin-chip" x="' + lx + '" y="' + (y - 8) + '" width="' + labelW + '" height="16" rx="5"/>');
      out.push('<line x1="' + (side === "L" ? lx + labelW : bx) + '" y1="' + y + '" x2="' + (side === "L" ? bx : lx) + '" y2="' + y + '" stroke="var(--line-2)" stroke-width="1"/>');
      out.push('<circle class="hole" cx="' + hx + '" cy="' + y + '" r="4.6"/>');
      var dim = gpio == null ? " dim" : "";
      var text = gpio != null ? (label.indexOf(" ") > 0 ? label : "GPIO " + label) : label;
      out.push('<text class="pin-label' + dim + '" x="' + (lx + labelW / 2) + '" y="' + (y + 3.4) + '" text-anchor="middle">' + esc(text) + "</text>");
      out.push("</g>");
    }
    LEFT.forEach(function (p, i) { pin("L", i, p[0], p[1]); });
    RIGHT.forEach(function (p, i) { pin("R", i, p[0], p[1]); });
    out.push("</svg>");
    container.innerHTML = out.join("");
    if (onSelect) {
      container.querySelectorAll("[data-pin]").forEach(function (g) {
        g.addEventListener("click", function () { onSelect(+g.getAttribute("data-pin")); });
      });
    }
  }

  function highlight(container, pin) {
    container.querySelectorAll("[data-pin]").forEach(function (g) {
      g.classList.toggle("sel", +g.getAttribute("data-pin") === pin);
    });
  }

  window.PinMap = { render: render, highlight: highlight, NOTE: NOTE };
})();
