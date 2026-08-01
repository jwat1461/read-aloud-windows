/**
 * Read Aloud — content script.
 *
 * Three jobs:
 *   1. Turn the page (or the current selection) into an ordered list of sentences.
 *   2. Remember where each sentence lives in the DOM so it can be highlighted
 *      while it is spoken. Highlighting uses the CSS Custom Highlight API, which
 *      paints ranges without touching the DOM — no wrapper <span>s, so React and
 *      friends never notice.
 *   3. Show a small "Read" bubble when text is highlighted with the mouse.
 */

(() => {
  if (window.__readAloudLoaded) return;
  window.__readAloudLoaded = true;

  const { chunks: chunkText } = self.ReadAloudChunker;

  const HIGHLIGHT_NAME = "readaloud-sentence";
  const SKIP_TAGS = new Set([
    "SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "SVG", "CANVAS",
    "AUDIO", "VIDEO", "OBJECT", "EMBED", "TEMPLATE", "SELECT",
  ]);
  const BLOCK_TAGS = new Set([
    "P", "DIV", "LI", "UL", "OL", "H1", "H2", "H3", "H4", "H5", "H6",
    "BLOCKQUOTE", "PRE", "TD", "TH", "TR", "TABLE", "SECTION", "ARTICLE",
    "ASIDE", "HEADER", "FOOTER", "NAV", "MAIN", "FIGURE", "FIGCAPTION",
    "DL", "DT", "DD", "FORM", "ADDRESS", "HR", "BR", "BODY",
  ]);
  // Chrome/page furniture we never want read as part of the article.
  const NOISE_SELECTOR = "nav, header, footer, aside, [role='navigation'], [aria-hidden='true']";

  const supportsHighlight =
    typeof Highlight !== "undefined" && typeof CSS !== "undefined" && CSS.highlights;

  /** Current document map: the flattened text plus where each piece came from. */
  let map = null;
  /** Ranges for the sentences of the active reading, indexed like the chunk list. */
  let ranges = [];

  // ------------------------------------------------------------ text mapping

  function blockAncestor(node) {
    let el = node.parentElement;
    while (el && !BLOCK_TAGS.has(el.tagName)) el = el.parentElement;
    return el || document.body;
  }

  function isRendered(el) {
    if (!el) return false;
    // offsetParent is null for display:none subtrees; position:fixed needs the
    // rect check as a fallback.
    if (el.offsetParent !== null) return true;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  }

  /**
   * Flatten `root` into a single string plus an index of the text nodes behind it.
   * Text nodes in different blocks are joined with a blank line so sentences do
   * not run together across paragraphs.
   */
  function buildMap(root, { skipNoise = false } = {}) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (SKIP_TAGS.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
        if (skipNoise && parent.closest(NOISE_SELECTOR)) return NodeFilter.FILTER_REJECT;
        if (!isRendered(parent)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const pieces = [];
    const byNode = new Map();
    let text = "";
    let lastBlock = null;

    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const block = blockAncestor(node);
      if (lastBlock !== null && block !== lastBlock) text += "\n\n";
      lastBlock = block;

      const start = text.length;
      text += node.nodeValue;
      const piece = { node, start, end: text.length };
      pieces.push(piece);
      byNode.set(node, piece);
    }

    return { text, pieces, byNode };
  }

  /** Binary search: global offset -> the text node and offset within it. */
  function locate(pieces, offset) {
    if (pieces.length === 0) return null;

    let lo = 0;
    let hi = pieces.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const piece = pieces[mid];
      if (offset < piece.start) hi = mid - 1;
      else if (offset > piece.end) lo = mid + 1;
      else return { node: piece.node, offset: offset - piece.start };
    }

    // The offset fell in one of the "\n\n" separators inserted between blocks,
    // which belong to no text node. Clamp to the nearest real one rather than
    // jumping to the end of the document.
    const piece = pieces[Math.max(0, Math.min(pieces.length - 1, hi))];
    return {
      node: piece.node,
      offset: Math.max(0, Math.min(offset - piece.start, piece.node.nodeValue.length)),
    };
  }

  function makeRange(pieces, start, end) {
    const a = locate(pieces, start);
    const b = locate(pieces, end);
    if (!a || !b) return null;
    try {
      const range = document.createRange();
      range.setStart(a.node, Math.min(a.offset, a.node.nodeValue.length));
      range.setEnd(b.node, Math.min(b.offset, b.node.nodeValue.length));
      return range;
    } catch {
      return null;
    }
  }

  /** Where a (container, offset) DOM position sits in the flattened string. */
  function offsetOf(container, offset, edge) {
    if (container.nodeType === Node.TEXT_NODE) {
      const piece = map.byNode.get(container);
      if (piece) return piece.start + offset;
      return null;
    }
    // Element container: fall back to the nearest text node in the right direction.
    const child = container.childNodes[Math.min(offset, container.childNodes.length - 1)];
    const scope = child || container;
    const inside = map.pieces.filter((p) => scope.contains(p.node));
    if (inside.length === 0) return null;
    return edge === "start" ? inside[0].start : inside[inside.length - 1].end;
  }

  // ------------------------------------------------------------ highlighting

  function clearHighlight() {
    if (supportsHighlight) CSS.highlights.delete(HIGHLIGHT_NAME);
    ranges = [];
  }

  function showHighlight(index) {
    if (!supportsHighlight) return;
    const range = ranges[index];
    if (!range) return;
    try {
      CSS.highlights.set(HIGHLIGHT_NAME, new Highlight(range));
    } catch {
      return;
    }
    scrollRangeIntoView(range);
  }

  function scrollRangeIntoView(range) {
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;
    const margin = 80;
    if (rect.top < margin || rect.bottom > window.innerHeight - margin) {
      window.scrollTo({
        top: window.scrollY + rect.top - window.innerHeight / 3,
        behavior: "smooth",
      });
    }
  }

  // ---------------------------------------------------------------- reading

  /** Chunk a slice of the mapped text and hand it to the service worker. */
  function read(from, to, scope) {
    const slice = map.text.slice(from, to);
    const pieces = chunkText(slice);
    if (pieces.length === 0) {
      chrome.runtime.sendMessage({ type: "noText" });
      return;
    }

    ranges = pieces.map((c) => makeRange(map.pieces, from + c.start, from + c.end));

    chrome.runtime.sendMessage({
      type: "read",
      chunks: pieces.map((c) => c.text),
      scope,
      highlight: supportsHighlight,
    });
  }

  function readSelection({ toEnd = false } = {}) {
    hideBubble();
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) {
      chrome.runtime.sendMessage({ type: "noText" });
      return;
    }

    const range = selection.getRangeAt(0);
    map = buildMap(document.body);

    const from = offsetOf(range.startContainer, range.startOffset, "start");
    const to = toEnd
      ? map.text.length
      : offsetOf(range.endContainer, range.endOffset, "end");

    if (from === null || to === null || to <= from) {
      // Could not place the selection in the map — read it without highlighting.
      const text = selection.toString();
      const pieces = chunkText(text);
      ranges = [];
      chrome.runtime.sendMessage({
        type: "read",
        chunks: pieces.map((c) => c.text),
        scope: toEnd ? "rest of page" : "selection",
        highlight: false,
      });
      return;
    }

    read(from, to, toEnd ? "rest of page" : "selection");
  }

  function readPage() {
    const container =
      document.querySelector("article, main, [role='main']") || document.body;
    map = buildMap(container, { skipNoise: container === document.body });
    read(0, map.text.length, "page");
  }

  // ----------------------------------------------------------- selection UI
  // A small floating button next to a fresh selection. Lives in a shadow root so
  // page styles cannot reach it and it cannot leak styles onto the page.

  let bubbleHost = null;
  let bubbleButton = null;
  let bubbleEnabled = true;

  function makeBubble() {
    if (bubbleHost) return;
    bubbleHost = document.createElement("div");
    bubbleHost.id = "read-aloud-bubble-host";
    const shadow = bubbleHost.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      button {
        all: unset;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        box-sizing: border-box;
        padding: 7px 12px;
        border-radius: 8px;
        background: #4f46e5;
        color: #fff;
        font: 600 13px/1.2 -apple-system, "Segoe UI", system-ui, sans-serif;
        cursor: pointer;
        box-shadow: 0 4px 14px rgba(0,0,0,.28);
        white-space: nowrap;
        user-select: none;
      }
      button:hover { background: #4338ca; }
      button:active { transform: translateY(1px); }
      svg { width: 14px; height: 14px; fill: currentColor; }
    `;

    bubbleButton = document.createElement("button");
    bubbleButton.innerHTML =
      '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a6.8 6.8 0 0 1 0 13.4v2.1a8.9 8.9 0 0 0 0-17.6z"/></svg>Read aloud';
    // mousedown would collapse the selection before we could read it.
    bubbleButton.addEventListener("mousedown", (e) => e.preventDefault());
    bubbleButton.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      readSelection();
    });

    shadow.append(style, bubbleButton);
    document.documentElement.appendChild(bubbleHost);
  }

  function showBubble(rect) {
    makeBubble();
    const top = window.scrollY + rect.top - 42;
    const left = window.scrollX + rect.left;
    bubbleHost.style.cssText = `
      position: absolute;
      z-index: 2147483647;
      top: ${Math.max(window.scrollY + 4, top)}px;
      left: ${Math.max(4, left)}px;
      margin: 0; padding: 0; border: 0; background: none;
    `;
    bubbleHost.hidden = false;
  }

  function hideBubble() {
    if (bubbleHost) bubbleHost.hidden = true;
  }

  function onSelectionSettled() {
    if (!bubbleEnabled) return hideBubble();
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return hideBubble();
    const text = selection.toString().trim();
    if (text.length < 2) return hideBubble();

    const rect = selection.getRangeAt(0).getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return hideBubble();
    showBubble(rect);
  }

  document.addEventListener("mouseup", (e) => {
    if (bubbleHost && bubbleHost.contains(e.target)) return;
    setTimeout(onSelectionSettled, 10);
  });
  document.addEventListener("mousedown", (e) => {
    if (!bubbleHost || !bubbleHost.contains(e.target)) hideBubble();
  });
  document.addEventListener("scroll", hideBubble, { passive: true, capture: true });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideBubble();
  });

  chrome.storage.sync.get({ bubble: true }, (s) => {
    bubbleEnabled = s.bubble;
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.bubble) {
      bubbleEnabled = changes.bubble.newValue;
      if (!bubbleEnabled) hideBubble();
    }
  });

  // --------------------------------------------------------------- messaging

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    switch (message.type) {
      case "trigger":
        if (message.mode === "selection") readSelection();
        else if (message.mode === "fromHere") readSelection({ toEnd: true });
        else if (message.mode === "page") readPage();
        break;
      case "highlight":
        showHighlight(message.index);
        break;
      case "clearHighlight":
        clearHighlight();
        break;
    }
    sendResponse({ ok: true });
    return false;
  });

  window.addEventListener("pagehide", clearHighlight);

  // Exposed for test/harness.html. Content scripts run in an isolated world, so
  // this is invisible to the pages the extension runs on.
  window.__readAloudInternals = {
    buildMap,
    locate,
    makeRange,
    blockAncestor,
    setMap: (m) => (map = m),
    getMap: () => map,
    offsetOf,
    supportsHighlight,
  };
})();
