/**
 * Read Aloud — service worker.
 *
 * Owns all playback state. The content script extracts and chunks text and asks
 * to have it read; this worker speaks one sentence at a time and tells the frame
 * which sentence to highlight. Keeping state here means closing the popup, or
 * scrolling away, never interrupts playback.
 */

const DEFAULTS = {
  voiceName: "",
  rate: 1.0,
  pitch: 1.0,
  volume: 1.0,
  bubble: true,
};

/**
 * Bumped on every stop/restart. chrome.tts delivers "interrupted" and "cancelled"
 * events for utterances we deliberately replaced; comparing generations lets us
 * ignore those instead of mistaking them for progress.
 */
let generation = 0;
let keepAliveTimer = null;
let watchdogTimer = null;

const state = {
  playing: false,
  paused: false,
  chunks: [],
  index: 0,
  tabId: null,
  frameId: null,
  scope: "",
};

// --------------------------------------------------------------- settings

async function getSettings() {
  return chrome.storage.sync.get(DEFAULTS);
}

// ----------------------------------------------------------- service worker
// MV3 shuts an idle worker down after ~30s, which would kill playback. A cheap
// periodic API call resets that timer for as long as we are actually speaking.

function startKeepAlive() {
  if (keepAliveTimer !== null) return;
  keepAliveTimer = setInterval(() => chrome.runtime.getPlatformInfo(), 20000);
}

function stopKeepAlive() {
  if (keepAliveTimer !== null) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
}

// ------------------------------------------------------------------ helpers

function toFrame(message) {
  if (state.tabId === null) return;
  chrome.tabs
    .sendMessage(state.tabId, message, { frameId: state.frameId ?? 0 })
    .catch(() => {
      /* frame navigated away or was closed */
    });
}

function clearWatchdog() {
  if (watchdogTimer !== null) {
    clearTimeout(watchdogTimer);
    watchdogTimer = null;
  }
}

/**
 * Some speech engines never deliver an "end" event. Estimate how long the
 * sentence should take and move on well after that, so playback cannot stall.
 */
function armWatchdog(text, rate, myGeneration) {
  clearWatchdog();
  const words = text.split(/\s+/).length;
  const expected = words / (2.6 * Math.max(0.1, rate));
  const limit = Math.min(120, expected * 2.5 + 6) * 1000;
  watchdogTimer = setTimeout(() => {
    if (myGeneration === generation && state.playing && !state.paused) advance();
  }, limit);
}

function broadcastState() {
  chrome.runtime
    .sendMessage({ type: "stateChanged", state: publicState() })
    .catch(() => {
      /* no popup open */
    });
}

function publicState() {
  return {
    playing: state.playing,
    paused: state.paused,
    index: state.index,
    total: state.chunks.length,
    scope: state.scope,
  };
}

// ----------------------------------------------------------------- playback

async function startReading({ chunks, scope, tabId, frameId }) {
  if (!chunks || chunks.length === 0) return;

  generation++;
  clearWatchdog();
  chrome.tts.stop();

  // Tell the previous frame to drop its highlight before we retarget.
  if (state.tabId !== null && state.tabId !== tabId) toFrame({ type: "clearHighlight" });

  state.chunks = chunks;
  state.index = 0;
  state.playing = true;
  state.paused = false;
  state.tabId = tabId ?? null;
  state.frameId = frameId ?? null;
  state.scope = scope || "";

  startKeepAlive();
  await speakCurrent();
  broadcastState();
}

async function speakCurrent() {
  const text = state.chunks[state.index];
  if (text === undefined) return finish();

  const settings = await getSettings();
  const myGeneration = generation;

  toFrame({ type: "highlight", index: state.index });

  const options = {
    rate: settings.rate,
    pitch: settings.pitch,
    volume: settings.volume,
    enqueue: false,
    onEvent(event) {
      if (myGeneration !== generation) return; // stale utterance
      if (event.type === "end" || event.type === "error") {
        clearWatchdog();
        advance();
      }
    },
  };
  if (settings.voiceName) options.voiceName = settings.voiceName;

  chrome.tts.speak(text, options);
  armWatchdog(text, settings.rate, myGeneration);
}

function advance() {
  if (!state.playing) return;
  state.index++;
  if (state.index < state.chunks.length) {
    speakCurrent();
    broadcastState();
  } else {
    finish();
  }
}

function finish() {
  generation++;
  clearWatchdog();
  stopKeepAlive();
  chrome.tts.stop();
  toFrame({ type: "clearHighlight" });
  state.playing = false;
  state.paused = false;
  state.chunks = [];
  state.index = 0;
  state.scope = "";
  broadcastState();
}

function stopReading() {
  if (!state.playing) return;
  finish();
}

function pauseReading() {
  if (!state.playing || state.paused) return;
  clearWatchdog(); // must not fire while the clock is stopped
  chrome.tts.pause();
  state.paused = true;
  broadcastState();
}

async function resumeReading() {
  if (!state.playing || !state.paused) return;
  chrome.tts.resume();
  state.paused = false;
  const settings = await getSettings();
  armWatchdog(state.chunks[state.index] || "", settings.rate, generation);
  broadcastState();
}

