/**
 * Bridge for the cross-implementation parity test.
 *
 * Loads extension/chunker.js (a classic script) inside a vm sandbox that stands
 * in for the browser's `self`, chunks every string in the JSON corpus given as
 * argv[2], and prints the results as JSON.
 *
 *     node tools/js_chunk.mjs corpus.json
 */

import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../extension/chunker.js", import.meta.url), "utf8");

const sandbox = {};
sandbox.self = sandbox; // the script attaches ReadAloudChunker to `self`
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const { chunks } = sandbox.ReadAloudChunker;
const corpus = JSON.parse(readFileSync(process.argv[2], "utf8"));

const results = corpus.map(({ text, maxLen }) =>
  chunks(text, maxLen).map((c) => [c.start, c.end, c.text]),
);

process.stdout.write(JSON.stringify(results));
