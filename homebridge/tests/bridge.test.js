'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const hap = require('@homebridge/hap-nodejs');
const { PlatformAccessory } = require('../node_modules/homebridge/dist/platformAccessory.js');
const { ButlerClient, CommandQueue } = require('../client');
const { HomeButlerPlatform, AcAccessory } = require('../index');
const state = { id: 'a'.repeat(32), name: 'Living AC', location: 'Living', power: 'on',
  mode: 'cool', temperature: 27, fan_speed: 'low', uncertain: false };
const tick = () => new Promise(resolve => setImmediate(resolve));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
function platform() {
  const api = new EventEmitter();
  Object.assign(api, { hap, platformAccessory: PlatformAccessory, registerPlatformAccessories() {} });
  const log = { error() {}, warn() {}, info() {} };
  const p = new HomeButlerPlatform(log, { backendUrl: 'https://butler.example', apiKey: 'x'.repeat(40) }, api);
  p.sensors = [{ name: 'Living sensor', location: 'Living', temperature: 25.5,
    online: true, updated_at: Date.now() / 1000 }];
  return p;
}
function device(p) {
  const accessory = new PlatformAccessory(state.name, hap.uuid.generate(state.id));
  const d = new AcAccessory(p, accessory, state.id);
  d.queue.delay = 1;
  p.devices.set(state.id, d);
  return d;
}

test('client requires HTTPS, blocks redirects, no key in URL, no automatic write retries', async () => {
  for (const url of ['http://butler.example', 'https://user:pass@butler.example', 'https://butler.example/?key=x']) {
    assert.throws(() => new ButlerClient(url, 'x'.repeat(40)));
  }
  let calls = 0;
  const client = new ButlerClient('https://butler.example/', 'x'.repeat(40), async (url, options) => {
    calls++;
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers['X-API-Key'], 'x'.repeat(40));
    assert.ok(!url.includes('xxx'));
    assert.match(JSON.parse(options.body).request_id, /^[0-9a-f-]{36}$/);
    throw new Error('timeout');
  });
  await assert.rejects(client.command(state.id, { power: 'off' }));
  assert.equal(calls, 1);
});

test('HomeKit companion writes coalesce; off dominates temperature writes', async () => {
  const sent = [];
  const q = new CommandQueue(async patch => sent.push(patch), 1);
  await Promise.all([q.enqueue({ power: 'on' }), q.enqueue({ temperature: 26 }), q.enqueue({ mode: 'cool' })]);
  assert.deepEqual(sent, [{ power: 'on', temperature: 26, mode: 'cool' }]);
  await Promise.all([q.enqueue({ power: 'off' }), q.enqueue({ temperature: 25 })]);
  assert.deepEqual(sent[1], { power: 'off' });
  q.close();
});

test('commands serialize and unknown outcome drops queued writes', async () => {
  const wait = deferred();
  let calls = 0;
  const q = new CommandQueue(async () => { calls++; return wait.promise; }, 1);
  const first = q.enqueue({ power: 'on' });
  const firstCheck = assert.rejects(first);
  await sleep(10);
  const secondCheck = assert.rejects(q.enqueue({ temperature: 25 }));
  wait.reject(new Error('uncertain'));
  await Promise.all([firstCheck, secondCheck]);
  assert.equal(calls, 1);
  assert.equal(q.busy, false);
  q.close();
});

test('real HAP service publishes state without sending commands and uses actual room temperature', async () => {
  const p = platform();
  let calls = 0;
  p.client.command = async () => { calls++; };
  const d = device(p);
  d.apply(state);
  const C = hap.Characteristic;
  assert.equal(await d.service.getCharacteristic(C.Active).handleGetRequest(), 1);
  assert.equal(await d.service.getCharacteristic(C.CurrentTemperature).handleGetRequest(), 25.5);
  assert.equal(await d.service.getCharacteristic(C.CoolingThresholdTemperature).handleGetRequest(), 27);
  d.apply({ ...state, power: 'off', temperature: 26 });
  assert.equal(d.service.getCharacteristic(C.Active).value, 0);
  assert.equal(calls, 0);
  p.api.emit('shutdown');
});

