// Author: Tom Sapletta · https://tom.sapletta.com
// Part of the ifURI solution.

export function parseUri(uri) {
  const match = String(uri).match(/^(?<scheme>[a-z][a-z0-9+.-]*):\/\/(?<target>[^/?#]+)(?<path>\/[^?#]*)?(?:\?(?<query>[^#]*))?(?:#(?<fragment>.*))?$/i);
  if (!match) throw new Error(`Invalid URI: ${uri}`);
  const path = match.groups.path || '/';
  const segments = path.split('/').filter(Boolean).map(decodeURIComponent);
  return {
    package: match.groups.scheme,
    target: decodeURIComponent(match.groups.target),
    segments,
    query: Object.fromEntries(new URLSearchParams(match.groups.query || '')),
    fragment: match.groups.fragment || null,
    raw: uri,
  };
}

export function buildInvocation(descriptor) {
  const functionName = descriptor.segments.slice(0, 2).join('_');
  const args = [descriptor.target, ...descriptor.segments.slice(2)];
  return { ...descriptor, functionName, args };
}

export async function dispatch(uri, registry, payload) {
  const descriptor = parseUri(uri);
  const invocation = buildInvocation(descriptor);
  const mod = registry[invocation.package];
  if (!mod) throw new Error(`Unknown package: ${invocation.package}`);
  const fn = mod[invocation.functionName];
  if (typeof fn !== 'function') throw new Error(`Unknown function: ${invocation.package}.${invocation.functionName}`);
  return await fn(...invocation.args, payload, invocation);
}

const URI_PREFIX_RE = /^([a-z][a-z0-9+.-]*:\/\/[^\s]+)[\s\r\n]+([\s\S]+)$/i;

function extractStepItem(defaultUri, item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    if (!defaultUri) throw new Error('URI address is required');
    return { uri: defaultUri, payload: { value: item } };
  }
  const stepUri = String(item.uri || defaultUri || '').trim();
  if (!stepUri) throw new Error("URI address is required either as argument or 'uri' field in JSONL item");
  if (item.payload && typeof item.payload === 'object' && !Array.isArray(item.payload)) {
    return { uri: stepUri, payload: { ...item.payload } };
  }
  if (item.uri && !defaultUri) {
    const rest = { ...item };
    delete rest.uri;
    return { uri: stepUri, payload: rest };
  }
  return { uri: stepUri, payload: { ...item } };
}

export function parseUriInvocations(address = '', payloadOrJsonl = null) {
  let rawAddr = String(address || '').trim();
  let inlineText = typeof payloadOrJsonl === 'string' ? payloadOrJsonl : null;
  let payload = typeof payloadOrJsonl === 'string' ? null : payloadOrJsonl;

  if (inlineText === null && payload === null && rawAddr) {
    const m = rawAddr.match(URI_PREFIX_RE);
    if (m && (m[2].trimStart().startsWith('{') || m[2].trimStart().startsWith('['))) {
      rawAddr = m[1];
      inlineText = m[2];
    }
  }

  if (inlineText !== null) {
    const lines = inlineText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (!lines.length) {
      return { steps: rawAddr ? [{ uri: rawAddr, payload: {} }] : [], isBatch: !rawAddr };
    }
    const steps = [];
    let isBatch = lines.length > 1 || inlineText.trim().includes('\n');
    for (const line of lines) {
      const parsed = JSON.parse(line);
      if (Array.isArray(parsed)) {
        isBatch = true;
        for (const sub of parsed) steps.push(extractStepItem(rawAddr, sub));
      } else {
        steps.push(extractStepItem(rawAddr, parsed));
      }
    }
    return { steps, isBatch };
  }

  if (Array.isArray(payload)) {
    return { steps: payload.map((item) => extractStepItem(rawAddr, item)), isBatch: true };
  }

  return { steps: [extractStepItem(rawAddr, payload ?? {})], isBatch: false };
}

async function invokeSingleUri(stepUri, stepPayload, options = {}) {
  const descriptor = parseUri(stepUri);
  const mergedPayload = { ...descriptor.query, ...(stepPayload || {}) };
  if (options.registry) {
    return await dispatch(stepUri, options.registry, mergedPayload);
  }
  const nodeUrl = options.nodeUrl || (typeof process !== 'undefined' ? process.env?.URIRUN_NODE_URL : undefined);
  if (nodeUrl && typeof fetch === 'function') {
    const res = await fetch(`${nodeUrl.replace(/\/+$/, '')}/call`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri: stepUri, payload: mergedPayload, mode: options.mode || 'execute' }),
    });
    return await res.json();
  }
  const { execFileSync } = await import('node:child_process');
  const cliBin = options.cli || 'uri';
  const args = [stepUri, JSON.stringify(mergedPayload)];
  if (options.mode === 'dry-run') args.push('--dry-run');
  const stdout = execFileSync(cliBin, args, { encoding: 'utf8' });
  return JSON.parse(stdout);
}

export async function uri(address = '', payloadOrJsonl = null, options = {}) {
  const { steps, isBatch } = parseUriInvocations(address, payloadOrJsonl);
  const results = [];
  for (const step of steps) {
    results.push(await invokeSingleUri(step.uri, step.payload, options));
  }
  return isBatch ? results : (results[0] ?? {});
}

