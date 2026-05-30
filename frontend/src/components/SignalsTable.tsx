import type { SignalsResponse, SignalItem, SignalAction, Fundamentals, RelativeStrength } from '../types'

function WarningIcon() {
  return (
    <svg className="w-3 h-3 inline-block flex-shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
    </svg>
  )
}

const ACTION_STYLE: Record<SignalAction, string> = {
  BUY:       'bg-green-900/70 text-green-300 border border-green-700',
  SELL:      'bg-red-900/70 text-red-300 border border-red-700',
  SELL_HALF: 'bg-orange-900/70 text-orange-300 border border-orange-700',
  HOLD:      'bg-slate-800 text-slate-400 border border-slate-700',
  WATCH:     'bg-yellow-900/70 text-yellow-300 border border-yellow-700',
  SKIP:      'bg-slate-900 text-slate-600 border border-slate-800',
  REVIEW:    'bg-amber-900/70 text-amber-300 border border-amber-700',
}

const PRIORITY_DOT: Record<string, string> = {
  HIGH: 'bg-red-400',
  MEDIUM: 'bg-yellow-400',
  LOW: 'bg-slate-600',
}

const GRADE_STYLE: Record<string, string> = {
  A: 'text-green-400 font-bold',
  B: 'text-teal-400 font-bold',
  C: 'text-yellow-400',
  D: 'text-red-400',
  '?': 'text-slate-600',
}

function RsiCell({ rsi }: { rsi: number | null }) {
  if (rsi === null) return <span className="text-slate-600">—</span>
  const color = rsi > 70 ? 'text-red-400 font-bold' : rsi < 40 ? 'text-green-400 font-bold' : 'text-slate-300'
  return <span className={`font-mono ${color}`}>{rsi.toFixed(0)}</span>
}

function SmaCell({ above }: { above: boolean | null }) {
  if (above === null) return <span className="text-slate-600">—</span>
  return above ? <span className="text-green-400">▲</span> : <span className="text-red-400">▼</span>
}

function EarningsCell({ days, warning }: { days: number | null; warning: boolean }) {
  if (days === null) return <span className="text-slate-700">—</span>
  if (warning) return <span className="text-amber-400 font-bold inline-flex items-center gap-1">{days}d <WarningIcon /></span>
  return <span className="text-slate-500">{days}d</span>
}

function FundamentalsCell({ fund }: { fund: Fundamentals | null }) {
  if (!fund) return <span className="text-slate-700">—</span>
  if (fund.is_etf) return <span className="text-slate-600 text-xs">ETF</span>

  const grade = fund.grade ?? '?'
  const tooltipLines = [
    fund.revenue_growth !== null ? `Rev: ${fund.revenue_growth > 0 ? '+' : ''}${fund.revenue_growth}%` : null,
    fund.earnings_growth !== null ? `Earn: ${fund.earnings_growth > 0 ? '+' : ''}${fund.earnings_growth}%` : null,
    fund.profit_margin !== null ? `Margin: ${fund.profit_margin}%` : null,
    fund.debt_equity !== null ? `D/E: ${fund.debt_equity}x` : null,
    fund.analyst_rating ? `Analysts: ${fund.analyst_rating.replace('_', ' ')}` : null,
    fund.upside_pct !== null ? `Target: ${fund.upside_pct > 0 ? '+' : ''}${fund.upside_pct}%` : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className="text-xs" title={tooltipLines}>
      <span className={GRADE_STYLE[grade] ?? 'text-slate-400'}>{grade}</span>
      <span className="text-slate-600 ml-1">{fund.score}/5</span>
      {fund.upside_pct !== null && (
        <div className={`text-xs ${fund.upside_pct > 10 ? 'text-green-500' : fund.upside_pct < 0 ? 'text-red-500' : 'text-slate-500'}`}>
          {fund.upside_pct > 0 ? '+' : ''}{fund.upside_pct}% target
        </div>
      )}
    </div>
  )
}

function RsCell({ rs }: { rs: RelativeStrength | null }) {
  if (!rs || rs.rs_3m === null) return <span className="text-slate-700">—</span>
  const v = rs.rs_3m
  const color = v > 5 ? 'text-green-400' : v < -10 ? 'text-red-400' : v < 0 ? 'text-yellow-500' : 'text-slate-300'
  return (
    <div className="text-xs">
      <span className={`font-mono ${color}`}>{v > 0 ? '+' : ''}{v}%</span>
      <span className="text-slate-600 ml-1">vs SPY</span>
    </div>
  )
}

