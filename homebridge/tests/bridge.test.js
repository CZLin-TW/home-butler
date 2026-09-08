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

const modeOn = (d, mode) => d.accessory.getServiceById(hap.Service.Switch, `mode-${mode}`)
  .getCharacteristic(hap.Characteristic.On);

test('all four modes sync from backend without SET, including powered-off switches', async () => {
  const p = platform();
  const d = device(p);
  p.client.command = async () => { assert.fail('Reading state must not control hardware'); };
  const C = hap.Characteristic;
  for (const [mode, current] of [['heat', 2], ['dry', 1], ['fan', 1], ['cool', 3]]) {
    d.apply({ ...state, mode });
    assert.equal(await d.service.getCharacteristic(C.CurrentHeaterCoolerState).handleGetRequest(), current);
    assert.equal(await modeOn(d, 'dry').handleGetRequest(), mode === 'dry');
    assert.equal(await modeOn(d, 'fan').handleGetRequest(), mode === 'fan');
    // In dry/fan the thermal selector remembers heat; it is not the active mode.
    assert.equal(d.service.getCharacteristic(C.TargetHeaterCoolerState).value, mode === 'cool' ? 2 : 1);
  }
  d.apply({ ...state, power: 'off', mode: 'dry' });
  assert.equal(modeOn(d, 'dry').value, false);
  assert.equal(modeOn(d, 'fan').value, false);
  d.apply({ ...state, mode: 'auto' });
  await assert.rejects(modeOn(d, 'dry').handleGetRequest());
  d.fail();
  await assert.rejects(modeOn(d, 'fan').handleGetRequest());
  p.api.emit('shutdown');
});

test('real HAP heating and cooling SET combine mode and target temperature', async () => {
  const p = platform();
  const d = device(p);
  d.apply({ ...state, power: 'off' });
  const C = hap.Characteristic;
  const sent = [];
  p.client.command = async (id, patch) => {
    sent.push(patch);
    return { status: 'success', device: { ...d.state, ...patch } };
  };
  for (const [target, mode, type, temperature] of [[1, 'heat', C.HeatingThresholdTemperature, 24.5],
    [2, 'cool', C.CoolingThresholdTemperature, 26.5]]) {
    assert.equal(d.service.getCharacteristic(type).props.minStep, 0.5);
    await Promise.all([d.service.getCharacteristic(C.TargetHeaterCoolerState).handleSetRequest(target),
      d.service.getCharacteristic(type).handleSetRequest(temperature)]);
    await tick();
    assert.deepEqual(sent.at(-1), { power: 'on', mode, temperature });
    assert.equal(d.service.getCharacteristic(C.HeatingThresholdTemperature).value, temperature);
    assert.equal(d.service.getCharacteristic(C.CoolingThresholdTemperature).value, temperature);
    assert.equal(modeOn(d, 'dry').value, false);
  }
  p.api.emit('shutdown');
});

test('backend rounded target replaces HomeKit optimistic half-degree SET', async () => {
  const p = platform(); const d = device(p); d.apply(state);
  const C = hap.Characteristic;
  const sent = [];
  p.client.command = async (_id, patch) => {
    sent.push(patch);
    return { status: 'success', device: { ...state, temperature: 27 } };
  };
  await d.service.getCharacteristic(C.CoolingThresholdTemperature).handleSetRequest(26.5);
  await tick();
  assert.deepEqual(sent, [{ temperature: 26.5 }]);
  assert.equal(d.service.getCharacteristic(C.CoolingThresholdTemperature).value, 27);
  assert.equal(d.service.getCharacteristic(C.HeatingThresholdTemperature).value, 27);
  p.api.emit('shutdown');
});

