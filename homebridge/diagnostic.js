'use strict';

// Separate UUID and local adapter: this accessory never enters the real device
// catalog or receives the platform's authenticated ButlerClient.
const ID = 'diagnostic-half-degree-v1';
const NAME = '半度測試空調';

function syncDiagnostic(platform, AcAccessory, plugin, alias) {
  const { api } = platform;
  const uuid = api.hap.uuid.generate(`${plugin}:${ID}`);
  const cached = platform.cached.get(uuid);
  if (cached) platform.cached.delete(uuid); // Never restore it as a real AC.
  if (platform.config.halfDegreeTest !== true) {
    if (cached) api.unregisterPlatformAccessories(plugin, alias, [cached]);
    return null;
  }
  const accessory = cached || new api.platformAccessory(NAME, uuid);
  accessory.context.butlerDiagnostic = ID;
  let state = { id: ID, name: NAME, location: '測試', power: 'on', mode: 'cool',
    temperature: 26, fan_speed: 'auto', uncertain: false };
  const local = {
    api,
    log: platform.log,
    temperatureSensor: () => ({ temperature: 26.8 }),
    client: {
      async command(id, patch) {
        if (id !== ID) throw new Error('Invalid diagnostic accessory');
        platform.log.info(`[半度測試] 收到 ${JSON.stringify(patch)}（僅模擬，未發送家電指令）`);
        if (!patch.off_if_mode || patch.off_if_mode.includes(state.mode)) {
          const { off_if_mode, ...values } = patch;
          state = { ...state, ...values };
        }
        return { status: 'success', device: { ...state } };
      },
    },
  };
  // Static local readings cannot become stale: no cloud poll or refresh timer.
  class DiagnosticAccessory extends AcAccessory {
    check() {}
  }
  const device = new DiagnosticAccessory(local, accessory, ID);
  accessory.getService(api.hap.Service.AccessoryInformation)
    .setCharacteristic(api.hap.Characteristic.Model, 'Half-degree diagnostic · no hardware');
  device.apply(state);
  // All properties, including minStep, exist before first registration.
  if (!cached) api.registerPlatformAccessories(plugin, alias, [accessory]);
  platform.log.info('[半度測試] 已啟用：目標 26°C、模擬室溫 26.8°C、步幅 0.5°C；重啟會重設測試值。');
  return device;
}

module.exports = { syncDiagnostic };
