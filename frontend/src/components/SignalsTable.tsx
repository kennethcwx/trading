import { useState } from 'react'
import type { SignalsResponse, SignalItem, SignalAction, Fundamentals, RelativeStrength, SectorEtf } from '../types'
import type { SignalGroup } from '../api'

const BADGE: Record<SignalAction, { color: string; bg: string; border: string }> = {
  BUY:       { color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'rgba(0,200,5,0.25)' },
  SELL:      { color: 'var(--red)',    bg: 'var(--red-dim)',    border: 'rgba(255,59,48,0.25)' },
  SELL_HALF: { color: 'var(--orange)', bg: 'var(--orange-dim)', border: 'rgba(255,149,0,0.25)' },
  REVIEW:    { color: 'var(--amber)',  bg: 'var(--amber-dim)',  border: 'rgba(255,159,10,0.25)' },
  HOLD:      { color: '#888',          bg: 'rgba(255,255,255,0.04)', border: 'var(--border-2)' },
  WATCH:     { color: 'var(--yellow)', bg: 'var(--yellow-dim)', border: 'rgba(255,214,10,0.25)' },
  SKIP:      { color: '#555',          bg: 'transparent',       border: 'transparent' },
}

const GRADE_COLOR: Record<string, string> = {
  A: 'var(--green)', B: 'var(--teal)', C: 'var(--yellow)', D: 'var(--red)', '?': '#555',
}

type FilterTab = 'all' | 'actionable' | 'watching' | 'hold'

function RsiBar({ rsi }: { rsi: number | null }) {
  if (rsi === null) return <span style={{ color: '#555' }}>—</span>
  const pct = Math.min(100, Math.max(0, rsi))
  const color = rsi > 70 ? 'var(--red)' : rsi < 40 ? 'var(--green)' : 'var(--yellow)'
  const label = rsi > 70 ? 'Overbought' : rsi < 40 ? 'Oversold' : 'Neutral'
  return (
    <div className="flex items-center gap-2" title={`RSI ${rsi.toFixed(0)} — ${label}`}>
      <div className="w-14 h-1.5 rounded-full overflow-hidden flex-shrink-0" style={{ background: 'var(--border-2)' }}>
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{rsi.toFixed(0)}</span>
    </div>
  )
}

function TrendIcon({ above }: { above: boolean | null }) {
  if (above === null) return <span style={{ color: '#555' }}>—</span>
  return above
    ? <span className="text-sm font-bold" style={{ color: 'var(--green)' }} title="Above 200-day average">▲</span>
    : <span className="text-sm font-bold" style={{ color: 'var(--red)' }} title="Below 200-day average">▼</span>
}

function GradeChip({ fund }: { fund: Fundamentals | null }) {
  if (!fund) return <span style={{ color: '#555' }}>—</span>
  if (fund.is_etf) return <span className="text-xs" style={{ color: '#555' }}>ETF</span>
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
    <div title={tooltipLines}>
      <span className="text-xs font-bold px-1.5 py-0.5 rounded" style={{
        color: GRADE_COLOR[grade] ?? '#888',
        background: 'rgba(255,255,255,0.05)',
      }}>{grade}</span>
    </div>
  )
}

function SectorCell({ etf }: { etf: SectorEtf | null }) {
  if (!etf) return <span style={{ color: '#555' }}>—</span>
  const color = etf.above_200sma ? 'var(--green)' : 'var(--red)'
  const arrow = etf.above_200sma ? '▲' : '▼'
  const title = `${etf.etf_symbol}: $${etf.etf_price} vs 200 SMA $${etf.etf_sma200} — ${etf.above_200sma ? 'above' : 'BELOW'}`
  return (
    <span className="text-xs font-mono font-medium" style={{ color }} title={title}>
      {etf.etf_symbol} {arrow}
    </span>
  )
}

function RsCell({ rs }: { rs: RelativeStrength | null }) {
  if (!rs || rs.rs_3m === null) return <span style={{ color: '#555' }}>—</span>
  const v = rs.rs_3m
  const color = v > 5 ? 'var(--green)' : v < -10 ? 'var(--red)' : v < 0 ? 'var(--yellow)' : '#aaa'
  return <span className="text-xs font-mono" style={{ color }}>{v > 0 ? '+' : ''}{v}%</span>
}

