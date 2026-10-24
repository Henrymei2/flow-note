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
    disconnect() { this.connections = []; },
    start(when, offset) { this.offset = offset; },
    stop() { this.stopped = true; },
  };
}

/** Load the production preview controller with independently advanced test clocks. */
function preview() {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { checked: false, value: "0.3", children: [],
      handlers: {}, addEventListener(name, callback) { this.handlers[name] = callback; },
      replaceChildren() {}, pause() { this.paused = true; } });
    return elements.get(id);
  };
  const video = element("session-video");
  Object.assign(video, { currentTime: 2, dataset: { offset: "3" }, paused: false,
    ended: false, seeking: false, readyState: 4, playbackRate: 1 });
  class AudioContext {
    /** Initialize an independently controllable output clock. */
    constructor() { this.currentTime = 0; this.state = "running"; this.destination = {}; }
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
    /** Create a source that records the requested audition offset. */
    createBufferSource() { return audioNode(); }
  }
  const context = vm.createContext({ $: element, AudioContext, AbortController, Float32Array,
    heartbeat: { enabled: false }, setInterval, clearInterval, setTimeout, clearTimeout,
    document: {}, formatTime: String });
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
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.limiter"), true);
  const controller = run("overlayPreview.controller");
  run("heartbeat.enabled = true; overlayPreview.buffer = {}; resetOverlayPreview('new-session')");
  assert.equal(controller.signal.aborted, true);
  assert.equal(run("overlayPreview.buffer"), null);
  assert.equal(element("overlay-mix").checked, false);
  assert.equal(run("heartbeat.enabled"), true);
  assert.equal(run("reviewAudio.bus.connections[0] === reviewAudio.limiter"), true);
});
