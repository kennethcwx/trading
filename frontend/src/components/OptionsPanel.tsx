import type { OptionsResponse, OptionsOpportunity, OptionsSignal, LivePremium } from '../types'

const BADGE: Record<OptionsSignal['action'], { color: string; bg: string; border: string }> = {
  'SELL PUT':  { color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'rgba(0,200,5,0.25)' },
  'SELL CALL': { color: 'var(--teal)',   bg: 'var(--teal-dim)',   border: 'rgba(0,212,170,0.25)' },
  'WATCH':     { color: 'var(--yellow)', bg: 'var(--yellow-dim)', border: 'rgba(255,214,10,0.25)' },
  'AVOID':     { color: '#555',          bg: 'transparent',       border: 'transparent' },
}

function PremiumDisplay({ p, isCall }: { p: LivePremium | null; isCall: boolean }) {
  if (!p) return <span className="text-xs" style={{ color: '#444' }}>—</span>
  const spread = p.ask - p.bid
  const wide = spread > p.mid * 0.15
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold font-mono" style={{ color: isCall ? 'var(--teal)' : 'var(--green)' }}>
          ${p.mid.toFixed(2)}
        </span>
        <span className="text-xs" style={{ color: '#666' }}>mid · <span className="font-mono text-white">${p.total_contract.toFixed(0)}/contract</span></span>
      </div>
      <div className="text-xs font-mono" style={{ color: wide ? 'var(--amber)' : '#555' }}>
        Bid ${p.bid.toFixed(2)} / Ask ${p.ask.toFixed(2)}{wide ? ' ⚠ wide spread' : ''}
      </div>
      <div className="text-xs" style={{ color: '#555' }}>
        Expiry <span className="text-white">{p.expiry}</span> · <span className="text-white">{p.dte}d</span>
        {p.iv_pct !== null && <span> · IV <span className="text-white">{p.iv_pct}%</span></span>}
      </div>
    </div>
  )
}

function RsiBar({ rsi }: { rsi: number | null }) {
  if (rsi === null) return <span style={{ color: '#555' }}>—</span>
  const pct = Math.min(100, Math.max(0, rsi))
  const color = rsi > 70 ? 'var(--red)' : rsi < 40 ? 'var(--green)' : 'var(--yellow)'
  return (
    <div className="flex items-center gap-2">
      <div className="w-12 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-2)' }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{rsi.toFixed(0)}</span>
    </div>
  )
}

// ── Action card (SELL PUT / SELL CALL) ─────────────────────────────────────

function ActionCard({ opp }: { opp: OptionsOpportunity }) {
  const { options_signal, phase, live_premium } = opp
  const badge = BADGE[options_signal.action]
  const isCall = phase === 2

  return (
    <div className="rounded-2xl border p-5" style={{ background: badge.bg, borderColor: badge.border }}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {/* Left: ticker + badge + phase */}
        <div className="flex items-center gap-3">
          <span className="text-2xl font-bold text-white">{opp.symbol}</span>
          <div>
            <span className="text-sm font-bold px-3 py-1 rounded-full block"
              style={{ color: badge.color, background: badge.bg, border: `1px solid ${badge.border}` }}>
              {options_signal.action}
            </span>
            <span className="text-[10px] mt-1 block text-center"
              style={{ color: isCall ? 'var(--teal)' : 'var(--green)' }}>
              {isCall ? 'Phase 2 · CC' : 'Phase 1 · CSP'}
            </span>
          </div>
        </div>

        {/* Right: premium */}
        <PremiumDisplay p={live_premium} isCall={isCall} />
      </div>

      {/* Reason */}
      <p className="mt-3 text-sm" style={{ color: '#ccc' }}>{options_signal.reason}</p>

      {/* Strike + collateral + RSI row */}
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs font-mono" style={{ color: '#666' }}>
        <span>Strike <span className="text-white">${opp.suggested_strike}</span></span>
        {opp.collateral_sgd > 0 && (
          <span>Collateral <span style={{ color: 'var(--yellow)' }}>S${opp.collateral_sgd.toLocaleString()}</span></span>
        )}
        {opp.avg_cost && (
          <span>Avg cost <span className="text-white">${opp.avg_cost.toFixed(2)}</span></span>
        )}
        <span>RSI <span className="text-white">{opp.rsi?.toFixed(0) ?? '—'}</span></span>
        {opp.days_to_earnings !== null && (
          <span>Earnings <span className="text-white">{opp.days_to_earnings}d</span></span>
        )}
      </div>

      {/* Suggested action */}
      <p className="mt-2 text-xs font-mono" style={{ color: '#777' }}>{options_signal.suggested_action}</p>
    </div>
  )
}

