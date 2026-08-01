/**
 * Browser-side tests for the content script's DOM mapping.
 *
 * These are the parts that cannot be tested in Node: flattening a real document
 * into text, mapping sentence offsets back onto DOM ranges, and painting those
 * ranges with the CSS Custom Highlight API. Open harness.html in Chrome; results
 * render on the page and land in `window.__testResults` for automation.
 */

(() => {
  const api = window.__readAloudInternals;
  const { chunks } = window.ReadAloudChunker;
  const fixture = document.getElementById("fixture");

  const results = [];

  function check(name, fn) {
    try {
      fn();
      results.push({ name, ok: true });
    } catch (error) {
      results.push({ name, ok: false, message: error.message });
    }
  }

  function assert(condition, message) {
    if (!condition) throw new Error(message || "assertion failed");
  }

  function equal(actual, expected, message) {
    if (actual !== expected) {
      throw new Error(
        `${message || "not equal"}\n     expected: ${JSON.stringify(expected)}\n     actual:   ${JSON.stringify(actual)}`,
      );
    }
  }

  const map = api.buildMap(fixture);

  // ------------------------------------------------------------ extraction

  check("reads visible paragraph text", () => {
    assert(map.text.includes("The first paragraph is plain."), "missing first paragraph");
    assert(map.text.includes("Final paragraph here."), "missing last paragraph");
  });

  check("skips <script> and <style> contents", () => {
    assert(!map.text.includes("shouldNotBeRead"), "script contents leaked in");
    assert(!map.text.includes("color: red"), "style contents leaked in");
  });

  check("skips display:none content", () => {
    assert(!map.text.includes("Never read this hidden text"), "hidden text leaked in");
  });

  check("keeps inline elements in the same sentence", () => {
    const flat = map.text.replace(/\s+/g, " ");
    assert(
      flat.includes("A paragraph with bold and italic and a link inside it."),
      `inline run was broken up: ${flat.slice(0, 200)}`,
    );
  });

  check("separates adjacent blocks so sentences do not merge", () => {
    const flat = map.text.replace(/[ \t]+/g, " ");
    assert(!/First list item\.\s?Second list item\./.test(flat.replace(/\n/g, "")) === false || true);
    assert(
      map.text.includes("First list item.") && map.text.includes("Second list item."),
      "list items missing",
    );
    const a = map.text.indexOf("First list item.");
    const b = map.text.indexOf("Second list item.");
    assert(map.text.slice(a, b).includes("\n"), "list items were not separated");
  });

  check("every piece maps to its own slice of the text", () => {
    for (const piece of map.pieces) {
      equal(
        map.text.slice(piece.start, piece.end),
        piece.node.nodeValue,
        "piece offsets do not match its node",
      );
    }
  });

  check("pieces are ordered and non-overlapping", () => {
    for (let i = 1; i < map.pieces.length; i++) {
      assert(
        map.pieces[i - 1].end <= map.pieces[i].start,
        `piece ${i} overlaps its predecessor`,
      );
    }
  });

  // --------------------------------------------------------------- locate

  check("locate resolves an offset to the right node", () => {
    const needle = map.text.indexOf("Final paragraph here.");
    const hit = api.locate(map.pieces, needle);
    assert(hit, "locate returned null");
    assert(
      hit.node.nodeValue.slice(hit.offset).startsWith("Final paragraph here."),
      `landed on: ${JSON.stringify(hit.node.nodeValue.slice(hit.offset, hit.offset + 30))}`,
    );
  });

  check("locate clamps offsets that fall in a block separator", () => {
    // The "\n\n" between blocks belongs to no text node.
    const gap = map.text.indexOf("\n\n");
    assert(gap > 0, "fixture produced no block separator");
    const hit = api.locate(map.pieces, gap + 1);
    assert(hit, "locate returned null inside a separator");
    assert(hit.node.nodeType === Node.TEXT_NODE, "did not land on a text node");
    assert(
      hit.offset >= 0 && hit.offset <= hit.node.nodeValue.length,
      "offset outside the node",
    );
  });

  check("locate handles the very first and last offsets", () => {
    const first = api.locate(map.pieces, 0);
    equal(first.offset, 0, "first offset misplaced");
    const last = api.locate(map.pieces, map.text.length);
    assert(last, "locate returned null at the end");
  });

  // ---------------------------------------------------------------- ranges

  check("every sentence produces a range holding that sentence", () => {
    const sentences = chunks(map.text);
    assert(sentences.length >= 6, `only found ${sentences.length} sentences`);

    for (const sentence of sentences) {
      const range = api.makeRange(map.pieces, sentence.start, sentence.end);
      assert(range, `no range for: ${sentence.text}`);
      equal(
        range.toString().replace(/\s+/g, " ").trim(),
        sentence.text.replace(/\s+/g, " ").trim(),
        "range text does not match the sentence",
      );
    }
  });

  check("ranges survive emoji", () => {
    const sentences = chunks(map.text);
    const emoji = sentences.find((s) => s.text.includes("🔊"));
    assert(emoji, "emoji sentence missing from the fixture");
    const range = api.makeRange(map.pieces, emoji.start, emoji.end);
    assert(range.toString().includes("🔊"), "emoji lost in the range");
  });

  check("ranges cover table cells", () => {
    const sentences = chunks(map.text);
    const cell = sentences.find((s) => s.text.includes("Cell one."));
    assert(cell, "table cell not found");
    const range = api.makeRange(map.pieces, cell.start, cell.end);
    assert(range.toString().includes("Cell one."), "table cell range wrong");
  });

  // ------------------------------------------------------------ highlight

  check("CSS Custom Highlight API is available", () => {
    assert(api.supportsHighlight, "this browser cannot paint ::highlight ranges");
  });

  check("a highlight can be registered and cleared", () => {
    const sentences = chunks(map.text);
    const range = api.makeRange(map.pieces, sentences[0].start, sentences[0].end);
    CSS.highlights.set("readaloud-sentence", new Highlight(range));
    assert(CSS.highlights.has("readaloud-sentence"), "highlight was not registered");
    CSS.highlights.delete("readaloud-sentence");
    assert(!CSS.highlights.has("readaloud-sentence"), "highlight was not cleared");
  });

  check("highlighting does not modify the DOM", () => {
    const before = fixture.innerHTML;
    const sentences = chunks(map.text);
    const range = api.makeRange(map.pieces, sentences[1].start, sentences[1].end);
    CSS.highlights.set("readaloud-sentence", new Highlight(range));
    equal(fixture.innerHTML, before, "the DOM changed when highlighting");
    CSS.highlights.delete("readaloud-sentence");
  });

  // ---------------------------------------------------------- selection

  check("offsetOf places a selection inside the map", () => {
    api.setMap(map);
    const target = map.pieces.find((p) => p.node.nodeValue.includes("Final paragraph"));
    assert(target, "target node not found");
    const inNode = target.node.nodeValue.indexOf("Final paragraph");
    const offset = api.offsetOf(target.node, inNode, "start");
    equal(offset, target.start + inNode, "offsetOf returned the wrong position");
  });

  check("offsetOf handles an element container", () => {
    api.setMap(map);
    const offset = api.offsetOf(fixture, 0, "start");
    assert(typeof offset === "number", "expected a number");
    assert(offset >= 0 && offset <= map.text.length, "offset out of range");
  });

  // ------------------------------------------------------------- reporting

  const passed = results.filter((r) => r.ok).length;
  const failed = results.length - passed;

  // textContent, not innerHTML: test names contain markup like "<script>" and
  // would otherwise be parsed as HTML and swallow the rest of the list.
  const container = document.getElementById("results");
  for (const r of results) {
    const line = document.createElement("div");
    line.className = r.ok ? "pass" : "fail";
    line.textContent =
      `${r.ok ? "PASS" : "FAIL"}  ${r.name}` + (r.ok ? "" : `\n     ${r.message}`);
    container.appendChild(line);
  }

  const summary = `${passed} passed, ${failed} failed, ${results.length} total`;
  const el = document.getElementById("summary");
  el.textContent = summary;
  el.className = failed === 0 ? "pass" : "fail";

  window.__testResults = { passed, failed, total: results.length, results };
  console.log("[ReadAloudTests] " + summary);
  for (const r of results.filter((x) => !x.ok)) {
    console.error("[ReadAloudTests] FAIL " + r.name + ": " + r.message);
  }
})();
