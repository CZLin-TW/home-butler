'use strict';
const { ButlerClient, CommandQueue } = require('./client');
const PLUGIN = 'homebridge-home-butler';
const PLATFORM = 'HomeButler';

class AcAccessory {
  constructor(platform, accessory, id) {
    this.platform = platform;
    this.accessory = accessory;
    this.id = id;
    this.generation = 0;
    this.available = false;
    this.lastSeen = 0;
    this.readers = new Map();
    const { Service: S, Characteristic: C } = platform.api.hap;
    this.C = C;
    accessory.getService(S.AccessoryInformation)
      .setCharacteristic(C.Manufacturer, 'HomeButler')
      .setCharacteristic(C.Model, 'IR AC · last command state')
      .setCharacteristic(C.SerialNumber, id);
    this.service = accessory.getService(S.HeaterCooler)
      || accessory.addService(S.HeaterCooler, accessory.displayName);
    this.queue = new CommandQueue(async patch => {
      this.generation++;
      try {
        const response = await platform.client.command(id, patch);
        if (response.status !== 'success' || response.device?.id !== id) throw new Error('Command not confirmed');
        this.apply(response.device);
      } catch (error) {
        this.fail();
        platform.log.warn('HomeButler 指令未確認；未自動重送。請先確認設備與後端狀態。');
        throw this.error();
      } finally {
        this.generation++;
        // HomeKit completes SET after our promise resolves. Apply backend state
        // on the next turn as well, so an OFF->antimold response stays ACTIVE.
        setImmediate(() => { if (!this.queue.busy) this.publish(); });
      }
    });
    this.bind(C.Active, () => this.state.power === 'on' ? 1 : 0,
      value => value === 1 ? { power: 'on' } : { power: 'off' });
    // First release exposes cooling mode only. Dry/fan remain controllable in
    // Dashboard and are represented as powered/idle, not silently rewritten.
    this.service.getCharacteristic(C.TargetHeaterCoolerState).updateValue(2).setProps({ validValues: [2] });
    this.bind(C.TargetHeaterCoolerState, () => 2, value => {
      if (value !== 2) throw this.error();
      return { mode: 'cool', power: 'on' };
    });
    this.service.getCharacteristic(C.CoolingThresholdTemperature)
      .updateValue(16).setProps({ minValue: 16, maxValue: 30, minStep: 1 });
    this.bind(C.CoolingThresholdTemperature, () => {
      if (!Number.isInteger(this.state.temperature)) throw this.error();
      return this.state.temperature;
    }, value => ({ temperature: value }));
    this.bind(C.CurrentHeaterCoolerState, () => {
      if (this.state.power === 'off') return 0;
      // Inferred operating mode, not compressor feedback or physical readback.
      return this.state.mode === 'cool' ? 3 : 1;
    });
    this.bind(C.CurrentTemperature, () => {
      const sensor = platform.temperatureSensor(this.state);
      if (!sensor) throw this.error();
      return sensor.temperature;
    });
    this.service.getCharacteristic(C.TemperatureDisplayUnits).updateValue(0);
    this.fail(); // Construction defaults are never advertised as live readings.
  }
  error() { return new this.platform.api.hap.HapStatusError(-70402); }
  check() {
    if (!this.available || Date.now() - this.lastSeen > 45000) throw this.error();
  }
  bind(type, read, write) {
    this.readers.set(type, read);
    const characteristic = this.service.getCharacteristic(type);
    characteristic.onGet(() => { this.check(); return read(); });
    if (write) characteristic.onSet(value => this.queue.enqueue(write(value)));
  }
  apply(state) {
    this.state = state;
    this.available = !state.uncertain && ['on', 'off'].includes(state.power)
      && ['cool', 'dry', 'fan'].includes(state.mode);
    this.lastSeen = Date.now();
    this.publish();
  }
  publish() {
    const C = this.C;
    const values = [C.Active, C.TargetHeaterCoolerState, C.CoolingThresholdTemperature,
      C.CurrentHeaterCoolerState, C.CurrentTemperature];
    for (const type of values) {
      const characteristic = this.service.getCharacteristic(type);
      // Invoke only GET handlers; updateValue does not call control SET handlers.
      try {
        this.check();
        const value = this.readers.get(type)();
        characteristic.updateValue(value);
      } catch {
        characteristic.updateValue(this.error());
      }
    }
  }
  fail() {
    this.available = false;
    this.publish();
  }
}