function togglePause() {
  if (!state.playing) return;
  state.paused ? resumeReading() : pauseReading();
}

/** Re-speak the current sentence so a voice/speed change is heard immediately. */
async function applySettingsNow() {
  if (!state.playing) return;
  generation++;
  clearWatchdog();
  chrome.tts.stop();
  state.paused = false;
  await speakCurrent();
  broadcastState();
}

// -------------------------------------------------------------- triggering
// The content script does the extracting: it is the only place that can see the
// selection and map sentences back onto DOM ranges for highlighting.

async function trigger(tabId, frameId, mode) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "trigger", mode }, { frameId });
  } catch {
    notifyUnavailable();
  }
}

function notifyUnavailable() {
  chrome.action.setBadgeText({ text: "!" });
  chrome.action.setBadgeBackgroundColor({ color: "#c0392b" });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
}

/** Find which frame holds a selection, so shortcuts work inside iframes. */
async function frameWithSelection(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => (window.getSelection()?.toString() || "").trim().length,
    });
    const hit = results.find((r) => r.result > 0);
    if (hit) return hit.frameId;
  } catch {
    /* restricted page */
  }
  return 0;
}

// ------------------------------------------------------------ context menus
// The right-click menu is the primary entry point: highlight text on any page,
// right-click, and pick how to read it.

const SPEEDS = [
  ["speed-slow", "Slow", 0.75],
  ["speed-normal", "Normal", 1.0],
  ["speed-fast", "Fast", 1.5],
  ["speed-faster", "Faster", 2.0],
];

async function buildMenus() {
  await chrome.contextMenus.removeAll();
  const { rate } = await getSettings();

  chrome.contextMenus.create({
    id: "read-selection",
    title: 'Read aloud: "%s"',
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "read-from-here",
    title: "Read from here to the end of the page",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "sep-selection",
    type: "separator",
    contexts: ["selection"],
  });

  chrome.contextMenus.create({
    id: "read-page",
    title: "Read this page aloud",
    contexts: ["page", "selection"],
  });
  chrome.contextMenus.create({
    id: "stop-reading",
    title: "Stop reading",
    contexts: ["page", "selection"],
  });

  chrome.contextMenus.create({
    id: "speed",
    title: "Reading speed",
    contexts: ["page", "selection"],
  });
  for (const [id, label, value] of SPEEDS) {
    chrome.contextMenus.create({
      id,
      parentId: "speed",
      title: `${label}  (${value}×)`,
      type: "radio",
      checked: Math.abs(rate - value) < 0.01,
      contexts: ["page", "selection"],
    });
  }
}

chrome.runtime.onInstalled.addListener(buildMenus);
chrome.runtime.onStartup.addListener(buildMenus);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab) return;
  const frameId = info.frameId ?? 0;

  switch (info.menuItemId) {
    case "read-selection":
      return trigger(tab.id, frameId, "selection");
    case "read-from-here":
      return trigger(tab.id, frameId, "fromHere");
    case "read-page":
      return trigger(tab.id, 0, "page");
    case "stop-reading":
      return stopReading();
    default: {
      const speed = SPEEDS.find(([id]) => id === info.menuItemId);
      if (speed) {
        await chrome.storage.sync.set({ rate: speed[2] });
        applySettingsNow();
      }
    }
  }
});

// -------------------------------------------------------- keyboard shortcuts

chrome.commands.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (command === "stop-reading") return stopReading();
  if (command === "toggle-pause") return togglePause();
  if (command === "read-selection" && tab) {
    const frameId = await frameWithSelection(tab.id);
    return trigger(tab.id, frameId, "selection");
  }
});

// ---------------------------------------------------------------- messaging

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case "read":
      startReading({
        chunks: message.chunks,
        scope: message.scope,
        tabId: message.highlight ? sender.tab?.id : null,
        frameId: message.highlight ? sender.frameId : null,
      });
      sendResponse({ ok: true });
      return false;

    case "control":
      if (message.action === "pause") pauseReading();
      else if (message.action === "resume") resumeReading();
      else if (message.action === "toggle") togglePause();
      else if (message.action === "stop") stopReading();
      sendResponse({ ok: true, state: publicState() });
      return false;

    case "settingsChanged":
      if (message.applyNow) applySettingsNow();
      buildMenus();
      sendResponse({ ok: true });
      return false;

    case "getState":
      sendResponse(publicState());
      return false;

    case "triggerActiveTab":
      (async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) return sendResponse({ ok: false });
        const frameId =
          message.mode === "page" ? 0 : await frameWithSelection(tab.id);
        await trigger(tab.id, frameId, message.mode);
        sendResponse({ ok: true });
      })();
      return true; // async response

    case "noText":
      notifyUnavailable();
      sendResponse({ ok: true });
      return false;
  }
  return false;
});

// A page that navigates away can no longer show a highlight; stop cleanly.
chrome.tabs.onRemoved.addListener((tabId) => {
  if (state.playing && state.tabId === tabId) finish();
});

// Same for a page that reloads or navigates. tabs.onUpdated avoids needing the
// webNavigation permission just to notice this.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && state.playing && state.tabId === tabId) {
    finish();
  }
});
