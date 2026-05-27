export interface Env {
  TV_SIGNALS_KV: KVNamespace;
  TV_SHARED_SECRET: string;
  TV_REQUIRE_HMAC?: string;
  TV_ALLOWED_SYMBOLS?: string;
  TV_ALLOWED_TIMEFRAMES?: string;
  SIGNAL_TTL_SECONDS?: string;
  READ_TOKEN?: string;
  GITHUB_TOKEN?: string;
  GITHUB_OWNER_REPO?: string;
  GITHUB_BRANCH?: string;
  GITHUB_AUDIT_PATH?: string;
}

type TvPayload = {
  secret?: string;
  source?: string;
  strategy?: string;
  symbol?: string;
  ticker?: string;
  timeframe?: string;
  interval?: string;
  side?: string;
  direction?: string;
  price?: string | number;
  time?: string | number;
  [key: string]: unknown;
};

type NormalizedSignal = {
  source: "tradingview";
  strategy: string;
  symbol: string;
  timeframe: string;
  side: "BUY" | "SELL";
  price: number | null;
  time: string;
  received_at: string;
  idempotency_key: string;
};

const DEFAULT_TTL_SECONDS = 36 * 60 * 60;
const DEFAULT_ALLOWED_SYMBOLS = "EURUSD,GBPUSD,USDJPY,AUDUSD,NZDUSD,USDCAD";
const DEFAULT_ALLOWED_TIMEFRAMES = "1,3,5,15,30,45,60,120,180,240,D,1D";

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "sabo-tradingview-webhook" });
    }

    if (request.method === "GET" && url.pathname === "/signals/today") {
      return handleSignalsToday(request, env);
    }

    if (request.method === "POST" && url.pathname === "/tradingview/webhook") {
      return handleTradingViewWebhook(request, env, ctx);
    }

    return json({ ok: false, error: "not_found" }, 404);
  },
};

async function handleTradingViewWebhook(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  const rawBody = await request.text();
  let payload: TvPayload;
  try {
    payload = JSON.parse(rawBody) as TvPayload;
  } catch {
    return json({ ok: false, error: "invalid_json" }, 400);
  }

  const auth = await authenticate(request, rawBody, payload, env);
  if (!auth.ok) {
    return json({ ok: false, error: auth.error }, auth.status);
  }

  const signal = await normalizePayload(payload);
  const validation = validateSignal(signal, env);
  if (!validation.ok) {
    return json({ ok: false, error: validation.error }, 400);
  }

  const ttl = parsePositiveInt(env.SIGNAL_TTL_SECONDS, DEFAULT_TTL_SECONDS);
  const dedupKey = `tv:dedup:${signal.idempotency_key}`;
  const existing = await env.TV_SIGNALS_KV.get(dedupKey);
  if (existing) {
    return json({
      ok: true,
      duplicate: true,
      idempotency_key: signal.idempotency_key,
    });
  }

  await env.TV_SIGNALS_KV.put(dedupKey, "1", { expirationTtl: ttl });

  const date = signal.time.slice(0, 10);
  const signalKey = [
    "tv:signal",
    date,
    signal.symbol,
    signal.timeframe,
    signal.side,
    signal.idempotency_key,
  ].join(":");
  await env.TV_SIGNALS_KV.put(signalKey, JSON.stringify(signal), { expirationTtl: ttl });

  ctx.waitUntil(
    appendGithubAudit(signal, env).catch((error) => {
      console.error("github_audit_failed", error);
    }),
  );

  return json({
    ok: true,
    accepted: true,
    idempotency_key: signal.idempotency_key,
    signal,
  });
}

async function handleSignalsToday(request: Request, env: Env): Promise<Response> {
  if (env.READ_TOKEN) {
    const token = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
    if (!constantTimeEqual(token, env.READ_TOKEN)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
  }

  const url = new URL(request.url);
  const date = url.searchParams.get("date") || new Date().toISOString().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return json({ ok: false, error: "invalid_date" }, 400);
  }

  const entries = await env.TV_SIGNALS_KV.list({ prefix: `tv:signal:${date}:` });
  const signals: NormalizedSignal[] = [];
  await Promise.all(entries.keys.map(async (key) => {
    const raw = await env.TV_SIGNALS_KV.get(key.name);
    if (!raw) return;
    try {
      signals.push(JSON.parse(raw) as NormalizedSignal);
    } catch {
      return;
    }
  }));

  signals.sort((a, b) => a.time.localeCompare(b.time));
  return json({ ok: true, date, count: signals.length, signals });
}

async function authenticate(
  request: Request,
  rawBody: string,
  payload: TvPayload,
  env: Env,
): Promise<{ ok: true } | { ok: false; status: number; error: string }> {
  const sharedSecret = env.TV_SHARED_SECRET || "";
  if (!sharedSecret) {
    return { ok: false, status: 500, error: "missing_shared_secret" };
  }

  const headerSig = request.headers.get("x-sabo-sig") || request.headers.get("x-sabo-signature");
  const requireHmac = env.TV_REQUIRE_HMAC === "1" || env.TV_REQUIRE_HMAC?.toLowerCase() === "true";
  if (headerSig) {
    const expected = await hmacSha256Hex(sharedSecret, rawBody);
    const normalizedHeader = headerSig.replace(/^sha256=/i, "").trim().toLowerCase();
    if (!constantTimeEqual(normalizedHeader, expected)) {
      return { ok: false, status: 401, error: "bad_signature" };
    }
    return { ok: true };
  }

  if (requireHmac) {
    return { ok: false, status: 401, error: "missing_signature" };
  }

  const bodySecret = String(payload.secret || "");
  if (!constantTimeEqual(bodySecret, sharedSecret)) {
    return { ok: false, status: 401, error: "bad_secret" };
  }
  return { ok: true };
}