test('mode switches coalesce scene OFF/ON in both orders and send only the selected mode', async () => {
  const p = platform();
  const d = device(p);
  d.apply({ ...state, mode: 'dry' });
  const sent = [];
  p.client.command = async (id, patch) => {
    sent.push(patch);
    return { status: 'success', device: { ...d.state, ...patch } };
  };
  for (const reverse of [false, true]) {
    const actions = [() => modeOn(d, 'dry').handleSetRequest(false),
      () => modeOn(d, 'fan').handleSetRequest(true)];
    if (reverse) actions.reverse();
    await Promise.all(actions.map(f => f()));
    await tick();
    assert.deepEqual(sent.at(-1), { power: 'on', mode: 'fan' });
    assert.equal(modeOn(d, 'dry').value, false);
    assert.equal(modeOn(d, 'fan').value, true);
  }
  await modeOn(d, 'dry').handleSetRequest(true);
  await tick();
  assert.deepEqual(sent.at(-1), { power: 'on', mode: 'dry' });
  assert.equal(modeOn(d, 'fan').value, false);
  p.api.emit('shutdown');
});

test('mode OFF uses fresh backend guard and publishes actual antimold/no-op response', async () => {
  const p = platform();
  const d = device(p);
  d.apply({ ...state, mode: 'dry' });
  p.client.command = async (id, patch) => {
    assert.deepEqual(patch, { power: 'off', off_if_mode: ['dry'] });
    return { status: 'success', device: { ...state, mode: 'fan' } };
  };
  await modeOn(d, 'dry').handleSetRequest(false);
  await tick();
  assert.equal(modeOn(d, 'dry').value, false);
  assert.equal(modeOn(d, 'fan').value, true);
  assert.equal(d.service.getCharacteristic(hap.Characteristic.Active).value, 1);
  p.client.command = async (id, patch) => {
    assert.deepEqual(patch, { power: 'off', off_if_mode: ['fan'] });
    // Dashboard switched to heat before the conditional OFF reached the backend.
    return { status: 'success', device: { ...state, mode: 'heat' } };
  };
  await modeOn(d, 'fan').handleSetRequest(false);
  await tick();
  assert.equal(d.state.mode, 'heat');
  assert.equal(d.service.getCharacteristic(hap.Characteristic.Active).value, 1);
  p.api.emit('shutdown');
});

test('combined mode OFF guards and main OFF have unambiguous precedence', async () => {
  const sent = [];
  const q = new CommandQueue(async patch => sent.push(patch), 1);
  await Promise.all([q.enqueue({ power: 'off', off_if_mode: ['dry'] }),
    q.enqueue({ power: 'off', off_if_mode: ['fan'] })]);
  assert.deepEqual(sent.at(-1), { power: 'off', off_if_mode: ['dry', 'fan'] });
  for (const reverse of [false, true]) {
    const patches = [{ power: 'off' }, { power: 'on', mode: 'heat' }, { temperature: 24 }];
    if (reverse) patches.reverse();
    await Promise.all(patches.map(patch => q.enqueue(patch)));
    assert.deepEqual(sent.at(-1), { power: 'off' });
  }
  q.close();
});

test('mode SET failure invalidates all controls and is not retried', async () => {
  const p = platform();
  const d = device(p);
  d.apply(state);
  let calls = 0;
  p.client.command = async () => { calls++; return { status: 'unknown' }; };
  await assert.rejects(modeOn(d, 'dry').handleSetRequest(true));
  await tick();
  for (const mode of ['dry', 'fan']) await assert.rejects(modeOn(d, mode).handleGetRequest());
  assert.equal(d.state.mode, 'cool');
  assert.equal(calls, 1);
  p.api.emit('shutdown');
});

test('upgrade reuses accessory and service identities without resetting user names', () => {
  const p = platform();
  const d = device(p);
  d.apply({ ...state, mode: 'heat' });
  const a = d.accessory;
  const ids = a.services.map(s => s.UUID + ':' + s.subtype);
  const dry = a.getServiceById(hap.Service.Switch, 'mode-dry');
  dry.setCharacteristic(hap.Characteristic.ConfiguredName, 'My dry mode');
  d.queue.close();
  const restored = new AcAccessory(p, a, state.id);
  assert.deepEqual(a.services.map(s => s.UUID + ':' + s.subtype), ids);
  assert.equal(dry.getCharacteristic(hap.Characteristic.ConfiguredName).value, 'My dry mode');
  restored.apply({ ...state, mode: 'fan' });
  assert.equal(restored.service.getCharacteristic(hap.Characteristic.TargetHeaterCoolerState).value, 1);
  restored.queue.close();
  p.api.emit('shutdown');
});
