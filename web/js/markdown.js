/* Tiny, safe Markdown renderer for chat messages.
   Everything is HTML-escaped first; only a few formats are turned back on:
   ``` code blocks ```, `inline code`, **bold**, *italic*, headings, lists, paragraphs. */
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s) {
    return s
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  }
  function render(md, options) {
    options = options || {};
    var out = [];
    var parts = String(md || "").split(/```/);
    for (var i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        // code block: first line is the language
        var body = parts[i].replace(/^[\w+-]*\r?\n/, "");
        if (options.codeBlock) { out.push(options.codeBlock(body, i === parts.length - 1)); }
        else { out.push("<pre><code>" + esc(body) + "</code></pre>"); }
        continue;
      }
      var lines = esc(parts[i]).split(/\r?\n/);
      var list = null, para = [];
      var flushPara = function () { if (para.length) { out.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } };
      var flushList = function () { if (list) { out.push("<" + list.type + ">" + list.items.map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + "</" + list.type + ">"); list = null; } };
      lines.forEach(function (line) {
        var m;
        if (/^\s*$/.test(line)) { flushPara(); flushList(); return; }
        if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flushPara(); flushList(); out.push("<h3>" + inline(m[2]) + "</h3>"); return; }
        if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); return; }
        if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); return; }
        flushList(); para.push(line);
      });
      flushPara(); flushList();
    }
    return out.join("");
  }
  window.Markdown = { render: render, escape: esc };
})();
