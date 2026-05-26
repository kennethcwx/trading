import { useState } from 'react'
import type { PortfolioResponse } from '../types'
import { postReconnect } from '../api'

export function PortfolioSummary({ portfolio, onReconnect }: { portfolio: PortfolioResponse; onReconnect: () => void }) {
  const [reconnecting, setReconnecting] = useState(false)

  async function handleReconnect() {
    setReconnecting(true)
    await postReconnect()
    await new Promise(r => setTimeout(r, 3000))
    onReconnect()
    setReconnecting(false)
  }

  if (!portfolio.ibkr_connected) {
    return (
      <div className="bg-[#1a1d2e] rounded-lg border border-slate-800 px-4 py-3 flex items-center justify-between text-xs">
        <span className="text-slate-500">
          <span className="text-yellow-500">● </span>
          IBKR offline — start IB Gateway (paper port 4002) then click reconnect
        </span>
        <button
          onClick={() => void handleReconnect()}
          disabled={reconnecting}
          className="text-xs bg-slate-800 hover:bg-slate-700 disabled:opacity-40 px-3 py-1.5 rounded border border-slate-700 text-slate-300 transition-colors cursor-pointer"
        >
          {reconnecting ? 'Connecting…' : 'Reconnect IBKR'}
        </button>
      </div>
    )
  }

  const { positions, account } = portfolio

  if (!positions.length) {
    return (
      <div className="bg-[#1a1d2e] rounded-lg border border-slate-800 px-4 py-3 text-xs text-slate-500">
        <span className="text-green-500">● </span>
        IBKR connected — no open positions. Edit <code className="text-slate-400">config.py</code> to add your watchlist.
      </div>
    )
  }

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Portfolio</h2>
        <div className="flex gap-4 text-xs text-slate-400">
          {account.total_value !== null && (
            <span>Total: <span className="text-slate-200 font-mono">{account.currency} {account.total_value.toFixed(2)}</span></span>
          )}
          {account.cash !== null && (
            <span>Cash: <span className="text-teal-400 font-mono">{account.currency} {account.cash.toFixed(2)}</span></span>
          )}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-slate-600 text-xs uppercase tracking-wider">
              <th className="px-4 py-2 text-left font-medium">Symbol</th>
              <th className="px-4 py-2 text-right font-medium">Shares</th>
              <th className="px-4 py-2 text-right font-medium">Avg Cost</th>
              <th className="px-4 py-2 text-right font-medium">Last Price</th>
              <th className="px-4 py-2 text-right font-medium">Mkt Value</th>
              <th className="px-4 py-2 text-right font-medium">Unr. P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(pos => {
              const pnlColor = pos.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'
              const pnlPrefix = pos.unrealized_pnl >= 0 ? '+' : ''
              return (
                <tr key={pos.symbol} className="border-b border-slate-800 hover:bg-slate-900/30 transition-colors">
                  <td className="px-4 py-2.5 font-bold text-slate-100">{pos.symbol}</td>
                  <td className="px-4 py-2.5 font-mono text-right text-slate-300">{pos.shares}</td>
                  <td className="px-4 py-2.5 font-mono text-right text-slate-400">${pos.avg_cost.toFixed(2)}</td>
                  <td className="px-4 py-2.5 font-mono text-right text-slate-300">${pos.market_price.toFixed(2)}</td>
                  <td className="px-4 py-2.5 font-mono text-right text-slate-300">${pos.market_value.toFixed(2)}</td>
                  <td className={`px-4 py-2.5 font-mono text-right font-bold ${pnlColor}`}>
                    {pnlPrefix}{pos.unrealized_pnl.toFixed(2)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
