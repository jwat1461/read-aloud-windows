/**
 * Sentence chunker — shared by the content script and the popup.
 *
 * Loaded as a classic script (not a module) so both runtimes can use the same
 * copy: content scripts share one isolated-world global, and popup.html pulls it
 * in with a plain <script> tag.
 *
 * Mirrors app/chunker.py. Returns spans into the source string rather than
 * copies, because the caller maps those offsets back onto DOM ranges to
 * highlight the sentence currently being spoken.
 */
(function (root) {
  const SENTENCE_ENDERS = new Set([".", "!", "?", "…"]);
  const TRAILING = new Set([
    '"', "'", "”", "’", ")", "]", "}", "…", "!", "?", ".",
  ]);
  const MAX_CHUNK = 280;

  const isSpace = (ch) => /\s/.test(ch);

  /** @returns {Array<[number, number]>} [start, end) spans covering the content. */
  function sentenceSpans(text, maxLen) {
    maxLen = maxLen || MAX_CHUNK;
    const spans = [];
    const n = text.length;
    let i = 0;

    while (i < n) {
      while (i < n && isSpace(text[i])) i++;
      if (i >= n) break;

      const start = i;
      let end = null;
      let lastSpace = -1;
      let j = i;

      while (j < n) {
        const ch = text[j];

        if (SENTENCE_ENDERS.has(ch)) {
          let k = j + 1;
          while (k < n && TRAILING.has(text[k])) k++;
          if (k >= n || isSpace(text[k])) {
            end = k;
            break;
          }
        }

        if (ch === "\n") {
          // A lone newline is a wrap; a blank line is a paragraph break.
          let k = j + 1;
          while (k < n && (text[k] === " " || text[k] === "\t" || text[k] === "\r")) k++;
          if (k < n && text[k] === "\n") {
            end = j;
            break;
          }
        }

        if (isSpace(ch)) lastSpace = j;

        if (j - start >= maxLen) {
          end = lastSpace > start ? lastSpace : j;
          break;
        }

        j++;
      }

      if (end === null) end = n;
      spans.push([start, end]);
      i = end;
    }

    return spans;
  }

  /**
   * @returns {Array<{start: number, end: number, text: string}>}
   * Spans whose trimmed text is non-empty, with the text carried along.
   */
  function chunks(text, maxLen) {
    const out = [];
    for (const [start, end] of sentenceSpans(text, maxLen)) {
      const piece = text.slice(start, end).trim();
      if (piece) out.push({ start, end, text: piece });
    }
    return out;
  }

  root.ReadAloudChunker = { sentenceSpans, chunks, MAX_CHUNK };
})(typeof self !== "undefined" ? self : this);
