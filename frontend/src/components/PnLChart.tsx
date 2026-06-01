import { useMemo } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import type { Trade } from '../types'

interface ChartPoint {
  date: string
  cumulative: number
  trade: string
  pnl: number
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-SG', { month: 'short', day: 'numeric' })
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as ChartPoint
  const color = d.pnl >= 0 ? 'var(--green)' : 'var(--red)'
  return (
    <div className="rounded-xl border px-4 py-3 text-xs shadow-xl"
      style={{ background: '#1e1e1e', borderColor: 'var(--border-2)' }}>
      <div className="mb-2" style={{ color: '#888' }}>{d.date} · {d.trade}</div>
      <div style={{ color }}>
        Trade: {d.pnl >= 0 ? '+' : ''}${d.pnl.toFixed(2)}
      </div>
      <div className="font-bold mt-0.5 text-white">
        Total: {d.cumulative >= 0 ? '+' : ''}${d.cumulative.toFixed(2)}
      </div>
    </div>
  )
}

export function PnLChart({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => t.exit_price != null && t.realized_pnl != null)

  const data = useMemo<ChartPoint[]>(() => {
    const sorted = [...closed].sort((a, b) =>
      (a.exit_date ?? '').localeCompare(b.exit_date ?? '')
    )
    let cumulative = 0
    return sorted.map(t => {
      cumulative += t.realized_pnl ?? 0
      return {
        date: formatDate(t.exit_date!),
        cumulative: parseFloat(cumulative.toFixed(2)),
        trade: t.symbol,
        pnl: t.realized_pnl!,
      }
    })
  }, [closed])

  if (closed.length < 2) {
    return (
      <div className="rounded-2xl border px-6 py-10 text-center text-sm"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)', color: '#555' }}>
        P&L chart will appear after you close 2+ trades
      </div>
    )
  }

  const totalPnL = data[data.length - 1]?.cumulative ?? 0
  const wins = closed.filter(t => (t.realized_pnl ?? 0) > 0).length
  const losses = closed.length - wins
  const winRate = closed.length ? Math.round((wins / closed.length) * 100) : 0
  const areaColor = totalPnL >= 0 ? 'var(--green)' : 'var(--red)'
  const areaColorHex = totalPnL >= 0 ? '#00C805' : '#FF3B30'
  const areaFill = totalPnL >= 0 ? 'rgba(0,200,5,0.08)' : 'rgba(255,59,48,0.08)'

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Stats row */}
      <div className="px-5 py-4 border-b flex flex-wrap items-center gap-x-8 gap-y-2"
        style={{ borderColor: 'var(--border)' }}>
        <div>
          <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: '#555' }}>Realized P&L</div>
          <div className="text-2xl font-bold font-mono" style={{ color: areaColor }}>
            {totalPnL >= 0 ? '+' : ''}${Math.abs(totalPnL).toFixed(2)}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: '#555' }}>Win Rate</div>
          <div className="text-lg font-semibold text-white">{winRate}%</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider mb-1" style={{ color: '#555' }}>Trades</div>
          <div className="text-lg font-semibold">
            <span style={{ color: 'var(--green)' }}>{wins}W</span>
            <span className="mx-1" style={{ color: '#444' }}>/</span>
            <span style={{ color: 'var(--red)' }}>{losses}L</span>
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="p-5">
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={areaColorHex} stopOpacity={0.15} />
                <stop offset="100%" stopColor={areaColorHex} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: '#555', fontSize: 11, fontFamily: 'Inter' }}
              axisLine={false}
              tickLine={false}
              dy={8}
            />
            <YAxis
              tick={{ fill: '#555', fontSize: 11, fontFamily: 'Inter' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={v => `$${v}`}
              width={52}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0} stroke="var(--border-2)" strokeDasharray="4 2" />
            <Area
              type="monotone"
              dataKey="cumulative"
              stroke={areaColorHex}
              strokeWidth={2.5}
              fill="url(#pnlGradient)"
              dot={{ fill: areaColorHex, r: 3.5, strokeWidth: 0 }}
              activeDot={{ r: 5.5, fill: areaColorHex, strokeWidth: 0 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
