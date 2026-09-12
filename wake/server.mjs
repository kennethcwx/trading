/**
 * Wake the Render backend on behalf of a scheduler that cannot.
 *
 * The backend sleeps by design (750 free hours a month, shared), and cron-job.org
 * knocks its /health to wake it for each window. Read on 2026-09-12: every
 * knock that lands on a COLD box gets an instant `503 Service Unavailable` from
 * Render's edge — 07:10 ✗ 07:20 ✗ 07:30 ✓, 16:00 ✗ 16:10 ✗ 16:20 ✓ — while the
 * same request from a laptop is held ~70 s and answered 200. Same method, same
 * user-agent, same HTTP version, no AAAA record; whatever Render's edge keys on
 * is not visible from outside, and three US entry windows were lost finding
 * out. The mornings only worked because something else happened to be awake.
 *
 * So the scheduler stops talking to Render at all. It hits this function on
 * Vercel, which answers 200 in milliseconds and keeps its own connection to
 * Render open — via the request context's `waitUntil` — until the box is up.
 * The scheduler sees a healthy job every time; the wake happens regardless.
 *
 * The original scheduler's user-agent is passed through, so the backend's knock
 * log (/api/schedules → knocks) still says who knocked.
 */

import http from 'node:http';

const TARGET = 'https://trading-backend-wruf.onrender.com/health';
// Cold starts have been measured at 51–71 s; give the hold room past that.
const HOLD_MS = 110_000;

/** Knock Render and keep the connection open until it answers or HOLD_MS. */
function knock(via) {
  const started = Date.now();
  return fetch(TARGET, {
    headers: { 'user-agent': `wake-proxy/1.0 via ${via}`.slice(0, 200) },
    signal: AbortSignal.timeout(HOLD_MS),
  })
    .then((r) => ({ status: r.status, ms: Date.now() - started }))
    .catch((e) => ({ status: null, error: e?.name ?? String(e), ms: Date.now() - started }));
}

const server = http.createServer((req, res) => {
  const via = req.headers['user-agent'] ?? 'unknown';
  const pending = knock(via);
  pending.then((r) => console.log('wake', JSON.stringify({ via, ...r })));

  // Answer at once; the knock runs on. Vercel's request context lets the work
  // outlive the response where it exists (this is what @vercel/functions'
  // waitUntil wraps); on a plain Node server the process simply stays up.
  const ctx = globalThis[Symbol.for('@vercel/request-context')]?.get?.();
  if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(pending);

  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ ok: true, target: TARGET, background: Boolean(ctx?.waitUntil) }));
});

export default server;

// Local run: `node server.mjs` and hit http://localhost:8787/
if (process.argv[1] && process.argv[1].endsWith('server.mjs') && !process.env.VERCEL) {
  server.listen(8787, () => console.log('wake proxy on :8787'));
}
