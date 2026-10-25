/** Verify the preview clock contract using controllable media and Web Audio clocks. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const path = require("node:path");

/** Build a minimal audio graph node that records routing and start/stop behavior. */
function audioNode() {
  return {
    connections: [], gain: { value: 1, setTargetAtTime(value) { this.value = value; } },
    threshold: {}, knee: {}, ratio: {}, attack: {}, release: {}, playbackRate: {},
    connect(target) { this.connections.push(target); return target; },
    disconnect(target) {
      this.connections = target ? this.connections.filter((node) => node !== target) : [];
    },
    start(when, offset, duration) { this.when = when; this.offset = offset; this.duration = duration; },
    stop() { this.stopped = true; },
  };
}

/** Load the production preview controller with independently advanced test clocks. */
function preview() {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { checked: false, value: id === "overlay-drum-offset" ? "0.5" : "0.3", children: [],
      handlers: {}, addEventListener(name, callback) { this.handlers[name] = callback; },
      replaceChildren() {}, pause() { this.paused = true; } });
    return elements.get(id);
  };
  const video = element("session-video");
  Object.assign(video, { currentTime: 2, dataset: { offset: "3" }, paused: false,
    ended: false, seeking: false, readyState: 4, playbackRate: 1 });
  class AudioContext {
    /** Initialize an independently controllable output clock. */
    constructor() { this.currentTime = 0; this.sampleRate = 48000; this.state = "running"; this.destination = {}; }
    /** Keep audio unlock deterministic. */
    async resume() { this.state = "running"; }
    /** Create instrumented graph nodes. */
    createGain() { return audioNode(); }
    /** Create an instrumented limiter. */
    createDynamicsCompressor() { return audioNode(); }
    /** Create an instrumented output ceiling. */
    createWaveShaper() { return audioNode(); }
    /** Attach an instrumented original media source. */
    createMediaElementSource() { return audioNode(); }
    /** Create a short stereo impulse and a fresh convolution history. */
    createConvolver() { return audioNode(); }
    createBuffer(channels, count) {
      const data = Array.from({ length: channels }, () => new Float32Array(count));
      return { getChannelData(index) { return data[index]; } };
    }
    /** Create a source that records the requested audition offset. */
    createBufferSource() { return audioNode(); }
  }
  const context = vm.createContext({ $: element, AudioContext, AbortController, Float32Array,
    heartbeat: { enabled: false }, setInterval, clearInterval, setTimeout, clearTimeout,
    document: {}, formatTime: String, console, performance,
    toggleHeartbeat: async (on) => { context.heartbeat.enabled = on; } });
  vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../src/fl_activity_recorder/static/overlays.js"), "utf8"), context);
  return { context, element, video, run: (code) => vm.runInContext(code, context) };
}

test("offset, pause, seek, rate, buffering and interval boundaries follow video", async () => {
  const { element, video, run } = preview();
  await run("ensureReviewAudio()");
  run("overlayPreview.audition = { start_time: 2, end_time: 12 }; overlayPreview.buffer = {};");
  element("overlay-mix").checked = true;
  run("syncOverlay()");
  const first = run("overlayPreview.source");
  assert.equal(first.offset, 3); // video 2 + capture offset 3 - audition start 2
  run("reviewAudio.context.currentTime = 1");
  video.currentTime = 3;
  run("syncOverlay()");
  assert.equal(run("overlayPreview.source"), first);
  video.currentTime = 3.3;
  run("syncOverlay()");
  assert.ok(first.stopped);
  assert.ok(Math.abs(run("overlayPreview.source.offset") - 4.3) < 1e-6);
  video.paused = true;
  run("syncOverlay()");
  assert.equal(run("overlayPreview.source"), null);
  video.paused = false;
  video.seeking = true;
  run("syncOverlay()");
  assert.equal(run("overlayPreview.source"), null);
  video.currentTime = 4;
  video.seeking = false;
  video.playbackRate = 2;
  run("syncOverlay()");
  assert.equal(run("overlayPreview.source.offset"), 5);
  assert.equal(run("overlayPreview.source.playbackRate.value"), 2);
  video.handlers.stalled();
  assert.ok(run("overlayPreview.source")); // A network stall with buffered media still plays.
  video.handlers.waiting();
  assert.equal(run("overlayPreview.source"), null);
  video.handlers.playing();
  assert.equal(run("overlayPreview.source.offset"), 5);
  video.currentTime = 9;
  run("syncOverlay()");
  assert.equal(run("overlayPreview.source"), null);
});

