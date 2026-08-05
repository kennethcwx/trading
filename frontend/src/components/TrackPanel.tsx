import { useEffect, useMemo, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { fetchTrack } from '../api'
import type { TrackResponse, TrackPosition, PaperTrade } from '../types'

// Green/red alone is unreadable for deuteranopia (validated: ΔE 5.3), so every
// figure that carries color also carries a sign, and every verdict carries an
// icon plus a word. Color is never the only channel here.
const money = (v: number) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`
const pct = (v: number, dp = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%`
const tone = (v: number) => (v >= 0 ? 'var(--green)' : 'var(--red)')
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-SG', { month: 'short', day: 'numeric' })

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border ${className}`}
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {children}
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: '#555' }}>
      {children}
    </div>
  )
}

// ── Chart ────────────────────────────────────────────────────────────────────

interface Point { date: string; cumulative: number; symbol: string; pnl: number }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as Point
  return (
    <div className="rounded-xl border px-4 py-3 text-xs shadow-xl"
      style={{ background: 'var(--surface-2)', borderColor: 'var(--border-2)' }}>
      <div className="mb-2" style={{ color: '#888' }}>{d.date} · {d.symbol}</div>
      <div style={{ color: tone(d.pnl) }}>This trade {money(d.pnl)}</div>
      <div className="font-bold mt-0.5 text-white">Cumulative {money(d.cumulative)}</div>
    </div>
  )
}

function EquityCurve({ closed }: { closed: PaperTrade[] }) {
  const data = useMemo<Point[]>(() => {
    const sorted = [...closed].sort((a, b) =>
      (a.exit_date ?? '').localeCompare(b.exit_date ?? '') || a.id - b.id)
    let cum = 0
    return sorted.map(t => {
      cum += t.realized_pnl_usd ?? 0
      return {
        date: shortDate(t.exit_date!),
        cumulative: parseFloat(cum.toFixed(2)),
        symbol: t.symbol,
        pnl: t.realized_pnl_usd ?? 0,
      }
    })
  }, [closed])

  if (data.length < 2) {
    return (
      <Card>
        <div className="px-6 py-10 text-center text-sm" style={{ color: '#555' }}>
          The equity curve appears once two trades have closed.
        </div>
      </Card>
    )
  }

  const total = data[data.length - 1].cumulative
  const hex = total >= 0 ? '#00C805' : '#FF3B30'

  return (
    <Card className="overflow-hidden">
      <div className="px-5 pt-4 pb-1">
        <Label>Realized equity curve · cumulative P&amp;L</Label>
      </div>
      <div className="px-2 pb-4 pt-2">
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="trackGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={hex} stopOpacity={0.15} />
                <stop offset="100%" stopColor={hex} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#555', fontSize: 11 }}
              axisLine={false} tickLine={false} dy={8} />
            <YAxis tick={{ fill: '#555', fontSize: 11 }} axisLine={false} tickLine={false}
              tickFormatter={v => `$${v}`} width={56} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'var(--border-2)', strokeWidth: 1 }} />
            <ReferenceLine y={0} stroke="var(--border-2)" strokeDasharray="4 2" />
            <Area type="monotone" dataKey="cumulative" stroke={hex} strokeWidth={2}
              fill="url(#trackGradient)"
              dot={{ fill: hex, r: 4, strokeWidth: 0 }}
              activeDot={{ r: 6, fill: hex, stroke: 'var(--surface)', strokeWidth: 2 }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

// ── Verdict ──────────────────────────────────────────────────────────────────

function Verdict({ data }: { data: TrackResponse }) {
  const { stats, expectations, min_legs_for_verdict: minLegs } = data
  const expLeg = expectations.per_trade * 100
  const actLeg = stats.avg_per_trade * 100

  let icon = '⏳'
  let title = `No verdict yet — ${stats.n} of ${minLegs} closed trades`
  let body = `Too few trades to separate skill from noise. Strategy D's walk-forward evidence rests on 283 trades, and one of its out-of-sample windows ran −0.41% per trade in a soft market. A weak reading this early is not a failing strategy.`
  let color = '#888'

  if (stats.n >= minLegs) {
    const gap = stats.avg_per_trade - expectations.per_trade
    if (stats.avg_per_trade <= 0) {
      icon = '🔴'; color = 'var(--red)'
      title = 'Underperforming'
      body = `Losing ${Math.abs(actLeg).toFixed(2)}% per trade where the backtest made ${expLeg.toFixed(2)}%. Worth checking whether live entries are filling where the backtest assumed.`
    } else if (gap < -expectations.per_trade / 2) {
      icon = '🟡'; color = 'var(--amber)'
      title = 'Below backtest'
      body = `Profitable, but ${Math.abs(gap * 100).toFixed(2)} points per trade short of expectation. Costs and slippage are the usual cause.`
    } else {
      icon = '🟢'; color = 'var(--green)'
      title = 'Tracking backtest'
      body = `Per-trade return is within half the expected edge of the backtest.`
    }
  }

  const rows: { k: string; actual: string; expected: string; tone?: string }[] = [
    { k: 'Per trade', actual: pct(actLeg, 2), expected: `+${expLeg.toFixed(2)}%`, tone: tone(actLeg) },
    { k: 'Max drawdown', actual: pct(stats.max_dd * 100), expected: `${(expectations.max_dd * 100).toFixed(1)}%` },
  ]

  return (
    <Card>
      <div className="px-5 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
        <Label>Live results vs backtest</Label>
        <div className="mt-3 space-y-2">
          <div className="grid grid-cols-3 text-[11px] uppercase tracking-wider" style={{ color: '#555' }}>
            <span /><span className="text-right">Actual</span><span className="text-right">Expected</span>
          </div>
          {rows.map(r => (
            <div key={r.k} className="grid grid-cols-3 text-sm items-baseline">
              <span style={{ color: '#aaa' }}>{r.k}</span>
              <span className="text-right font-mono font-semibold"
                style={{ color: r.tone ?? '#e5e5e5' }}>{r.actual}</span>
              <span className="text-right font-mono" style={{ color: '#666' }}>{r.expected}</span>
            </div>
          ))}
        </div>
        {stats.n > 0 && (
          <div className="mt-3 pt-3 border-t text-xs font-mono" style={{ borderColor: 'var(--border)', color: '#777' }}>
            Win rate {(stats.win_rate * 100).toFixed(0)}% ({stats.wins}/{stats.n}) ·
            {' '}best {pct(stats.best * 100)} · worst {pct(stats.worst * 100)}
          </div>
        )}
      </div>

      <div className="px-5 py-4 flex gap-3">
        <span className="text-lg leading-none mt-0.5" aria-hidden>{icon}</span>
        <div>
          <div className="text-sm font-semibold" style={{ color }}>{title}</div>
          <p className="text-xs mt-1 leading-relaxed" style={{ color: '#888' }}>{body}</p>
        </div>
      </div>

      <div className="px-5 pb-4 text-[11px] leading-relaxed" style={{ color: '#555' }}>
        Max drawdown counts closed trades only, so it is a floor — dips on positions
        still open are not in it.
      </div>
    </Card>
  )
}

