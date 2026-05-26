import type { OptionsResponse } from '../types'

export function OptionsPanel({ options }: { options: OptionsResponse }) {
  const feasible = options.opportunities.filter(o => o.feasible)
  const infeasible = options.opportunities.filter(o => !o.feasible)

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Options Opportunities</h2>
        <p className="text-xs text-slate-600 mt-0.5">{options.note}</p>
      </div>

      {feasible.length === 0 && (
        <div className="px-4 py-4 text-xs text-slate-500 space-y-1">
          <p>
            <span className="text-yellow-500">⚠ </span>
            At S${options.portfolio_size_sgd.toLocaleString()}, cash-secured puts require more capital than available
            (min ~S$10,000–15,000 for lower-priced stocks).
          </p>
          <p className="text-slate-600">
            Focus on building the stock portfolio first. Options strategies (Wheel, CSP) become practical above S$15,000.
          </p>
        </div>
      )}

      {feasible.length > 0 && (
        <div className="divide-y divide-slate-800">
          {feasible.map(opp => (
            <div key={opp.symbol} className="px-4 py-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <div>
                <div className="font-bold text-slate-100 text-sm">{opp.symbol}</div>
                <div className="text-slate-500 mt-0.5">{opp.strategy}</div>
                <div className="text-slate-400 mt-1">Price: <span className="font-mono text-slate-200">${opp.price.toFixed(2)}</span></div>
              </div>
              <div className="space-y-1 text-slate-400">
                <div>Strike: <span className="font-mono text-teal-400">${opp.suggested_strike}</span></div>
                <div>Expiry: <span className="font-mono">{opp.dte_target}</span></div>
                <div>Collateral: <span className="font-mono text-yellow-400">S${opp.collateral_sgd.toLocaleString()}</span></div>
              </div>
              <div className="space-y-1 text-slate-400">
                <div>RSI: <span className="font-mono">{opp.rsi?.toFixed(0) ?? '—'}</span></div>
                <div>Earnings: <span className="font-mono">{opp.days_to_earnings !== null ? `${opp.days_to_earnings}d` : '—'}</span></div>
              </div>
              <div className="text-slate-500 italic">{opp.note}</div>
            </div>
          ))}
        </div>
      )}

      {infeasible.length > 0 && (
        <div className="px-4 py-3 border-t border-slate-800">
          <div className="text-xs text-slate-600 mb-2 uppercase tracking-wider">Below capital threshold</div>
          <div className="flex flex-wrap gap-2">
            {infeasible.map(opp => (
              <div key={opp.symbol} className="text-xs bg-slate-900 px-2 py-1.5 rounded text-slate-600 border border-slate-800">
                {opp.symbol} — needs S${opp.collateral_sgd.toLocaleString()}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
