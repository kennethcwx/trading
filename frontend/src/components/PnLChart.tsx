import { useMemo } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import type { Trade } from '../types'

interface Props {
  trades: Trade[]
}

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
  const color = d.pnl >= 0 ? '#4ade80' : '#f87171'
  return (
    <div className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs shadow-lg">
      <div className="text-slate-400 mb-1">{d.date} · {d.trade}</div>
      <div style={{ color }}>
        Trade: {d.pnl >= 0 ? '+' : ''}${d.pnl.toFixed(2)}
      </div>
      <div className="text-slate-300 font-bold">
        Total: {d.cumulative >= 0 ? '+' : ''}${d.cumulative.toFixed(2)}
      </div>
    </div>
  )
}

export function PnLChart({ trades }: Props) {
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
      <div className="bg-[#1a1d2e] rounded-lg border border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
        P&L chart will appear after you close 2+ trades
      </div>
    )
  }

  const totalPnL = data[data.length - 1]?.cumulative ?? 0
  const wins = closed.filter(t => (t.realized_pnl ?? 0) > 0).length
  const losses = closed.length - wins
  const areaColor = totalPnL >= 0 ? '#14b8a6' : '#f87171'
  const areaFill = totalPnL >= 0 ? '#14b8a620' : '#f8717120'

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">P&L Over Time</h2>
        <div className="flex gap-4 text-xs text-slate-500">
          <span>
            Total:{' '}
            <span className={totalPnL >= 0 ? 'text-green-400 font-bold' : 'text-red-400 font-bold'}>
              {totalPnL >= 0 ? '+' : ''}${totalPnL.toFixed(2)}
            </span>
          </span>
          <span className="text-green-500">{wins}W</span>
          <span className="text-red-500">{losses}L</span>
        </div>
      </div>
      <div className="p-4">
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="date"
              tick={{ fill: '#475569', fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#475569', fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={v => `$${v}`}
              width={48}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0} stroke="#334155" strokeDasharray="4 2" />
            <Area
              type="monotone"
              dataKey="cumulative"
              stroke={areaColor}
              strokeWidth={2}
              fill={areaFill}
              dot={{ fill: areaColor, r: 3, strokeWidth: 0 }}
              activeDot={{ r: 5, fill: areaColor }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
