const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../../app/static/js/app.js"), "utf8");

async function runPolling(fetch) {
  const status = { textContent: "Your result will appear automatically." };
  const timers = new Map();
  const listeners = {};
  let reloads = 0;
  let timerId = 0;
  const panel = {
    dataset: { analysisId: "saved-id" },
    querySelector: () => status,
    setAttribute(name, value) { this[name] = value; },
  };
  const window = {
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(name, fn) { listeners[name] = fn; },
    location: { reload() { reloads++; } },
  };
  vm.runInNewContext(script, {
    document: { querySelector: (selector) => selector === "[data-analysis-id]" ? panel : null },
    window, fetch, AbortController,
  });
  await new Promise(setImmediate);
  return { status, panel, timers, listeners, reloads: () => reloads };
}

test("running analysis polls again without reloading", async () => {
  const state = await runPolling(async (url, options) => {
    assert.equal(url, "/api/v1/analyses/saved-id");
    assert.equal(options.cache, "no-store");
    return { ok: true, json: async () => ({ status: "running" }) };
  });
  assert.equal(state.reloads(), 0);
  assert.deepEqual([...state.timers.values()].map(t => t.ms), [2000]);
});

for (const status of ["completed", "failed"]) {
  test(`${status} analysis reloads the server-rendered result and stops polling`, async () => {
    const state = await runPolling(async () => ({ ok: true, json: async () => ({ status }) }));
    assert.equal(state.reloads(), 1);
    assert.equal(state.timers.size, 0);
  });
}

test("network failure retries and navigation clears the retry", async () => {
  const state = await runPolling(async () => { throw new Error("offline"); });
  assert.match(state.status.textContent, /Reconnecting automatically/);
  assert.equal(state.timers.size, 1);
  state.listeners.pagehide();
  assert.equal(state.timers.size, 0);
});

test("missing analysis stops polling", async () => {
  const state = await runPolling(async () => ({ status: 404, ok: false }));
  assert.match(state.status.textContent, /could not be found/);
  assert.equal(state.panel["aria-busy"], "false");
  assert.equal(state.timers.size, 0);
});

test("restoring a cached page resumes polling", async () => {
  let calls = 0;
  const state = await runPolling(async () => {
    calls++;
    return { ok: true, json: async () => ({ status: "running" }) };
  });
  state.listeners.pagehide();
  state.listeners.pageshow({ persisted: true });
  await new Promise(setImmediate);
  assert.equal(calls, 2);
  assert.equal(state.timers.size, 1);
});
