import type { Trade } from '../types'

interface Props {
  trades: Trade[]
}

function panel(trades: Trade[], label: string, subtitle: string) {
  const open = trades.filter(t => t.exit_price == null)
  const closed = trades.filter(t => t.exit_price != null)
  const winners = closed.filter(t => (t.realized_pnl ?? 0) > 0)
  const winRate = closed.length > 0 ? Math.round((winners.length / closed.length) * 100) : null
  const avgGain = closed.length > 0
    ? closed.reduce((s, t) => s + (t.realized_pnl_pct ?? 0), 0) / closed.length
    : null
  const unrealizedTotal = open.reduce((s, t) => s + (t.unrealized_pnl ?? 0), 0)
  const realizedTotal = closed.reduce((s, t) => s + (t.realized_pnl ?? 0), 0)

  const pnlColor = (v: number) => v >= 0 ? 'var(--green)' : 'var(--red)'
  const fmt = (v: number) => `${v >= 0 ? '+' : ''}$${Math.abs(v).toFixed(2)}`
  const fmtPct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

  return (
    <div className="rounded-2xl border p-5 space-y-4 flex-1"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div>
        <div className="text-base font-semibold text-white">Strategy {label}</div>
        <div className="text-xs mt-0.5" style={{ color: '#666' }}>{subtitle}</div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
          <div className="text-xs mb-1" style={{ color: '#666' }}>Open trades</div>
          <div className="text-xl font-bold text-white">{open.length}</div>
          {open.length > 0 && (
            <div className="text-xs mt-0.5 font-medium" style={{ color: pnlColor(unrealizedTotal) }}>
              {fmt(unrealizedTotal)} unrealized
            </div>
          )}
        </div>

        <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
          <div className="text-xs mb-1" style={{ color: '#666' }}>Closed trades</div>
          <div className="text-xl font-bold text-white">{closed.length}</div>
          {closed.length > 0 && (
            <div className="text-xs mt-0.5 font-medium" style={{ color: pnlColor(realizedTotal) }}>
              {fmt(realizedTotal)} realized
            </div>
          )}
        </div>

        <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
          <div className="text-xs mb-1" style={{ color: '#666' }}>Win rate</div>
          {winRate != null
            ? <div className="text-xl font-bold" style={{ color: winRate >= 50 ? 'var(--green)' : 'var(--red)' }}>
                {winRate}%
              </div>
            : <div className="text-xl font-bold" style={{ color: '#555' }}>—</div>
          }
          {closed.length > 0 && (
            <div className="text-xs mt-0.5" style={{ color: '#555' }}>
              {winners.length}/{closed.length} trades
            </div>
          )}
        </div>

        <div className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
          <div className="text-xs mb-1" style={{ color: '#666' }}>Avg return</div>
          {avgGain != null
            ? <div className="text-xl font-bold" style={{ color: pnlColor(avgGain) }}>
                {fmtPct(avgGain)}
              </div>
            : <div className="text-xl font-bold" style={{ color: '#555' }}>—</div>
          }
          {closed.length > 0 && (
            <div className="text-xs mt-0.5" style={{ color: '#555' }}>per closed trade</div>
          )}
        </div>
      </div>

      {open.length > 0 && (
        <div>
          <div className="text-xs font-medium mb-2" style={{ color: '#888' }}>Open positions</div>
          <div className="space-y-1">
            {open.map(t => (
              <div key={t.id} className="flex justify-between items-center text-xs px-3 py-2 rounded-lg"
                style={{ background: 'var(--surface-2)' }}>
                <span className="font-mono font-semibold text-white">{t.symbol}</span>
                <span style={{ color: (t.unrealized_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {t.unrealized_pnl != null ? fmt(t.unrealized_pnl) : '—'}
                  {t.unrealized_pnl_pct != null ? ` (${fmtPct(t.unrealized_pnl_pct)})` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {trades.length === 0 && (
        <div className="text-sm text-center py-4" style={{ color: '#555' }}>
          No trades logged for Strategy {label} yet
        </div>
      )}
    </div>
  )
}

export function StrategyCompare({ trades }: Props) {
  const tradesA = trades.filter(t => !t.strategy || t.strategy === 'A')
  const tradesB = trades.filter(t => t.strategy === 'B')

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-white">Strategy Comparison</h2>
        <p className="text-xs mt-1" style={{ color: '#666' }}>
          A/B live paper trading — Strategy A (Combined) vs Strategy B (Mean-Rev only).
          Log trades with the correct strategy label to track performance over time.
        </p>
      </div>
      <div className="flex flex-col md:flex-row gap-4">
        {panel(tradesA, 'A', 'Combined: Mean-Rev or Momentum')}
        {panel(tradesB, 'B', 'Mean-Rev only')}
      </div>
    </div>
  )
}