test("original bypass, shared limiting and selection cleanup preserve independent heartbeat", async () => {
  const { element, run } = preview();
  await run("ensureReviewAudio()");
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.context.destination"), true);
  element("overlay-mix").checked = true;
  run("updateReviewOutput()");
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.context.destination"), true);
  run("overlayPreview.active = true; updateReviewOutput()");
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.limiter"), true);
  const controller = run("overlayPreview.controller");
  run("heartbeat.enabled = true; overlayPreview.buffer = {}; resetOverlayPreview('new-session')");
  assert.equal(controller.signal.aborted, true);
  assert.equal(run("overlayPreview.buffer"), null);
  assert.equal(element("overlay-mix").checked, false);
  assert.equal(run("heartbeat.enabled"), true);
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.limiter"), true);
});

test("both stems and original share EDA space; sliders and seek preserve isolation", async () => {
  const { element, video, run } = preview();
  await run("ensureReviewAudio()");
  run(`overlayPreview.audition = { start_time: 2, end_time: 12 };
    overlayPreview.buffer = {};
    overlayPreview.buffers = { ambient: {}, drums: {} };
    overlayPreview.record = { spatial: { values: [0, 1], duration_seconds: 10,
      reverb_seconds: 1.2, max_wet: 0.35 } };`);
  element("overlay-mix").checked = true;
  element("overlay-gain").value = "0.2";
  element("overlay-drums").value = "0.4";
  element("overlay-space").value = "1";
  run("syncOverlay()");
  const voices = run("overlayPreview.sources");
  assert.equal(voices.length, 2);
  assert.equal(voices[0].offset, 3);
  assert.equal(voices[1].offset, 2.7); // Default half-beat delay at 100 BPM.
  assert.equal(run("overlayPreview.gain.gain.value"), 0.2);
  assert.equal(run("overlayPreview.drumGain.gain.value"), 0.4);
  const space = run("overlayPreview.reverb");
  assert.equal(run("reviewAudio.video.connections.includes(overlayPreview.reverb.input)"), true);
  assert.equal(run("overlayPreview.gain.connections.includes(overlayPreview.reverb.input)"), true);
  assert.equal(run("overlayPreview.drumGain.connections.includes(overlayPreview.reverb.input)"), true);
  assert.ok(Math.abs(space.send.gain.value - 0.105) < 1e-8);
  element("overlay-drums").value = "0.7";
  element("overlay-drums").oninput();
  assert.equal(run("overlayPreview.drumGain.gain.value"), 0.7);
  assert.equal(run("overlayPreview.gain.gain.value"), 0.2);
  element("overlay-space").value = "0";
  element("overlay-space").oninput();
  assert.equal(space.send.gain.value, 0);
  assert.equal(run("overlayPreview.sources[0]"), voices[0]);
  video.currentTime = 4;
  run("syncOverlay(true)");
  assert.ok(voices.every((source) => source.stopped));
  assert.notEqual(run("overlayPreview.reverb"), space);
  assert.equal(run("reviewAudio.video.connections.length"), 2); // dry + one live send
  element("overlay-mix").checked = false;
  await element("overlay-mix").onchange();
  assert.equal(run("overlayPreview.reverb"), null);
  assert.equal(run("reviewAudio.video.connections.length"), 1);
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.context.destination"), true);
});

test("EDA missing leaves original dry; enabling overlay disables independent heartbeat", async () => {
  const { element, run } = preview();
  run(`heartbeat.enabled = true;
    overlayPreview.audition = { start_time: 2, end_time: 12 };
    overlayPreview.buffer = {};
    overlayPreview.record = { spatial: { values: [] } };`);
  element("overlay-mix").checked = true;
  await element("overlay-mix").onchange();
  assert.equal(run("heartbeat.enabled"), false);
  assert.equal(run("overlayPreview.reverb"), null);
  assert.equal(run("reviewAudio.video.connections.length"), 1);
  element("overlay-mix").checked = false;
  await element("overlay-mix").onchange();
  run("clearInterval(overlayPreview.timer)");
  assert.equal(run("heartbeat.enabled"), false);
});