async function normalizePayload(payload: TvPayload): Promise<NormalizedSignal> {
  const symbol = normalizeSymbol(String(payload.symbol || payload.ticker || ""));
  const timeframe = normalizeTimeframe(String(payload.timeframe || payload.interval || ""));
  const side = normalizeSide(String(payload.side || payload.direction || ""));
  const eventTime = normalizeTime(payload.time);
  const idempotency_key = await sha256Hex(`${symbol}|${timeframe}|${side}|${eventTime}`);

  return {
    source: "tradingview",
    strategy: String(payload.strategy || "tradingview_alert"),
    symbol,
    timeframe,
    side,
    price: parsePrice(payload.price),
    time: eventTime,
    received_at: new Date().toISOString(),
    idempotency_key,
  };
}

function validateSignal(
  signal: NormalizedSignal,
  env: Env,
): { ok: true } | { ok: false; error: string } {
  const allowedSymbols = csvSet(env.TV_ALLOWED_SYMBOLS || DEFAULT_ALLOWED_SYMBOLS, normalizeSymbol);
  if (!signal.symbol || !allowedSymbols.has(signal.symbol)) {
    return { ok: false, error: "symbol_not_allowed" };
  }

  const allowedTimeframes = csvSet(env.TV_ALLOWED_TIMEFRAMES || DEFAULT_ALLOWED_TIMEFRAMES, normalizeTimeframe);
  if (!signal.timeframe || !allowedTimeframes.has(signal.timeframe)) {
    return { ok: false, error: "timeframe_not_allowed" };
  }

  if (signal.side !== "BUY" && signal.side !== "SELL") {
    return { ok: false, error: "side_not_allowed" };
  }

  return { ok: true };
}

async function appendGithubAudit(signal: NormalizedSignal, env: Env): Promise<void> {
  if (!env.GITHUB_TOKEN || !env.GITHUB_OWNER_REPO) {
    return;
  }

  const path = env.GITHUB_AUDIT_PATH || "sabo_lit/sandbox/live/tradingview_signals.jsonl";
  const branch = env.GITHUB_BRANCH || "main";
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const apiUrl = `https://api.github.com/repos/${env.GITHUB_OWNER_REPO}/contents/${encodedPath}`;

  let sha: string | undefined;
  let existing = "";
  const getResp = await fetch(`${apiUrl}?ref=${encodeURIComponent(branch)}`, {
    headers: githubHeaders(env.GITHUB_TOKEN),
  });
  if (getResp.ok) {
    const body = await getResp.json() as { content?: string; sha?: string };
    sha = body.sha;
    existing = decodeBase64(body.content || "");
  } else if (getResp.status !== 404) {
    throw new Error(`github_get_failed_${getResp.status}`);
  }

  const line = `${JSON.stringify(signal)}\n`;
  if (existing.includes(`"idempotency_key":"${signal.idempotency_key}"`)) {
    return;
  }

  const content = encodeBase64(existing.endsWith("\n") || existing.length === 0 ? existing + line : `${existing}\n${line}`);
  const putBody: Record<string, unknown> = {
    branch,
    message: `Append TradingView signal ${signal.symbol} ${signal.side}`,
    content,
  };
  if (sha) {
    putBody.sha = sha;
  }

  const putResp = await fetch(apiUrl, {
    method: "PUT",
    headers: githubHeaders(env.GITHUB_TOKEN),
    body: JSON.stringify(putBody),
  });
  if (!putResp.ok) {
    throw new Error(`github_put_failed_${putResp.status}`);
  }
}

function githubHeaders(token: string): HeadersInit {
  return {
    "Authorization": `Bearer ${token}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "sabo-tradingview-worker",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function normalizeSymbol(value: string): string {
  const raw = value.trim().toUpperCase();
  const withoutPrefix = raw.includes(":") ? raw.split(":").pop() || raw : raw;
  return withoutPrefix.replace(/[^A-Z0-9]/g, "");
}

function normalizeTimeframe(value: string): string {
  const raw = value.trim().toUpperCase();
  if (raw === "1D") return "D";
  if (raw === "1H") return "60";
  if (raw === "4H") return "240";
  return raw;
}

function normalizeSide(value: string): "BUY" | "SELL" {
  const raw = value.trim().toUpperCase();
  if (["BUY", "LONG", "BULL", "1"].includes(raw)) return "BUY";
  if (["SELL", "SHORT", "BEAR", "-1"].includes(raw)) return "SELL";
  return "" as "BUY" | "SELL";
}

function normalizeTime(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    const millis = value > 10_000_000_000 ? value : value * 1000;
    return new Date(millis).toISOString();
  }
  const raw = String(value || "").trim();
  if (!raw) return new Date().toISOString();
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return new Date().toISOString();
  return parsed.toISOString();
}

function parsePrice(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function csvSet(value: string, normalize: (part: string) => string): Set<string> {
  return new Set(
    value
      .split(",")
      .map((part) => normalize(part))
      .filter(Boolean),
  );
}

async function hmacSha256Hex(secret: string, body: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  return bytesToHex(new Uint8Array(signature));
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return bytesToHex(new Uint8Array(digest));
}

function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i += 1) {
    out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return out === 0;
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function decodeBase64(value: string): string {
  if (!value) return "";
  const binary = atob(value.replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encodeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}
