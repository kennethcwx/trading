import { useState, useEffect, useCallback } from 'react'
import type { Trade } from '../types'
import { fetchTrades, addTrade, closeTrade, deleteTrade } from '../api'

const today = () => new Date().toISOString().split('T')[0]

const pnlColor = (v: number | null | undefined) =>
  v == null ? 'text-slate-400' : v >= 0 ? 'text-green-400' : 'text-red-400'

const fmt = (v: number | null | undefined, prefix = '$') =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${prefix}${Math.abs(v).toFixed(2)}`

const fmtPct = (v: number | null | undefined) =>
  v == null ? '' : ` (${v >= 0 ? '+' : ''}${v.toFixed(1)}%)`

function AddForm({ onSave, onCancel }: { onSave: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    symbol: '', shares: '', entry_date: today(), entry_price: '', signal_reason: '', notes: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.symbol || !form.shares || !form.entry_price) {
      setError('Symbol, Shares, and Entry Price are required')
      return
    }
    const shares = parseFloat(form.shares)
    const price = parseFloat(form.entry_price)
    if (isNaN(shares) || shares <= 0) {
      setError('Shares must be a positive number')
      return
    }
    if (isNaN(price) || price <= 0) {
      setError('Entry Price must be a positive number')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await addTrade({
        symbol: form.symbol.toUpperCase().trim(),
        shares,
        entry_date: form.entry_date,
        entry_price: price,
        signal_reason: form.signal_reason || undefined,
        notes: form.notes || undefined,
      })
      onSave()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save trade — check backend is running')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={e => void handleSubmit(e)} className="bg-slate-900 border border-slate-700 rounded-lg p-4 mb-4">
      <div className="text-xs font-bold text-slate-300 uppercase tracking-widest mb-3">Log New Trade</div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
        {[
          { label: 'Symbol', key: 'symbol', placeholder: 'AAPL', required: true },
          { label: 'Shares', key: 'shares', placeholder: '1.5', required: true },
          { label: 'Entry Date', key: 'entry_date', placeholder: '', required: false },
          { label: 'Entry Price (USD)', key: 'entry_price', placeholder: '182.50', required: true },
          { label: 'Signal (optional)', key: 'signal_reason', placeholder: 'RSI <40 + 200 SMA', required: false },
          { label: 'Notes (optional)', key: 'notes', placeholder: '', required: false },
        ].map(({ label, key, placeholder, required }) => (
          <div key={key}>
            <label className="block text-xs text-slate-500 mb-1">
              {label}{required && <span className="text-red-400 ml-0.5">*</span>}
            </label>
            <input
              type={key.includes('date') ? 'date' : 'text'}
              value={form[key as keyof typeof form]}
              onChange={set(key)}
              placeholder={placeholder}
              className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-teal-500"
            />
          </div>
        ))}
      </div>
      <div className="flex gap-2 items-center flex-wrap">
        <button
          type="submit"
          disabled={saving}
          className="text-xs bg-teal-700 hover:bg-teal-600 disabled:opacity-40 px-3 py-1.5 rounded text-white transition-colors cursor-pointer"
        >
          {saving ? 'Saving…' : 'Log Trade'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded border border-slate-700 text-slate-400 transition-colors cursor-pointer"
        >
          Cancel
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </form>
  )
}

function CloseForm({ trade, onSave, onCancel }: { trade: Trade; onSave: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({ exit_date: today(), exit_price: '', notes: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const exitPrice = parseFloat(form.exit_price)
    if (!form.exit_price || isNaN(exitPrice) || exitPrice <= 0) {
      setError('Exit Price must be a positive number')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await closeTrade(trade.id, {
        exit_date: form.exit_date,
        exit_price: exitPrice,
        notes: form.notes || undefined,
      })
      onSave()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to close trade — check backend is running')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={e => void handleSubmit(e)} className="bg-slate-900/80 border border-slate-700 rounded p-3 mt-2">
      <div className="text-xs text-slate-400 mb-2">Close {trade.symbol} — enter exit details</div>
      <div className="flex gap-2 flex-wrap">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Exit Date</label>
          <input type="date" value={form.exit_date} onChange={set('exit_date')}
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-teal-500" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Exit Price (USD)</label>
          <input type="text" value={form.exit_price} onChange={set('exit_price')} placeholder="195.00"
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 w-28 focus:outline-none focus:border-teal-500" />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Notes</label>
          <input type="text" value={form.notes} onChange={set('notes')} placeholder="RSI >70 exit"
            className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 w-40 focus:outline-none focus:border-teal-500" />
        </div>
      </div>
      <div className="flex gap-2 mt-2 items-center flex-wrap">
        <button type="submit" disabled={saving}
          className="text-xs bg-teal-700 hover:bg-teal-600 disabled:opacity-40 px-3 py-1 rounded text-white transition-colors cursor-pointer">
          {saving ? 'Saving…' : 'Confirm Close'}
        </button>
        <button type="button" onClick={onCancel}
          className="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1 rounded border border-slate-700 text-slate-400 transition-colors cursor-pointer">
          Cancel
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </form>
  )
}

export function TradeLog({ onTradesChange }: { onTradesChange?: (trades: Trade[]) => void }) {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [closingId, setClosingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await fetchTrades()
      setTrades(res.trades)
      onTradesChange?.(res.trades)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load trades')
    } finally {
      setLoading(false)
    }
  }, [onTradesChange])

  useEffect(() => { void load() }, [load])

  const open = trades.filter(t => t.exit_price == null)
  const closed = trades.filter(t => t.exit_price != null)

  const totalRealized = closed.reduce((s, t) => s + (t.realized_pnl ?? 0), 0)
  const wins = closed.filter(t => (t.realized_pnl ?? 0) > 0).length
  const winRate = closed.length ? Math.round((wins / closed.length) * 100) : null

  const handleDelete = async (id: number) => {
    if (!window.confirm('Delete this trade record?')) return
    await deleteTrade(id)
    await load()
  }

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4">
          <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Trade Log</h2>
          {closed.length > 0 && (
            <div className="flex gap-3 text-xs text-slate-500">
              <span>
                Realized:{' '}
                <span className={pnlColor(totalRealized)}>
                  {fmt(totalRealized)}
                </span>
              </span>
              {winRate !== null && (
                <span>Win rate: <span className="text-slate-300">{winRate}%</span> ({wins}/{closed.length})</span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-600 hidden md:block">
            Execute BUY/SELL at next market open · Set stop immediately after entry
          </span>
          <button
            onClick={() => { setShowAdd(v => !v); setClosingId(null) }}
            className="text-xs bg-teal-800 hover:bg-teal-700 px-3 py-1.5 rounded border border-teal-700 text-teal-200 transition-colors cursor-pointer"
          >
            + Log Trade
          </button>
        </div>
      </div>

      <div className="p-4">
        {/* Add form */}
        {showAdd && (
          <AddForm onSave={async () => { setShowAdd(false); await load() }} onCancel={() => setShowAdd(false)} />
        )}

        {loading && <div className="text-xs text-slate-500 py-4 text-center">Loading…</div>}

        {loadError && (
          <div className="text-xs text-red-400 py-4 text-center">
            {loadError} — <button onClick={() => void load()} className="underline cursor-pointer">retry</button>
          </div>
        )}

        {!loading && !loadError && trades.length === 0 && (
          <div className="text-xs text-slate-500 py-6 text-center">
            No trades logged yet. Click <span className="text-teal-400">+ Log Trade</span> when you execute a signal.
          </div>
        )}

        {/* Open trades */}
        {open.length > 0 && (
          <div className="mb-6">
            <div className="text-xs font-medium text-slate-500 uppercase tracking-widest mb-2">
              Open Positions ({open.length})
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-600 uppercase tracking-wider">
                    <th className="py-2 text-left font-medium">Symbol</th>
                    <th className="py-2 text-right font-medium">Shares</th>
                    <th className="py-2 text-right font-medium">Entry</th>
                    <th className="py-2 text-right font-medium">Current</th>
                    <th className="py-2 text-right font-medium">P&L</th>
                    <th className="py-2 text-left font-medium pl-4">Signal</th>
                    <th className="py-2 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {open.map(t => (
                    <>
                      <tr key={t.id} className="border-b border-slate-800/50 hover:bg-slate-900/20 transition-colors">
                        <td className="py-2.5 font-bold text-slate-100">{t.symbol}</td>
                        <td className="py-2.5 font-mono text-right text-slate-300">{t.shares}</td>
                        <td className="py-2.5 font-mono text-right text-slate-400">
                          <div>${t.entry_price.toFixed(2)}</div>
                          <div className="text-slate-600">{t.entry_date}</div>
                        </td>
                        <td className="py-2.5 font-mono text-right text-slate-300">
                          {t.current_price != null ? `$${t.current_price.toFixed(2)}` : '—'}
                        </td>
                        <td className={`py-2.5 font-mono text-right font-bold ${pnlColor(t.unrealized_pnl)}`}>
                          {fmt(t.unrealized_pnl)}
                          <div className="font-normal text-xs">{fmtPct(t.unrealized_pnl_pct)}</div>
                        </td>
                        <td className="py-2.5 pl-4 text-slate-500 max-w-[200px] truncate">
                          {t.signal_reason ?? '—'}
                        </td>
                        <td className="py-2.5 text-right">
                          <div className="flex gap-1 justify-end">
                            <button
                              onClick={() => setClosingId(closingId === t.id ? null : t.id)}
                              className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition-colors cursor-pointer"
                            >
                              Close
                            </button>
                            <button
                              onClick={() => void handleDelete(t.id)}
                              className="px-2 py-1 rounded bg-slate-800 hover:bg-red-900/40 border border-slate-700 text-slate-500 hover:text-red-400 transition-colors cursor-pointer"
                            >
                              ×
                            </button>
                          </div>
                        </td>
                      </tr>
                      {closingId === t.id && (
                        <tr key={`close-${t.id}`}>
                          <td colSpan={7} className="pb-2 pt-0">
                            <CloseForm
                              trade={t}
                              onSave={async () => { setClosingId(null); await load() }}
                              onCancel={() => setClosingId(null)}
                            />
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Closed trades */}
        {closed.length > 0 && (
          <div>
            <div className="text-xs font-medium text-slate-500 uppercase tracking-widest mb-2">
              Closed Trades ({closed.length})
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-600 uppercase tracking-wider">
                    <th className="py-2 text-left font-medium">Symbol</th>
                    <th className="py-2 text-right font-medium">Shares</th>
                    <th className="py-2 text-right font-medium">Entry</th>
                    <th className="py-2 text-right font-medium">Exit</th>
                    <th className="py-2 text-right font-medium">Realized P&L</th>
                    <th className="py-2 text-left font-medium pl-4">Notes</th>
                    <th className="py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {closed.map(t => (
                    <tr key={t.id} className="border-b border-slate-800/50 hover:bg-slate-900/20 transition-colors">
                      <td className="py-2.5 font-bold text-slate-300">{t.symbol}</td>
                      <td className="py-2.5 font-mono text-right text-slate-400">{t.shares}</td>
                      <td className="py-2.5 font-mono text-right text-slate-500">
                        <div>${t.entry_price.toFixed(2)}</div>
                        <div className="text-slate-600">{t.entry_date}</div>
                      </td>
                      <td className="py-2.5 font-mono text-right text-slate-500">
                        <div>${t.exit_price!.toFixed(2)}</div>
                        <div className="text-slate-600">{t.exit_date}</div>
                      </td>
                      <td className={`py-2.5 font-mono text-right font-bold ${pnlColor(t.realized_pnl)}`}>
                        {fmt(t.realized_pnl)}
                        <div className="font-normal">{fmtPct(t.realized_pnl_pct)}</div>
                      </td>
                      <td className="py-2.5 pl-4 text-slate-500 max-w-[200px] truncate">
                        {t.notes ?? t.signal_reason ?? '—'}
                      </td>
                      <td className="py-2.5 text-right">
                        <button
                          onClick={() => void handleDelete(t.id)}
                          className="px-2 py-1 rounded bg-slate-800 hover:bg-red-900/40 border border-slate-700 text-slate-500 hover:text-red-400 transition-colors cursor-pointer"
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
