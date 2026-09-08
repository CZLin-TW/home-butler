'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const hap = require('@homebridge/hap-nodejs');
const { PlatformAccessory } = require('../node_modules/homebridge/dist/platformAccessory.js');
const { HomeButlerPlatform } = require('../index');
const C = hap.Characteristic;
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(enabled, cached = []) {
  const registered = [], removed = [], logs = [];
  const api = new EventEmitter();
  Object.assign(api, { hap, platformAccessory: PlatformAccessory,
    registerPlatformAccessories(plugin, alias, accessories) {
      for (const a of accessories) {
        if (!a.context.butlerDiagnostic) continue;
        const s = a.getService(hap.Service.HeaterCooler);
        for (const type of [C.HeatingThresholdTemperature, C.CoolingThresholdTemperature]) {
          const ch = s.getCharacteristic(type);
          ch.iid = type === C.HeatingThresholdTemperature ? 10 : 11;
          assert.equal(ch.internalHAPRepresentation().minStep, 0.5);
          assert.equal(ch.value, 26);
        }
      }
      registered.push(...accessories);
    },
    unregisterPlatformAccessories(plugin, alias, accessories) { removed.push(...accessories); },
  });
  const log = { info(message) { logs.push(message); }, warn() {}, error() {} };
  const p = new HomeButlerPlatform(log, { halfDegreeTest: enabled }, api);
  for (const a of cached) p.configureAccessory(a);
  api.emit('didFinishLaunching');
  // Installation deliberately has no backend client. Any accidental forwarding fails.
  assert.equal(p.client, undefined);
  return { p, api, registered, removed, logs };
}

test('diagnostic is opt-in and has half steps before first registration', () => {
  for (const enabled of [undefined, false, 'true']) {
    const s = setup(enabled);
    assert.equal(s.registered.length, 0);
    s.api.emit('shutdown');
  }
  const s = setup(true);
  assert.equal(s.registered.length, 1);
  assert.equal(s.registered[0].displayName, '半度測試空調');
  assert.equal(s.p.devices.size, 0);
  s.api.emit('shutdown');
});

test('diagnostic accepts half steps, heat and power locally and logs received target', async () => {
  const s = setup(true), d = s.p.diagnostic;
  d.queue.delay = 1;
  for (const [mode, type] of [[2, C.CoolingThresholdTemperature], [1, C.HeatingThresholdTemperature]]) {
    await d.service.getCharacteristic(C.TargetHeaterCoolerState).handleSetRequest(mode);
    await d.service.getCharacteristic(type).handleSetRequest(26.5);
    await tick();
    assert.equal(await d.service.getCharacteristic(type).handleGetRequest(), 26.5);
  }
  await d.service.getCharacteristic(C.Active).handleSetRequest(0);
  await tick();
  assert.equal(d.state.power, 'off');
  d.lastSeen = 0; // Simulation stays available without a backend polling timer.
  assert.ok(Math.abs(await d.service.getCharacteristic(C.CurrentTemperature).handleGetRequest() - 26.8) < 1e-9);
  assert.ok(s.logs.some(x => x.includes('"temperature":26.5')));
  assert.equal(s.p.devices.size, 0);
  s.api.emit('shutdown');
});

test('diagnostic restores its UUID without duplication; disabling removes only test accessory', () => {
  const first = setup(true), cached = first.registered[0];
  const real = new PlatformAccessory('Real AC', hap.uuid.generate('real'));
  real.context.butlerId = 'a'.repeat(32);
  first.api.emit('shutdown');
  const restored = setup(true, [cached, real]);
  assert.equal(restored.registered.length, 0);
  assert.equal(restored.p.diagnostic.accessory, cached);
  assert.equal(restored.p.devices.size, 1);
  assert.ok(restored.p.devices.has(real.context.butlerId));
  restored.api.emit('shutdown');
  const disabled = setup(false, [cached, real]);
  assert.deepEqual(disabled.removed, [cached]);
  assert.equal(disabled.p.diagnostic, null);
  assert.equal(disabled.p.devices.size, 1);
  disabled.api.emit('shutdown');
});

test('real catalog outages never invalidate or forward diagnostic controls', async () => {
  const s = setup(true), d = s.p.diagnostic;
  let calls = 0;
  s.p.client = { snapshot: async () => { throw Error('offline'); },
    command: async () => { calls++; throw Error('must never be called'); } };
  await s.p.poll();
  d.queue.delay = 1;
  await d.service.getCharacteristic(C.CoolingThresholdTemperature).handleSetRequest(27.5);
  await tick();
  assert.equal(await d.service.getCharacteristic(C.CoolingThresholdTemperature).handleGetRequest(), 27.5);
  assert.equal(calls, 0);
  s.api.emit('shutdown');
});
