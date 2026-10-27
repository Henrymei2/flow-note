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
  const makeElement = () => ({
    children: [], style: {}, attributes: {},
    classList: { toggle() {} },
    append(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    setAttribute(key, value) { this.attributes[key] = value; },
  });
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { ...makeElement(), checked: false, value: id === "overlay-drum-offset" ? "180" : "0.3",
      handlers: {}, addEventListener(name, callback) { this.handlers[name] = callback; },
      pause() { this.paused = true; } });
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
    document: { createElement: makeElement }, formatTime: String, console, performance,
    state: { timeline: null }, timelineContentBox: () => ({ left: 128, width: 800 }),
    selectInterval: (interval) => {
      context.state.selectedInterval = interval;
      vm.runInContext("reconcileAuditionSelection(state.selectedInterval)", context);
    },
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

test("audition ruler clips intervals, selects markers, and toggles visuals only", async () => {
  const { element, run } = preview();
  run(`state.timeline = { start: 5, end: 20 };
    auditionTimeline.items = [
      { id: 'a', start_time: 0, end_time: 10, duration: 10, eligible: true, bpm: 100 },
      { id: 'b', start_time: 15, end_time: 25, duration: 10, eligible: false, reason: 'Missing audio' }
    ]; drawAuditions();`);
  const markers = element("audition-strip").children;
  assert.equal(markers.length, 2);
  assert.equal(markers[0].style.left, "0%");
  assert.ok(Math.abs(parseFloat(markers[0].style.width) - 100 / 3) < 1e-6);
  assert.ok(Math.abs(parseFloat(markers[1].style.left) - 200 / 3) < 1e-6);
  assert.match(markers[1].title, /Missing audio/);
  assert.match(markers[1].className, /unavailable/);
  await run("selectAudition(auditionTimeline.items[0])");
  assert.equal(run("state.selectedInterval.auditionId"), "a");
  assert.equal(element("overlay-panel").hidden, false);
  assert.equal(element("audition-strip").children[0].attributes["aria-pressed"], "true");
  run("overlayPreview.active = true");
  element("overlay-mix").checked = true;
  element("audition-toggle-button").onclick();
  assert.equal(element("audition-strip").hidden, true);
  assert.equal(element("audition-overlay").children.length, 0);
  assert.equal(run("overlayPreview.active"), true);
  assert.equal(element("overlay-mix").checked, true);
  assert.equal(element("overlay-panel").hidden, false);
  element("audition-toggle-button").onclick();
  assert.equal(element("audition-strip").children.length, 2);
  element("audition-strip").children[1].onclick({ stopPropagation() {} });
  assert.equal(run("overlayPreview.audition.id"), "b");
  assert.equal(element("overlay-generate").disabled, true);
  assert.equal(element("overlay-status").textContent, "Missing audio");
});

test("new selections and edited audition boundaries detach audio but retain cached intervals", async () => {
  const { element, run } = preview();
  await run("ensureReviewAudio()");
  run(`auditionTimeline.items = [{ id: 'a', start_time: 2, end_time: 12, duration: 10, eligible: true }];
    overlayPreview.audition = auditionTimeline.items[0];
    overlayPreview.buffer = { duration: 10 };
    overlayPreview.buffers = { ambient: { duration: 10 }, drums: { duration: 10 } };`);
  element("overlay-mix").checked = true;
  run("syncOverlay()");
  const voice = run("overlayPreview.sources[0]");
  run("reconcileAuditionSelection({ auditionId: 'a', start: 2, end: 12 })");
  assert.equal(run("overlayPreview.sources[0]"), voice);
  run("reconcileAuditionSelection({ auditionId: 'a', start: 3, end: 12 })");
  assert.equal(voice.stopped, true);
  assert.equal(run("overlayPreview.audition"), null);
  assert.equal(element("overlay-panel").hidden, true);
  assert.equal(run("auditionTimeline.items.length"), 1);
  run("resetOverlayPreview('next')");
  assert.equal(run("auditionTimeline.items.length"), 0);
  assert.equal(element("audition-toggle-button").hidden, true);
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
      reverb_seconds: 2.4, max_wet: 0.6 } };`);
  element("overlay-mix").checked = true;
  element("overlay-gain").value = "0.2";
  element("overlay-drums").value = "0.4";
  element("overlay-space").value = "1";
  run("syncOverlay()");
  const voices = run("overlayPreview.sources");
  assert.equal(voices.length, 2);
  assert.equal(voices[0].offset, 3);
  assert.equal(voices[1].offset, 2.82); // Default 180 ms delay.
  assert.equal(run("overlayPreview.gain.gain.value"), 0.2);
  assert.equal(run("overlayPreview.drumGain.gain.value"), 0.4);
  const space = run("overlayPreview.reverb");
  assert.equal(run("reviewAudio.video.connections.includes(overlayPreview.reverb.input)"), true);
  assert.equal(run("overlayPreview.gain.connections.includes(overlayPreview.reverb.input)"), true);
  assert.equal(run("overlayPreview.drumGain.connections.includes(overlayPreview.reverb.input)"), true);
  assert.ok(Math.abs(space.send.gain.value - 0.348) < 1e-8);
  assert.equal(space.convolver.normalize, false);
  const impulse = space.convolver.buffer.getChannelData(0);
  assert.equal(impulse.length, 115200);
  assert.ok(impulse.slice(0, 1200).every((value) => value === 0));
  assert.ok(Math.abs(impulse.reduce((sum, sample) => sum + sample * sample, 0) - 1) < 1e-6);
  element("overlay-space").value = "0.3";
  run("updateOverlaySpace(5)");
  assert.ok(Math.abs(space.send.gain.value - 0.126) < 1e-8);
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

test("default 180 ms delay schedules drums independently of BPM", async () => {
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
  assert.equal(voices[1].when, 0.18);
  assert.equal(voices[1].offset, 0);
  assert.equal(voices[1].duration, 9.82); // Delayed drums still end at the audition boundary.
  video.currentTime = 3;
  run("syncOverlay(true)");
  assert.equal(run("overlayPreview.sources[0].offset"), 3);
  assert.equal(run("overlayPreview.sources[1].offset"), 2.82);
  video.playbackRate = 2;
  video.currentTime = 0;
  run("syncOverlay(true)");
  assert.equal(run("overlayPreview.sources[1].when"), 0.09);
  assert.equal(run("overlayPreview.sources[1].playbackRate.value"), 2);
});

test("phase tuning changes drums only and shows its BPM-relative beat value", async () => {
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
  assert.equal(oldDrums.offset, 2.82); // 180 ms at 120 BPM is 0.36 beat.
  element("overlay-drum-offset").value = "-250";
  element("overlay-drum-offset").oninput();
  assert.ok(oldDrums.stopped);
  assert.equal(run("overlayPreview.sources[0]"), background);
  assert.equal(run("overlayPreview.reverb"), room);
  assert.equal(run("overlayPreview.sources[1].offset"), 3.25);
  assert.equal(run("overlayPreview.audition.drumOffsetMs"), -250);
  assert.equal(element("overlay-drum-offset-value").textContent, "-250 ms · -0.50 beat");
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
  element("overlay-drum-offset").value = "-250";
  run("syncOverlay()");
  assert.equal(run("overlayPreview.sources.length"), 1);
  assert.equal(run("overlayPreview.sources[0].stem"), "ambient");
});