function EarningsWarning({ days, warning }: { days: number | null; warning: boolean }) {
  if (!warning || days === null) return null
  return (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded" style={{
      background: 'var(--amber-dim)', color: 'var(--amber)', border: '1px solid rgba(255,159,10,0.25)',
    }}>
      Earnings {days}d
    </span>
  )
}

function SignalRow({ item }: { item: SignalItem }) {
  const { symbol, in_portfolio, analysis, fundamentals, rel_strength, sector_etf, signal, position_size } = item
  const badge = BADGE[signal.action]

  return (
    <div
      className="flex flex-wrap items-center gap-4 px-5 py-4 border-b last:border-0 transition-colors hover:bg-white/[0.02]"
      style={{ borderColor: 'var(--border)', opacity: signal.action === 'SKIP' ? 0.4 : 1 }}
    >
      {/* Left: ticker + badge */}
      <div className="flex items-center gap-3 w-36 flex-shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-white text-base">{symbol}</span>
            {in_portfolio && (
              <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
                style={{ background: 'var(--teal-dim)', color: 'var(--teal)', border: '1px solid rgba(0,212,170,0.25)' }}>
                HELD
              </span>
            )}
          </div>
          <span
            className="text-[11px] font-bold px-2 py-0.5 rounded-full mt-1 inline-block"
            style={{ color: badge.color, background: badge.bg, border: `1px solid ${badge.border}` }}
          >
            {signal.action.replace('_', ' ')}
          </span>
        </div>
      </div>

      {/* Price */}
      <div className="flex-shrink-0 w-20">
        <div className="text-sm font-mono font-semibold text-white">${analysis.price.toFixed(2)}</div>
        <div className="text-[11px] font-mono mt-0.5" style={{ color: '#555' }}>USD</div>
      </div>

      {/* Signals cluster */}
      <div className="flex items-center gap-5 flex-1 min-w-0">
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: '#555' }}>Momentum</div>
          <RsiBar rsi={analysis.rsi} />
        </div>
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: '#555' }}>Trend</div>
          <TrendIcon above={analysis.above_200sma} />
        </div>
        <div className="space-y-1 hidden sm:block">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: '#555' }}>Health</div>
          <GradeChip fund={fundamentals ?? null} />
        </div>
        <div className="space-y-1 hidden md:block">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: '#555' }}>vs SPY</div>
          <RsCell rs={rel_strength ?? null} />
        </div>
        <div className="space-y-1 hidden lg:block">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: '#555' }}>Sector</div>
          <SectorCell etf={sector_etf ?? null} />
        </div>
        {analysis.earnings_warning && (
          <div className="hidden sm:block">
            <EarningsWarning days={analysis.days_to_earnings} warning={analysis.earnings_warning} />
          </div>
        )}
      </div>

      {/* Action */}
      <div className="text-xs leading-relaxed max-w-[200px] hidden lg:block" style={{ color: '#888' }}>
        {signal.action === 'BUY' && position_size ? (
          <div className="space-y-0.5">
            <div className="font-mono font-semibold" style={{ color: 'var(--teal)' }}>
              {position_size.shares.toFixed(3)} shares
            </div>
            <div style={{ color: '#666' }}>
              Stop ${analysis.stop_loss.toFixed(2)} → ${analysis.profit_target.toFixed(2)}
            </div>
          </div>
        ) : (
          signal.suggested_action
        )}
      </div>
    </div>
  )
}

const FILTER_TABS: { id: FilterTab; label: string }[] = [
  { id: 'all',        label: 'Active' },
  { id: 'actionable', label: 'Actionable' },
  { id: 'watching',   label: 'Watching' },
  { id: 'hold',       label: 'Hold' },
]

const ACTIONABLE: SignalAction[] = ['BUY', 'SELL', 'SELL_HALF', 'REVIEW']

const GROUP_META: Record<SignalGroup, { label: string; desc: string }> = {
  core:          { label: 'Watchlist',  desc: 'Your stocks · swing trades · RSI mean-reversion + momentum signals' },
  long_term:     { label: 'Long Term',  desc: 'Accumulate on dips · hold 1–3 years · fractional shares · SELL signals suppressed' },
  quantum:       { label: 'Quantum',    desc: 'Quantum computing positions · trend-driven' },
  covered_calls: { label: 'Covered Calls', desc: 'High-IV stocks · Wheel strategy entries' },
  screener:      { label: 'Screener',   desc: '70 stocks scanned · BUY and WATCH setups only · RS rank + sector filter applied' },
}