test('real HomeKit OFF SET is corrected to fan-on after antimold response', async () => {
  const p = platform();
  let calls = 0;
  p.client.command = async (id, patch) => {
    calls++;
    assert.deepEqual(patch, { power: 'off' });
    return { status: 'success', device: { ...state, mode: 'fan' } };
  };
  const d = device(p);
  d.apply(state);
  const active = d.service.getCharacteristic(hap.Characteristic.Active);
  await active.handleSetRequest(0);
  await tick();
  assert.equal(active.value, 1);
  assert.equal(d.service.getCharacteristic(hap.Characteristic.CurrentHeaterCoolerState).value, 1);
  assert.equal(calls, 1);
  p.api.emit('shutdown');
});

test('missing/stale/ambiguous room sensor is unavailable, never target temperature fallback', async () => {
  const p = platform();
  const d = device(p);
  for (const sensors of [[], [{ ...p.sensors[0], online: false }],
    [{ ...p.sensors[0], updated_at: 1 }], [p.sensors[0], { ...p.sensors[0], name: 'Other' }]]) {
    p.sensors = sensors;
    d.apply(state);
    await assert.rejects(d.service.getCharacteristic(hap.Characteristic.CurrentTemperature).handleGetRequest());
  }
  p.api.emit('shutdown');
});

test('configured sensor resolves ambiguity only in the same location', () => {
  const p = platform();
  p.config.devices = [{ name: state.name, temperatureSensor: 'Living sensor' }];
  p.sensors.push({ ...p.sensors[0], name: 'Other' });
  assert.equal(p.temperatureSensor(state).name, 'Living sensor');
  assert.equal(p.temperatureSensor({ ...state, location: 'Bedroom' }), null);
});

test('old poll cannot overwrite a newer command result', async () => {
  const p = platform();
  const d = device(p);
  d.apply(state);
  const wait = deferred();
  p.client.snapshot = () => wait.promise;
  p.client.command = async () => ({ status: 'success', device: { ...state, temperature: 26 } });
  const poll = p.poll();
  await d.queue.enqueue({ temperature: 26 });
  wait.resolve({ protocol: 1, devices: [state], sensors: p.sensors });
  await poll;
  assert.equal(d.state.temperature, 26);
  p.api.emit('shutdown');
});

test('backend outages and revoked devices mark cached accessories unavailable', async () => {
  const p = platform();
  const d = device(p);
  d.apply(state);
  p.client.snapshot = async () => { throw new Error('offline'); };
  await p.poll();
  await assert.rejects(d.service.getCharacteristic(hap.Characteristic.Active).handleGetRequest());
  clearTimeout(p.timer);
  d.apply(state);
  p.client.snapshot = async () => ({ protocol: 1, devices: [], sensors: [] });
  await p.poll();
  assert.equal(d.available, false);
  p.api.emit('shutdown');
});

test('unknown command outcome reports HAP error and never assumes target succeeded', async () => {
  const p = platform();
  const d = device(p);
  d.apply(state);
  p.client.command = async () => ({ status: 'unknown', device: null });
  await assert.rejects(d.service.getCharacteristic(hap.Characteristic.Active).handleSetRequest(0));
  assert.equal(d.available, false);
  assert.equal(d.state.power, 'on');
  p.api.emit('shutdown');
});

test('restored accessories stay unavailable while backend is offline', async () => {
  const p = platform();
  const a = new PlatformAccessory(state.name, hap.uuid.generate(state.id));
  a.context.butlerId = state.id;
  p.configureAccessory(a);
  p.client.snapshot = async () => { throw new Error('offline'); };
  p.api.emit('didFinishLaunching');
  await tick();
  assert.equal(p.devices.get(state.id).available, false);
  p.api.emit('shutdown');
});
