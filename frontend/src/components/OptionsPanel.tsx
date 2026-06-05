import type { OptionsResponse, OptionsOpportunity, OptionsSignal } from '../types'

const BADGE: Record<OptionsSignal['action'], { color: string; bg: string; border: string }> = {
  'SELL PUT':  { color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'rgba(0,200,5,0.25)' },
  'SELL CALL': { color: 'var(--teal)',   bg: 'var(--teal-dim)',   border: 'rgba(0,212,170,0.25)' },
  'WATCH':     { color: 'var(--yellow)', bg: 'var(--yellow-dim)', border: 'rgba(255,214,10,0.25)' },
  'AVOID':     { color: '#555',          bg: 'transparent',       border: 'transparent' },
}

function RsiBar({ rsi }: { rsi: number | null }) {
  if (rsi === null) return <span style={{ color: '#555' }}>—</span>
  const pct = Math.min(100, Math.max(0, rsi))
  const color = rsi > 70 ? 'var(--red)' : rsi < 40 ? 'var(--green)' : 'var(--yellow)'
  return (
    <div className="flex items-center gap-2">
      <div className="w-12 h-1.5 rounded-full overflow-hidden flex-shrink-0" style={{ background: 'var(--border-2)' }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{rsi.toFixed(0)}</span>
    </div>
  )
}

function OpportunityRow({ opp }: { opp: OptionsOpportunity }) {
  const { options_signal, phase } = opp
  const badge = BADGE[options_signal.action]
  const dimmed = options_signal.action === 'AVOID'
  const isPhase2 = phase === 2

  const pnlColor = opp.avg_cost && opp.price < opp.avg_cost ? 'var(--red)' : 'var(--green)'
  const pnlPct = opp.avg_cost
    ? (((opp.price - opp.avg_cost) / opp.avg_cost) * 100).toFixed(1)
    : null

  return (
    <div
      className="px-5 py-4 border-b last:border-0 flex flex-wrap items-start gap-4 transition-colors hover:bg-white/[0.02]"
      style={{ borderColor: 'var(--border)', opacity: dimmed ? 0.45 : 1 }}
    >
      {/* Ticker + badge + phase */}
      <div className="flex-shrink-0 w-36">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white text-base">{opp.symbol}</span>
          <span
            className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
            style={isPhase2
              ? { background: 'var(--teal-dim)', color: 'var(--teal)', border: '1px solid rgba(0,212,170,0.2)' }
              : { background: 'rgba(255,255,255,0.04)', color: '#555', border: '1px solid var(--border-2)' }
            }
          >
            P{phase}
          </span>
        </div>
        <span
          className="text-[11px] font-bold px-2 py-0.5 rounded-full mt-1 inline-block"
          style={{ color: badge.color, background: badge.bg, border: `1px solid ${badge.border}` }}
        >
          {options_signal.action}
        </span>
      </div>

      {/* Price + strike + DTE */}
      <div className="flex-shrink-0 w-28 space-y-1.5 text-xs" style={{ color: '#666' }}>
        <div>Price <span className="font-mono text-white">${opp.price.toFixed(2)}</span></div>
        <div>
          Strike{' '}
          <span className="font-mono" style={{ color: isPhase2 ? 'var(--teal)' : 'var(--orange)' }}>
            ${opp.suggested_strike}
          </span>
          <span className="ml-1" style={{ color: '#444' }}>{isPhase2 ? '↑' : '↓'}</span>
        </div>
        <div>Expiry <span className="font-mono text-white">{opp.dte_target}</span></div>
      </div>

      {/* Collateral / position context */}
      <div className="flex-shrink-0 w-32 space-y-1.5 text-xs" style={{ color: '#666' }}>
        {isPhase2 ? (
          <>
            <div style={{ color: '#555' }}>Collateral <span className="font-mono text-white">100 shares</span></div>
            {opp.avg_cost && (
              <div>
                Avg cost{' '}
                <span className="font-mono" style={{ color: '#999' }}>${opp.avg_cost.toFixed(2)}</span>
              </div>
            )}
            {pnlPct && (
              <div>
                Position{' '}
                <span className="font-mono font-semibold" style={{ color: pnlColor }}>
                  {parseFloat(pnlPct) >= 0 ? '+' : ''}{pnlPct}%
                </span>
              </div>
            )}
          </>
        ) : (
          <>
            <div>
              Collateral{' '}
              <span className="font-mono" style={{ color: 'var(--yellow)' }}>
                {opp.collateral_sgd > 0 ? `S$${opp.collateral_sgd.toLocaleString()}` : '—'}
              </span>
            </div>
            <div className="flex items-center gap-1">RSI <RsiBar rsi={opp.rsi} /></div>
          </>
        )}
        {isPhase2 && (
          <div className="flex items-center gap-1">RSI <RsiBar rsi={opp.rsi} /></div>
        )}
        {opp.days_to_earnings !== null && (
          <div>Earnings <span className="font-mono text-white">{opp.days_to_earnings}d</span></div>
        )}
      </div>

      {/* Reason + suggested action */}
      <div className="flex-1 min-w-[160px] space-y-1 text-xs">
        <div style={{ color: '#999' }}>{options_signal.reason}</div>
        {options_signal.action !== 'AVOID' && (
          <div style={{ color: '#555' }}>{options_signal.suggested_action}</div>
        )}
      </div>
    </div>
  )
}

export function OptionsPanel({ options }: { options: OptionsResponse }) {
  const actionable = options.opportunities.filter(
    o => o.options_signal.action === 'SELL PUT' || o.options_signal.action === 'SELL CALL'
  )
  const phase2 = actionable.filter(o => o.phase === 2)
  const phase1 = actionable.filter(o => o.phase === 1)
  const watching = options.opportunities.filter(o => o.options_signal.action === 'WATCH')
  const avoided  = options.opportunities.filter(o => o.options_signal.action === 'AVOID')

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b flex items-center justify-between gap-3" style={{ borderColor: 'var(--border)' }}>
        <div>
          <h2 className="text-sm font-semibold text-white">Wheel Strategy</h2>
          <p className="text-xs mt-0.5" style={{ color: '#555' }}>{options.note}</p>
        </div>
        <div className="flex gap-2 text-xs flex-shrink-0">
          {phase2.length > 0 && (
            <span className="px-2 py-1 rounded-lg font-semibold" style={{ background: 'var(--teal-dim)', color: 'var(--teal)', border: '1px solid rgba(0,212,170,0.25)' }}>
              {phase2.length} call{phase2.length > 1 ? 's' : ''} to sell
            </span>
          )}
          {phase1.length > 0 && (
            <span className="px-2 py-1 rounded-lg font-semibold" style={{ background: 'var(--green-dim)', color: 'var(--green)', border: '1px solid rgba(0,200,5,0.25)' }}>
              {phase1.length} put{phase1.length > 1 ? 's' : ''} to sell
            </span>
          )}
          {watching.length > 0 && (
            <span className="px-2 py-1 rounded-lg" style={{ background: 'var(--yellow-dim)', color: 'var(--yellow)', border: '1px solid rgba(255,214,10,0.25)' }}>
              {watching.length} watching
            </span>
          )}
        </div>
      </div>

      {options.opportunities.length === 0 ? (
        <div className="px-5 py-8 text-center text-sm" style={{ color: '#555' }}>
          No watchlist symbols to evaluate
        </div>
      ) : (
        <div>
          {[...phase2, ...phase1, ...watching, ...avoided].map(opp => (
            <OpportunityRow key={opp.symbol} opp={opp} />
          ))}
        </div>
      )}

      {/* Legend */}
      <div className="px-5 py-3 border-t flex flex-wrap gap-x-5 gap-y-1 text-[11px]"
        style={{ borderColor: 'var(--border)', color: '#444' }}>
        <span><span style={{ color: 'var(--teal)' }}>P2</span> = owns shares → sell covered call</span>
        <span><span style={{ color: '#666' }}>P1</span> = no position → sell cash-secured put</span>
        <span>IVR &gt; 30 required before entering</span>
      </div>
    </div>
  )
}