// ── Tables ───────────────────────────────────────────────────────────────────

function OpenPositions({ positions }: { positions: TrackPosition[] }) {
  if (!positions.length) {
    return (
      <Card>
        <div className="px-5 py-4"><Label>Open positions</Label></div>
        <div className="px-5 pb-5 text-sm" style={{ color: '#555' }}>Nothing open right now.</div>
      </Card>
    )
  }
  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
        <Label>Open positions · {positions.length}</Label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wider" style={{ color: '#555' }}>
              <th className="text-left font-medium px-5 py-2">Symbol</th>
              <th className="text-right font-medium px-3 py-2">Shares</th>
              <th className="text-right font-medium px-3 py-2">Entry</th>
              <th className="text-right font-medium px-3 py-2">Now</th>
              <th className="text-right font-medium px-3 py-2">Stop</th>
              <th className="text-right font-medium px-5 py-2">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.id} className="border-t" style={{ borderColor: 'var(--border)' }}>
                <td className="px-5 py-3 font-semibold text-white">
                  {p.symbol}
                  {p.half_sold ? (
                    <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium"
                      style={{ background: 'var(--teal-dim)', color: 'var(--teal)' }}>half sold</span>
                  ) : null}
                </td>
                <td className="px-3 py-3 text-right font-mono" style={{ color: '#aaa' }}>{p.shares.toFixed(2)}</td>
                <td className="px-3 py-3 text-right font-mono" style={{ color: '#aaa' }}>${p.entry_price.toFixed(2)}</td>
                <td className="px-3 py-3 text-right font-mono" style={{ color: '#e5e5e5' }}>
                  {p.current != null ? `$${p.current.toFixed(2)}` : '—'}
                </td>
                <td className="px-3 py-3 text-right font-mono" style={{ color: '#666' }}>
                  {p.stop_loss != null ? `$${p.stop_loss.toFixed(2)}` : '—'}
                </td>
                <td className="px-5 py-3 text-right font-mono font-semibold"
                  style={{ color: p.pnl_pct != null ? tone(p.pnl_pct) : '#555' }}>
                  {p.pnl_pct != null ? pct(p.pnl_pct) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function ClosedTrades({ closed }: { closed: PaperTrade[] }) {
  if (!closed.length) return null
  const recent = [...closed].sort((a, b) =>
    (b.exit_date ?? '').localeCompare(a.exit_date ?? '') || b.id - a.id)
  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
        <Label>Closed trades · {closed.length}</Label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wider" style={{ color: '#555' }}>
              <th className="text-left font-medium px-5 py-2">Symbol</th>
              <th className="text-left font-medium px-3 py-2">Held</th>
              <th className="text-right font-medium px-3 py-2">Entry</th>
              <th className="text-right font-medium px-3 py-2">Exit</th>
              <th className="text-left font-medium px-3 py-2">Reason</th>
              <th className="text-right font-medium px-5 py-2">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {recent.map(t => {
              const basis = t.entry_price * (t.entry_shares || t.shares)
              const retPct = basis > 0 ? (t.realized_pnl_usd / basis) * 100 : 0
              return (
                <tr key={t.id} className="border-t" style={{ borderColor: 'var(--border)' }}>
                  <td className="px-5 py-3 font-semibold text-white">{t.symbol}</td>
                  <td className="px-3 py-3 whitespace-nowrap" style={{ color: '#777' }}>
                    {shortDate(t.entry_date)} → {t.exit_date ? shortDate(t.exit_date) : '—'}
                  </td>
                  <td className="px-3 py-3 text-right font-mono" style={{ color: '#aaa' }}>${t.entry_price.toFixed(2)}</td>
                  <td className="px-3 py-3 text-right font-mono" style={{ color: '#aaa' }}>
                    {t.exit_price != null ? `$${t.exit_price.toFixed(2)}` : '—'}
                  </td>
                  <td className="px-3 py-3 text-xs" style={{ color: '#666' }}>{t.exit_reason ?? '—'}</td>
                  <td className="px-5 py-3 text-right font-mono font-semibold" style={{ color: tone(retPct) }}>
                    {pct(retPct)}
                    <span className="block text-[11px] font-normal" style={{ color: '#666' }}>
                      {money(t.realized_pnl_usd)}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

// ── Panel ────────────────────────────────────────────────────────────────────

export function TrackPanel() {
  const [data, setData] = useState<TrackResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const d = await fetchTrack()
        if (!cancelled) setData(d)
      } catch {
        if (!cancelled) setError('Could not load the track.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <Card><div className="px-6 py-10 text-center text-sm" style={{ color: '#555' }}>Loading the track…</div></Card>
    )
  }
  if (error || !data) {
    return (
      <Card><div className="px-6 py-10 text-center text-sm" style={{ color: 'var(--red)' }}>{error}</div></Card>
    )
  }

  const total = data.realized_usd + data.unrealized_usd
  const baseUsd = data.base_sgd * data.sgd_to_usd
  const retPct = baseUsd ? (total / baseUsd) * 100 : 0
  const equitySgd = data.base_sgd + (data.sgd_to_usd ? total / data.sgd_to_usd : 0)

  return (
    <div className="space-y-5">
      {/* Header */}
      <Card>
        <div className="px-5 py-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-base font-semibold text-white">
              US Track · S${data.base_sgd.toLocaleString()}
            </div>
            <div className="text-xs mt-1" style={{ color: '#666' }}>
              Strategy D · fills itself · since {data.start}
            </div>
          </div>
          <span className="px-2.5 py-1 rounded-lg text-[11px] font-medium"
            style={{ background: 'var(--teal-dim)', color: 'var(--teal)' }}>
            {data.enabled ? 'AUTO-LOGGED' : 'PAUSED'}
          </span>
        </div>
      </Card>

      {data.n_entries === 0 ? (
        <Card>
          <div className="px-6 py-10 text-center space-y-2">
            <div className="text-sm" style={{ color: '#aaa' }}>No entries yet.</div>
            <p className="text-xs max-w-md mx-auto leading-relaxed" style={{ color: '#666' }}>
              This track logs itself. The next strategy D buy signal in the
              03:30–04:00 SGT confirmation window opens the first position —
              there is nothing for you to enter.
            </p>
          </div>
        </Card>
      ) : (
        <>
          {/* Headline figures — a stat row, deliberately not a chart */}
          <Card>
            <div className="px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-y-4 gap-x-6">
              <div>
                <Label>Total P&amp;L</Label>
                <div className="text-2xl font-bold font-mono" style={{ color: tone(total) }}>
                  {money(total)}
                </div>
                <div className="text-xs font-mono mt-0.5" style={{ color: '#666' }}>{pct(retPct, 2)}</div>
              </div>
              <div>
                <Label>Equity</Label>
                <div className="text-lg font-semibold font-mono text-white">
                  S${equitySgd.toLocaleString('en-SG', { maximumFractionDigits: 0 })}
                </div>
              </div>
              <div>
                <Label>Realized</Label>
                <div className="text-lg font-semibold font-mono" style={{ color: tone(data.realized_usd) }}>
                  {money(data.realized_usd)}
                </div>
              </div>
              <div>
                <Label>Unrealized</Label>
                <div className="text-lg font-semibold font-mono" style={{ color: tone(data.unrealized_usd) }}>
                  {money(data.unrealized_usd)}
                </div>
              </div>
            </div>
            <div className="px-5 py-3 border-t text-xs font-mono" style={{ borderColor: 'var(--border)', color: '#666' }}>
              {data.n_entries} entries · {data.closed.length} closed · {data.positions.length} open
            </div>
          </Card>

          <Verdict data={data} />
          <EquityCurve closed={data.closed} />
          <OpenPositions positions={data.positions} />
          <ClosedTrades closed={data.closed} />
        </>
      )}
    </div>
  )
}
