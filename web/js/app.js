/* AI-Driven Embedded Programmer - browser app (no build step, plain JS). */
(function () {
  "use strict";

  // ------------------------------------------------------------------ state
  const S = {
    projects: [],
    project: null,          // full project from the server
    editor: null,
    diffEditor: null,
    monaco: null,
    savedCode: "",          // code of the current version (to detect manual edits)
    busy: false,
    controller: null,
    monitor: null,
    settings: null,
    groqModels: [],
    lastChange: null,       // {before, after, n, label}
    diffRestore: null,      // version number the diff "undo" button restores
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => window.Markdown.escape(String(s == null ? "" : s));
  const EXAMPLES = [
    "Blink the onboard LED every 500 ms",
    "Read a DHT22 on GPIO 4 every 2 seconds and print temperature and humidity",
    "Fade an LED on GPIO 18 up and down with PWM",
    "Read a potentiometer on GPIO 34 and print the voltage in millivolts",
    "Toggle an LED on GPIO 4 each time the button on GPIO 27 is pressed, with debounce",
    "Sweep a servo on GPIO 13 from 0 to 180 degrees and back",
    "Measure distance with an HC-SR04 (trigger GPIO 5, echo GPIO 18) and print it in cm",
    "Connect to Wi-Fi and serve a web page with a button that turns an LED on GPIO 2 on and off",
  ];

  // ------------------------------------------------------------------- utils
  async function api(path, options) {
    options = options || {};
    const res = await fetch(path, {
      method: options.method || "GET",
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : {},
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).error || msg; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("json") ? res.json() : res;
  }

  async function stream(path, body, onEvent) {
    S.controller = new AbortController();
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal: S.controller.signal,
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

  let toastTimer = null;
  function toast(text, isError) {
    const t = $("toast");
    t.textContent = text;
    t.className = "toast" + (isError ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), isError ? 6000 : 3000);
  }

  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; }
    return null;
  }

  function askText(title, value) {
    return new Promise((resolve) => {
      const d = $("promptDialog");
      $("promptTitle").textContent = title;
      $("promptInput").value = value || "";
      d.returnValue = "";
      d.onclose = () => resolve(d.returnValue === "ok" ? $("promptInput").value.trim() : null);
      d.showModal();
      $("promptInput").select();
    });
  }

  function setBusy(busy, label) {
    S.busy = busy;
    ["sendBtn", "buildBtn", "fixBtn", "simBtn", "flashBtn", "explainBtn", "undoBtn", "diffUndoBtn"].forEach((id) => { $(id).disabled = busy; });
    $("stopBtn").classList.toggle("hidden", !busy);
    $("sendBtn").classList.toggle("hidden", busy);
    if (label !== undefined) jobStatus(label);
  }

  function jobStatus(text, kind) {
    const el = $("jobStatus");
    el.textContent = text || "";
    el.className = "job-status" + (kind ? " " + kind : "");
    el.title = text || "";
  }

  // ------------------------------------------------------------------ Monaco
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
    const monaco = window.monaco;
    S.monaco = monaco;
    monaco.editor.defineTheme("workbench", {
      base: "vs-dark", inherit: true, rules: [],
      colors: { "editor.background": "#161a23", "editorGutter.background": "#161a23", "editor.lineHighlightBackground": "#1d2230" },
    });
    S.editor = monaco.editor.create($("editor"), {
      value: "", language: "c", theme: "workbench", automaticLayout: true, fontSize: 13.5,
      fontFamily: "Cascadia Code, JetBrains Mono, Consolas, monospace", minimap: { enabled: false },
      scrollBeyondLastLine: false, tabSize: 4, renderWhitespace: "none", smoothScrolling: true,
      padding: { top: 8 },
    });
    S.diffEditor = monaco.editor.createDiffEditor($("diffEditor"), {
      theme: "workbench", automaticLayout: true, readOnly: true, renderSideBySide: true,
      fontSize: 13, minimap: { enabled: false }, originalEditable: false, scrollBeyondLastLine: false,
    });
    S.editor.onDidChangeModelContent(updateDirty);
    S.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveEdits);
    $("editorLoading").classList.add("hidden");
  }

  function setEditorCode(code) {
    S.savedCode = code || "";
    if (S.editor && S.editor.getValue() !== S.savedCode) S.editor.setValue(S.savedCode);
    updateDirty();
  }

  function editorCode() { return S.editor ? S.editor.getValue() : S.savedCode; }

  function updateDirty() {
    const dirty = S.editor && S.editor.getValue() !== S.savedCode && S.editor.getValue().trim() !== "";
    $("dirtyBadge").classList.toggle("hidden", !dirty);
    $("editorEmpty").classList.toggle("hidden", !S.editor || S.editor.getValue().trim() !== "");
  }

  function setErrorMarkers(errors) {
    if (!S.monaco || !S.editor) return;
    const markers = (errors || []).filter((e) => e.file === "main.c" && e.line > 0).map((e) => ({
      startLineNumber: e.line, endLineNumber: e.line, startColumn: Math.max(1, e.column), endColumn: 1000,
      message: e.message + (e.hint ? "\n" + e.hint : ""), severity: S.monaco.MarkerSeverity.Error,
    }));
    S.monaco.editor.setModelMarkers(S.editor.getModel(), "build", markers);
  }

  // ---------------------------------------------------------------- diff view
  function showDiff(before, after, label, restoreN) {
    if (!S.diffEditor) return;
    const m = S.monaco;
    S.diffEditor.setModel({ original: m.editor.createModel(before || "", "c"), modified: m.editor.createModel(after || "", "c") });
    $("diffLabel").textContent = label;
    S.diffRestore = restoreN;
    $("diffUndoBtn").textContent = restoreN ? "Restore v" + restoreN : "Undo this change";
    $("diffUndoBtn").classList.toggle("hidden", !restoreN);
    $("diffBar").classList.remove("hidden");
    $("diffEditor").classList.remove("hidden");
    $("editor").classList.add("hidden");
  }

  function hideDiff() {
    $("diffBar").classList.add("hidden");
    $("diffEditor").classList.add("hidden");
    $("editor").classList.remove("hidden");
    if (S.editor) S.editor.layout();
  }

  // ---------------------------------------------------------------- status
  async function loadStatus() {
    try {
      const st = await api("/api/status");
      const pill = $("idfPill");
      if (st.esp_idf.found) {
        pill.textContent = "ESP-IDF v" + st.esp_idf.version + (st.esp_idf.qemu ? "" : " · no simulator");
        pill.className = "pill ok";
        pill.title = st.esp_idf.path + (st.esp_idf.qemu ? "" : "\nSimulator (QEMU) not installed - run Setup.bat");
      } else {
        pill.textContent = "ESP-IDF not found";
        pill.className = "pill bad";
        pill.title = st.esp_idf.message;
      }
      return st;
    } catch (e) {
      return { monaco_local: false };
    }
  }

  // --------------------------------------------------------------- settings
  async function loadSettings() {
    const data = await api("/api/settings");
    S.settings = data.settings;
    S.groqModels = data.groq_models;
    renderModelSelect();
  }

  function renderModelSelect() {
    const sel = $("modelSelect");
    const s = S.settings;
    const opts = S.groqModels.map((m) => `<option value="groq|${esc(m.id)}">☁ ${esc(m.label)}</option>`);
    opts.push(`<option value="ollama|${esc(s.ollama_model)}">💻 Local: ${esc(s.ollama_model)} (offline)</option>`);
    sel.innerHTML = opts.join("");
    sel.value = s.provider === "ollama" ? "ollama|" + s.ollama_model : "groq|" + s.groq_model;
    if (!sel.value) sel.selectedIndex = 0;
  }

  async function onModelChange() {
    const [provider, model] = $("modelSelect").value.split("|");
    const changes = { provider: provider };
    if (provider === "groq") changes.groq_model = model; else changes.ollama_model = model;
    const data = await api("/api/settings", { method: "PUT", body: changes });
    S.settings = data.settings;
    if (provider === "groq" && !S.settings.groq_api_key_set) {
      toast("Add your free Groq API key in Settings first.", true);
      openSettings();
    } else {
      toast("Model: " + $("modelSelect").selectedOptions[0].textContent.replace(/^\S+\s/, ""));
    }
  }

  function openSettings() {
    const f = $("settingsForm");
    const s = S.settings;
    f.groq_model.innerHTML = S.groqModels.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
    for (const el of f.elements) {
      if (!el.name || !(el.name in s)) continue;
      if (el.type === "checkbox") el.checked = !!s[el.name];
      else el.value = s[el.name];
    }
    f.groq_api_key.value = "";
    f.groq_api_key.placeholder = s.groq_api_key_set ? "Key saved (" + s.groq_api_key + ") - paste a new one to replace it" : "Paste your free key from console.groq.com/keys";
    $("testResult").textContent = "";
    loadStatus().then((st) => {
      $("idfInfo").textContent = st.esp_idf && st.esp_idf.found
        ? "ESP-IDF v" + st.esp_idf.version + " found at " + st.esp_idf.path + " (via " + st.esp_idf.found_via + ")."
          + (st.esp_idf.qemu ? " Simulator ready." : " Simulator not installed.")
        : (st.esp_idf ? st.esp_idf.message : "");
    });
    $("settingsDialog").returnValue = "";
    $("settingsDialog").showModal();
  }

  function settingsFromForm() {
    const f = $("settingsForm");
    const out = {};
    for (const el of f.elements) {
      if (!el.name) continue;
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    }
    return out;
  }

  async function saveSettings() {
    const data = await api("/api/settings", { method: "PUT", body: settingsFromForm() });
    S.settings = data.settings;
    renderModelSelect();
    loadStatus();
    toast("Settings saved");
  }

  async function testModel() {
    const r = $("testResult");
    r.className = "note"; r.textContent = "Testing…";
    try {
      await api("/api/settings", { method: "PUT", body: settingsFromForm() });
      const res = await api("/api/settings/test", { method: "POST" });
      r.textContent = res.message;
      r.className = "note " + (res.ok ? "ok" : "err");
      const data = await api("/api/settings");
      S.settings = data.settings;
      renderModelSelect();
    } catch (e) { r.textContent = e.message; r.className = "note err"; }
  }

  // --------------------------------------------------------------- projects
  async function loadProjects(selectId) {
    S.projects = await api("/api/projects");
    if (!S.projects.length) {
      const p = await api("/api/projects", { method: "POST", body: { name: "My first project" } });
      S.projects = [p];
      selectId = p.id;
    }
    const sel = $("projectSelect");
    sel.innerHTML = S.projects.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}${p.versions ? " · v" + p.versions : ""}</option>`).join("");
    const want = selectId || store("adep.project") || S.projects[0].id;
    sel.value = S.projects.some((p) => p.id === want) ? want : S.projects[0].id;
    await openProject(sel.value);
  }

  async function openProject(id) {
    if (S.monitor) closeMonitor();
    S.project = await api("/api/projects/" + id);
    store("adep.project", id);
    S.lastChange = null;
    hideDiff();
    setEditorCode(S.project.code);
    setErrorMarkers([]);
    renderVersions();
    renderChat();
    $("apiBadge").classList.add("hidden");
    jobStatus("");
  }

  async function refreshProject() {
    if (!S.project) return;
    const keepEdits = S.editor && S.editor.getValue() !== S.savedCode ? S.editor.getValue() : null;
    S.project = await api("/api/projects/" + S.project.id);
    S.savedCode = S.project.code;
    if (keepEdits === null) setEditorCode(S.project.code); else updateDirty();
    renderVersions();
    renderChat();
    const sel = $("projectSelect").selectedOptions[0];
    if (sel) sel.textContent = S.project.name + (S.project.versions ? " · v" + S.project.versions : "");
  }

  function sourceLabel(src) {
    return { ai: "AI", fix: "AI fix", user: "your edit", restore: "restored" }[src] || src;
  }

  function renderVersions() {
    const vs = (S.project && S.project.version_list) || [];
    const label = $("versionLabel");
    if (!vs.length) {
      label.textContent = "no code yet"; label.className = "badge";
      $("versionSelect").innerHTML = "<option>No versions yet</option>";
      $("undoBtn").disabled = true; $("diffBtn").disabled = true;
      return;
    }
    const cur = vs[vs.length - 1];
    label.textContent = "v" + cur.n + " · " + sourceLabel(cur.source) + (cur.build === "ok" ? " · builds ✓" : cur.build === "failed" ? " · build failed" : "");
    label.className = "badge" + (cur.build === "ok" ? " ok" : cur.build === "failed" ? " err" : "");
    $("versionSelect").innerHTML = vs.slice().reverse().map((v) =>
      `<option value="${v.n}">v${v.n} · ${esc(sourceLabel(v.source))}${v.build === "ok" ? " ✓" : v.build === "failed" ? " ✗" : ""} · ${esc((v.note || "").slice(0, 40))}</option>`).join("");
    $("undoBtn").disabled = S.busy || vs.length < 2;
    $("diffBtn").disabled = vs.length < 2 && !S.lastChange;
  }

  // ------------------------------------------------------------------ chat
  function codeCard(version, pending, lines) {
    if (pending) {
      return `<div class="code-card pending"><span class="title">main.c</span><span class="meta">writing… ${lines || 0} lines</span><span class="typing"></span></div>`;
    }
    if (!version) return `<div class="code-card"><span class="title">main.c</span><span class="meta">(not applied)</span></div>`;
    return `<div class="code-card"><span class="title">main.c · v${version}</span><span class="meta">full file updated</span>
      <span class="spacer"></span><button class="btn small ghost" data-diff="${version}">Changes</button><button class="btn small ghost" data-restore="${version}">Restore</button></div>`;
  }

  function messageHtml(m, streaming) {
    const kind = m.kind || "chat";
    if (m.role === "user" && kind === "fix") {
      return `<div class="msg fix"><div class="bubble">🔧 Auto-fix ${m.attempt || ""}: sent the build errors to the AI.</div></div>`;
    }
    if (m.role === "user" && kind === "explain") {
      return `<div class="msg note"><div class="bubble">💡 Explain the code</div></div>`;
    }
    if (m.role === "user") {
      return `<div class="msg user"><div class="who">You</div><div class="bubble">${esc(m.content)}</div></div>`;
    }
    const html = window.Markdown.render(m.content, {
      codeBlock: (body, unfinished) => {
        if (/app_main/.test(body) || streaming) return codeCard(m.version, streaming && unfinished, body.split("\n").length);
        return "<pre><code>" + esc(body) + "</code></pre>";
      },
    });
    const who = kind === "fix" ? "AI fix" : kind === "explain" ? "Explanation" : "AI";
    const meta = m.model ? ` · ${esc(m.model.split(":").slice(1).join(":"))}${m.seconds ? " · " + m.seconds + " s" : ""}` : "";
    const cls = kind === "fix" ? "msg assistant fix" : "msg assistant";
    return `<div class="${cls}"><div class="who">${who}${meta}</div><div class="bubble">${html}${streaming ? '<span class="typing"></span>' : ""}</div></div>`;
  }

  function renderChat() {
    const box = $("messages");
    const chat = (S.project && S.project.chat) || [];
    box.innerHTML = chat.map((m) => messageHtml(m)).join("");
    $("emptyChat").classList.toggle("hidden", chat.length > 0);
    box.scrollTop = box.scrollHeight;
  }

  function appendHtml(html) {
    $("emptyChat").classList.add("hidden");
    const box = $("messages");
    const wrap = document.createElement("div");
    wrap.innerHTML = html;
    const el = wrap.firstElementChild;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
    return el;
  }

  function nearBottom() {
    const b = $("messages");
    return b.scrollHeight - b.scrollTop - b.clientHeight < 120;
  }

  // A live assistant message that fills in as tokens arrive.
  function liveMessage(kind) {
    const el = appendHtml(`<div class="msg assistant${kind === "fix" ? " fix" : ""}"><div class="who">${kind === "fix" ? "AI fix" : kind === "explain" ? "Explanation" : "AI"}</div><div class="bubble"><div class="status-line"><span class="dots">Thinking</span></div></div></div>`);
    let text = "", timer = null, contextNote = null;
    const draw = () => {
      timer = null;
      const stick = nearBottom();
      const tmp = document.createElement("div");
      tmp.innerHTML = messageHtml({ role: "assistant", kind: kind, content: text }, true);
      const fresh = tmp.firstElementChild;
      el.className = fresh.className;
      el.innerHTML = fresh.innerHTML;
      if (contextNote) el.querySelector(".who").after(contextNote);
      if (stick) $("messages").scrollTop = $("messages").scrollHeight;
    };
    return {
      add(t) { text += t; if (!timer) timer = setTimeout(draw, 50); },
      status(t) {
        const line = el.querySelector(".status-line");
        if (line) line.innerHTML = `<span class="dots">${esc(t)}</span>`;
      },
      context(list) {
        if (!list || !list.length) return;
        contextNote = document.createElement("div");
        contextNote.className = "examples-used";
        contextNote.textContent = "📚 Using ESP-IDF examples: " + list.map((x) => x.path.replace(/^examples\//, "")).join(", ");
        el.querySelector(".who").after(contextNote);
      },
      done() { clearTimeout(timer); if (text) draw(); el.querySelectorAll(".typing").forEach((x) => x.remove()); },
    };
  }

  // ------------------------------------------------------------ event handler
  function makeHandler() {
    let live = null;
    let liveKind = null;
    let pendingContext = null;
    const ensureLive = (kind) => {
      if (!live || liveKind !== kind) { live = liveMessage(kind); liveKind = kind; if (pendingContext) { live.context(pendingContext); pendingContext = null; } }
      return live;
    };
    return function (ev) {
      switch (ev.type) {
        case "status":
          if (live) live.status(ev.text); else jobStatus(ev.text);
          if (/limit|retry/i.test(ev.text)) jobStatus(ev.text, "");
          break;
        case "context": ensureLive("chat").context(ev.examples); break;
        case "token": ensureLive(ev.kind || "chat").add(ev.text); break;
        case "message_done": if (live) live.done(); live = null; break;
        case "version": onVersion(ev); break;
        case "build_start":
          $("outputPane").innerHTML = "";
          showTab("output");
          jobStatus("Building v" + ev.version + "…");
          outLine("▶ Building version " + ev.version + " with ESP-IDF…", "dim");
          break;
        case "build_log": outLine(ev.line); break;
        case "build_result": onBuildResult(ev); break;
        case "fix_start":
          appendHtml(`<div class="msg fix"><div class="bubble">🔧 Auto-fix ${ev.attempt}/${ev.max}: sending ${ev.errors} error${ev.errors === 1 ? "" : "s"} to the AI…</div></div>`);
          jobStatus("Auto-fix " + ev.attempt + "/" + ev.max + "…");
          live = null;
          break;
        case "sim_log": serialLine(ev.line); break;
        case "sim_result":
          serialLine("--- " + ev.message + " ---", ev.ok ? "ok" : "err");
          jobStatus(ev.ok ? "Simulation finished: no crash" : ev.message, ev.ok ? "ok" : "err");
          break;
        case "flash_log": outLine(ev.line); break;
        case "flash_result":
          outLine(ev.message, ev.ok ? "ok" : "err");
          jobStatus(ev.message, ev.ok ? "ok" : "err");
          if (ev.ok) toast("Flashed! Click Monitor to see the board's output.");
          break;
        case "error":
          if (live) live.done();
          appendHtml(`<div class="msg assistant error"><div class="bubble">⚠ ${esc(ev.text)}</div></div>`);
          jobStatus(ev.text, "err");
          live = null;
          break;
        default: break;
      }
    };
  }

  function onVersion(ev) {
    const before = ev.previous != null ? ev.previous : S.savedCode;
    S.savedCode = ev.code;
    if (S.editor) {
      S.editor.setValue(ev.code);
      updateDirty();
    }
    setErrorMarkers([]);
    if (S.project) {
      S.project.version_list = (S.project.version_list || []).concat([ev.version]);
      S.project.code = ev.code;
    }
    renderVersions();
    if (ev.version.source !== "user") {
      S.lastChange = { before: before, after: ev.code, n: ev.version.n, label: "What changed in v" + ev.version.n };
      $("diffBtn").disabled = false;
    }
    const api = ev.api_check;
    const badge = $("apiBadge");
    if (api && api.unknown && api.unknown.length) {
      badge.textContent = api.unknown.length + " unknown function" + (api.unknown.length > 1 ? "s" : "") + ": " + api.unknown.slice(0, 3).join(", ");
      badge.className = "badge err";
      badge.title = "These names do not exist in ESP-IDF v5.5: " + api.unknown.join(", ");
    } else if (api && api.deprecated && api.deprecated.length) {
      badge.textContent = "old API: " + api.deprecated.slice(0, 2).join(", ");
      badge.className = "badge warn";
      badge.title = "Deprecated in ESP-IDF v5: " + api.deprecated.join(", ");
    } else if (api) {
      badge.textContent = "✓ " + api.called + " functions checked";
      badge.className = "badge ok";
      badge.title = "Every called function exists in the installed ESP-IDF.";
    } else {
      badge.className = "badge hidden";
    }
  }

  function onBuildResult(ev) {
    renderProblems(ev.errors || []);
    setErrorMarkers(ev.errors || []);
    if (ev.ok) {
      outLine("✔ Build succeeded in " + ev.seconds + " s" + (ev.size ? " · firmware " + ev.size : ""), "ok");
      jobStatus("✔ v" + ev.version + " builds" + (ev.size ? " · " + ev.size : ""), "ok");
    } else if (ev.message) {
      outLine(ev.message, "err");
      jobStatus(ev.message, "err");
    } else {
      const n = (ev.errors || []).length;
      outLine("✘ Build failed" + (n ? " with " + n + " error" + (n > 1 ? "s" : "") : ""), "err");
      jobStatus("✘ v" + ev.version + " build failed" + (n ? " · " + n + " error" + (n > 1 ? "s" : "") : ""), "err");
      if (n) showTab("problems");
    }
    if (S.project && ev.version) {
      (S.project.version_list || []).forEach((v) => { if (v.n === ev.version) v.build = ev.ok ? "ok" : "failed"; });
      renderVersions();
    }
  }

  // ---------------------------------------------------------- bottom panel
  function showTab(name) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $("outputPane").classList.toggle("hidden", name !== "output");
    $("problemsPane").classList.toggle("hidden", name !== "problems");
    $("serialPane").classList.toggle("hidden", name !== "serial");
  }

  function paneLine(pane, text, cls) {
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
  const outLine = (t, c) => paneLine($("outputPane"), t, c);
  const serialLine = (t, c) => paneLine($("serialLog"), t, c);

  function renderProblems(errors) {
    const pane = $("problemsPane");
    const count = $("problemCount");
    count.textContent = errors.length;
    count.classList.toggle("hidden", !errors.length);
    if (!errors.length) { pane.innerHTML = '<div class="dim">No problems.</div>'; return; }
    pane.innerHTML = errors.map((e, i) =>
      `<div class="problem" data-i="${i}"><span class="where">${e.file === "linker" ? "linker" : "line " + e.line}</span><span class="text">${esc(e.message)}</span>${e.hint ? `<span class="hint">💡 ${esc(e.hint)}</span>` : ""}</div>`).join("");
    pane.querySelectorAll(".problem").forEach((el) => {
      el.onclick = () => {
        const e = errors[+el.dataset.i];
        if (e.line && S.editor) { hideDiff(); S.editor.revealLineInCenter(e.line); S.editor.setPosition({ lineNumber: e.line, column: e.column || 1 }); S.editor.focus(); }
      };
    });
  }

  // --------------------------------------------------------------- actions
  async function runStream(path, body, label) {
    if (S.busy) return;
    setBusy(true, label);
    try {
      await stream(path, body, makeHandler());
    } catch (e) {
      if (e.name === "AbortError") { jobStatus("Stopped"); appendHtml('<div class="msg note"><div class="bubble">⏹ Stopped.</div></div>'); }
      else { toast(e.message, true); jobStatus(e.message, "err"); }
    } finally {
      setBusy(false);
      S.controller = null;
      await refreshProject().catch(() => {});
    }
  }

  async function sendChat(text) {
    text = (text || $("chatInput").value).trim();
    if (!text || S.busy || !S.project) return;
    if (S.settings.provider === "groq" && !S.settings.groq_api_key_set) {
      toast("Add your free Groq API key in Settings (or switch to the local model).", true);
      openSettings();
      return;
    }
    $("chatInput").value = "";
    appendHtml(messageHtml({ role: "user", content: text }));
    hideDiff();
    await runStream(`/api/projects/${S.project.id}/chat`, { message: text, code: editorCode() }, "AI is writing…");
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
    toast("Saved as v" + S.project.versions);
  }

  async function restoreVersion(n) {
    if (!S.project || S.busy) return;
    const res = await api(`/api/projects/${S.project.id}/restore/${n}`, { method: "POST" });
    S.project = res.project;
    hideDiff();
    setEditorCode(S.project.code);
    setErrorMarkers([]);
    renderVersions();
    renderChat();
    toast(res.version ? "Restored v" + n + " as v" + res.version.n : "Already the current code");
  }

  async function showVersionDiff(n) {
    if (!S.project) return;
    n = +n;
    const vs = S.project.version_list || [];
    const cur = vs.length ? vs[vs.length - 1].n : 0;
    if (n === cur && n > 1) {
      const prev = await api(`/api/projects/${S.project.id}/versions/${n - 1}`);
      showDiff(prev.code, S.project.code, `v${n - 1} → v${n}: what changed`, n - 1);
    } else if (n !== cur) {
      const old = await api(`/api/projects/${S.project.id}/versions/${n}`);
      showDiff(old.code, S.project.code, `v${n} (left) compared with current v${cur} (right)`, n);
    }
  }

  // --------------------------------------------------------------- monitor
  function toggleMonitor() { if (S.monitor) closeMonitor(); else openMonitor(); }

  function openMonitor() {
    const port = $("portSelect").value;
    if (!port) { toast("Plug in the ESP32 and pick its port first.", true); return; }
    showTab("serial");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/monitor?port=${encodeURIComponent(port)}&baud=${S.settings.serial_baud}`);
    S.monitor = ws;
    $("monitorBtn").textContent = "■ Stop monitor";
    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "line") serialLine(ev.text);
      else serialLine("--- " + ev.text + " ---", ev.type === "error" || ev.type === "closed" ? "err" : "ok");
      if (ev.type === "error" || ev.type === "closed") closeMonitor(true);
    };
    ws.onclose = () => closeMonitor(true);
  }

  function closeMonitor(silent) {
    if (S.monitor) { try { S.monitor.close(); } catch (e) { /* ignore */ } }
    S.monitor = null;
    $("monitorBtn").textContent = "⌁ Monitor";
    if (!silent) serialLine("--- monitor stopped ---", "dim");
  }

  async function loadPorts() {
    const sel = $("portSelect");
    try {
      const ports = await api("/api/ports");
      const prev = sel.value;
      sel.innerHTML = ports.length
        ? ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)}${p.likely_esp32 ? " · ESP32?" : ""} ${esc((p.description || "").slice(0, 28))}</option>`).join("")
        : '<option value="">No board found</option>';
      if (prev && ports.some((p) => p.device === prev)) sel.value = prev;
    } catch (e) { sel.innerHTML = '<option value="">Ports unavailable</option>'; }
  }

  // ----------------------------------------------------------------- board
  function partRow(p) {
    p = p || {};
    return `<tr><td><input data-k="part" value="${esc(p.part || "")}" placeholder="e.g. DHT22 sensor"></td>
      <td><input data-k="pins" value="${esc(p.pins || "")}" placeholder="e.g. DATA → GPIO 4"></td>
      <td><input data-k="notes" value="${esc(p.notes || "")}" placeholder="optional"></td>
      <td><button type="button" class="btn ghost small" data-remove>✕</button></td></tr>`;
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
    const parts = [...$("partsBody").querySelectorAll("tr")].map((tr) => {
      const o = {};
      tr.querySelectorAll("input").forEach((i) => { o[i.dataset.k] = i.value; });
      return o;
    }).filter((p) => p.part && p.part.trim());
    S.project = await api("/api/projects/" + S.project.id, { method: "PATCH", body: { board: { name: f.name.value, notes: f.notes.value, parts: parts } } });
    toast("Board saved. The AI will use these pins.");
  }

  // ------------------------------------------------------------ project menu
  async function projectAction(act) {
    $("projectMenu").classList.add("hidden");
    const p = S.project;
    if (!p) return;
    if (act === "rename") {
      const name = await askText("Rename project", p.name);
      if (name) { await api("/api/projects/" + p.id, { method: "PATCH", body: { name: name } }); await loadProjects(p.id); }
    } else if (act === "board") openBoard();
    else if (act === "export") { location.href = `/api/projects/${p.id}/export`; }
    else if (act === "vscode" || act === "folder") {
      const r = await api(`/api/projects/${p.id}/open-folder`, { method: "POST", body: { app: act === "vscode" ? "vscode" : "folder" } });
      toast(r.message, !r.ok);
    } else if (act === "clear") {
      S.project = await api(`/api/projects/${p.id}/clear-chat`, { method: "POST" });
      renderChat();
      toast("Chat cleared. The code and versions are kept.");
    } else if (act === "delete") {
      const typed = await askText(`Type DELETE to remove "${p.name}" and all its versions`, "");
      if (typed === "DELETE") { await api("/api/projects/" + p.id, { method: "DELETE" }); store("adep.project", ""); await loadProjects(); toast("Project deleted"); }
    }
  }

  // ----------------------------------------------------------------- layout
  function initSplitter() {
    const saved = store("adep.chatWidth");
    if (saved) document.documentElement.style.setProperty("--chat-width", saved);
    const bar = $("splitter");
    bar.addEventListener("mousedown", (e) => {
      e.preventDefault();
      bar.classList.add("dragging");
      const move = (ev) => {
        const w = Math.min(Math.max(ev.clientX, 300), window.innerWidth * 0.7) + "px";
        document.documentElement.style.setProperty("--chat-width", w);
      };
      const up = () => {
        bar.classList.remove("dragging");
        store("adep.chatWidth", getComputedStyle(document.documentElement).getPropertyValue("--chat-width").trim());
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }

  // ------------------------------------------------------------------ wire up
  function bind() {
    $("chatForm").addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
    $("chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
    $("stopBtn").onclick = () => { if (S.controller) S.controller.abort(); };
    $("exampleChips").innerHTML = EXAMPLES.map((t) => `<button type="button" class="chip">${esc(t)}</button>`).join("");
    $("exampleChips").addEventListener("click", (e) => { if (e.target.classList.contains("chip")) sendChat(e.target.textContent); });

    $("messages").addEventListener("click", (e) => {
      const d = e.target.closest("[data-diff]");
      const r = e.target.closest("[data-restore]");
      if (d) showVersionDiff(d.dataset.diff);
      if (r) restoreVersion(+r.dataset.restore);
    });

    $("projectSelect").onchange = (e) => openProject(e.target.value);
    $("newProjectBtn").onclick = async () => {
      const name = await askText("New project name", "");
      if (name === null) return;
      const p = await api("/api/projects", { method: "POST", body: { name: name || "Untitled project" } });
      await loadProjects(p.id);
      $("chatInput").focus();
    };
    $("projectMenuBtn").onclick = (e) => { e.stopPropagation(); $("projectMenu").classList.toggle("hidden"); };
    $("projectMenu").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) projectAction(b.dataset.act); });
    document.addEventListener("click", () => $("projectMenu").classList.add("hidden"));

    $("modelSelect").onchange = onModelChange;
    $("settingsBtn").onclick = openSettings;
    $("settingsDialog").addEventListener("close", () => { if ($("settingsDialog").returnValue === "save") saveSettings(); });
    $("testModelBtn").onclick = testModel;
    $("boardBtn").onclick = openBoard;
    $("boardDialog").addEventListener("close", () => { if ($("boardDialog").returnValue === "save") saveBoard(); });
    $("addPartBtn").onclick = () => $("partsBody").insertAdjacentHTML("beforeend", partRow());
    $("partsBody").addEventListener("click", (e) => { if (e.target.closest("[data-remove]")) e.target.closest("tr").remove(); });

    $("saveBtn").onclick = saveEdits;
    $("copyBtn").onclick = async () => { try { await navigator.clipboard.writeText(editorCode()); toast("Code copied"); } catch (e) { toast("Copy failed", true); } };
    $("undoBtn").onclick = () => {
      const vs = (S.project && S.project.version_list) || [];
      if (vs.length >= 2) restoreVersion(vs[vs.length - 2].n);
    };
    $("diffBtn").onclick = () => {
      if (S.lastChange) showDiff(S.lastChange.before, S.lastChange.after, S.lastChange.label, S.lastChange.n > 1 ? S.lastChange.n - 1 : null);
      else { const vs = S.project.version_list; if (vs.length >= 2) showVersionDiff(vs[vs.length - 1].n); }
    };
    $("diffCloseBtn").onclick = hideDiff;
    $("diffUndoBtn").onclick = () => { if (S.diffRestore) restoreVersion(S.diffRestore); };
    $("versionSelect").onchange = (e) => showVersionDiff(e.target.value);
    $("explainBtn").onclick = () => runStream(`/api/projects/${S.project.id}/explain`, { code: editorCode() }, "Explaining…");

    $("buildBtn").onclick = () => runStream(`/api/projects/${S.project.id}/build`, { code: editorCode(), fix: false }, "Building…");
    $("fixBtn").onclick = () => runStream(`/api/projects/${S.project.id}/build`, { code: editorCode(), fix: true }, "Building, then fixing errors with the AI…");
    $("simBtn").onclick = () => { $("serialLog").innerHTML = ""; showTab("serial"); runStream(`/api/projects/${S.project.id}/simulate`, { code: editorCode() }, "Simulating…"); };
    $("flashBtn").onclick = () => {
      const port = $("portSelect").value;
      if (!port) { toast("Plug in the ESP32 and pick its port first.", true); return; }
      closeMonitor(true);
      runStream(`/api/projects/${S.project.id}/flash`, { code: editorCode(), port: port }, "Flashing " + port + "…");
    };
    $("monitorBtn").onclick = toggleMonitor;
    $("portRefresh").onclick = loadPorts;
    $("serialForm").addEventListener("submit", (e) => {
      e.preventDefault();
      if (!S.monitor) { toast("Start the Monitor first.", true); return; }
      S.monitor.send(JSON.stringify({ type: "send", text: $("serialInput").value }));
      $("serialInput").value = "";
    });
    $("resetBoardBtn").onclick = () => { if (S.monitor) S.monitor.send(JSON.stringify({ type: "reset" })); else toast("Start the Monitor first.", true); };

    document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => showTab(t.dataset.tab); });
    $("clearPanel").onclick = () => { $("outputPane").innerHTML = ""; $("serialLog").innerHTML = ""; };
    window.addEventListener("beforeunload", (e) => {
      if (S.editor && S.editor.getValue() !== S.savedCode) { e.preventDefault(); e.returnValue = ""; }
    });
    initSplitter();
  }

  async function main() {
    bind();
    const st = await loadStatus();
    await loadSettings();
    try { await initEditor(st.monaco_local); } catch (e) { $("editorLoading").textContent = "Could not load the code editor. Run Setup.bat again."; }
    await loadProjects();
    loadPorts();
    renderProblems([]);
    if (S.settings.provider === "groq" && !S.settings.groq_api_key_set) {
      setTimeout(() => toast("Welcome! Add your free Groq API key in Settings to start (or choose the local model)."), 600);
    }
  }

  main();
})();
