/* AI-Driven Embedded Programmer – browser app, v2 (no build step, plain JS).
   Layout and behaviour follow docs/UI_DESIGN.md. Sections:
   state · helpers · theme & layout · status & settings · projects & sidebar ·
   composer · conversation (turns, steps, cards) · code panel · actions · start-up */
(function () {
  "use strict";

  // =================================================================== state
  const S = {
    projects: [], project: null, settings: null, groqModels: [], status: null,
    busy: false, controller: null, monitor: null,
    editor: null, diffEditor: null, monaco: null, savedCode: "",
    tab: "code", check: null, follow: true, lastDiff: null, streaming: false, liveTurn: null,
    checkTimer: null, pinSel: null,
  };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => window.Markdown.escape(String(s == null ? "" : s));
  const icon = (name, cls) => `<svg class="i${cls ? " " + cls : ""}"><use href="#i-${name}"/></svg>`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const isEval = (p) => /^eval /i.test(p.name || "");

  const SUGGESTIONS = [
    ["zap", "Blink the onboard LED every 500 ms"],
    ["cpu", "Read a DHT22 on GPIO 4 every 2 seconds"],
    ["play", "Sweep a servo on GPIO 13 from 0 to 180°"],
    ["monitor", "Web page with a button that toggles an LED"],
    ["terminal", "Read a potentiometer on GPIO 34 in millivolts"],
  ];
  const SUGGESTION_TEXT = {
    "Blink the onboard LED every 500 ms": "Blink the onboard LED every 500 ms",
    "Read a DHT22 on GPIO 4 every 2 seconds": "Read a DHT22 sensor on GPIO 4 every 2 seconds and print temperature and humidity",
    "Sweep a servo on GPIO 13 from 0 to 180°": "Sweep a servo motor on GPIO 13 from 0 to 180 degrees and back, continuously",
    "Web page with a button that toggles an LED": "Connect to Wi-Fi and serve a web page with a button that turns an LED on GPIO 2 on and off",
    "Read a potentiometer on GPIO 34 in millivolts": "Read a potentiometer on GPIO 34 every 200 ms and print the voltage in millivolts",
  };

  // ================================================================= helpers
  async function api(path, options) {
    options = options || {};
    const res = await fetch(path, {
      method: options.method || "GET",
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : {},
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).error || msg; } catch (e) { /* not JSON */ }
      throw new Error(msg);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res;
  }

  async function stream(path, body, onEvent) {
    S.controller = new AbortController();
    const res = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}), signal: S.controller.signal,
    });
    if (!res.ok || !res.body) throw new Error("Server error " + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (chunk.startsWith("data: ")) {
          try { onEvent(JSON.parse(chunk.slice(6))); } catch (e) { console.error(e, chunk); }
        }
      }
    }
  }

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* storage blocked */ }
    return null;
  }

  function toast(text, kind) {
    const el = document.createElement("div");
    el.className = "toast" + (kind === "err" ? " err" : "");
    el.innerHTML = icon(kind === "err" ? "alert" : "check") + `<span>${esc(text)}</span>`;
    $("toasts").appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, kind === "err" ? 6000 : 2800);
  }

  function askText(title, value, note) {
    return new Promise((resolve) => {
      const d = $("promptDialog");
      $("promptTitle").textContent = title;
      $("promptInput").value = value || "";
      $("promptNote").textContent = note || "";
      d.returnValue = "";
      d.onclose = () => resolve(d.returnValue === "ok" ? $("promptInput").value.trim() : null);
      d.showModal();
      setTimeout(() => $("promptInput").select(), 30);
    });
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    if (s < 86400 * 7) return Math.floor(s / 86400) + " d ago";
    return new Date(iso).toLocaleDateString();
  }

  function firstSentence(text) {
    const plain = String(text || "").replace(/```[\s\S]*?(```|$)/g, "").replace(/[*`#>]/g, "").trim();
    const m = plain.match(/^(.+?[.!?])(\s|$)/s);
    return (m ? m[1] : plain).replace(/\s+/g, " ").slice(0, 140);
  }

  // Tooltips for anything with data-tip
  (function tooltips() {
    let timer = null, current = null;
    const tip = $("tooltip");
    document.addEventListener("mouseover", (e) => {
      const el = e.target.closest("[data-tip]");
      if (el === current) return;
      current = el;
      clearTimeout(timer);
      tip.hidden = true;
      if (!el) return;
      timer = setTimeout(() => {
        if (!document.body.contains(el)) return;
        tip.textContent = el.getAttribute("data-tip");
        tip.hidden = false;
        const r = el.getBoundingClientRect();
        const t = tip.getBoundingClientRect();
        let x = r.left + r.width / 2 - t.width / 2;
        x = Math.max(8, Math.min(x, window.innerWidth - t.width - 8));
        let y = r.bottom + 8;
        if (y + t.height > window.innerHeight - 8) y = r.top - t.height - 8;
        tip.style.left = x + "px";
        tip.style.top = y + "px";
      }, 450);
    });
    document.addEventListener("mousedown", () => { clearTimeout(timer); tip.hidden = true; });
  })();

  // Menus: one open at a time, closed by outside click or Esc
  let openMenuEl = null;
  function closeMenus() {
    document.querySelectorAll(".menu").forEach((m) => { m.hidden = true; });
    document.querySelectorAll(".menu.floating").forEach((m) => m.remove());
    openMenuEl = null;
  }
  function toggleMenu(menu) {
    const wasOpen = !menu.hidden;
    closeMenus();
    if (!wasOpen) { menu.hidden = false; openMenuEl = menu; }
  }
  function floatingMenu(anchor, items, onPick) {
    closeMenus();
    const m = document.createElement("div");
    m.className = "menu floating";
    m.innerHTML = items.map((it) => it === "-" ? '<div class="menu-sep"></div>'
      : `<button data-k="${it.k}" class="${it.danger ? "danger" : ""}">${icon(it.icon)}${esc(it.label)}</button>`).join("");
    document.body.appendChild(m);
    const r = anchor.getBoundingClientRect();
    m.style.position = "fixed";
    m.style.left = Math.min(r.left, window.innerWidth - 240) + "px";
    m.style.top = Math.min(r.bottom + 4, window.innerHeight - m.offsetHeight - 8) + "px";
    m.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { closeMenus(); onPick(b.dataset.k); } });
    openMenuEl = m;
  }
  document.addEventListener("click", (e) => {
    if (openMenuEl && !e.target.closest(".menu") && !e.target.closest("[aria-haspopup]") && !e.target.closest(".p-more")) closeMenus();
  });

  // ========================================================= theme & layout
  function themeSetting() { return store("adep.theme") || "system"; }
  function applyTheme() {
    const t = themeSetting();
    const dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    $("themeBtn").innerHTML = icon(t === "system" ? "monitor" : dark ? "moon" : "sun");
    $("themeBtn").setAttribute("data-tip", "Theme: " + (t === "system" ? "same as Windows" : t));
    if (S.monaco) S.monaco.editor.setTheme(dark ? "adep-dark" : "adep-light");
    if (S.check) renderPins();
  }
  function cycleTheme() {
    const order = ["system", "light", "dark"];
    store("adep.theme", order[(order.indexOf(themeSetting()) + 1) % order.length]);
    applyTheme();
    toast("Theme: " + (themeSetting() === "system" ? "same as Windows" : themeSetting()));
  }

  function setSidebar(open) {
    $("app").classList.remove("sb-auto");
    $("app").classList.toggle("sb-closed", !open);
    store("adep.sidebar", open ? "1" : "0");
    setTimeout(layoutEditors, 360);
  }

  function setPanel(open, tab) {
    // Like Claude: opening the panel on a smaller screen tucks the sidebar away (without saving that choice).
    if (open && !panelOpen() && window.innerWidth < 1600 && !$("app").classList.contains("sb-closed")) {
      $("app").classList.add("sb-closed", "sb-auto");
    } else if (!open && $("app").classList.contains("sb-auto")) {
      $("app").classList.remove("sb-closed", "sb-auto");
    }
    $("app").classList.toggle("panel-open", open);
    $("panelToggle").classList.toggle("on", open);
    store("adep.panel", open ? "1" : "0");
    if (tab) showTab(tab);
    setTimeout(layoutEditors, 360);
  }
  const panelOpen = () => $("app").classList.contains("panel-open");

  function layoutEditors() {
    if (S.editor) S.editor.layout();
    if (S.diffEditor) S.diffEditor.layout();
  }

  function initSplitter() {
    const saved = store("adep.panelW");
    if (saved) document.documentElement.style.setProperty("--panel-w", saved);
    const bar = $("splitter");
    bar.addEventListener("mousedown", (e) => {
      e.preventDefault();
      $("app").classList.add("resizing");
      const move = (ev) => {
        const w = Math.min(Math.max(window.innerWidth - ev.clientX, 380), window.innerWidth - 520);
        document.documentElement.style.setProperty("--panel-w", w + "px");
        layoutEditors();
      };
      const up = () => {
        $("app").classList.remove("resizing");
        store("adep.panelW", getComputedStyle(document.documentElement).getPropertyValue("--panel-w").trim());
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        layoutEditors();
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }

  // ===================================================== status & settings
  async function loadStatus() {
    try {
      const st = await api("/api/status");
      S.status = st;
      const env = $("envStatus");
      const idf = st.esp_idf || {};
      env.className = "env " + (idf.found ? "ok" : "bad");
      env.querySelector(".env-text").textContent = idf.found
        ? `ESP-IDF v${idf.version}${idf.qemu ? " · simulator ready" : " · no simulator"}`
        : "ESP-IDF not found";
      env.title = idf.found ? idf.path : (idf.message || "");
      $("appVersion").textContent = `${st.app} v${st.version}`;
      return st;
    } catch (e) {
      return { monaco_local: false };
    }
  }

  async function loadSettings() {
    const data = await api("/api/settings");
    S.settings = data.settings;
    S.groqModels = data.groq_models;
    renderModel();
  }

  function modelLabel(id) {
    const m = S.groqModels.find((x) => x.id === id);
    return m ? m.label.replace(/\s*\(.*\)\s*$/, "") : id;
  }

  function renderModel() {
    const s = S.settings;
    $("modelName").textContent = s.provider === "ollama" ? s.ollama_model.replace(/:.*$/, "") + " · local" : modelLabel(s.groq_model);
    const rows = S.groqModels.map((m) => {
      const sel = s.provider === "groq" && s.groq_model === m.id;
      const sub = (m.label.match(/\((.*)\)/) || [])[1] || "Groq cloud";
      return `<button data-p="groq" data-m="${esc(m.id)}" class="${sel ? "selected" : ""}">${icon("cloud")}<span class="m-text"><span class="m-title">${esc(modelLabel(m.id))}</span><br><span class="m-sub">${esc(sub)} · Groq cloud</span></span>${icon("check", "m-check")}</button>`;
    });
    const localSel = s.provider === "ollama";
    rows.push('<div class="menu-sep"></div>');
    rows.push(`<button data-p="ollama" data-m="${esc(s.ollama_model)}" class="${localSel ? "selected" : ""}">${icon("laptop")}<span class="m-text"><span class="m-title">${esc(s.ollama_model)}</span><br><span class="m-sub">On this laptop · works offline</span></span>${icon("check", "m-check")}</button>`);
    $("modelMenu").innerHTML = '<div class="m-head">Model</div>' + rows.join("");
  }

  async function pickModel(provider, model) {
    const changes = { provider: provider };
    if (provider === "groq") changes.groq_model = model; else changes.ollama_model = model;
    const data = await api("/api/settings", { method: "PUT", body: changes });
    S.settings = data.settings;
    renderModel();
    if (provider === "groq" && !S.settings.groq_api_key_set) {
      toast("Add your free Groq API key in Settings first.", "err");
      openSettings();
    } else toast("Model: " + $("modelName").textContent);
  }

  function openSettings() {
    const f = $("settingsForm");
    const s = S.settings;
    f.groq_model.innerHTML = S.groqModels.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
    for (const el of f.elements) {
      if (!el.name || !(el.name in s)) continue;
      if (el.type === "checkbox") el.checked = !!s[el.name];
      else if (el.type === "radio") el.checked = el.value === s[el.name];
      else el.value = s[el.name];
    }
    f.groq_api_key.value = "";
    f.groq_api_key.placeholder = s.groq_api_key_set ? "Saved (" + s.groq_api_key + ") – paste a new key to replace it" : "Paste your free key";
    $("testResult").textContent = "";
    loadStatus().then((st) => {
      const i = st.esp_idf;
      $("idfInfo").textContent = i && i.found
        ? `ESP-IDF v${i.version} found at ${i.path} (via ${i.found_via}).` + (i.qemu ? " Simulator ready." : " Simulator not installed.")
        : (i ? i.message : "");
    });
    $("settingsDialog").returnValue = "";
    $("settingsDialog").showModal();
  }

  function settingsFromForm() {
    const out = {};
    for (const el of $("settingsForm").elements) {
      if (!el.name) continue;
      if (el.type === "radio") { if (el.checked) out[el.name] = el.value; continue; }
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    }
    return out;
  }

  async function saveSettings() {
    const data = await api("/api/settings", { method: "PUT", body: settingsFromForm() });
    S.settings = data.settings;
    renderModel();
    loadStatus();
    toast("Settings saved");
  }

  async function testModel() {
    const r = $("testResult");
    r.className = "note"; r.innerHTML = '<span class="spinner"></span> Testing…';
    try {
      await api("/api/settings", { method: "PUT", body: settingsFromForm() });
      const res = await api("/api/settings/test", { method: "POST" });
      r.textContent = res.message;
      r.className = "note " + (res.ok ? "ok" : "err");
      S.settings = (await api("/api/settings")).settings;
      renderModel();
    } catch (e) { r.textContent = e.message; r.className = "note err"; }
  }

  // ===================================================== projects & sidebar
  async function loadProjects(selectId) {
    S.projects = await api("/api/projects");
    const mine = S.projects.filter((p) => !isEval(p));
    if (!mine.length) {
      const p = await api("/api/projects", { method: "POST", body: { name: "My first project" } });
      S.projects.unshift(p);
      selectId = p.id;
    }
    renderProjectList();
    const want = selectId || store("adep.project");
    const pick = S.projects.find((p) => p.id === want) || S.projects.find((p) => !isEval(p));
    await openProject(pick.id);
  }

  function projectItem(p) {
    const active = S.project && S.project.id === p.id;
    const build = p.build === "ok" ? `<span class="ok">${icon("check", "small")}</span>` : p.build === "failed" ? `<span class="bad">${icon("x", "small")}</span>` : "";
    const sub = p.versions ? `v${p.versions} ${build}<span>· ${esc(timeAgo(p.updated))}</span>` : `<span>${esc(timeAgo(p.updated) || "new")}</span>`;
    return `<li class="project-item${active ? " active" : ""}" data-id="${esc(p.id)}">
      <div class="p-text"><div class="p-name">${esc(p.name)}</div><div class="p-sub">${sub}</div></div>
      <button class="icon-btn p-more" data-more="${esc(p.id)}" aria-label="More">${icon("more")}</button></li>`;
  }

  function renderProjectList() {
    const q = $("projectSearch").value.trim().toLowerCase();
    const match = (p) => !q || (p.name || "").toLowerCase().includes(q);
    const mine = S.projects.filter((p) => !isEval(p) && match(p));
    const evals = S.projects.filter((p) => isEval(p) && match(p));
    $("projectList").innerHTML = mine.length ? mine.map(projectItem).join("") : `<li class="sb-empty">${q ? "No projects match." : "No projects yet."}</li>`;
    $("evalList").innerHTML = evals.slice(0, 200).map(projectItem).join("");
    $("evalGroup").hidden = !evals.length;
    $("evalCount").textContent = evals.length;
  }

  function updateProjectSummary() {
    if (!S.project) return;
    const p = S.projects.find((x) => x.id === S.project.id);
    const vs = S.project.version_list || [];
    if (p) {
      p.name = S.project.name;
      p.versions = vs.length ? vs[vs.length - 1].n : 0;
      p.build = vs.length ? vs[vs.length - 1].build : null;
      p.updated = new Date().toISOString();
      S.projects.sort((a, b) => (b.updated || "").localeCompare(a.updated || ""));
    }
    renderProjectList();
  }

  async function openProject(id) {
    if (S.busy) { toast("Wait for the current job to finish (or press stop).", "err"); return; }
    if (S.monitor) closeMonitor(true);
    S.project = await api("/api/projects/" + id);
    store("adep.project", id);
    S.lastDiff = null;
    S.check = null;
    $("projectName").textContent = S.project.name;
    document.title = S.project.name + " · AI-Driven Embedded Programmer";
    setEditorCode(S.project.code);
    setErrorMarkers([]);
    renderVersions();
    renderBoardLabel();
    renderConversation();
    renderProjectList();
    $("problems").innerHTML = "";
    $("problemCount").classList.add("hidden");
    $("outputLog").textContent = "";
    $("serialLog").textContent = "";
    job("");
    if (S.project.code.trim()) runCheck(S.project.code);
    else { S.check = null; renderPins(); renderApiNote(); }
    if (S.diffEditor) $("diffEmpty").hidden = false;
  }

  async function refreshProject() {
    if (!S.project) return;
    const keepEdits = S.editor && S.editor.getValue() !== S.savedCode ? S.editor.getValue() : null;
    S.project = await api("/api/projects/" + S.project.id);
    S.savedCode = S.project.code;
    if (keepEdits === null) setEditorCode(S.project.code); else updateDirty();
    renderVersions();
    updateProjectSummary();
  }

  async function newProject() {
    if (S.busy) return;
    const name = await askText("New project", "", "A short name, e.g. “Plant watering” or “Weather station”.");
    if (name === null) return;
    const p = await api("/api/projects", { method: "POST", body: { name: name || "Untitled project" } });
    S.projects.unshift(p);
    await openProject(p.id);
    focusComposer();
  }

  async function renameProject(id) {
    const p = S.projects.find((x) => x.id === id) || S.project;
    const name = await askText("Rename project", p.name);
    if (!name) return;
    await api("/api/projects/" + id, { method: "PATCH", body: { name: name } });
    p.name = name;
    if (S.project && S.project.id === id) { S.project.name = name; $("projectName").textContent = name; }
    renderProjectList();
  }

  async function deleteProject(id) {
    const p = S.projects.find((x) => x.id === id);
    const typed = await askText("Delete project", "", `Type DELETE to remove “${p ? p.name : "this project"}” with its chat and all versions. This cannot be undone.`);
    if (typed !== "DELETE") { if (typed !== null) toast("Not deleted – type DELETE to confirm.", "err"); return; }
    await api("/api/projects/" + id, { method: "DELETE" });
    S.projects = S.projects.filter((x) => x.id !== id);
    if (S.project && S.project.id === id) { store("adep.project", ""); await loadProjects(); }
    else renderProjectList();
    toast("Project deleted");
  }

  async function projectAction(act) {
    closeMenus();
    const p = S.project;
    if (!p) return;
    if (act === "rename") renameProject(p.id);
    else if (act === "board") openBoard();
    else if (act === "explain") explainCode();
    else if (act === "export") location.href = `/api/projects/${p.id}/export`;
    else if (act === "vscode" || act === "folder") {
      const r = await api(`/api/projects/${p.id}/open-folder`, { method: "POST", body: { app: act === "vscode" ? "vscode" : "folder" } });
      toast(r.message, r.ok ? "" : "err");
    } else if (act === "clear") {
      S.project = await api(`/api/projects/${p.id}/clear-chat`, { method: "POST" });
      renderConversation();
      toast("Chat cleared. The code and versions are kept.");
    } else if (act === "delete") deleteProject(p.id);
  }

  // ================================================================ composer
  function autosize() {
    const t = $("chatInput");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.38) + "px";
    $("sendBtn").disabled = !t.value.trim() || S.busy;
  }

  function focusComposer() { setTimeout(() => $("chatInput").focus(), 50); }

  function placeComposer(home) {
    const form = $("composer");
    $("app").classList.toggle("is-home", home);
    if (home) $("homeComposerSlot").appendChild(form);
    else $("composerDock").insertBefore(form, $("composerDock").querySelector(".disclaimer"));
    const hasCode = S.project && S.project.code && S.project.code.trim();
    $("chatInput").placeholder = home ? "Describe what the ESP32 should do…" : (hasCode ? "Ask for a change, e.g. “make it blink faster”…" : "Describe what the ESP32 should do…");
    autosize();
  }

  function setBusy(busy) {
    S.busy = busy;
    $("sendBtn").hidden = busy;
    $("stopBtn").hidden = !busy;
    ["buildBtn", "fixBtn", "simBtn", "flashBtn", "explainBtn", "undoBtn", "saveBtn"].forEach((id) => { $(id).disabled = busy; });
    autosize();
    if (!busy) renderVersions();
  }

  function renderBoardLabel() {
    const b = (S.project && S.project.board) || {};
    const parts = (b.parts || []).filter((p) => (p.part || "").trim());
    $("boardLabel").textContent = parts.length ? plural(parts.length, "part") : "Board";
    $("composerHint").textContent = parts.length ? "Wiring: " + parts.map((p) => p.part).join(", ") : "";
  }

  // =========================================================== conversation
  const thread = () => $("thread");
  function nearBottom() { const c = $("conversation"); return c.scrollHeight - c.scrollTop - c.clientHeight < 140; }
  // Follow the reply while it is written, unless the user scrolls up to read something.
  function scrollDown(force) {
    const c = $("conversation");
    if (force) S.follow = true;
    if (force || S.follow || nearBottom()) c.scrollTop = c.scrollHeight;
  }

  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  }

  function versionInfo(n) {
    return ((S.project && S.project.version_list) || []).find((v) => v.n === n);
  }

  function codeCardHtml(n, opts) {
    opts = opts || {};
    if (opts.writing) {
      return `<button class="code-card writing" data-open-code="1"><span class="cc-icon">${icon("file")}</span>
        <span class="cc-text"><span class="cc-title">main.c</span><span class="cc-sub">Writing… <span data-lines>${opts.lines || 0}</span> lines</span></span></button>`;
    }
    const v = versionInfo(n);
    const lines = v && v.lines ? `${v.lines} lines` : "";
    const build = v && v.build === "ok" ? `<span class="tag ok">${icon("check")}Builds</span>`
      : v && v.build === "failed" ? `<span class="tag bad">${icon("x")}Build failed</span>` : "";
    return `<button class="code-card" data-open-version="${n || ""}"><span class="cc-icon">${icon("file")}</span>
      <span class="cc-text"><span class="cc-title">main.c</span><span class="cc-sub">${n ? "Version " + n : "Code"}${lines ? " · " + lines : ""} ${build}</span></span>
      <span class="cc-open">Open ${icon("chev-right", "small")}</span></button>`;
  }

  function renderMarkdown(text, cardHtml) {
    let usedCard = false;
    const html = window.Markdown.render(text, {
      codeBlock: (body) => {
        if (/app_main/.test(body)) { if (usedCard) return ""; usedCard = true; return cardHtml || ""; }
        return "<pre><code>" + esc(body) + "</code></pre>";
      },
    });
    return html;
  }

  // A fix reply shown under the main answer, so the explanation always matches the final code.
  function fixNoteHtml(m, attempt) {
    const text = String(m.content || "").replace(/```[\s\S]*?(```|$)/g, "").trim();
    if (!text) return "";
    return `<div class="fix-note"><div class="fix-head">${icon("wand")}Fixed automatically${attempt ? ` <span>· attempt ${attempt}</span>` : ""}${m.version ? ` <span>→ v${m.version}</span>` : ""}</div>
      <div class="ai-text small">${window.Markdown.render(text)}</div></div>`;
  }

  // Group the saved chat into turns: one user request + everything the AI did for it.
  function groupTurns(chat) {
    const turns = [];
    let cur = null;
    chat.forEach((m) => {
      const kind = m.kind || "chat";
      if (m.role === "user" && (kind === "chat" || kind === "explain")) { cur = { user: m, items: [] }; turns.push(cur); return; }
      if (!cur) { cur = { user: null, items: [] }; turns.push(cur); }
      cur.items.push(m);
    });
    return turns;
  }

  function stepHtml(cls, iconName, title, detail, extra) {
    return `<div class="step ${cls || ""}"><span class="s-icon">${iconName === "spin" ? '<span class="spinner"></span>' : icon(iconName)}</span>
      <div class="s-body"><div class="s-title">${title}</div>${detail ? `<div class="s-detail">${detail}</div>` : ""}${extra || ""}</div></div>`;
  }

  function historyTurnHtml(turn, isLast) {
    const out = [];
    if (turn.user) {
      const text = (turn.user.kind || "chat") === "explain" ? "Explain this code" : turn.user.content;
      out.push(`<div class="turn-user"><div class="bubble-user">${esc(text)}</div></div>`);
    }
    const main = turn.items.find((m) => m.role === "assistant" && (m.kind || "chat") !== "fix");
    const versions = turn.items.filter((m) => m.role === "assistant" && m.version).map((m) => m.version);
    const finalV = versions.length ? versions[versions.length - 1] : null;
    const steps = [];
    turn.items.forEach((m) => {
      const kind = m.kind || "chat";
      if (m.role === "assistant" && kind === "chat" && m.version) steps.push(stepHtml("ok", "file", `Wrote main.c <b>v${m.version}</b>`));
      if (m.role === "user" && kind === "fix") {
        const errs = (m.content.match(/^- line/mg) || []).length;
        const hw = /pin checker/.test(m.content);
        steps.push(stepHtml("bad", hw ? "cpu" : "x-circle", hw ? "Pin check found a problem – sent it to the AI" : `Build failed${errs ? " with " + plural(errs, "error") : ""} – sent to the AI (fix ${m.attempt || ""})`));
      }
      if (m.role === "assistant" && kind === "fix") steps.push(stepHtml("ok", "wand", `Fixed${m.version ? " → <b>v" + m.version + "</b>" : ""}: ${esc(firstSentence(m.content))}`));
    });
    const fv = finalV && versionInfo(finalV);
    if (fv && fv.build === "ok") steps.push(stepHtml("ok", "check-circle", `v${finalV} builds with ESP-IDF`));
    else if (fv && fv.build === "failed") steps.push(stepHtml("bad", "x-circle", `v${finalV} does not build yet`));
    const fixes = turn.items.filter((m) => m.role === "user" && m.kind === "fix").length;
    const summary = [finalV ? "Wrote v" + finalV : "", fixes ? plural(fixes, "fix") : "", fv && fv.build === "ok" ? "builds" : fv && fv.build === "failed" ? "build failed" : ""].filter(Boolean).join(" · ");

    const ai = [];
    if (steps.length) ai.push(`<div class="steps"><button class="steps-summary">${icon("chev-right", "chev")}<span>${esc(summary || plural(steps.length, "step"))}</span></button><div class="steps-list"><div>${steps.join("")}</div></div></div>`);
    const fixNotes = [];
    let attempt = 0;
    turn.items.forEach((m) => {
      if (m.role === "user" && m.kind === "fix") attempt = m.attempt || attempt + 1;
      if (m.role === "assistant" && m.kind === "fix") fixNotes.push(fixNoteHtml(m, attempt));
    });
    if (main) {
      ai.push(`<div class="ai-text">${renderMarkdown(main.content, finalV ? codeCardHtml(finalV) : "")}</div>`);
      if (finalV && !/```/.test(main.content)) ai.push(codeCardHtml(finalV));
    } else if (finalV) ai.push(codeCardHtml(finalV));
    ai.push(fixNotes.join(""));
    if (isLast) ai.push('<div class="notice-slot"></div>');
    const meta = main && main.model ? `${esc(main.model.split(":").slice(1).join(":"))}${main.seconds ? " · " + main.seconds + " s" : ""}` : "";
    if (main) ai.push(`<div class="msg-actions"><button class="icon-btn" data-copy-msg="${esc(main.id)}" data-tip="Copy reply">${icon("copy")}</button><span class="msg-meta">${meta}</span></div>`);
    if (ai.length) out.push(`<div class="turn-ai">${ai.join("")}</div>`);
    return out.join("");
  }

  function renderConversation() {
    const chat = (S.project && S.project.chat) || [];
    const home = chat.length === 0;
    $("home").hidden = !home;
    $("greetText").textContent = greeting();
    placeComposer(home);
    if (home) { thread().innerHTML = ""; return; }
    const turns = groupTurns(chat);
    thread().innerHTML = turns.map((t, i) => historyTurnHtml(t, i === turns.length - 1)).join("");
    renderNotices();
    requestAnimationFrame(() => scrollDown(true));
  }

  // Notices under the last turn: hardware problems and made-up functions of the CURRENT code.
  function renderNotices(slot) {
    slot = slot || thread().querySelector(".turn-ai:last-child .notice-slot");
    if (!slot) return;
    const c = S.check;
    if (!c || S.busy) { slot.innerHTML = ""; return; }
    const html = [];
    const hw = c.hardware || { findings: [] };
    const errs = hw.findings.filter((f) => f.level === "error");
    const warns = hw.findings.filter((f) => f.level === "warning");
    if (errs.length || warns.length) {
      const cls = errs.length ? "err" : "warn";
      const title = errs.length ? `Pin check: ${plural(errs.length, "problem")} that would fail on the real board` : `Pin check: ${plural(warns.length, "thing")} to look at`;
      html.push(`<div class="notice ${cls}"><div class="notice-head">${icon(errs.length ? "alert" : "info")}${esc(title)}</div><ul>` +
        errs.concat(warns).slice(0, 4).map((f) => `<li><b>${esc(f.title)}.</b> ${esc(f.detail)}<div class="fix">Fix: ${esc(f.fix)}</div></li>`).join("") +
        `</ul><div class="notice-actions">${errs.length ? `<button class="btn sm clay" data-ask-fix="hw">${icon("wand")}Ask the AI to fix it</button>` : ""}<button class="btn sm" data-show-pins="1">${icon("cpu")}Show pins</button></div></div>`);
    }
    const sugg = c.suggestions || {};
    const names = Object.keys(sugg);
    if (names.length) {
      html.push(`<div class="notice err"><div class="notice-head">${icon("search")}${plural(names.length, "function")} in the code do${names.length === 1 ? "es" : ""} not exist in ESP-IDF</div><ul>` +
        names.slice(0, 3).map((n) => `<li><b><code>${esc(n)}</code></b> – did you mean:` +
          sugg[n].slice(0, 3).map((o) => `<span class="sig">${esc(o.signature)}${o.header ? `  <small>// ${esc(o.header)}</small>` : ""}</span>`).join("") + "</li>").join("") +
        `</ul><div class="notice-actions"><button class="btn sm clay" data-ask-fix="api">${icon("wand")}Ask the AI to use the real functions</button></div></div>`);
    }
    slot.innerHTML = html.join("");
  }

  // ---------------------------------------------------------- live turn
  function LiveTurn(userText, opts) {
    opts = opts || {};
    if (userText) thread().insertAdjacentHTML("beforeend", `<div class="turn-user"><div class="bubble-user">${esc(userText)}</div></div>`);
    thread().insertAdjacentHTML("beforeend", `<div class="turn-ai live">
      <div class="steps live"><button class="steps-summary">${icon("chev-right", "chev")}<span></span></button><div class="steps-list"><div class="steps-inner"></div></div></div>
      <div class="thinking">${icon("mark", "mark")}<span class="shim">${esc(opts.thinking || "Thinking…")}</span></div>
      <div class="ai-text"></div><div class="card-slot"></div><div class="notice-slot"></div>
      <div class="msg-actions" hidden></div></div>`);
    const el = thread().lastElementChild;
    this.el = el;
    this.stepsEl = el.querySelector(".steps-inner");
    this.textEl = el.querySelector(".ai-text");
    this.cardSlot = el.querySelector(".card-slot");
    this.text = "";
    this.fixText = "";
    this.kind = "chat";
    this.timer = null;
    this.version = null;
    this.buildStep = null;
    this.fixStep = null;
    this.fixes = 0;
    this.built = null;
    this.stepCount = 0;
    scrollDown(true);
  }
  LiveTurn.prototype = {
    step(cls, iconName, title, detail, extra) {
      this.stepsEl.insertAdjacentHTML("beforeend", stepHtml(cls, iconName, title, detail, extra));
      this.stepCount++;
      scrollDown();
      return this.stepsEl.lastElementChild;
    },
    setStep(stepEl, cls, iconName, title, detail, extra) {
      if (!stepEl) return this.step(cls, iconName, title, detail, extra);
      const fresh = document.createElement("div");
      fresh.innerHTML = stepHtml(cls, iconName, title, detail, extra);
      fresh.firstElementChild.style.animation = "none";
      stepEl.replaceWith(fresh.firstElementChild);
      return fresh.firstElementChild;
    },
    status(text) {
      const t = this.el.querySelector(".thinking .shim");
      if (t) t.textContent = text;
      else if (/limit|retry|waiting|again/i.test(text)) this.step("warn", "info", esc(text));
    },
    hideThinking() { const t = this.el.querySelector(".thinking"); if (t) t.remove(); },
    showThinking(text) {
      if (this.el.querySelector(".thinking")) { this.status(text); return; }
      this.cardSlot.insertAdjacentHTML("afterend", `<div class="thinking">${icon("mark", "mark")}<span class="shim">${esc(text)}</span></div>`);
      scrollDown();
    },
    context(list) {
      if (!list || !list.length) return;
      this.step("", "book", `Read ${plural(list.length, "official ESP-IDF example")}`,
        list.map((x) => `<code>${esc(x.path.replace(/^examples\//, ""))}</code>`).join(", "));
    },
    token(text, kind) {
      if (kind === "fix") { this.fixText += text; this.streamCode(this.fixText); return; }
      this.kind = kind || "chat";
      this.text += text;
      this.hideThinking();
      if (!this.timer) this.timer = setTimeout(() => this.draw(), 45);
    },
    draw(final) {
      this.timer = null;
      const writing = !final && /```[^\n]*\n[\s\S]*$/.test(this.text) && (this.text.match(/```/g) || []).length % 2 === 1;
      const lines = writing ? this.text.split("```").pop().split("\n").length : 0;
      const card = this.version ? codeCardHtml(this.version) : codeCardHtml(null, { writing: !final, lines: lines });
      let html = window.Markdown.render(this.text, {
        codeBlock: (body, unfinished) => {
          if (/app_main/.test(body) || (unfinished && !final) || /#include/.test(body)) return '<div class="inline-card"></div>';
          return "<pre><code>" + esc(body) + "</code></pre>";
        },
      });
      if (!final) html += '<span class="caret"></span>';
      this.textEl.innerHTML = html;
      const slot = this.textEl.querySelector(".inline-card");
      if (slot) { slot.outerHTML = card; this.cardSlot.innerHTML = ""; }
      if (writing) this.streamCode(this.text);
      scrollDown();
    },
    streamCode(text) {
      const m = text.match(/```[^\n]*\n([\s\S]*)$/);
      if (!m) return;
      const parts = text.split("```");
      if (parts.length % 2 === 0) streamIntoEditor(parts[parts.length - 1].replace(/^[^\n]*\n/, ""));
    },
    messageDone(msg) {
      clearTimeout(this.timer);
      if ((msg.kind || "chat") === "fix") {
        if (this.fixStep) this.fixStep = this.setStep(this.fixStep, "ok", "wand", `Fixed: ${esc(firstSentence(msg.content))}`);
        this.cardSlot.insertAdjacentHTML("beforebegin", fixNoteHtml(msg, this.fixes));
        this.lastFixNote = this.cardSlot.previousElementSibling;
        this.fixText = "";
        endStreamingEditor(true);
        return;
      }
      this.message = msg;
      this.text = msg.content;
      this.draw(true);
      endStreamingEditor(true);
    },
    onVersion(ev) {
      this.version = ev.version.n;
      if (ev.version.source === "fix" && this.lastFixNote) {
        const h = this.lastFixNote.querySelector(".fix-head");
        if (h && !/→ v/.test(h.textContent)) h.insertAdjacentHTML("beforeend", ` <span>→ v${this.version}</span>`);
      }
      const api = ev.api_check;
      const isFix = ev.version.source === "fix";
      let detail = "";
      if (api && api.unknown && api.unknown.length) {
        const sugg = ev.suggestions || {};
        detail = `<span style="color:var(--danger)">${plural(api.unknown.length, "made-up function")}: ${api.unknown.slice(0, 4).map((n) => `<code>${esc(n)}</code>` + (sugg[n] && sugg[n][0] ? ` → did you mean <code>${esc(sugg[n][0].name)}</code>?` : "")).join(", ")}</span>`;
      } else if (api && api.deprecated && api.deprecated.length) {
        detail = `Old ESP-IDF API: ${api.deprecated.slice(0, 3).map((n) => `<code>${esc(n)}</code>`).join(", ")}`;
      } else if (api) detail = `All ${api.called} called functions exist in ESP-IDF v5.5`;
      const madeUp = api && api.unknown && api.unknown.length;
      if (!isFix) this.step(madeUp ? "warn" : "ok", "file", `Wrote main.c <b>v${this.version}</b>`, detail);
      else if (madeUp) this.step("warn", "search", `Checked v${this.version}`, detail);
      const hw = ev.hardware;
      if (hw && hw.findings && hw.findings.length) {
        const e = hw.findings.filter((f) => f.level === "error").length;
        this.pinProblem = true;
        this.step(e ? "bad" : "warn", "cpu", `Pin check: ${e ? plural(e, "error") : plural(hw.findings.length, "warning")}`,
          esc(hw.findings.slice(0, 2).map((f) => f.title).join(" · ")) + ` <a class="s-link" data-show-pins="1" href="#">Show pins</a>`);
      } else if (hw && hw.pins && hw.pins.length && (!this.pinChecked || this.pinProblem)) {
        this.pinChecked = true;
        this.pinProblem = false;
        this.step("ok", "cpu", `Pin check passed`, `Uses GPIO ${hw.pins.map((p) => p.pin).join(", ")}`);
      }
      this.cardSlot.innerHTML = "";
      const existing = this.el.querySelector(".code-card");
      const html = codeCardHtml(this.version);
      if (existing) existing.outerHTML = html; else this.cardSlot.innerHTML = html;
    },
    buildStart(ev) {
      this.hideThinking();
      this.buildStep = this.step("", "spin", `Building v${ev.version} with ESP-IDF…`);
    },
    buildResult(ev) {
      const n = (ev.errors || []).length;
      if (ev.ok) {
        this.built = true;
        this.buildStep = this.setStep(this.buildStep, "ok", "check-circle", `Built v${ev.version} in ${ev.seconds} s${ev.size ? " · " + esc(ev.size) : ""}`);
      } else if (ev.message && !n) {
        this.built = false;
        this.buildStep = this.setStep(this.buildStep, "bad", "x-circle", esc(ev.message));
      } else {
        this.built = false;
        const errs = (ev.errors || []).slice(0, 6).map((e) => `${e.file === "linker" ? "linker" : "line " + e.line}: ${e.message}`).join("\n");
        this.buildStep = this.setStep(this.buildStep, "bad", "x-circle", `Build of v${ev.version} failed${n ? " · " + plural(n, "error") : ""}`, "",
          errs ? `<div class="s-errors">${esc(errs)}</div>` : "");
      }
      const existing = this.el.querySelector(".code-card");
      if (existing && this.version) existing.outerHTML = codeCardHtml(this.version);
    },
    fixStart(ev) {
      this.fixes++;
      const why = ev.reason === "hardware" ? "pin problem" : "error";
      this.fixStep = this.step("", "spin", `Fixing ${plural(ev.errors || 1, why)} with the AI <span style="color:var(--text-3)">(attempt ${ev.attempt}/${ev.max})</span>`);
    },
    error(text) {
      this.hideThinking();
      this.el.insertAdjacentHTML("beforeend", `<div class="ai-error">${icon("alert")}<div>${esc(text)}</div></div>`);
      scrollDown();
    },
    note(text) { this.el.insertAdjacentHTML("beforeend", `<div class="ai-note">${icon("info", "small")}${esc(text)}</div>`); },
    finish(stopped) {
      clearTimeout(this.timer);
      if (this.text && !this.message) this.draw(true);
      this.hideThinking();
      endStreamingEditor();
      if (stopped) this.note("Stopped.");
      const steps = this.el.querySelector(".steps");
      steps.classList.remove("live");
      if (!this.stepCount) steps.remove();
      else {
        const v = this.version ? "Wrote v" + this.version : "";
        const sum = [v, this.fixes ? plural(this.fixes, "fix") : "", this.built === true ? "builds" : this.built === false ? "build failed" : ""].filter(Boolean).join(" · ");
        steps.querySelector(".steps-summary span").textContent = sum || plural(this.stepCount, "step");
      }
      this.el.classList.remove("live");
      requestAnimationFrame(() => scrollDown());
      if (this.message) {
        const a = this.el.querySelector(".msg-actions");
        const meta = this.message.model ? `${esc(this.message.model.split(":").slice(1).join(":"))}${this.message.seconds ? " · " + this.message.seconds + " s" : ""}` : "";
        a.innerHTML = `<button class="icon-btn" data-copy-msg="${esc(this.message.id)}" data-tip="Copy reply">${icon("copy")}</button><span class="msg-meta">${meta}</span>`;
        a.hidden = false;
      }
    },
  };

  // ========================================================== code panel
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  async function initEditor(localAvailable) {
    let base = window.MONACO_LOCAL;
    try {
      if (!localAvailable) throw new Error("no local copy");
      await loadScript(base + "/loader.js");
    } catch (e) {
      base = window.MONACO_CDN;
      await loadScript(base + "/loader.js");
    }
    window.require.config({ paths: { vs: base } });
    await new Promise((resolve) => window.require(["vs/editor/editor.main"], resolve));
    try { await document.fonts.load('13px "JetBrains Mono"'); } catch (e) { /* fallback font */ }
    const monaco = window.monaco;
    S.monaco = monaco;
    monaco.editor.defineTheme("adep-light", {
      base: "vs", inherit: true,
      rules: [{ token: "comment", foreground: "8A877F", fontStyle: "italic" }, { token: "keyword", foreground: "B4532A" },
        { token: "string", foreground: "2F7D32" }, { token: "number", foreground: "1F5FA8" }, { token: "type", foreground: "7A3EA0" }],
      colors: { "editor.background": "#FFFFFF", "editorGutter.background": "#FFFFFF", "editor.lineHighlightBackground": "#F7F6F2", "editor.lineHighlightBorder": "#00000000",
        "editorLineNumber.foreground": "#B9B6AE", "editorLineNumber.activeForeground": "#5E5C57", "editor.selectionBackground": "#F2D5C9", "editorIndentGuide.background1": "#EFEDE7",
        "scrollbarSlider.background": "#13131318", "editorCursor.foreground": "#D97757", "diffEditor.insertedTextBackground": "#1E8E3E22", "diffEditor.removedTextBackground": "#C9383822" },
    });
    monaco.editor.defineTheme("adep-dark", {
      base: "vs-dark", inherit: true,
      rules: [{ token: "comment", foreground: "7F7C74", fontStyle: "italic" }, { token: "keyword", foreground: "EFA084" },
        { token: "string", foreground: "9DD39A" }, { token: "number", foreground: "8FBCF2" }, { token: "type", foreground: "D2A8F0" }],
      colors: { "editor.background": "#2A2927", "editorGutter.background": "#2A2927", "editor.lineHighlightBackground": "#32312E", "editor.lineHighlightBorder": "#00000000",
        "editorLineNumber.foreground": "#6F6D67", "editorLineNumber.activeForeground": "#C9C7BD", "editor.selectionBackground": "#6A3E2E", "editorIndentGuide.background1": "#3A3936",
        "scrollbarSlider.background": "#F9F9F71A", "editorCursor.foreground": "#D97757" },
    });
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const common = { automaticLayout: true, fontFamily: '"JetBrains Mono", Consolas, monospace', fontSize: 13, lineHeight: 21, fontLigatures: true,
      minimap: { enabled: false }, scrollBeyondLastLine: false, smoothScrolling: true, padding: { top: 12, bottom: 12 },
      renderLineHighlight: "line", scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 }, overviewRulerBorder: false };
    S.editor = monaco.editor.create($("editor"), Object.assign({ value: "", language: "c", theme: dark ? "adep-dark" : "adep-light", tabSize: 4,
      cursorSmoothCaretAnimation: "on", cursorBlinking: "smooth" }, common));
    S.diffEditor = monaco.editor.createDiffEditor($("diffEditor"), Object.assign({ theme: dark ? "adep-dark" : "adep-light", readOnly: true,
      renderSideBySide: true, originalEditable: false, renderOverviewRuler: false }, common));
    monaco.editor.remeasureFonts();
    S.editor.onDidChangeModelContent(() => { if (!S.streaming) { updateDirty(); scheduleCheck(); } });
    S.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveEdits);
    S.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyB, () => doBuild(false));
    $("editorLoading").hidden = true;
    setEditorCode(S.project ? S.project.code : "");
  }

  function setEditorCode(code) {
    S.savedCode = code || "";
    if (S.editor && S.editor.getValue() !== S.savedCode) S.editor.setValue(S.savedCode);
    updateDirty();
  }
  function editorCode() { return S.editor ? S.editor.getValue() : S.savedCode; }

  function updateDirty() {
    const value = editorCode();
    const dirty = !!S.editor && value !== S.savedCode && value.trim() !== "";
    $("dirtyBadge").classList.toggle("hidden", !dirty);
    $("editorEmpty").hidden = !!value.trim() || !S.editor;
  }

  let streamTimer = null, streamPending = null;
  function streamIntoEditor(code) {
    if (!S.editor) return;
    S.streamTouched = true;
    if (!S.streaming) {
      S.streaming = true;
      S.editor.updateOptions({ readOnly: true });
      $("streamingTag").classList.remove("hidden");
      if (!panelOpen() && window.innerWidth > 1100) setPanel(true, "code");
      else if (panelOpen() && S.tab !== "code") showTab("code");
    }
    streamPending = code;
    if (streamTimer) return;
    streamTimer = setTimeout(() => {
      streamTimer = null;
      if (streamPending == null || !S.streaming) return;
      S.editor.setValue(streamPending);
      $("editorEmpty").hidden = true;
      S.editor.revealLine(S.editor.getModel().getLineCount());
      const n = streamPending.split("\n").length;
      document.querySelectorAll(".code-card.writing [data-lines]").forEach((el) => { el.textContent = n; });
    }, 90);
  }
  // keep=true: leave the streamed code on screen (the final version arrives a moment later).
  function endStreamingEditor(keep) {
    if (S.streaming) {
      S.streaming = false;
      clearTimeout(streamTimer); streamTimer = null; streamPending = null;
      if (S.editor) S.editor.updateOptions({ readOnly: false });
      $("streamingTag").classList.add("hidden");
    }
    if (!keep && S.streamTouched) {
      S.streamTouched = false;
      if (S.editor && S.editor.getValue() !== S.savedCode) S.editor.setValue(S.savedCode);
      updateDirty();
    }
  }

  function setErrorMarkers(errors) {
    if (!S.monaco || !S.editor) return;
    const markers = (errors || []).filter((e) => e.file === "main.c" && e.line > 0).map((e) => ({
      startLineNumber: e.line, endLineNumber: e.line, startColumn: Math.max(1, e.column || 1), endColumn: 1000,
      message: e.message + (e.hint ? "\n" + e.hint : ""), severity: S.monaco.MarkerSeverity.Error,
    }));
    S.monaco.editor.setModelMarkers(S.editor.getModel(), "build", markers);
  }

  function showTab(name) {
    S.tab = name;
    document.querySelectorAll("#panelTabs .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".panel-body .view").forEach((v) => { v.hidden = v.dataset.view !== name; });
    if (name === "diff" && !S.lastDiff) showLatestDiff();
    if (name === "pins") renderPins();
    setTimeout(layoutEditors, 20);
  }

  function sourceLabel(src) { return { ai: "AI", fix: "AI fix", user: "Your edit", restore: "Restored" }[src] || src; }

  function renderVersions() {
    const vs = (S.project && S.project.version_list) || [];
    const top = $("topVersion");
    if (!vs.length) {
      $("versionLabel").textContent = "No versions yet";
      top.classList.add("hidden");
      $("undoBtn").disabled = true;
      $("versionMenu").innerHTML = '<div class="m-head">No versions yet</div>';
      return;
    }
    const cur = vs[vs.length - 1];
    $("versionLabel").textContent = `Version ${cur.n} · ${sourceLabel(cur.source)}${cur.build === "ok" ? " · builds" : cur.build === "failed" ? " · build failed" : ""}`;
    top.textContent = "v" + cur.n;
    top.className = "ver-pill" + (cur.build === "ok" ? " ok" : cur.build === "failed" ? " bad" : "");
    $("undoBtn").disabled = S.busy || vs.length < 2;
    $("versionMenu").innerHTML = '<div class="m-head">Versions · newest first</div>' + vs.slice().reverse().map((v) => {
      const b = v.build === "ok" ? `<span class="tag ok">${icon("check")}builds</span>` : v.build === "failed" ? `<span class="tag bad">${icon("x")}failed</span>` : "";
      return `<button class="v-row" data-v="${v.n}"><span class="v-n">v${v.n}</span><span class="v-text"><span class="v-note">${esc(v.note || sourceLabel(v.source))}</span>
        <span class="v-sub">${esc(sourceLabel(v.source))} · ${esc(timeAgo(v.time))} ${b}</span></span>${v.n === cur.n ? '<span class="tag">current</span>' : ""}</button>`;
    }).join("");
  }

  // ---------------------------------------------------------- changes
  function showDiff(before, after, label, restoreN) {
    S.lastDiff = { before: before, after: after, label: label, restoreN: restoreN };
    if (!S.diffEditor) return;
    const m = S.monaco;
    const old = S.diffEditor.getModel();
    S.diffEditor.setModel({ original: m.editor.createModel(before || "", "c"), modified: m.editor.createModel(after || "", "c") });
    if (old) { old.original.dispose(); old.modified.dispose(); }
    $("diffLabel").textContent = label;
    $("diffEmpty").hidden = true;
    $("diffRestoreBtn").hidden = !restoreN;
    $("diffRestoreBtn").querySelector("span").textContent = restoreN ? "Restore v" + restoreN : "";
  }

  async function showLatestDiff() {
    const vs = (S.project && S.project.version_list) || [];
    if (vs.length < 2) { $("diffEmpty").hidden = false; $("diffLabel").textContent = "No changes yet"; $("diffRestoreBtn").hidden = true; return; }
    const cur = vs[vs.length - 1].n;
    const prev = await api(`/api/projects/${S.project.id}/versions/${vs[vs.length - 2].n}`);
    showDiff(prev.code, S.project.code, `v${vs[vs.length - 2].n} → v${cur}: what changed`, vs[vs.length - 2].n);
  }

  async function showVersionDiff(n) {
    const vs = (S.project && S.project.version_list) || [];
    const cur = vs.length ? vs[vs.length - 1].n : 0;
    setPanel(true);
    if (n === cur) { S.lastDiff = null; showTab("diff"); return; }
    const old = await api(`/api/projects/${S.project.id}/versions/${n}`);
    showDiff(old.code, S.project.code, `v${n} (left) compared with the current v${cur} (right)`, n);
    showTab("diff");
  }

  // ------------------------------------------------------- check & pins
  function scheduleCheck() {
    clearTimeout(S.checkTimer);
    S.checkTimer = setTimeout(() => runCheck(editorCode()), 1100);
  }
  async function runCheck(code) {
    if (!S.project) return;
    try {
      S.check = await api(`/api/projects/${S.project.id}/check`, { method: "POST", body: { code: code } });
    } catch (e) { return; }
    renderApiNote();
    renderPins();
    if (!S.busy) renderNotices();
  }

  function renderApiNote() {
    const el = $("apiNote");
    const a = S.check && S.check.api_check;
    if (!a) { el.className = "api-note"; el.innerHTML = ""; return; }
    if (a.unknown.length) { el.className = "api-note bad"; el.innerHTML = icon("alert") + `${plural(a.unknown.length, "function")} not in ESP-IDF: ${esc(a.unknown.slice(0, 3).join(", "))}`; }
    else if (a.deprecated.length) { el.className = "api-note warn"; el.innerHTML = icon("info") + `Old API: ${esc(a.deprecated.slice(0, 3).join(", "))}`; }
    else { el.className = "api-note ok"; el.innerHTML = icon("check-circle") + `All ${a.called} functions exist in ESP-IDF v5.5`; }
  }

  function renderPins() {
    const hw = (S.check && S.check.hardware) || { pins: [], findings: [], errors: 0, warnings: 0 };
    const dot = $("pinsDot");
    dot.classList.toggle("hidden", !hw.findings.length);
    dot.classList.toggle("err", hw.errors > 0);
    if (S.tab !== "pins") return;
    window.PinMap.render($("boardWrap"), hw, selectPin);
    $("pinsSummary").innerHTML = `
      <div class="stat"><div class="n">${hw.pins.length}</div><div class="l">pins used</div></div>
      <div class="stat ${hw.errors ? "err" : "ok"}"><div class="n">${hw.errors}</div><div class="l">${hw.errors === 1 ? "error" : "errors"}</div></div>
      <div class="stat ${hw.warnings ? "warn" : ""}"><div class="n">${hw.warnings}</div><div class="l">${hw.warnings === 1 ? "warning" : "warnings"}</div></div>` +
      (hw.wifi ? `<div class="stat"><div class="n" style="font-size:15px;padding-top:4px">Wi-Fi on</div><div class="l">ADC2 pins blocked</div></div>` : "");
    const f = hw.findings;
    $("findings").innerHTML = !editorCode().trim() ? "" : !f.length
      ? `<div class="all-good">${icon("check-circle")}No pin problems found. Still check your wiring before powering the board.</div>`
      : f.map((x) => `<div class="finding ${x.level}"><div class="f-head">${icon(x.level === "error" ? "alert" : "info")}<span>${esc(x.title)}</span></div>
          <div class="f-detail">${esc(x.detail)}</div><div class="f-fix"><b>Fix:</b> ${esc(x.fix)}</div>${x.line > 1 || x.pin != null ? `<div class="f-line">line ${x.line}</div>` : ""}</div>`).join("") +
        (hw.errors ? `<button class="btn sm clay" data-ask-fix="hw">${icon("wand")}Ask the AI to fix the pin problems</button>` : "");
    $("pinTable").innerHTML = hw.pins.length ? `<table><thead><tr><th>Pin</th><th>Used as</th><th>Line</th></tr></thead><tbody>` +
      hw.pins.map((p) => `<tr data-pin-row="${p.pin}" class="${S.pinSel === p.pin ? "sel" : ""}"><td>GPIO ${p.pin}</td><td>${esc(p.label)}${window.PinMap.NOTE[p.pin] ? `<div style="color:var(--text-3);font-size:12px">${esc(window.PinMap.NOTE[p.pin])}</div>` : ""}</td>
        <td><span class="lines" data-line="${Math.min.apply(null, p.lines)}">${p.lines.slice().sort((a, b) => a - b).slice(0, 4).join(", ")}</span></td></tr>`).join("") + "</tbody></table>" +
      '<p class="pins-note">Found by reading the code with fixed rules for the ESP32 DevKit (no AI). It cannot see your real wiring.</p>'
      : (editorCode().trim() ? '<p class="pins-note">This program does not set up any GPIO pins.</p>' : '<p class="pins-note">The pins your program uses will show here.</p>');
    if (S.pinSel != null) window.PinMap.highlight($("boardWrap"), S.pinSel);
  }

  function selectPin(pin) {
    S.pinSel = pin;
    window.PinMap.highlight($("boardWrap"), pin);
    document.querySelectorAll("[data-pin-row]").forEach((r) => r.classList.toggle("sel", +r.dataset.pinRow === pin));
    const row = document.querySelector(`[data-pin-row="${pin}"]`);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function gotoLine(line) {
    if (!S.editor || !line) return;
    setPanel(true, "code");
    setTimeout(() => { S.editor.revealLineInCenter(line); S.editor.setPosition({ lineNumber: line, column: 1 }); S.editor.focus(); }, 60);
  }

  // ---------------------------------------------------------- output
  function logLine(pane, text, cls) {
    const stick = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
    const span = document.createElement("span");
    if (!cls) {
      if (/\berror\b|FAILED|Guru Meditation|panic/i.test(text)) cls = "err";
      else if (/\bwarning\b/i.test(text)) cls = "warn";
      else if (/^[IWE] \(\d+\)/.test(text)) cls = text[0] === "E" ? "err" : text[0] === "W" ? "warn" : "";
    }
    if (cls) span.className = cls;
    span.textContent = text + "\n";
    pane.appendChild(span);
    while (pane.childNodes.length > 4000) pane.removeChild(pane.firstChild);
    if (stick) pane.scrollTop = pane.scrollHeight;
  }
  const outLine = (t, c) => logLine($("outputLog"), t, c);
  const serialLine = (t, c) => logLine($("serialLog"), t, c);

  function renderProblems(errors) {
    const c = $("problemCount");
    c.textContent = errors.length;
    c.classList.toggle("hidden", !errors.length);
    $("problems").innerHTML = errors.map((e, i) =>
      `<div class="problem" data-i="${i}"><span class="where">${e.file === "linker" ? "linker" : "line " + e.line}</span><span class="text">${esc(e.message)}</span>${e.hint ? `<span class="hint">${esc(e.hint)}</span>` : ""}</div>`).join("");
    $("problems").querySelectorAll(".problem").forEach((el) => {
      el.onclick = () => { const e = errors[+el.dataset.i]; if (e.line) gotoLine(e.line); };
    });
  }

  function job(text, kind, spinning) {
    const el = $("jobStatus");
    el.className = "job" + (kind ? " " + kind : "");
    el.innerHTML = (spinning ? '<span class="spinner"></span>' : "") + `<span>${esc(text || "")}</span>`;
    el.title = text || "";
  }

  // =============================================================== actions
  function makeHandler(turn) {
    let live = turn;
    return function (ev) {
      switch (ev.type) {
        case "status": if (live) live.status(ev.text); job(ev.text, "", true); break;
        case "context": if (live) live.context(ev.examples); break;
        case "token": if (live) live.token(ev.text, ev.kind); break;
        case "message_done": if (live) live.messageDone(ev.message); break;
        case "user_saved": break;
        case "version": onVersion(ev); if (live && ev.version.source !== "user") live.onVersion(ev); break;
        case "build_start":
          $("outputLog").textContent = "";
          job("Building v" + ev.version + "…", "", true);
          outLine("▶ Building version " + ev.version + " with ESP-IDF…", "dim");
          if (live) live.buildStart(ev);
          break;
        case "build_log": outLine(ev.line); break;
        case "build_result": onBuildResult(ev); if (live) live.buildResult(ev); break;
        case "fix_start":
          job("AI fix " + ev.attempt + "/" + ev.max + "…", "", true);
          if (live) { live.fixStart(ev); }
          break;
        case "sim_log": serialLine(ev.line); break;
        case "sim_result":
          serialLine("--- " + ev.message + " ---", ev.ok ? "ok" : "err");
          job(ev.ok ? "Simulation finished: no crash" : ev.message, ev.ok ? "ok" : "err");
          break;
        case "flash_log": outLine(ev.line); break;
        case "flash_result":
          outLine(ev.message, ev.ok ? "ok" : "err");
          job(ev.message, ev.ok ? "ok" : "err");
          if (ev.ok) toast("Flashed! Press Monitor to see the board's output.");
          break;
        case "error":
          if (live) live.error(ev.text); else toast(ev.text, "err");
          job(ev.text, "err");
          break;
        default: break;
      }
    };
  }

  function onVersion(ev) {
    S.savedCode = ev.code;
    endStreamingEditor(true);
    S.streamTouched = false;
    if (S.editor && S.editor.getValue() !== ev.code) S.editor.setValue(ev.code);
    updateDirty();
    setErrorMarkers([]);
    if (S.project) {
      S.project.version_list = (S.project.version_list || []).concat([ev.version]);
      S.project.code = ev.code;
    }
    renderVersions();
    updateProjectSummary();
    if (ev.version.source !== "user" && ev.previous != null) {
      const prevN = ev.version.n - 1;
      if (ev.previous.trim()) showDiff(ev.previous, ev.code, `v${prevN} → v${ev.version.n}: what changed`, prevN > 0 ? prevN : null);
    }
    if (ev.hardware || ev.api_check) {
      S.check = { api_check: ev.api_check, suggestions: ev.suggestions || {}, hardware: ev.hardware || { pins: [], findings: [] } };
      renderApiNote();
      renderPins();
    }
  }

  function onBuildResult(ev) {
    renderProblems(ev.errors || []);
    setErrorMarkers(ev.errors || []);
    if (ev.ok) {
      outLine("✔ Build succeeded in " + ev.seconds + " s" + (ev.size ? " · firmware " + ev.size : ""), "ok");
      job("v" + ev.version + " builds" + (ev.size ? " · " + ev.size : ""), "ok");
    } else if (ev.message) {
      outLine(ev.message, "err");
      job(ev.message, "err");
    } else {
      const n = (ev.errors || []).length;
      outLine("✘ Build failed" + (n ? " with " + plural(n, "error") : ""), "err");
      job("v" + ev.version + " build failed" + (n ? " · " + plural(n, "error") : ""), "err");
    }
    if (S.project && ev.version) {
      (S.project.version_list || []).forEach((v) => { if (v.n === ev.version) v.build = ev.ok ? "ok" : "failed"; });
      renderVersions();
      updateProjectSummary();
    }
  }

  async function runStream(path, body, opts) {
    if (S.busy) return;
    opts = opts || {};
    setBusy(true);
    const turn = opts.turn || null;
    S.liveTurn = turn;
    let stopped = false;
    try {
      await stream(path, body, makeHandler(turn));
    } catch (e) {
      if (e.name === "AbortError") { stopped = true; job("Stopped"); }
      else { if (turn) turn.error(e.message); else toast(e.message, "err"); job(e.message, "err"); }
    } finally {
      S.controller = null;
      setBusy(false);
      if (turn) turn.finish(stopped);
      S.liveTurn = null;
      await refreshProject().catch(() => {});
      if (turn) { await runCheck(S.project.code); renderNotices(turn.el.querySelector(".notice-slot")); scrollDown(); }
      else runCheck(editorCode());
      if (/ ok| err/.test($("jobStatus").className) === false && !stopped) job("");
    }
  }

  async function sendChat(text) {
    text = (text || $("chatInput").value).trim();
    if (!text || S.busy || !S.project) return;
    if (S.settings.provider === "groq" && !S.settings.groq_api_key_set) {
      toast("Add your free Groq API key in Settings (or pick the local model).", "err");
      openSettings();
      return;
    }
    $("chatInput").value = "";
    autosize();
    if (!$("home").hidden) { $("home").hidden = true; placeComposer(false); }
    thread().querySelectorAll(".notice-slot").forEach((s) => { s.innerHTML = ""; });
    const turn = new LiveTurn(text, { thinking: `Asking ${$("modelName").textContent}…` });
    S.project.chat = (S.project.chat || []).concat([{ role: "user", content: text }]);
    await runStream(`/api/projects/${S.project.id}/chat`, { message: text, code: editorCode() }, { turn: turn });
  }

  function askFix(kind) {
    const c = S.check || {};
    if (kind === "hw") {
      const errs = ((c.hardware || {}).findings || []).filter((f) => f.level === "error");
      if (!errs.length) return;
      sendChat("Fix these pin problems found by the hardware check:\n" + errs.map((f) => `- ${f.title}. ${f.fix}`).join("\n"));
    } else {
      const names = Object.keys(c.suggestions || {});
      if (!names.length) return;
      sendChat("These functions do not exist in ESP-IDF v5.5: " + names.join(", ") + ". Use the real ESP-IDF functions instead (for example " +
        names.map((n) => (c.suggestions[n][0] || {}).name).filter(Boolean).join(", ") + ").");
    }
  }

  async function saveEdits() {
    if (!S.project) return;
    const code = editorCode();
    if (code === S.savedCode) { toast("No changes to save"); return; }
    const res = await api(`/api/projects/${S.project.id}/code`, { method: "POST", body: { code: code } });
    S.project = res.project;
    S.savedCode = S.project.code;
    updateDirty();
    renderVersions();
    updateProjectSummary();
    toast("Saved as version " + S.project.versions);
  }

  async function restoreVersion(n) {
    if (!S.project || S.busy) return;
    const res = await api(`/api/projects/${S.project.id}/restore/${n}`, { method: "POST" });
    S.project = res.project;
    setEditorCode(S.project.code);
    setErrorMarkers([]);
    renderVersions();
    updateProjectSummary();
    S.lastDiff = null;
    showTab("code");
    runCheck(S.project.code);
    toast(res.version ? `Restored v${n} as v${res.version.n}` : "That is already the current code");
  }

  function doBuild(fix) {
    if (!S.project || S.busy) return;
    if (!editorCode().trim()) { toast("There is no code to build yet.", "err"); return; }
    setPanel(true);
    showTab("output");
    const turn = fix ? new LiveTurn("", { thinking: "Building, then fixing any errors with the AI…" }) : null;
    if (fix && !$("home").hidden) { $("home").hidden = true; placeComposer(false); }
    runStream(`/api/projects/${S.project.id}/build`, { code: editorCode(), fix: !!fix }, { turn: turn });
  }

  function explainCode() {
    if (!S.project || S.busy) return;
    if (!editorCode().trim()) { toast("There is no code to explain yet.", "err"); return; }
    if (!$("home").hidden) { $("home").hidden = true; placeComposer(false); }
    const turn = new LiveTurn("Explain this code", { thinking: "Reading the code…" });
    runStream(`/api/projects/${S.project.id}/explain`, { code: editorCode() }, { turn: turn });
  }

  function simulate() {
    if (!S.project || S.busy) return;
    $("serialLog").textContent = "";
    setPanel(true, "serial");
    serialLine("▶ Starting the ESP32 simulator (QEMU)…", "dim");
    runStream(`/api/projects/${S.project.id}/simulate`, { code: editorCode() });
  }

  function flash() {
    const port = $("portSelect").value;
    if (!port) { toast("Plug in the ESP32 with a USB cable, then press ↻ to find it.", "err"); return; }
    closeMonitor(true);
    setPanel(true, "output");
    runStream(`/api/projects/${S.project.id}/flash`, { code: editorCode(), port: port });
  }

  // --------------------------------------------------------------- monitor
  function toggleMonitor() { if (S.monitor) closeMonitor(); else openMonitor(); }
  function openMonitor() {
    const port = $("portSelect").value;
    if (!port) { toast("Plug in the ESP32 and pick its port first.", "err"); return; }
    setPanel(true, "serial");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/monitor?port=${encodeURIComponent(port)}&baud=${S.settings.serial_baud}`);
    S.monitor = ws;
    $("monitorLabel").textContent = "Stop monitor";
    $("monitorBtn").classList.add("live");
    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "line") serialLine(ev.text);
      else serialLine("--- " + ev.text + " ---", ev.type === "error" || ev.type === "closed" ? "err" : "ok");
      if (ev.type === "error" || ev.type === "closed") closeMonitor(true);
    };
    ws.onclose = () => closeMonitor(true);
  }
  function closeMonitor(silent) {
    if (S.monitor) { try { S.monitor.close(); } catch (e) { /* closed */ } }
    S.monitor = null;
    $("monitorLabel").textContent = "Monitor";
    $("monitorBtn").classList.remove("live");
    if (!silent) serialLine("--- monitor stopped ---", "dim");
  }
  async function loadPorts() {
    const sel = $("portSelect");
    try {
      const ports = await api("/api/ports");
      const prev = sel.value;
      sel.innerHTML = ports.length
        ? ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)}${p.likely_esp32 ? " · ESP32" : ""}</option>`).join("")
        : '<option value="">No board connected</option>';
      if (prev && ports.some((p) => p.device === prev)) sel.value = prev;
    } catch (e) { sel.innerHTML = '<option value="">Ports unavailable</option>'; }
  }

  // ----------------------------------------------------------------- board
  function partRow(p) {
    p = p || {};
    return `<div class="part-row"><input data-k="part" value="${esc(p.part || "")}" placeholder="e.g. DHT22 sensor">
      <input data-k="pins" value="${esc(p.pins || "")}" placeholder="e.g. DATA → GPIO 4"><input data-k="notes" value="${esc(p.notes || "")}" placeholder="optional">
      <button type="button" class="icon-btn" data-remove aria-label="Remove">${icon("x")}</button></div>`;
  }
  function openBoard() {
    if (!S.project) return;
    const b = S.project.board || {};
    const f = $("boardForm");
    f.name.value = b.name || "";
    f.notes.value = b.notes || "";
    $("partsBody").innerHTML = (b.parts && b.parts.length ? b.parts : [{}]).map(partRow).join("");
    $("boardDialog").returnValue = "";
    $("boardDialog").showModal();
  }
  async function saveBoard() {
    const f = $("boardForm");
    const parts = [...$("partsBody").querySelectorAll(".part-row")].map((row) => {
      const o = {};
      row.querySelectorAll("input").forEach((i) => { o[i.dataset.k] = i.value; });
      return o;
    }).filter((p) => p.part && p.part.trim());
    const updated = await api("/api/projects/" + S.project.id, { method: "PATCH", body: { board: { name: f.name.value, notes: f.notes.value, parts: parts } } });
    S.project.board = updated.board;
    renderBoardLabel();
    toast("Board saved. The AI will use these pins.");
  }

  // ============================================================== wiring up
  function bind() {
    // sidebar & layout
    $("sidebarClose").onclick = () => setSidebar(false);
    $("sidebarOpen").onclick = () => setSidebar(true);
    $("panelToggle").onclick = () => setPanel(!panelOpen());
    $("panelClose").onclick = () => setPanel(false);
    $("themeBtn").onclick = cycleTheme;
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
    $("newProjectBtn").onclick = newProject;
    $("projectSearch").addEventListener("input", renderProjectList);
    $("sbScroll").addEventListener("click", (e) => {
      const more = e.target.closest("[data-more]");
      if (more) {
        e.stopPropagation();
        const id = more.dataset.more;
        floatingMenu(more, [{ k: "rename", icon: "pencil", label: "Rename" }, "-", { k: "delete", icon: "trash", label: "Delete", danger: true }],
          (k) => (k === "rename" ? renameProject(id) : deleteProject(id)));
        return;
      }
      const item = e.target.closest(".project-item");
      if (item && (!S.project || item.dataset.id !== S.project.id)) openProject(item.dataset.id);
    });

    // top bar
    $("projectTitle").onclick = (e) => { e.stopPropagation(); toggleMenu($("projectMenu")); };
    $("projectMenu").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) projectAction(b.dataset.act); });
    $("boardBtn").onclick = openBoard;
    $("composerBoard").onclick = openBoard;

    // composer
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
    $("composer").addEventListener("click", (e) => { if (e.target === $("composer")) $("chatInput").focus(); });
    $("chatInput").addEventListener("input", autosize);
    $("chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
    });
    $("stopBtn").onclick = () => { if (S.controller) S.controller.abort(); };
    $("modelBtn").onclick = (e) => { e.stopPropagation(); toggleMenu($("modelMenu")); };
    $("modelMenu").addEventListener("click", (e) => { const b = e.target.closest("button[data-p]"); if (b) { closeMenus(); pickModel(b.dataset.p, b.dataset.m); } });
    $("suggestions").innerHTML = SUGGESTIONS.map(([ic, t], i) => `<button class="suggestion" style="animation-delay:${80 + i * 40}ms" data-s="${esc(t)}">${icon(ic)}${esc(t)}</button>`).join("");
    $("suggestions").addEventListener("click", (e) => { const b = e.target.closest("[data-s]"); if (b) sendChat(SUGGESTION_TEXT[b.dataset.s] || b.dataset.s); });

    // conversation
    $("conversation").addEventListener("click", (e) => {
      const sum = e.target.closest(".steps-summary");
      if (sum) { sum.parentElement.classList.toggle("open"); return; }
      const v = e.target.closest("[data-open-version]");
      if (v) {
        const n = +v.dataset.openVersion;
        const vs = S.project.version_list || [];
        setPanel(true, "code");
        if (n && vs.length && n !== vs[vs.length - 1].n) showVersionDiff(n);
        return;
      }
      if (e.target.closest("[data-open-code]")) { setPanel(true, "code"); return; }
      if (e.target.closest("[data-show-pins]")) { e.preventDefault(); setPanel(true, "pins"); return; }
      const fix = e.target.closest("[data-ask-fix]");
      if (fix) { askFix(fix.dataset.askFix); return; }
      const copy = e.target.closest("[data-copy-msg]");
      if (copy) {
        const m = (S.project.chat || []).find((x) => x.id === copy.dataset.copyMsg);
        if (m) navigator.clipboard.writeText(m.content).then(() => toast("Reply copied"), () => toast("Copy failed", "err"));
      }
    });
    $("conversation").addEventListener("scroll", () => {
      if (nearBottom()) S.follow = true;
      $("toBottom").hidden = nearBottom() || !$("home").hidden;
    });
    $("conversation").addEventListener("wheel", (e) => { if (e.deltaY < 0) S.follow = false; }, { passive: true });
    $("toBottom").onclick = () => scrollDown(true);

    // panel
    document.querySelectorAll("#panelTabs .seg-btn").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
    $("versionBtn").onclick = (e) => { e.stopPropagation(); toggleMenu($("versionMenu")); };
    $("versionMenu").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) { closeMenus(); showVersionDiff(+b.dataset.v); } });
    $("diffRestoreBtn").onclick = () => { if (S.lastDiff && S.lastDiff.restoreN) restoreVersion(S.lastDiff.restoreN); };
    $("copyBtn").onclick = () => navigator.clipboard.writeText(editorCode()).then(() => toast("Code copied"), () => toast("Copy failed", "err"));
    $("downloadBtn").onclick = () => {
      const blob = new Blob([editorCode()], { type: "text/x-c" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "main.c";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };
    $("undoBtn").onclick = () => { const vs = S.project.version_list || []; if (vs.length >= 2) restoreVersion(vs[vs.length - 2].n); };
    $("saveBtn").onclick = saveEdits;
    $("explainBtn").onclick = explainCode;
    $("buildBtn").onclick = () => doBuild(false);
    $("fixBtn").onclick = () => doBuild(true);
    $("simBtn").onclick = simulate;
    $("flashBtn").onclick = flash;
    $("monitorBtn").onclick = toggleMonitor;
    $("portRefresh").onclick = loadPorts;
    $("panel").addEventListener("click", (e) => {
      const f = e.target.closest("[data-ask-fix]");
      if (f) { askFix(f.dataset.askFix); return; }
      const l = e.target.closest("[data-line]");
      if (l) gotoLine(+l.dataset.line);
      const r = e.target.closest("[data-pin-row]");
      if (r && !l) selectPin(+r.dataset.pinRow);
    });
    $("serialForm").addEventListener("submit", (e) => {
      e.preventDefault();
      if (!S.monitor) { toast("Start the Monitor first.", "err"); return; }
      S.monitor.send(JSON.stringify({ type: "send", text: $("serialInput").value }));
      $("serialInput").value = "";
    });
    $("resetBoardBtn").onclick = () => { if (S.monitor) S.monitor.send(JSON.stringify({ type: "reset" })); else toast("Start the Monitor first.", "err"); };
    $("clearLogBtn").onclick = () => { $("serialLog").textContent = ""; };
    $("outputLog").setAttribute("data-empty", "Build output will appear here. Press Build, or ask the AI for code.");
    $("serialLog").setAttribute("data-empty", "Simulator and board output will appear here. Press Simulate, or flash a board and press Monitor.");

    // dialogs
    $("settingsBtn").onclick = openSettings;
    $("settingsDialog").addEventListener("close", () => { if ($("settingsDialog").returnValue === "save") saveSettings(); });
    $("testModelBtn").onclick = testModel;
    $("boardDialog").addEventListener("close", () => { if ($("boardDialog").returnValue === "save") saveBoard(); });
    $("addPartBtn").onclick = () => $("partsBody").insertAdjacentHTML("beforeend", partRow());
    $("partsBody").addEventListener("click", (e) => { if (e.target.closest("[data-remove]")) e.target.closest(".part-row").remove(); });

    // keyboard
    document.addEventListener("keydown", (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (e.key === "Escape") closeMenus();
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); newProject(); }
      else if (mod && e.key === "\\") { e.preventDefault(); setPanel(!panelOpen()); }
      else if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); doBuild(false); }
      else if (mod && e.key.toLowerCase() === "s" && !(e.target.closest && e.target.closest(".monaco-editor"))) { e.preventDefault(); saveEdits(); }
      else if (e.key === "/" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName) && !(document.activeElement.closest && document.activeElement.closest(".monaco-editor"))) { e.preventDefault(); $("chatInput").focus(); }
    });
    window.addEventListener("beforeunload", (e) => { if (S.editor && S.editor.getValue() !== S.savedCode && !S.streaming) { e.preventDefault(); e.returnValue = ""; } });
    window.addEventListener("resize", () => { layoutEditors(); });
    initSplitter();
  }

  async function main() {
    bind();
    applyTheme();
    if (store("adep.sidebar") === "0") $("app").classList.add("sb-closed");
    if (store("adep.panel") === "1") { $("app").classList.add("panel-open"); $("panelToggle").classList.add("on"); }
    const st = await loadStatus();
    await loadSettings();
    const editorReady = initEditor(st.monaco_local).catch(() => { $("editorLoading").innerHTML = "Could not load the code editor. Run Setup.bat again."; });
    await loadProjects();
    await editorReady;
    loadPorts();
    setInterval(() => { if (!S.busy) renderProjectList(); }, 60000);
    if (S.settings.provider === "groq" && !S.settings.groq_api_key_set) {
      setTimeout(() => toast("Welcome! Add your free Groq API key in Settings, or pick the local model."), 700);
    }
  }

  main();
})();
