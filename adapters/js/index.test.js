// Author: Tom Sapletta · https://tom.sapletta.com
// Part of the ifURI solution.

import assert from 'node:assert/strict';
import test from 'node:test';
import { buildInvocation, dispatch, parseUri, uri } from './index.js';

test('parses URI into descriptor', () => {
  assert.deepEqual(parseUri('device://device-01/led/set/on?trace=1#ui'), {
    fragment: 'ui',
    package: 'device',
    query: { trace: '1' },
    raw: 'device://device-01/led/set/on?trace=1#ui',
    segments: ['led', 'set', 'on'],
    target: 'device-01',
  });
});

test('builds invocation from descriptor', () => {
  assert.deepEqual(buildInvocation({
    package: 'device',
    target: 'device-01',
    segments: ['led', 'set', 'on'],
  }), {
    args: ['device-01', 'on'],
    functionName: 'led_set',
    package: 'device',
    segments: ['led', 'set', 'on'],
    target: 'device-01',
  });
});

test('dispatches through explicit registry', async () => {
  const result = await dispatch('device://device-01/led/set/on', {
    device: {
      led_set(target, state, payload) {
        return { ok: true, payload, state, target };
      },
    },
  }, { source: 'test' });
  assert.deepEqual(result, {
    ok: true,
    payload: { source: 'test' },
    state: 'on',
    target: 'device-01',
  });
});

test('rejects missing packages and functions', async () => {
  await assert.rejects(() => dispatch('device://device-01/led/set/on', {}, {}), /Unknown package/);
  await assert.rejects(() => dispatch('device://device-01/led/set/on', { device: {} }, {}), /Unknown function/);
});

test('invokes uri() with single payload, inline URI+JSON, and JSONL batch', async () => {
  const registry = {
    plesk: {
      site_command(target, action, payload) {
        return { ok: true, target, action, domain: payload.domain };
      },
    },
  };

  const single = await uri('plesk://host/site/command/sync', { domain: 'heyasa.com' }, { registry });
  assert.deepEqual(single, { ok: true, target: 'host', action: 'sync', domain: 'heyasa.com' });

  const inline = await uri('plesk://host/site/command/sync {"domain":"get.heyasa.com"}', null, { registry });
  assert.deepEqual(inline, { ok: true, target: 'host', action: 'sync', domain: 'get.heyasa.com' });

  const batch = await uri(
    'plesk://host/site/command/sync',
    '{"domain":"heyasa.com"}\n{"domain":"get.heyasa.com"}',
    { registry },
  );
  assert.equal(batch.length, 2);
  assert.equal(batch[0].domain, 'heyasa.com');
  assert.equal(batch[1].domain, 'get.heyasa.com');
});

