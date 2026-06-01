import type { MarketRegime } from '../types'

function VixBar({ status }: { status: string }) {
  const pct = status === 'EXTREME' ? 90 : status === 'ELEVATED' ? 55 : 20
  const color = status === 'EXTREME' ? 'var(--red)' : status === 'ELEVATED' ? 'var(--yellow)' : 'var(--green)'
  const label = status === 'EXTREME' ? 'Very High' : status === 'ELEVATED' ? 'Elevated' : 'Low'
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs" style={{ color: '#888' }}>Fear</span>
      <div className="w-20 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-2)' }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs font-medium" style={{ color }}>{label}</span>
    </div>
  )
}

export function MarketBanner({ regime }: { regime: MarketRegime }) {
  const bullish = regime.regime_ok
  const statusColor = bullish ? 'var(--green)' : 'var(--red)'
  const statusBg = bullish ? 'var(--green-dim)' : 'var(--red-dim)'
  const statusBorder = bullish ? 'rgba(0,200,5,0.2)' : 'rgba(255,59,48,0.2)'

  return (
    <div className="rounded-2xl border p-4" style={{ background: statusBg, borderColor: statusBorder }}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        {/* Regime status */}
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: statusColor }} />
          <div>
            <div className="font-semibold text-sm" style={{ color: statusColor }}>
              Market {bullish ? 'Healthy' : 'Weak'}
            </div>
            <div className="text-xs mt-0.5" style={{ color: '#888' }}>
              {bullish ? 'New positions OK' : 'Avoid new entries'}
            </div>
          </div>
        </div>

        {/* VIX bar */}
        <div className="flex flex-col gap-1">
          <VixBar status={regime.vix_status} />
          <span className="text-[11px] font-mono" style={{ color: '#555' }}>VIX {regime.vix.toFixed(1)}</span>
        </div>

        {/* Half size warning */}
        {regime.new_position_size_multiplier < 1 && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--orange-dim)', color: 'var(--orange)', border: '1px solid rgba(255,149,0,0.25)' }}>
            <svg className="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 4a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 8 5zm0 7a1 1 0 1 1 0-2 1 1 0 0 1 0 2z" />
            </svg>
            Use half position size
          </div>
        )}

        {/* FX rate */}
        <div className="ml-auto text-xs font-mono hidden sm:block" style={{ color: '#555' }}>
          SGD/USD {regime.sgd_to_usd.toFixed(4)}
        </div>
      </div>
    </div>
  )
}