test("default half-beat delay schedules drums 300 ms later at 100 BPM", async () => {
  const { element, video, run } = preview();
  await run("ensureReviewAudio()");
  video.currentTime = 0;
  video.dataset.offset = "0";
  run(`overlayPreview.audition = { start_time: 0, end_time: 10 };
    overlayPreview.buffer = { duration: 10 };
    overlayPreview.buffers = { ambient: { duration: 10 }, drums: { duration: 10 } };
    overlayPreview.record = { bpm: 100 };`);
  element("overlay-mix").checked = true;
  run("syncOverlay()");
  const voices = run("overlayPreview.sources");
  assert.equal(voices[0].when, 0);
  assert.equal(voices[0].offset, 0);
  assert.equal(voices[1].when, 0.3);
  assert.equal(voices[1].offset, 0);
  assert.equal(voices[1].duration, 9.7); // Delayed drums still end at the audition boundary.
  video.currentTime = 3;
  run("syncOverlay(true)");
  assert.equal(run("overlayPreview.sources[0].offset"), 3);
  assert.equal(run("overlayPreview.sources[1].offset"), 2.7);
  video.playbackRate = 2;
  video.currentTime = 0;
  run("syncOverlay(true)");
  assert.equal(run("overlayPreview.sources[1].when"), 0.15);
  assert.equal(run("overlayPreview.sources[1].playbackRate.value"), 2);
});

test("phase tuning changes drums only and uses actual BPM", async () => {
  const { element, run } = preview();
  await run("ensureReviewAudio()");
  run(`overlayPreview.audition = { start_time: 2, end_time: 12 };
    overlayPreview.buffer = { duration: 10 };
    overlayPreview.buffers = { ambient: { duration: 10 }, drums: { duration: 10 } };
    overlayPreview.record = { bpm: 120, spatial: { values: [.5, .5], duration_seconds: 10 } };`);
  element("overlay-mix").checked = true;
  run("syncOverlay()");
  const background = run("overlayPreview.sources[0]");
  const oldDrums = run("overlayPreview.sources[1]");
  const room = run("overlayPreview.reverb");
  assert.equal(oldDrums.offset, 2.75); // Half beat at 120 BPM is 250 ms.
  element("overlay-drum-offset").value = "-0.5";
  element("overlay-drum-offset").oninput();
  assert.ok(oldDrums.stopped);
  assert.equal(run("overlayPreview.sources[0]"), background);
  assert.equal(run("overlayPreview.reverb"), room);
  assert.equal(run("overlayPreview.sources[1].offset"), 3.25);
  assert.equal(run("overlayPreview.audition.drumOffsetBeats"), -0.5);
  assert.equal(element("overlay-drum-offset-value").textContent, "-0.50 beat · -250 ms");
  element("overlay-drum-offset").value = "0";
  element("overlay-drum-offset").oninput();
  assert.equal(run("overlayPreview.sources[1].offset"), 3);
  assert.equal(run("overlayPreview.sources.length"), 2);
});

test("advanced drums do not read past their buffer near the audition end", async () => {
  const { element, video, run } = preview();
  await run("ensureReviewAudio()");
  video.currentTime = 11.9;
  video.dataset.offset = "0";
  run(`overlayPreview.audition = { start_time: 2, end_time: 12 };
    overlayPreview.buffer = { duration: 10 };
    overlayPreview.buffers = { ambient: { duration: 10 }, drums: { duration: 10 } };
    overlayPreview.record = { bpm: 100 };`);
  element("overlay-mix").checked = true;
  element("overlay-drum-offset").value = "-0.5";
  run("syncOverlay()");
  assert.equal(run("overlayPreview.sources.length"), 1);
  assert.equal(run("overlayPreview.sources[0].stem"), "ambient");
});
