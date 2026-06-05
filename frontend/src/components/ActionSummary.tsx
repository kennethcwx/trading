import type { SignalsResponse, SignalItem, SignalAction } from '../types'

const BADGE: Record<SignalAction, { color: string; bg: string; border: string }> = {
  BUY:       { color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'rgba(0,200,5,0.3)' },
  SELL:      { color: 'var(--red)',    bg: 'var(--red-dim)',    border: 'rgba(255,59,48,0.3)' },
  SELL_HALF: { color: 'var(--orange)', bg: 'var(--orange-dim)', border: 'rgba(255,149,0,0.3)' },
  REVIEW:    { color: 'var(--amber)',  bg: 'var(--amber-dim)',  border: 'rgba(255,159,10,0.3)' },
  HOLD:      { color: '#888',          bg: 'rgba(255,255,255,0.04)', border: 'var(--border-2)' },
  WATCH:     { color: 'var(--yellow)', bg: 'var(--yellow-dim)', border: 'rgba(255,214,10,0.3)' },
  SKIP:      { color: '#555',          bg: 'transparent',       border: 'transparent' },
}

function plainReason(item: SignalItem): string {
  const r = item.signal.reasons[0] ?? ''
  const action = item.signal.action
  if (action === 'BUY') {
    if (r.includes('Mean-reversion')) return 'Pulled back to entry zone, still in an uptrend'
    if (r.includes('Momentum')) return 'Breaking to new highs on strong volume'
    return r
  }
  if (action === 'SELL' || action === 'SELL_HALF') {
    if (r.includes('Stop loss')) return 'Stop loss hit — exit to protect your capital'
    if (r.includes('RSI overbought')) return 'Overbought — take some profit'
    if (r.includes('Profit target')) return 'Price target reached — take gains'
    if (r.includes('200-day SMA') || r.includes('trend broken')) return 'Broke below the long-term trend — exit'
    if (r.includes('Time stop')) return 'Held too long without moving — free up the capital'
    return r
  }
  if (action === 'REVIEW') {
    const days = r.match(/\d+/)?.[0]
    return `Earnings in ${days ?? 'a few'} days — hold or exit before then`
  }
  if (action === 'WATCH') return 'Nearing buy zone — watch closely'
  return r
}

function plainAction(item: SignalItem): string {
  const { signal, analysis, position_size } = item
  if (signal.action === 'BUY' && position_size) {
    return `Buy ${position_size.shares.toFixed(2)} shares at ~$${analysis.price.toFixed(2)} · Stop $${analysis.stop_loss.toFixed(2)} · Target $${analysis.profit_target.toFixed(2)}`
  }
  if (signal.action === 'SELL_HALF') return 'Sell half at market open · Move stop to breakeven on the rest'
  if (signal.action === 'SELL') return 'Close the full position at market open'
  if (signal.action === 'REVIEW') return 'Decide before earnings: hold, exit, or trim'
  return signal.suggested_action
}

function RsiProgress({ rsi, suggestedAction }: { rsi: number | null; suggestedAction: string }) {
  if (rsi === null) return null
  const trigger = 40
  const pct = Math.min(100, Math.max(0, ((70 - rsi) / (70 - trigger)) * 100))
  const color = rsi < 45 ? 'var(--green)' : rsi < 50 ? 'var(--yellow)' : 'var(--orange)'
  return (
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center justify-between text-xs" style={{ color: '#666' }}>
        <span>RSI to trigger</span>
        <span className="font-mono" style={{ color }}>{rsi.toFixed(0)} → need &lt;40</span>
      </div>
      <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-2)' }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <p className="text-xs font-mono" style={{ color: '#777' }}>{suggestedAction}</p>
    </div>
  )
}

function WatchCard({ item }: { item: SignalItem }) {
  const { symbol, analysis, signal } = item
  return (
    <div
      className="rounded-2xl border p-4"
      style={{ background: 'var(--yellow-dim)', borderColor: 'rgba(255,214,10,0.15)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="text-lg font-bold text-white">{symbol}</span>
        <span className="text-[11px] font-bold px-2.5 py-1 rounded-full flex-shrink-0"
          style={{ color: 'var(--yellow)', background: 'rgba(255,214,10,0.12)', border: '1px solid rgba(255,214,10,0.25)' }}>
          WATCH
        </span>
      </div>
      <p className="text-sm mt-2" style={{ color: '#bbb' }}>{signal.reasons[0]}</p>
      <RsiProgress rsi={analysis.rsi} suggestedAction={signal.suggested_action} />
    </div>
  )
}

function ActionCard({ item }: { item: SignalItem }) {
  const badge = BADGE[item.signal.action]
  const label = item.signal.action.replace('_', ' ')

  return (
    <div
      className="rounded-2xl border p-5 transition-all"
      style={{ background: badge.bg, borderColor: badge.border }}
    >
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="text-2xl font-bold text-white">{item.symbol}</span>
          {item.in_portfolio && (
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
              style={{ background: 'var(--teal-dim)', color: 'var(--teal)', border: '1px solid rgba(0,212,170,0.3)' }}>
              HELD
            </span>
          )}
        </div>
        <span
          className="text-sm font-bold px-4 py-1.5 rounded-full"
          style={{ color: badge.color, background: badge.bg, border: `1px solid ${badge.border}` }}
        >
          {label}
        </span>
      </div>

      <p className="mt-3 text-sm" style={{ color: '#ccc' }}>{plainReason(item)}</p>

      <p className="mt-1.5 text-xs font-mono" style={{ color: '#777' }}>{plainAction(item)}</p>
    </div>
  )
}

export function ActionSummary({ signals }: { signals: SignalsResponse }) {
  const actionable: SignalAction[] = ['BUY', 'SELL', 'SELL_HALF', 'REVIEW']
  const watching: SignalAction[] = ['WATCH']

  const active = signals.signals.filter(s => actionable.includes(s.signal.action))
  const watches = signals.signals.filter(s => watching.includes(s.signal.action))
  const holds = signals.signals.filter(s => s.signal.action === 'HOLD').length
  const skips = signals.signals.filter(s => s.signal.action === 'SKIP').length

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-4 gap-1">
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>This Week's Action Plan</h2>
        <span className="text-xs" style={{ color: '#555' }}>Execute at next market open · 9:30 AM ET</span>
      </div>

      {active.length === 0 ? (
        <div className="rounded-2xl border p-6 text-center" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
          <p className="text-sm font-medium text-white">Nothing to do this week.</p>
          <p className="text-xs mt-1" style={{ color: '#666' }}>
            {holds > 0 && `Hold your ${holds} position${holds > 1 ? 's' : ''} as planned. `}
            {watches.length > 0 && `Keep an eye on: ${watches.map(w => w.symbol).join(', ')}`}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {active.map(item => <ActionCard key={item.symbol} item={item} />)}
        </div>
      )}

      {watches.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
            Watching — not triggered yet
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {watches.map(item => <WatchCard key={item.symbol} item={item} />)}
          </div>
        </div>
      )}

      {(holds > 0 || skips > 0) && (
        <div className="mt-3 flex flex-wrap gap-3 text-xs" style={{ color: '#555' }}>
          {holds > 0 && <span>{holds} position{holds > 1 ? 's' : ''} — hold, no action</span>}
          {skips > 0 && <span>{skips} skipped — conditions not met</span>}
        </div>
      )}
    </div>
  )
}
