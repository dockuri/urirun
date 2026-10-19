// urirun — TypeScript SDK for building urirun.bindings.v2 documents.

export const BINDINGS_VERSION = "urirun.bindings.v2";

export interface Schema {
  required?: string[];
  properties?: Record<string, unknown>;
}

export class Connector {
  private bindings: Record<string, unknown> = {};
  constructor(private id: string, private scheme: string, private target = "host") {}

  command(route: string, schema: Schema, argv: string[]): this {
    const uri = `${this.scheme}://${this.target}/${route}`;
    const input: Record<string, unknown> = {
      type: "object",
      additionalProperties: false,
      properties: schema.properties ?? {},
    };
    if (schema.required && schema.required.length) input.required = schema.required;
    this.bindings[uri] = {
      uri, kind: "command", adapter: "argv-template",
      inputSchema: input, argv,
      meta: { connector: this.id }, policy: { allowExecute: true },
    };
    return this;
  }

  document(): Record<string, unknown> {
    return { version: BINDINGS_VERSION, bindings: this.bindings };
  }

  toJSON(): string {
    return JSON.stringify(this.document(), null, 2);
  }
}

export function connector(id: string, opts: { scheme: string; target?: string }): Connector {
  return new Connector(id, opts.scheme, opts.target);
}

export interface UriOptions {
  registry?: Record<string, Record<string, (...args: unknown[]) => unknown>>;
  nodeUrl?: string;
  mode?: "execute" | "dry-run";
  cli?: string;
}

const URI_PREFIX_RE = /^([a-z][a-z0-9+.-]*:\/\/[^\s]+)[\s\r\n]+([\s\S]+)$/i;

export function parseUriInvocations(
  address = "",
  payloadOrJsonl: unknown = null,
): { steps: Array<{ uri: string; payload: Record<string, unknown> }>; isBatch: boolean } {
  let rawAddr = String(address || "").trim();
  let inlineText = typeof payloadOrJsonl === "string" ? payloadOrJsonl : null;
  const payload = typeof payloadOrJsonl === "string" ? null : payloadOrJsonl;

  if (inlineText === null && payload === null && rawAddr) {
    const m = rawAddr.match(URI_PREFIX_RE);
    if (m && (m[2].trimStart().startsWith("{") || m[2].trimStart().startsWith("["))) {
      rawAddr = m[1];
      inlineText = m[2];
    }
  }

  const toStep = (item: unknown): { uri: string; payload: Record<string, unknown> } => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      if (!rawAddr) throw new Error("URI address is required");
      return { uri: rawAddr, payload: { value: item } };
    }
    const obj = item as Record<string, unknown>;
    const stepUri = String(obj.uri || rawAddr || "").trim();
    if (!stepUri) throw new Error("URI address is required");
    if (obj.payload && typeof obj.payload === "object" && !Array.isArray(obj.payload)) {
      return { uri: stepUri, payload: { ...(obj.payload as Record<string, unknown>) } };
    }
    return { uri: stepUri, payload: { ...obj } };
  };

  if (inlineText !== null) {
    const lines = inlineText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (!lines.length) {
      return { steps: rawAddr ? [{ uri: rawAddr, payload: {} }] : [], isBatch: !rawAddr };
    }
    const steps: Array<{ uri: string; payload: Record<string, unknown> }> = [];
    let isBatch = lines.length > 1 || inlineText.trim().includes("\n");
    for (const line of lines) {
      const parsed = JSON.parse(line);
      if (Array.isArray(parsed)) {
        isBatch = true;
        for (const sub of parsed) steps.push(toStep(sub));
      } else {
        steps.push(toStep(parsed));
      }
    }
    return { steps, isBatch };
  }

  if (Array.isArray(payload)) {
    return { steps: payload.map(toStep), isBatch: true };
  }

  return { steps: [toStep(payload ?? {})], isBatch: false };
}

export async function uri(
  address = "",
  payloadOrJsonl: unknown = null,
  options: UriOptions = {},
): Promise<unknown> {
  const { steps, isBatch } = parseUriInvocations(address, payloadOrJsonl);
  const results: unknown[] = [];
  for (const step of steps) {
    if (options.nodeUrl && typeof fetch === "function") {
      const res = await fetch(`${options.nodeUrl.replace(/\/+$/, "")}/call`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uri: step.uri, payload: step.payload, mode: options.mode ?? "execute" }),
      });
      results.push(await res.json());
    } else {
      results.push({ ok: true, uri: step.uri, payload: step.payload });
    }
  }
  return isBatch ? results : (results[0] ?? {});
}

