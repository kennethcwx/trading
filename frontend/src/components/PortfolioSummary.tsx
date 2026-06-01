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
      <div className="rounded-2xl border px-5 py-3.5 flex items-center justify-between text-xs"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
        <span style={{ color: '#666' }}>
          <span style={{ color: 'var(--yellow)' }}>● </span>
          IBKR offline — start IB Gateway (paper port 4002) then reconnect
        </span>
        <button
          onClick={() => void handleReconnect()}
          disabled={reconnecting}
          className="text-xs px-4 py-2 rounded-lg transition-colors cursor-pointer disabled:opacity-40"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#ccc' }}
        >
          {reconnecting ? 'Connecting…' : 'Reconnect IBKR'}
        </button>
      </div>
    )
  }

  const { positions, account } = portfolio

  if (!positions.length) {
    return (
      <div className="rounded-2xl border px-5 py-3.5 text-xs"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)', color: '#666' }}>
        <span style={{ color: 'var(--green)' }}>● </span>
        IBKR connected — no open positions.
      </div>
    )
  }

  const totalUnrealized = positions.reduce((s, p) => s + p.unrealized_pnl, 0)

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Hero row */}
      <div className="px-5 py-5 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          {account.total_value !== null && (
            <div>
              <div className="text-xs uppercase tracking-wider mb-1" style={{ color: '#555' }}>Portfolio Value</div>
              <div className="text-3xl font-bold font-mono text-white">
                {account.currency} {account.total_value.toLocaleString('en-SG', { minimumFractionDigits: 2 })}
              </div>
            </div>
          )}
          <div>
            <div className="text-xs uppercase tracking-wider mb-1" style={{ color: '#555' }}>Unrealized P&L</div>
            <div className="text-xl font-semibold font-mono"
              style={{ color: totalUnrealized >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {totalUnrealized >= 0 ? '+' : ''}${totalUnrealized.toFixed(2)}
            </div>
          </div>
          {account.cash !== null && (
            <div>
              <div className="text-xs uppercase tracking-wider mb-1" style={{ color: '#555' }}>Cash</div>
              <div className="text-xl font-semibold font-mono" style={{ color: 'var(--teal)' }}>
                {account.currency} {account.cash.toFixed(2)}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Positions list */}
      <div>
        {positions.map(pos => {
          const pnlColor = pos.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)'
          return (
            <div key={pos.symbol}
              className="flex items-center gap-4 px-5 py-3.5 border-b last:border-0 hover:bg-white/[0.02] transition-colors"
              style={{ borderColor: 'var(--border)' }}>
              <span className="font-bold text-white text-base w-16 flex-shrink-0">{pos.symbol}</span>
              <span className="text-xs font-mono w-16 flex-shrink-0" style={{ color: '#666' }}>{pos.shares} sh</span>
              <span className="text-xs font-mono flex-1" style={{ color: '#555' }}>
                avg ${pos.avg_cost.toFixed(2)} → ${pos.market_price.toFixed(2)}
              </span>
              <span className="text-xs font-mono hidden sm:block" style={{ color: '#666' }}>
                ${pos.market_value.toFixed(2)}
              </span>
              <span className="text-sm font-mono font-bold w-24 text-right" style={{ color: pnlColor }}>
                {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
