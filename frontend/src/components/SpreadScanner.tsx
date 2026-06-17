import type { SpreadOpportunitiesResponse, SpreadOpportunity, SpreadSignal, BullPutSpread } from '../types'

const BADGE: Record<SpreadSignal['action'], { color: string; bg: string; border: string }> = {
  'OPEN SPREAD': { color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'rgba(0,200,5,0.25)' },
  'WATCH':       { color: 'var(--yellow)', bg: 'var(--yellow-dim)', border: 'rgba(255,214,10,0.25)' },
  'AVOID':       { color: '#555',          bg: 'transparent',       border: 'transparent' },
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

function SpreadDetails({ s, fitsAccount }: { s: BullPutSpread; fitsAccount: boolean | null }) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold font-mono" style={{ color: 'var(--green)' }}>
          ${s.net_credit.toFixed(2)}
        </span>
        <span className="text-xs" style={{ color: '#666' }}>
          credit · <span className="font-mono text-white">${s.max_profit.toFixed(0)}/spread</span>
        </span>
      </div>
      <div className="text-xs font-mono" style={{ color: '#555' }}>
        Sell ${s.short_strike} put / Buy ${s.long_strike} put · ${s.width.toFixed(0)} wide
      </div>
      <div className="text-xs font-mono">
        <span style={{ color: fitsAccount === false ? 'var(--amber)' : '#555' }}>
          Max loss <span className="text-white">${s.max_loss.toFixed(0)}</span>
        </span>
        {s.roi_pct !== null && <span style={{ color: '#555' }}> · ROI <span className="text-white">{s.roi_pct}%</span></span>}
        <span style={{ color: '#555' }}> · Breakeven <span className="text-white">${s.breakeven.toFixed(2)}</span></span>
      </div>
      <div className="text-xs" style={{ color: '#555' }}>
        Expiry <span className="text-white">{s.expiry}</span> · <span className="text-white">{s.dte}d</span>
        {s.short_iv_pct !== null && <span> · IV <span className="text-white">{s.short_iv_pct}%</span></span>}
      </div>
    </div>
  )
}

function SpreadCard({ opp }: { opp: SpreadOpportunity }) {
  const { signal, spread, fits_account } = opp
  const badge = BADGE[signal.action]

  return (
    <div className="rounded-2xl border p-5" style={{ background: badge.bg, borderColor: badge.border }}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="text-2xl font-bold text-white">{opp.symbol}</span>
          <span className="text-sm font-bold px-3 py-1 rounded-full"
            style={{ color: badge.color, background: badge.bg, border: `1px solid ${badge.border}` }}>
            {signal.action}
          </span>
        </div>
        {spread
          ? <SpreadDetails s={spread} fitsAccount={fits_account} />
          : <span className="text-xs" style={{ color: '#444' }}>No live spread quote</span>
        }
      </div>

      <p className="mt-3 text-sm" style={{ color: '#ccc' }}>{signal.reason}</p>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs font-mono" style={{ color: '#666' }}>
        <span>Price <span className="text-white">${opp.price.toFixed(2)}</span></span>
        <span className="flex items-center gap-1">RSI <RsiBar rsi={opp.rsi} /></span>
        {fits_account === false && (
          <span style={{ color: 'var(--amber)' }}>⚠ max loss exceeds half the options account</span>
        )}
        {opp.days_to_earnings !== null && (
          <span>Earnings <span className="text-white">{opp.days_to_earnings}d</span></span>
        )}
      </div>

      <p className="mt-2 text-xs font-mono" style={{ color: '#777' }}>{signal.suggested_action}</p>
    </div>
  )
}

function WatchCard({ opp }: { opp: SpreadOpportunity }) {
  const { signal } = opp
  return (
    <div className="rounded-2xl border p-4" style={{ background: 'var(--yellow-dim)', borderColor: 'rgba(255,214,10,0.15)' }}>
      <div className="flex items-start justify-between gap-3">
        <span className="text-lg font-bold text-white">{opp.symbol}</span>
        <span className="text-[11px] font-bold px-2.5 py-1 rounded-full flex-shrink-0"
          style={{ color: 'var(--yellow)', background: 'rgba(255,214,10,0.12)', border: '1px solid rgba(255,214,10,0.25)' }}>
          WATCH
        </span>
      </div>
      <p className="text-sm mt-2" style={{ color: '#bbb' }}>{signal.reason}</p>
      <div className="mt-2 flex gap-4 text-xs font-mono" style={{ color: '#666' }}>
        <span>Price <span className="text-white">${opp.price.toFixed(2)}</span></span>
        <span className="flex items-center gap-1">RSI <RsiBar rsi={opp.rsi} /></span>
      </div>
      <p className="mt-1.5 text-xs font-mono" style={{ color: '#555' }}>{signal.suggested_action}</p>
    </div>
  )
}

export function SpreadScanner({ spreads }: { spreads: SpreadOpportunitiesResponse }) {
  const open    = spreads.opportunities.filter(o => o.signal.action === 'OPEN SPREAD')
  const watch   = spreads.opportunities.filter(o => o.signal.action === 'WATCH')
  const avoided = spreads.opportunities.filter(o => o.signal.action === 'AVOID')

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-1">
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Bull Put Spreads · SPY/QQQ</h2>
        <span className="text-xs" style={{ color: '#555' }}>
          Options account ≈ S${spreads.account_size_sgd.toLocaleString()} (${spreads.account_size_usd.toLocaleString()})
        </span>
      </div>

      {open.length === 0 && (
        <div className="rounded-2xl border p-6 text-center" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
          <p className="text-sm font-medium text-white">No spread entries right now.</p>
          <p className="text-xs mt-1" style={{ color: '#666' }}>
            {watch.length > 0
              ? `Watching ${watch.length} setup${watch.length > 1 ? 's' : ''} — conditions not quite there yet.`
              : 'Conditions for a defined-risk credit spread aren’t lined up — check back after the next refresh.'}
          </p>
        </div>
      )}

      {open.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {open.map(opp => <SpreadCard key={opp.symbol} opp={opp} />)}
        </div>
      )}

      {watch.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
            Watching — not triggered yet
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {watch.map(opp => <WatchCard key={opp.symbol} opp={opp} />)}
          </div>
        </div>
      )}

      {avoided.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {avoided.map(opp => (
            <span key={opp.symbol} className="text-xs px-2.5 py-1 rounded-lg"
              style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#444' }}>
              {opp.symbol} — {opp.signal.reason}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 text-[11px]" style={{ color: '#444' }}>
        {spreads.note}
      </div>
    </div>
  )
}
