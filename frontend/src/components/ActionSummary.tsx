import type { SignalsResponse, SignalItem, SignalAction } from '../types'

const ACTION_BORDER: Record<SignalAction, string> = {
  BUY:       'border-l-green-500',
  SELL:      'border-l-red-500',
  SELL_HALF: 'border-l-orange-500',
  REVIEW:    'border-l-amber-500',
  HOLD:      'border-l-slate-700',
  WATCH:     'border-l-yellow-500',
  SKIP:      'border-l-slate-800',
}

const BADGE: Record<SignalAction, string> = {
  BUY:       'bg-green-900/70 text-green-300 border border-green-700',
  SELL:      'bg-red-900/70 text-red-300 border border-red-700',
  SELL_HALF: 'bg-orange-900/70 text-orange-300 border border-orange-700',
  REVIEW:    'bg-amber-900/70 text-amber-300 border border-amber-700',
  HOLD:      'bg-slate-800 text-slate-400 border border-slate-700',
  WATCH:     'bg-yellow-900/70 text-yellow-300 border border-yellow-700',
  SKIP:      'bg-slate-900 text-slate-600 border border-slate-800',
}

function plainReason(item: SignalItem): string {
  const r = item.signal.reasons[0] ?? ''
  const action = item.signal.action
  if (action === 'BUY') {
    if (r.includes('Mean-reversion')) return "Pulled back to entry zone, still in an uptrend"
    if (r.includes('Momentum')) return "Breaking to new highs on strong volume"
    return r
  }
  if (action === 'SELL' || action === 'SELL_HALF') {
    if (r.includes('Stop loss')) return "Stop loss hit — exit to protect your capital"
    if (r.includes('RSI overbought')) return "Overbought — take some profit"
    if (r.includes('Profit target')) return "Price target reached — take gains"
    if (r.includes('200-day SMA') || r.includes('trend broken')) return "Broke below the long-term trend — exit"
    if (r.includes('Time stop')) return "Held too long without moving — free up the capital"
    return r
  }
  if (action === 'REVIEW') {
    const days = r.match(/\d+/)?.[0]
    return `Earnings in ${days ?? 'a few'} days — hold or exit before then`
  }
  if (action === 'WATCH') return "Nearing buy zone — watch closely"
  return r
}

function plainAction(item: SignalItem): string {
  const { signal, analysis, position_size } = item
  if (signal.action === 'BUY' && position_size) {
    return `Buy ${position_size.shares.toFixed(2)} shares at ~$${analysis.price.toFixed(2)} · Stop if it falls to $${analysis.stop_loss.toFixed(2)} · Target $${analysis.profit_target.toFixed(2)}`
  }
  if (signal.action === 'SELL_HALF') {
    return `Sell half at market open · Move stop to breakeven on the rest`
  }
  if (signal.action === 'SELL') {
    return `Close the full position at market open`
  }
  if (signal.action === 'REVIEW') {
    return `Decide before earnings: hold, exit, or trim`
  }
  return signal.suggested_action
}

function ActionCard({ item }: { item: SignalItem }) {
  const label = item.signal.action.replace('_', ' ')
  return (
    <div className={`flex gap-3 items-start py-3 pl-4 border-b border-slate-800/60 last:border-0 border-l-2 ${ACTION_BORDER[item.signal.action]}`}>
      <span className={`text-xs font-bold px-2 py-1 rounded flex-shrink-0 whitespace-nowrap ${BADGE[item.signal.action]}`}>
        {label}
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-bold text-slate-100">{item.symbol}</span>
          <span className="text-xs text-slate-400">{plainReason(item)}</span>
        </div>
        <div className="text-xs text-slate-500 mt-0.5">
          {plainAction(item)}
        </div>
      </div>
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

  const noAction = active.length === 0

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">This Week's Action Plan</h2>
        <span className="text-xs text-slate-600">Execute at market open · Mon 9:30 AM ET</span>
      </div>

      <div className="pr-4">
        {noAction ? (
          <div className="py-4 pl-4 text-sm text-slate-400">
            Nothing to do this week.{' '}
            {holds > 0 && <span>Hold your {holds} position{holds > 1 ? 's' : ''} as planned. </span>}
            {watches.length > 0 && (
              <span className="text-yellow-500">
                Keep an eye on: {watches.map(w => w.symbol).join(', ')}
              </span>
            )}
          </div>
        ) : (
          active.map(item => <ActionCard key={item.symbol} item={item} />)
        )}
      </div>

      {(holds > 0 || skips > 0 || watches.length > 0) && (
        <div className="px-4 py-2.5 border-t border-slate-800 flex flex-wrap gap-4 text-xs text-slate-600">
          {holds > 0 && <span>{holds} position{holds > 1 ? 's' : ''} — hold, no action</span>}
          {watches.length > 0 && (
            <span className="text-yellow-700">
              Watching: {watches.map(w => w.symbol).join(', ')}
            </span>
          )}
          {skips > 0 && <span>{skips} skipped — not ready to buy</span>}
        </div>
      )}
    </div>
  )
}
