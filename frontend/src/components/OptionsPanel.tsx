import type { OptionsResponse } from '../types'

export function OptionsPanel({ options }: { options: OptionsResponse }) {
  const feasible = options.opportunities.filter(o => o.feasible)
  const infeasible = options.opportunities.filter(o => !o.feasible)

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Options Opportunities</h2>
        <p className="text-xs mt-1" style={{ color: '#555' }}>{options.note}</p>
      </div>

      {feasible.length === 0 && (
        <div className="px-5 py-5 space-y-2 text-sm" style={{ color: '#666' }}>
          <p>
            <span style={{ color: 'var(--amber)' }}>⚠ </span>
            At S${options.portfolio_size_sgd.toLocaleString()}, cash-secured puts require more capital than available
            (min ~S$10,000–15,000 for lower-priced stocks).
          </p>
          <p className="text-xs" style={{ color: '#444' }}>
            Focus on building the stock portfolio first. Options strategies become practical above S$15,000.
          </p>
        </div>
      )}

      {feasible.length > 0 && (
        <div>
          {feasible.map(opp => (
            <div key={opp.symbol}
              className="px-5 py-4 border-b last:border-0 grid grid-cols-2 md:grid-cols-4 gap-4"
              style={{ borderColor: 'var(--border)' }}>
              <div>
                <div className="font-bold text-white text-base">{opp.symbol}</div>
                <div className="text-xs mt-0.5" style={{ color: '#666' }}>{opp.strategy}</div>
                <div className="text-xs mt-1 font-mono" style={{ color: '#888' }}>
                  Price <span className="text-white">${opp.price.toFixed(2)}</span>
                </div>
              </div>
              <div className="text-xs space-y-1.5" style={{ color: '#666' }}>
                <div>Strike <span className="font-mono" style={{ color: 'var(--teal)' }}>${opp.suggested_strike}</span></div>
                <div>Expiry <span className="font-mono text-white">{opp.dte_target}</span></div>
                <div>Collateral <span className="font-mono" style={{ color: 'var(--yellow)' }}>S${opp.collateral_sgd.toLocaleString()}</span></div>
              </div>
              <div className="text-xs space-y-1.5" style={{ color: '#666' }}>
                <div>RSI <span className="font-mono text-white">{opp.rsi?.toFixed(0) ?? '—'}</span></div>
                <div>Earnings <span className="font-mono text-white">{opp.days_to_earnings !== null ? `${opp.days_to_earnings}d` : '—'}</span></div>
              </div>
              <div className="text-xs" style={{ color: '#555' }}>{opp.note}</div>
            </div>
          ))}
        </div>
      )}

      {infeasible.length > 0 && (
        <div className="px-5 py-4 border-t" style={{ borderColor: 'var(--border)' }}>
          <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
            Below capital threshold
          </div>
          <div className="flex flex-wrap gap-2">
            {infeasible.map(opp => (
              <div key={opp.symbol}
                className="text-xs px-3 py-1.5 rounded-lg"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#555' }}>
                {opp.symbol} — needs S${opp.collateral_sgd.toLocaleString()}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
