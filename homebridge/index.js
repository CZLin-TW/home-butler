'use strict';
const { ButlerClient, CommandQueue } = require('./client');
const { syncDiagnostic } = require('./diagnostic');
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
    this.service.setPrimaryService(true);
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
    // HAP has no dry/fan target enum. Separate switches represent those modes;
    // the thermal selector retains its last cool/heat choice while they run.
    this.service.getCharacteristic(C.TargetHeaterCoolerState).updateValue(2).setProps({ validValues: [1, 2] });
    this.bind(C.TargetHeaterCoolerState, () => accessory.context.butlerThermalMode === 'heat' ? 1 : 2, value => {
      if (![1, 2].includes(value)) throw this.error();
      return { mode: value === 1 ? 'heat' : 'cool', power: 'on' };
    });
    for (const type of [C.CoolingThresholdTemperature, C.HeatingThresholdTemperature]) {
      this.service.getCharacteristic(type)
        .updateValue(16).setProps({ minValue: 16, maxValue: 30, minStep: 0.5 });
      this.bind(type, () => {
        if (!Number.isFinite(this.state.temperature) || !Number.isInteger(this.state.temperature * 2)
          || this.state.temperature < 16 || this.state.temperature > 30) throw this.error();
        return this.state.temperature;
      }, value => ({ temperature: value }));
    }
    this.bind(C.CurrentHeaterCoolerState, () => {
      if (this.state.power === 'off') return 0;
      // Inferred operating mode, not compressor feedback or physical readback.
      return this.state.mode === 'cool' ? 3 : this.state.mode === 'heat' ? 2 : 1;
    });
    this.bind(C.CurrentTemperature, () => {
      const sensor = platform.temperatureSensor(this.state);
      if (!sensor) throw this.error();
      return sensor.temperature;
    });
    this.service.getCharacteristic(C.TemperatureDisplayUnits).updateValue(0);
    for (const [mode, label] of [['dry', '除濕'], ['fan', '送風']]) {
      const name = `${accessory.displayName}${label}`;
      const service = accessory.getServiceById(S.Switch, `mode-${mode}`)
        || accessory.addService(S.Switch, name, `mode-${mode}`);
      service.setCharacteristic(C.Name, name);
      if (!service.testCharacteristic(C.ConfiguredName)) {
        service.addOptionalCharacteristic(C.ConfiguredName);
        service.setCharacteristic(C.ConfiguredName, name);
      }
      this.bind(C.On, () => this.state.power === 'on' && this.state.mode === mode,
        value => value ? { power: 'on', mode } : { power: 'off', off_if_mode: [mode] }, service);
    }
    this.fail(); // Construction defaults are never advertised as live readings.
  }
  error() { return new this.platform.api.hap.HapStatusError(-70402); }
  check() {
    if (!this.available || Date.now() - this.lastSeen > 45000) throw this.error();
  }
  bind(type, read, write, service = this.service) {
    const characteristic = service.getCharacteristic(type);
    this.readers.set(characteristic, read);
    characteristic.onGet(() => { this.check(); return read(); });
    if (write) characteristic.onSet(value => this.queue.enqueue(write(value)));
  }
  apply(state) {
    this.state = state;
    this.available = !state.uncertain && ['on', 'off'].includes(state.power)
      && ['cool', 'heat', 'dry', 'fan'].includes(state.mode);
    if (this.available && ['cool', 'heat'].includes(state.mode)) {
      this.accessory.context.butlerThermalMode = state.mode;
    }
    this.lastSeen = Date.now();
    this.publish();
  }
  publish() {
    for (const [characteristic, read] of this.readers) {
      // Invoke only GET handlers; updateValue does not call control SET handlers.
      try {
        this.check();
        const value = read();
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
      this.diagnostic = syncDiagnostic(this, AcAccessory, PLUGIN, PLATFORM);
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
      this.diagnostic?.queue.close();
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