function ActionCell({ item }: { item: SignalItem }) {
  const { signal, analysis, position_size } = item
  if (signal.action !== 'BUY' || !position_size) {
    return <span className="text-slate-500 text-xs leading-relaxed">{signal.suggested_action}</span>
  }
  return (
    <div className="text-xs space-y-0.5">
      <div className="text-teal-400 font-bold">{position_size.shares.toFixed(3)} shares</div>
      <div className="text-slate-400">S${position_size.position_value_sgd.toFixed(0)} · risk S${position_size.risk_sgd.toFixed(0)}</div>
      <div className="text-slate-500">Stop ${analysis.stop_loss.toFixed(2)} → ${analysis.profit_target.toFixed(2)}</div>
      {position_size.note && <div className="text-slate-600 italic">{position_size.note}</div>}
    </div>
  )
}

function SignalRow({ item }: { item: SignalItem }) {
  const { symbol, in_portfolio, analysis, fundamentals, rel_strength, signal } = item
  const dimmed = signal.action === 'SKIP'

  return (
    <tr className={`border-b border-slate-800 transition-colors hover:bg-slate-900/40 ${dimmed ? 'opacity-40' : ''}`}>
      <td className="px-4 py-3 whitespace-nowrap">
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${PRIORITY_DOT[signal.priority]}`} />
          <span className="font-bold text-slate-100">{symbol}</span>
          {in_portfolio && (
            <span className="text-xs bg-teal-900/60 text-teal-300 px-1.5 py-0.5 rounded">HELD</span>
          )}
        </div>
      </td>
      <td className="px-4 py-3 font-mono text-slate-300 whitespace-nowrap">
        ${analysis.price.toFixed(2)}
      </td>
      <td className="px-4 py-3 whitespace-nowrap hidden sm:table-cell">
        <RsiCell rsi={analysis.rsi} />
      </td>
      <td className="px-4 py-3 whitespace-nowrap hidden sm:table-cell">
        <SmaCell above={analysis.above_200sma} />
      </td>
      <td className="px-4 py-3 whitespace-nowrap hidden md:table-cell">
        <EarningsCell days={analysis.days_to_earnings} warning={analysis.earnings_warning} />
      </td>
      <td className="px-4 py-3 hidden md:table-cell">
        <FundamentalsCell fund={fundamentals ?? null} />
      </td>
      <td className="px-4 py-3 hidden lg:table-cell">
        <RsCell rs={rel_strength ?? null} />
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <span className={`text-xs font-bold px-2 py-1 rounded ${ACTION_STYLE[signal.action]}`}>
          {signal.action.replace('_', ' ')}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-slate-400 max-w-xs hidden lg:table-cell">
        {signal.reasons[0]}
      </td>
      <td className="px-4 py-3 min-w-[180px]">
        <ActionCell item={item} />
      </td>
    </tr>
  )
}

function MobileSignalCard({ item }: { item: SignalItem }) {
  const { symbol, in_portfolio, analysis, fundamentals, signal, position_size } = item
  const dimmed = signal.action === 'SKIP'
  const borderColor: Record<SignalAction, string> = {
    BUY: 'border-l-green-500', SELL: 'border-l-red-500', SELL_HALF: 'border-l-orange-500',
    REVIEW: 'border-l-amber-500', HOLD: 'border-l-slate-700', WATCH: 'border-l-yellow-500', SKIP: 'border-l-slate-800',
  }

  return (
    <div className={`border-b border-slate-800 last:border-0 border-l-2 pl-3 pr-4 py-3 ${borderColor[signal.action]} ${dimmed ? 'opacity-40' : ''}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${PRIORITY_DOT[signal.priority]}`} />
          <span className="font-bold text-slate-100 font-mono">{symbol}</span>
          {in_portfolio && <span className="text-xs bg-teal-900/60 text-teal-300 px-1.5 py-0.5 rounded">HELD</span>}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="font-mono text-slate-300 text-sm">${analysis.price.toFixed(2)}</span>
          <span className={`text-xs font-bold px-2 py-1 rounded ${ACTION_STYLE[signal.action]}`}>
            {signal.action.replace('_', ' ')}
          </span>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-3 text-xs text-slate-500">
        {analysis.rsi !== null && <RsiCell rsi={analysis.rsi} />}
        <SmaCell above={analysis.above_200sma} />
        {fundamentals && !fundamentals.is_etf && (
          <span className={GRADE_STYLE[fundamentals.grade ?? '?'] ?? 'text-slate-400'}>
            {fundamentals.grade ?? '?'}
          </span>
        )}
        {analysis.earnings_warning && (
          <span className="text-amber-400 inline-flex items-center gap-1">
            <WarningIcon /> {analysis.days_to_earnings}d
          </span>
        )}
      </div>

      {signal.reasons[0] && (
        <div className="mt-1.5 text-xs text-slate-500 leading-relaxed">{signal.reasons[0]}</div>
      )}

      {signal.action === 'BUY' && position_size ? (
        <div className="mt-2 text-xs space-y-0.5">
          <div className="text-teal-400 font-bold">{position_size.shares.toFixed(3)} shares</div>
          <div className="text-slate-400">S${position_size.position_value_sgd.toFixed(0)} · risk S${position_size.risk_sgd.toFixed(0)}</div>
          <div className="text-slate-500">Stop ${analysis.stop_loss.toFixed(2)} → ${analysis.profit_target.toFixed(2)}</div>
        </div>
      ) : signal.action !== 'SKIP' && signal.action !== 'HOLD' ? (
        <div className="mt-1.5 text-xs text-slate-400">{signal.suggested_action}</div>
      ) : null}
    </div>
  )
}

export function SignalsTable({ signals }: { signals: SignalsResponse }) {
  const counts = signals.signals.reduce<Record<string, number>>((acc, s) => {
    acc[s.signal.action] = (acc[s.signal.action] || 0) + 1
    return acc
  }, {})

  const actionable: SignalAction[] = ['BUY', 'SELL', 'SELL_HALF', 'REVIEW']
  const sorted = [
    ...signals.signals.filter(s => actionable.includes(s.signal.action)),
    ...signals.signals.filter(s => s.signal.action === 'WATCH'),
    ...signals.signals.filter(s => s.signal.action === 'HOLD'),
    ...signals.signals.filter(s => s.signal.action === 'SKIP'),
  ]

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex flex-wrap items-center gap-3 justify-between">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Signals</h2>
        <div className="flex flex-wrap gap-2">
          {(['BUY', 'SELL', 'SELL_HALF', 'WATCH', 'REVIEW'] as SignalAction[]).map(a => {
            const n = counts[a]
            if (!n) return null
            return (
              <span key={a} className={`text-xs px-2 py-0.5 rounded ${ACTION_STYLE[a]}`}>
                {n} {a.replace('_', ' ')}
              </span>
            )
          })}
        </div>
      </div>

      {/* Mobile card layout */}
      <div className="sm:hidden divide-y divide-slate-800">
        {sorted.map(item => <MobileSignalCard key={item.symbol} item={item} />)}
      </div>

      {/* Desktop table layout */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-slate-600 text-xs uppercase tracking-wider">
              <th className="px-4 py-2 text-left font-medium">Stock</th>
              <th className="px-4 py-2 text-left font-medium">Price</th>
              <th className="px-4 py-2 text-left font-medium" title="RSI: below 40 = oversold (cheap), above 70 = overbought (expensive)">Momentum</th>
              <th className="px-4 py-2 text-left font-medium" title="Is the stock above its 200-day average? Up = healthy trend, Down = broken">Trend</th>
              <th className="px-4 py-2 text-left font-medium hidden md:table-cell" title="Days until next earnings report">Earnings</th>
              <th className="px-4 py-2 text-left font-medium hidden md:table-cell" title="Company financial health graded A–D">Health</th>
              <th className="px-4 py-2 text-left font-medium hidden lg:table-cell" title="How this stock performed vs the S&P 500 over 3 months">vs Market</th>
              <th className="px-4 py-2 text-left font-medium">Signal</th>
              <th className="px-4 py-2 text-left font-medium hidden lg:table-cell">Why</th>
              <th className="px-4 py-2 text-left font-medium">What to do</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(item => <SignalRow key={item.symbol} item={item} />)}
          </tbody>
        </table>
      </div>

      <div className="px-4 py-2 border-t border-slate-800 text-xs text-slate-600 flex flex-wrap gap-x-4 gap-y-1">
        <span>Updated {new Date(signals.generated_at).toLocaleString('en-SG')}</span>
        <span className="hidden sm:inline">Momentum: &lt;40 oversold · &gt;70 overbought</span>
        <span className="hidden sm:inline">Health grade: A = strong · B = good · C = weak · D = avoid</span>
        {signals.regime.new_position_size_multiplier < 1 && (
          <span className="text-yellow-600 inline-flex items-center gap-1"><WarningIcon /> Use half position size — market is risky</span>
        )}
      </div>
    </div>
  )
}
