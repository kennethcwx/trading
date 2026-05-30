import type { MarketRegime } from '../types'

function WarningIcon() {
  return (
    <svg className="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
    </svg>
  )
}

export function MarketBanner({ regime }: { regime: MarketRegime }) {
  const bullish = regime.regime_ok

  const vixLabel =
    regime.vix_status === 'EXTREME' ? 'Very High' :
    regime.vix_status === 'ELEVATED' ? 'Elevated' :
    'Low'

  const vixColor =
    regime.vix_status === 'EXTREME' ? 'text-red-400' :
    regime.vix_status === 'ELEVATED' ? 'text-yellow-400' :
    'text-green-400'

  const vixBadge =
    regime.vix_status === 'EXTREME' ? 'bg-red-900/60 text-red-300' :
    regime.vix_status === 'ELEVATED' ? 'bg-yellow-900/60 text-yellow-300' :
    'bg-green-900/60 text-green-300'

  return (
    <div className={`px-4 sm:px-6 py-2.5 border-b text-xs ${
      bullish ? 'bg-green-950/20 border-green-900/40' : 'bg-red-950/20 border-red-900/40'
    }`}>
      <div className="flex flex-wrap gap-x-6 gap-y-1.5 items-center">
        {/* Market health */}
        <div className="flex items-center gap-2">
          <span className={`font-bold text-sm ${bullish ? 'text-green-400' : 'text-red-400'}`}>
            {bullish ? '▲ Healthy' : '▼ Weak'}
          </span>
          <span className="text-slate-600">
            {bullish ? 'New buys OK' : 'Avoid new positions'}
          </span>
        </div>

        {/* Fear level */}
        <div className="flex items-center gap-2">
          <span className="text-slate-500">Fear:</span>
          <span className={`font-bold ${vixColor}`}>{vixLabel}</span>
          <span className={`px-1.5 py-0.5 rounded font-mono ${vixBadge}`}>
            VIX {regime.vix.toFixed(1)}
          </span>
        </div>

        {/* Size warning */}
        {regime.new_position_size_multiplier < 1 && (
          <div className="flex items-center gap-1.5 text-yellow-400 font-medium">
            <WarningIcon />
            <span>Use half position size</span>
          </div>
        )}

        <div className="text-slate-700 ml-auto hidden sm:block">
          SGD/USD {regime.sgd_to_usd.toFixed(4)}
        </div>
      </div>
    </div>
  )
}
