'use strict';
const { randomUUID } = require('node:crypto');

class ButlerClient {
  constructor(baseUrl, key, transport = fetch) {
    const url = new URL(baseUrl);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
      throw new Error('Use the HTTPS HomeButler backend URL without credentials/query/fragment');
    }
    if (typeof key !== 'string' || key.length < 32) throw new Error('Configure the dedicated bridge key');
    this.baseUrl = url.href.replace(/\/$/, '');
    this.key = key;
    this.transport = transport;
  }

  async request(path, body) {
    // Do not follow redirects carrying the key, retry writes, or log server bodies.
    const response = await this.transport(this.baseUrl + path, {
      method: body ? 'POST' : 'GET', redirect: 'error',
      headers: { 'X-API-Key': this.key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) throw new Error(`HomeButler HTTP ${response.status}`);
    return response.json();
  }

  snapshot() { return this.request('/api/homebridge/devices'); }
  command(id, patch) {
    return this.request(`/api/homebridge/devices/${encodeURIComponent(id)}/ac`,
      { ...patch, request_id: randomUUID() });
  }
}

// One command at a time per accessory. HomeKit often writes several attributes
// for one gesture; merge them before dispatch and retain every caller's promise.
class CommandQueue {
  constructor(send, delay = 300) {
    this.send = send;
    this.delay = delay;
    this.pending = null;
    this.waiters = [];
    this.running = false;
    this.closed = false;
  }
  get busy() { return this.running || this.pending !== null; }
  enqueue(patch) {
    if (this.closed) return Promise.reject(new Error('Bridge stopped'));
    // Off wins over companion temperature/mode writes within the same gesture.
    this.pending = { ...this.pending, ...patch };
    if (this.pending.power === 'off') this.pending = { power: 'off' };
    const promise = new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
    if (!this.running) this.schedule();
    return promise;
  }
  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), this.delay);
  }
  async flush() {
    const patch = this.pending;
    if (!patch || this.running || this.closed) return;
    const waiters = this.waiters;
    this.pending = null;
    this.waiters = [];
    this.running = true;
    try {
      const value = await this.send(patch);
      for (const waiter of waiters) waiter.resolve(value);
    } catch (error) {
      for (const waiter of waiters) waiter.reject(error);
      // A command may have reached the appliance. Drop queued gestures too;
      // require fresh state and a new explicit user action after an unknown result.
      for (const waiter of this.waiters) waiter.reject(error);
      this.waiters = [];
      this.pending = null;
    } finally {
      this.running = false;
      if (this.pending && !this.closed) this.schedule();
    }
  }
  close() {
    this.closed = true;
    clearTimeout(this.timer);
    for (const waiter of this.waiters) waiter.reject(new Error('Bridge stopped'));
    this.waiters = [];
    this.pending = null;
  }
}

module.exports = { ButlerClient, CommandQueue };