class HomeButlerPlatform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config;
    this.api = api;
    this.cached = new Map();
    this.devices = new Map();
    this.sensors = [];
    this.stopped = false;
    try { this.client = new ButlerClient(config.backendUrl, config.apiKey); }
    catch { log.error('請設定 HomeButler HTTPS 後端網址與獨立橋接金鑰。'); }
    api.on('didFinishLaunching', () => {
      // Restore handlers immediately, before the first network request, so a
      // backend outage cannot expose cached HomeKit values as live state.
      for (const accessory of this.cached.values()) {
        const id = accessory.context.butlerId;
        this.devices.set(id, new AcAccessory(this, accessory, id));
        this.devices.get(id).fail();
      }
      if (this.client) void this.poll();
    });
    api.on('shutdown', () => {
      this.stopped = true;
      clearTimeout(this.timer);
      for (const device of this.devices.values()) device.queue.close();
    });
  }
  configureAccessory(accessory) { this.cached.set(accessory.UUID, accessory); }
  temperatureSensor(state) {
    const mapping = (this.config.devices || []).find(d => d.name === state.name);
    const candidates = this.sensors.filter(s => s.location === state.location
      && (!mapping?.temperatureSensor || s.name === mapping.temperatureSensor));
    if (candidates.length !== 1) return null;
    const sensor = candidates[0];
    return sensor.online && Number.isFinite(sensor.temperature)
      && Date.now() / 1000 - sensor.updated_at <= 900 ? sensor : null;
  }
  async poll() {
    const generations = new Map([...this.devices].map(([id, d]) => [id, [d.generation, d.queue.busy]]));
    try {
      const response = await this.client.snapshot();
      if (this.stopped) return;
      if (response.protocol !== 1 || !Array.isArray(response.devices) || !Array.isArray(response.sensors)) {
        throw new Error('Incompatible response');
      }
      this.sensors = response.sensors;
      const seen = new Set();
      for (const state of response.devices) {
        if (typeof state.id !== 'string' || typeof state.name !== 'string') throw new Error('Invalid device');
        seen.add(state.id);
        let device = this.devices.get(state.id);
        if (!device) {
          const uuid = this.api.hap.uuid.generate(PLUGIN + ':' + state.id);
          const accessory = new this.api.platformAccessory(state.name, uuid);
          accessory.context.butlerId = state.id;
          device = new AcAccessory(this, accessory, state.id);
          this.devices.set(state.id, device);
          this.api.registerPlatformAccessories(PLUGIN, PLATFORM, [accessory]);
        }
        const initial = generations.get(state.id);
        if (!device.queue.busy && (!initial || (!initial[1] && initial[0] === device.generation))) device.apply(state);
      }
      // A successful catalog response may revoke/remove a device. Mark unavailable
      // instead of deleting HomeKit room assignments and automations on transient edits.
      for (const [id, device] of this.devices) if (!seen.has(id)) device.fail();
      if (this.disconnected) this.log.info('HomeButler 狀態同步已恢復。');
      this.disconnected = false;
    } catch {
      if (!this.disconnected) this.log.warn('HomeButler 狀態同步中斷；設備標示無回應，稍後重新讀取。');
      this.disconnected = true;
      for (const [id, device] of this.devices) {
        const initial = generations.get(id);
        if (!device.queue.busy && initial && initial[0] === device.generation) device.fail();
      }
    } finally {
      const seconds = Math.min(30, Math.max(5, Number(this.config.pollSeconds) || 5));
      if (!this.stopped) this.timer = setTimeout(() => this.poll(), seconds * 1000);
    }
  }
}

module.exports = api => api.registerPlatform(PLUGIN, PLATFORM, HomeButlerPlatform);
module.exports.HomeButlerPlatform = HomeButlerPlatform;
module.exports.AcAccessory = AcAccessory;
