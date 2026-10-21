/* Tiny, safe Markdown renderer for chat messages.
   Everything is HTML-escaped first; only a few formats are turned back on:
   ``` code blocks ```, `inline code`, **bold**, *italic*, headings, lists, tables, links, paragraphs. */
(function () {
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s) {
    return s
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function isTableSep(line) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line); }
  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return inline(c.trim()); });
  }
  function render(md, options) {
    options = options || {};
    var out = [];
    var parts = String(md || "").split(/```/);
    for (var i = 0; i < parts.length; i++) {
      if (i % 2 === 1) {
        var body = parts[i].replace(/^[\w+-]*\r?\n/, "");
        if (options.codeBlock) out.push(options.codeBlock(body, i === parts.length - 1));
        else out.push("<pre><code>" + esc(body) + "</code></pre>");
        continue;
      }
      var lines = esc(parts[i]).split(/\r?\n/);
      var list = null, para = [];
      var flushPara = function () { if (para.length) { out.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } };
      var flushList = function () {
        if (list) { out.push("<" + list.type + ">" + list.items.map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + "</" + list.type + ">"); list = null; }
      };
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j], m;
        if (/^\s*$/.test(line)) { flushPara(); flushList(); continue; }
        if (/^\s*\|.*\|\s*$/.test(line) && j + 1 < lines.length && isTableSep(lines[j + 1])) {
          flushPara(); flushList();
          var head = cells(line), rows = [];
          j += 2;
          while (j < lines.length && /^\s*\|.*\|\s*$/.test(lines[j])) { rows.push(cells(lines[j])); j++; }
          j--;
          out.push("<table><thead><tr>" + head.map(function (c) { return "<th>" + c + "</th>"; }).join("") + "</tr></thead><tbody>" +
            rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table>");
          continue;
        }
        if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flushPara(); flushList(); out.push("<h3>" + inline(m[2]) + "</h3>"); continue; }
        if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flushPara(); flushList(); continue; }
        if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); continue; }
        if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); continue; }
        flushList(); para.push(line);
      }
      flushPara(); flushList();
    }
    return out.join("");
  }
  window.Markdown = { render: render, escape: esc };
})();
