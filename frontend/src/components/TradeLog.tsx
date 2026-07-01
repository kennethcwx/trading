import { useState, useEffect, useCallback } from 'react'
import type { Trade } from '../types'
import { fetchTrades, addTrade, closeTrade, deleteTrade } from '../api'

const today = () => new Date().toISOString().split('T')[0]

const pnlColor = (v: number | null | undefined) =>
  v == null ? '#888' : v >= 0 ? 'var(--green)' : 'var(--red)'

const fmt = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`

const fmtPct = (v: number | null | undefined) =>
  v == null ? '' : ` (${v >= 0 ? '+' : ''}${v.toFixed(1)}%)`

const inputCls = "w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-colors"
const inputStyle = { background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#e5e5e5' }

function AddForm({ onSave, onCancel }: { onSave: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    symbol: '', shares: '', entry_date: today(), entry_price: '', signal_reason: '', notes: '', strategy: 'A' as 'A' | 'B' | 'C',
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
    if (isNaN(shares) || shares <= 0) { setError('Shares must be a positive number'); return }
    if (isNaN(price) || price <= 0) { setError('Entry Price must be a positive number'); return }
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
        strategy: form.strategy,
      })
      onSave()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save — check backend is running')
    } finally {
      setSaving(false)
    }
  }

  const fields = [
    { label: 'Symbol',          key: 'symbol',        placeholder: 'AAPL',         required: true },
    { label: 'Shares',          key: 'shares',        placeholder: '1.5',          required: true },
    { label: 'Entry Date',      key: 'entry_date',    placeholder: '',             required: false },
    { label: 'Entry Price (USD)',key: 'entry_price',   placeholder: '182.50',       required: true },
    { label: 'Signal (optional)',key: 'signal_reason', placeholder: 'RSI <40 + SMA',required: false },
    { label: 'Notes (optional)', key: 'notes',         placeholder: '',             required: false },
  ]

  return (
    <form onSubmit={e => void handleSubmit(e)}
      className="rounded-2xl border p-5 mb-5"
      style={{ background: 'var(--surface-2)', borderColor: 'var(--border-2)' }}>
      <div className="text-sm font-semibold text-white mb-4">Log New Trade</div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
        {fields.map(({ label, key, placeholder, required }) => (
          <div key={key}>
            <label className="block text-xs mb-1.5" style={{ color: '#888' }}>
              {label}{required && <span className="ml-0.5" style={{ color: 'var(--red)' }}>*</span>}
            </label>
            <input
              type={key.includes('date') ? 'date' : 'text'}
              value={form[key as keyof typeof form]}
              onChange={set(key)}
              placeholder={placeholder}
              className={inputCls}
              style={inputStyle}
            />
          </div>
        ))}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Strategy</label>
          <div className="flex gap-2">
            {(['A', 'B', 'C'] as const).map(s => (
              <button
                key={s}
                type="button"
                onClick={() => setForm(f => ({ ...f, strategy: s }))}
                className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all cursor-pointer"
                style={form.strategy === s
                  ? { background: 'var(--teal)', color: '#000' }
                  : { background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#666' }
                }
              >
                {s === 'A' ? 'A (Combined)' : s === 'B' ? 'B (Mean-Rev)' : 'C (MR·No RS)'}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="flex gap-2 items-center flex-wrap">
        <button
          type="submit"
          disabled={saving}
          className="px-5 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer disabled:opacity-40"
          style={{ background: 'var(--green)', color: '#000' }}
        >
          {saving ? 'Saving…' : 'Log Trade'}
        </button>
        <button type="button" onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors cursor-pointer"
          style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#888' }}>
          Cancel
        </button>
        {error && <span className="text-xs" style={{ color: 'var(--red)' }}>{error}</span>}
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
      setError(err instanceof Error ? err.message : 'Failed to close trade')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={e => void handleSubmit(e)}
      className="rounded-xl border p-4 mt-3"
      style={{ background: 'var(--surface)', borderColor: 'var(--border-2)' }}>
      <div className="text-xs font-medium mb-3" style={{ color: '#888' }}>
        Close {trade.symbol} — enter exit details
      </div>
      <div className="flex flex-wrap gap-3">
        {[
          { label: 'Exit Date',       key: 'exit_date',   type: 'date',   placeholder: '' },
          { label: 'Exit Price (USD)', key: 'exit_price',  type: 'text',   placeholder: '195.00' },
          { label: 'Notes',            key: 'notes',       type: 'text',   placeholder: 'RSI >70 exit' },
        ].map(({ label, key, type, placeholder }) => (
          <div key={key}>
            <label className="block text-[11px] mb-1" style={{ color: '#666' }}>{label}</label>
            <input type={type} value={form[key as keyof typeof form]} onChange={set(key)}
              placeholder={placeholder}
              className="rounded-lg px-3 py-1.5 text-xs focus:outline-none"
              style={{ ...inputStyle, width: key === 'notes' ? '160px' : key === 'exit_price' ? '110px' : 'auto' }} />
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3 items-center">
        <button type="submit" disabled={saving}
          className="px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-40"
          style={{ background: 'var(--green)', color: '#000' }}>
          {saving ? 'Saving…' : 'Confirm Close'}
        </button>
        <button type="button" onClick={onCancel}
          className="px-3 py-1.5 rounded-lg text-xs cursor-pointer"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#888' }}>
          Cancel
        </button>
        {error && <span className="text-xs" style={{ color: 'var(--red)' }}>{error}</span>}
      </div>
    </form>
  )
}

function OpenPositionCard({ trade, onClose, onDelete }: {
  trade: Trade
  onClose: () => void
  onDelete: () => void
}) {
  const pnl = trade.unrealized_pnl
  const pct = trade.unrealized_pnl_pct
  const color = pnlColor(pnl)

  return (
    <div className="rounded-2xl border p-4" style={{ background: 'var(--surface-2)', borderColor: 'var(--border-2)' }}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="font-bold text-white text-base">{trade.symbol}</span>
          <div className="text-xs mt-0.5" style={{ color: '#666' }}>
            {trade.shares} shares · entered {trade.entry_date}
          </div>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold font-mono" style={{ color }}>
            {fmt(pnl)}
          </div>
          {pct != null && (
            <div className="text-xs font-mono" style={{ color }}>
              {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center gap-4 text-xs font-mono" style={{ color: '#666' }}>
        <span>Entry <span className="text-white">${trade.entry_price.toFixed(2)}</span></span>
        {trade.current_price != null && (
          <span>Now <span className="text-white">${trade.current_price.toFixed(2)}</span></span>
        )}
        {trade.signal_reason && (
          <span className="truncate max-w-[160px]" style={{ color: '#555' }}>{trade.signal_reason}</span>
        )}
      </div>

      <div className="mt-3 flex gap-2">
        <button onClick={onClose}
          className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer transition-colors"
          style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#ccc' }}>
          Close position
        </button>
        <button onClick={onDelete}
          className="px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition-colors"
          style={{ color: '#555' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
          onMouseLeave={e => (e.currentTarget.style.color = '#555')}>
          ×
        </button>
      </div>
    </div>
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
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Header */}
      <div className="px-5 py-4 border-b flex items-center justify-between flex-wrap gap-3"
        style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-5">
          <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Trade Log</h2>
          {closed.length > 0 && (
            <div className="flex gap-4 text-xs">
              <span>Realized <span className="font-mono font-semibold" style={{ color: pnlColor(totalRealized) }}>{fmt(totalRealized)}</span></span>
              {winRate !== null && (
                <span style={{ color: '#666' }}>Win rate <span className="text-white">{winRate}%</span> ({wins}/{closed.length})</span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={() => { setShowAdd(v => !v); setClosingId(null) }}
          className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer transition-colors"
          style={{ background: 'var(--green)', color: '#000' }}
        >
          + Log Trade
        </button>
      </div>

      <div className="p-5">
        {showAdd && (
          <AddForm onSave={async () => { setShowAdd(false); await load() }} onCancel={() => setShowAdd(false)} />
        )}

        {loading && <div className="text-sm py-6 text-center" style={{ color: '#555' }}>Loading…</div>}
        {loadError && (
          <div className="text-sm py-6 text-center" style={{ color: 'var(--red)' }}>
            {loadError} —{' '}
            <button onClick={() => void load()} className="underline cursor-pointer">retry</button>
          </div>
        )}
        {!loading && !loadError && trades.length === 0 && (
          <div className="text-sm py-8 text-center" style={{ color: '#555' }}>
            No trades logged yet.{' '}
            <span style={{ color: 'var(--teal)' }}>Click + Log Trade when you execute a signal.</span>
          </div>
        )}

        {/* Open positions */}
        {open.length > 0 && (
          <div className="mb-6">
            <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
              Open Positions ({open.length})
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {open.map(t => (
                <div key={t.id}>
                  <OpenPositionCard
                    trade={t}
                    onClose={() => setClosingId(closingId === t.id ? null : t.id)}
                    onDelete={() => void handleDelete(t.id)}
                  />
                  {closingId === t.id && (
                    <CloseForm
                      trade={t}
                      onSave={async () => { setClosingId(null); await load() }}
                      onCancel={() => setClosingId(null)}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Closed trades */}
        {closed.length > 0 && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
              Closed Trades ({closed.length})
            </div>
            <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border-2)' }}>
              {closed.map((t, i) => (
                <div
                  key={t.id}
                  className="flex items-center gap-4 px-4 py-3 border-b last:border-0 hover:bg-white/[0.02] transition-colors"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <span className="font-bold text-sm w-14 flex-shrink-0" style={{ color: i < 3 ? 'white' : '#888' }}>
                    {t.symbol}
                  </span>
                  <span className="text-xs font-mono w-10 flex-shrink-0" style={{ color: '#666' }}>
                    {t.shares}
                  </span>
                  <span className="text-xs font-mono flex-1" style={{ color: '#555' }}>
                    ${t.entry_price.toFixed(2)} → ${t.exit_price!.toFixed(2)}
                  </span>
                  <span className="text-xs hidden sm:block" style={{ color: '#555' }}>
                    {t.exit_date}
                  </span>
                  <span className="text-sm font-mono font-semibold w-20 text-right" style={{ color: pnlColor(t.realized_pnl) }}>
                    {fmt(t.realized_pnl)}
                    {t.realized_pnl_pct != null && (
                      <span className="text-[10px] block font-normal">{fmtPct(t.realized_pnl_pct)}</span>
                    )}
                  </span>
                  <button
                    onClick={() => void handleDelete(t.id)}
                    className="text-xs px-2 py-1 rounded cursor-pointer transition-colors"
                    style={{ color: '#444' }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
                    onMouseLeave={e => (e.currentTarget.style.color = '#444')}>
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
