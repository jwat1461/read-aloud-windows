/**
 * Read Aloud — popup.
 *
 * A thin control surface: all playback state lives in the service worker, so the
 * popup can be closed and reopened mid-sentence without disturbing anything.
 */

const DEFAULTS = {
  voiceName: "",
  rate: 1.0,
  pitch: 1.0,
  volume: 1.0,
  bubble: true,
};

const $ = (id) => document.getElementById(id);
const el = {
  text: $("text"),
  read: $("read"),
  pause: $("pause"),
  stop: $("stop"),
  readSelection: $("read-selection"),
  readPage: $("read-page"),
  status: $("status"),
  progress: $("progress"),
  voice: $("voice"),
  rate: $("rate"),
  rateValue: $("rate-value"),
  pitch: $("pitch"),
  pitchValue: $("pitch-value"),
  volume: $("volume"),
  volumeValue: $("volume-value"),
  bubble: $("bubble"),
};

let settings = { ...DEFAULTS };

// ------------------------------------------------------------------- voices

function loadVoices() {
  chrome.tts.getVoices((voices) => {
    const usable = voices.filter((v) => v.voiceName);
    const preferred = navigator.language.slice(0, 2);

    usable.sort((a, b) => {
      // Same-language voices first, then local before network, then by name.
      const aLang = (a.lang || "").startsWith(preferred) ? 0 : 1;
      const bLang = (b.lang || "").startsWith(preferred) ? 0 : 1;
      if (aLang !== bLang) return aLang - bLang;
      if (!!a.remote !== !!b.remote) return a.remote ? 1 : -1;
      return a.voiceName.localeCompare(b.voiceName);
    });

    el.voice.innerHTML = "";
    const auto = new Option("System default", "");
    el.voice.add(auto);

    for (const voice of usable) {
      const lang = voice.lang ? ` — ${voice.lang}` : "";
      const remote = voice.remote ? " (network)" : "";
      el.voice.add(new Option(voice.voiceName + lang + remote, voice.voiceName));
    }

    el.voice.value = usable.some((v) => v.voiceName === settings.voiceName)
      ? settings.voiceName
      : "";
  });
}

// ----------------------------------------------------------------- settings

async function loadSettings() {
  settings = await chrome.storage.sync.get(DEFAULTS);
  el.rate.value = settings.rate;
  el.pitch.value = settings.pitch;
  el.volume.value = settings.volume;
  el.bubble.checked = settings.bubble;
  paintSettingLabels();
}

function paintSettingLabels() {
  el.rateValue.textContent = `${Number(el.rate.value).toFixed(1)}×`;
  el.pitchValue.textContent = Number(el.pitch.value).toFixed(1);
  el.volumeValue.textContent = Math.round(Number(el.volume.value) * 100);
}

let saveTimer = null;
function saveSettings({ applyNow = false } = {}) {
  settings = {
    voiceName: el.voice.value,
    rate: Number(el.rate.value),
    pitch: Number(el.pitch.value),
    volume: Number(el.volume.value),
    bubble: el.bubble.checked,
  };
  clearTimeout(saveTimer);
  // Debounced: dragging a slider must not restart the sentence on every pixel.
  saveTimer = setTimeout(async () => {
    await chrome.storage.sync.set(settings);
    chrome.runtime.sendMessage({ type: "settingsChanged", applyNow });
  }, 250);
}

// ----------------------------------------------------------------- playback

function send(message) {
  return chrome.runtime.sendMessage(message).catch(() => null);
}

function readPastedText() {
  const text = el.text.value;
  if (!text.trim()) {
    el.status.textContent = "Nothing to read — paste some text";
    el.text.focus();
    return;
  }
  const pieces = self.ReadAloudChunker.chunks(text);
  send({
    type: "read",
    chunks: pieces.map((c) => c.text),
    scope: "pasted text",
    highlight: false,
  });
}

function paintState(state) {
  if (!state) return;

  const { playing, paused, index, total, scope } = state;

  el.pause.disabled = !playing;
  el.stop.disabled = !playing;
  el.pause.textContent = paused ? "▶  Resume" : "❚❚  Pause";
  el.read.textContent = paused ? "▶  Resume" : "▶  Read";

  if (playing) {
    el.status.textContent = paused
      ? `Paused — ${index + 1}/${total}`
      : `Reading ${scope} — ${index + 1}/${total}`;
    el.progress.style.width = `${((index + 1) / Math.max(1, total)) * 100}%`;
  } else {
    el.status.textContent = "Ready";
    el.progress.style.width = "0%";
  }
}

async function refresh() {
  paintState(await send({ type: "getState" }));
}

// -------------------------------------------------------------------- wiring

el.read.addEventListener("click", async () => {
  const state = await send({ type: "getState" });
  if (state?.playing && state.paused) return send({ type: "control", action: "resume" });
  readPastedText();
});

el.pause.addEventListener("click", () => send({ type: "control", action: "toggle" }));
el.stop.addEventListener("click", () => send({ type: "control", action: "stop" }));

el.readSelection.addEventListener("click", async () => {
  await send({ type: "triggerActiveTab", mode: "selection" });
  window.close();
});

el.readPage.addEventListener("click", async () => {
  await send({ type: "triggerActiveTab", mode: "page" });
  window.close();
});

el.text.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    readPastedText();
  }
});

el.voice.addEventListener("change", () => saveSettings({ applyNow: true }));
for (const input of [el.rate, el.pitch, el.volume]) {
  input.addEventListener("input", () => {
    paintSettingLabels();
    saveSettings({ applyNow: true });
  });
}
el.bubble.addEventListener("change", () => saveSettings());

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "stateChanged") paintState(message.state);
});

// The worker only pushes on transitions; poll so the sentence counter ticks too.
setInterval(refresh, 500);

(async () => {
  await loadSettings();
  loadVoices();
  refresh();
  el.text.focus();
})();