// ── Watch card ──────────────────────────────────────────────────────────────

function WatchCard({ opp }: { opp: OptionsOpportunity }) {
  const { options_signal } = opp
  return (
    <div className="rounded-2xl border p-4" style={{ background: 'var(--yellow-dim)', borderColor: 'rgba(255,214,10,0.15)' }}>
      <div className="flex items-start justify-between gap-3">
        <span className="text-lg font-bold text-white">{opp.symbol}</span>
        <span className="text-[11px] font-bold px-2.5 py-1 rounded-full flex-shrink-0"
          style={{ color: 'var(--yellow)', background: 'rgba(255,214,10,0.12)', border: '1px solid rgba(255,214,10,0.25)' }}>
          WATCH
        </span>
      </div>
      <p className="text-sm mt-2" style={{ color: '#bbb' }}>{options_signal.reason}</p>
      <div className="mt-2 flex gap-4 text-xs font-mono" style={{ color: '#666' }}>
        <span>Strike <span className="text-white">${opp.suggested_strike}</span></span>
        <span className="flex items-center gap-1">RSI <RsiBar rsi={opp.rsi} /></span>
        {opp.days_to_earnings !== null && <span>Earnings <span className="text-white">{opp.days_to_earnings}d</span></span>}
      </div>
      <p className="mt-1.5 text-xs font-mono" style={{ color: '#555' }}>{options_signal.suggested_action}</p>
    </div>
  )
}

// ── Main panel ──────────────────────────────────────────────────────────────

export function OptionsPanel({ options }: { options: OptionsResponse }) {
  const actionable = options.opportunities.filter(
    o => o.options_signal.action === 'SELL PUT' || o.options_signal.action === 'SELL CALL'
  )
  const watching = options.opportunities.filter(o => o.options_signal.action === 'WATCH')
  const avoided  = options.opportunities.filter(o => o.options_signal.action === 'AVOID')

  const phase2 = actionable.filter(o => o.phase === 2)
  const phase1 = actionable.filter(o => o.phase === 1)

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-1">
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Wheel Strategy</h2>
        <span className="text-xs" style={{ color: '#555' }}>
          Verify IVR &gt; 30 on Market Chameleon before executing
        </span>
      </div>

      {/* No actionable — show empty state */}
      {actionable.length === 0 && (
        <div className="rounded-2xl border p-6 text-center" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
          <p className="text-sm font-medium text-white">No wheel entries right now.</p>
          <p className="text-xs mt-1" style={{ color: '#666' }}>
            {watching.length > 0 && `Watching ${watching.length} setup${watching.length > 1 ? 's' : ''} — conditions not quite there yet.`}
          </p>
        </div>
      )}

      {/* Action cards — Phase 2 (covered calls) first, then Phase 1 (CSP) */}
      {actionable.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[...phase2, ...phase1].map(opp => (
            <ActionCard key={opp.symbol} opp={opp} />
          ))}
        </div>
      )}

      {/* Watch section */}
      {watching.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
            Watching — not triggered yet
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {watching.map(opp => <WatchCard key={opp.symbol} opp={opp} />)}
          </div>
        </div>
      )}

      {/* Avoid footnote */}
      {avoided.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {avoided.map(opp => (
            <span key={opp.symbol} className="text-xs px-2.5 py-1 rounded-lg"
              style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#444' }}>
              {opp.symbol} — {opp.options_signal.reason}
            </span>
          ))}
        </div>
      )}

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-x-4 text-[11px]" style={{ color: '#444' }}>
        <span><span style={{ color: 'var(--teal)' }}>Phase 2</span> = owns shares → sell covered call</span>
        <span><span style={{ color: '#666' }}>Phase 1</span> = no position → sell cash-secured put</span>
      </div>
    </div>
  )
}