export function SignalsTable({ signals, group = 'core' }: { signals: SignalsResponse; group?: SignalGroup }) {
  const [filter, setFilter] = useState<FilterTab>('all')
  const meta = GROUP_META[group]
  const desc = group === 'screener' && signals.total_scanned
    ? meta.desc.replace(/^\d+ stocks scanned/, `${signals.total_scanned} stocks scanned`)
    : meta.desc

  const sorted = [
    ...signals.signals.filter(s => ACTIONABLE.includes(s.signal.action)),
    ...signals.signals.filter(s => s.signal.action === 'WATCH'),
    ...signals.signals.filter(s => s.signal.action === 'HOLD'),
    ...signals.signals.filter(s => s.signal.action === 'SKIP'),
  ]

  const filtered = filter === 'all'        ? sorted
    : filter === 'actionable' ? sorted.filter(s => ACTIONABLE.includes(s.signal.action))
    : filter === 'watching'   ? sorted.filter(s => s.signal.action === 'WATCH')
    :                           sorted.filter(s => s.signal.action === 'HOLD')

  const counts = signals.signals.reduce<Record<string, number>>((acc, s) => {
    acc[s.signal.action] = (acc[s.signal.action] || 0) + 1
    return acc
  }, {})
  const actionableCount = ACTIONABLE.reduce((n, a) => n + (counts[a] ?? 0), 0)

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Header */}
      <div className="px-5 py-4 border-b flex flex-wrap items-center justify-between gap-3"
        style={{ borderColor: 'var(--border)' }}>
        <div>
          <h2 className="text-sm font-semibold text-white">{meta.label}</h2>
          <p className="text-[11px] mt-0.5" style={{ color: '#555' }}>{desc}</p>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1 p-1 rounded-xl" style={{ background: 'var(--surface-2)' }}>
          {FILTER_TABS.map(t => {
            const count = t.id === 'actionable' ? actionableCount
              : t.id === 'watching' ? (counts['WATCH'] ?? 0)
              : t.id === 'hold' ? (counts['HOLD'] ?? 0)
              : signals.signals.length
            return (
              <button
                key={t.id}
                onClick={() => setFilter(t.id)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer"
                style={filter === t.id
                  ? { background: 'var(--surface)', color: 'white' }
                  : { color: '#666' }
                }
              >
                {t.label}
                {count > 0 && (
                  <span className="ml-1.5 text-[10px]" style={{ color: filter === t.id ? '#aaa' : '#444' }}>
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* List */}
      <div>
        {filtered.length === 0 ? (
          <div className="py-10 text-center text-sm" style={{ color: '#555' }}>
            No signals in this category
          </div>
        ) : group === 'screener' ? (
          // Screener: visually group BUY and WATCH with section headers
          (() => {
            const buys    = filtered.filter(s => s.signal.action === 'BUY')
            const watches = filtered.filter(s => s.signal.action === 'WATCH')
            const SectionHeader = ({ label, count }: { label: string; count: number }) => (
              <div className="px-5 py-2 flex items-center gap-2 border-b"
                style={{ background: 'var(--surface-2)', borderColor: 'var(--border)' }}>
                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: '#666' }}>
                  {label}
                </span>
                <span className="text-xs font-mono" style={{ color: '#444' }}>{count}</span>
              </div>
            )
            return (
              <>
                {buys.length > 0 && (
                  <>
                    <SectionHeader label="Buy Setups" count={buys.length} />
                    {buys.map(item => <SignalRow key={item.symbol} item={item} />)}
                  </>
                )}
                {watches.length > 0 && (
                  <>
                    <SectionHeader label="Watching" count={watches.length} />
                    {watches.map(item => <SignalRow key={item.symbol} item={item} />)}
                  </>
                )}
              </>
            )
          })()
        ) : (
          filtered.map(item => <SignalRow key={item.symbol} item={item} />)
        )}
      </div>

      {/* Footer */}
      <div className="px-5 py-3 border-t flex flex-wrap gap-x-5 gap-y-1 text-[11px]"
        style={{ borderColor: 'var(--border)', color: '#444' }}>
        <span>Updated {new Date(signals.generated_at).toLocaleString('en-SG')}</span>
        <span className="hidden sm:inline">RSI &lt;40 oversold · &gt;70 overbought</span>
        <span className="hidden sm:inline">Health A=strong · D=avoid</span>
        {signals.regime.new_position_size_multiplier < 1 && (
          <span style={{ color: 'var(--orange)' }}>Use half position size — market is risky</span>
        )}
      </div>
    </div>
  )
}
