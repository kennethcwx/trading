import { useState, useEffect, useCallback } from 'react'
import type { OptionsTrade, OptionsTradeStatus } from '../types'
import { fetchOptionsTrades, addOptionsTrade, closeOptionsTrade, deleteOptionsTrade } from '../api'

const today = () => new Date().toISOString().split('T')[0]

const fmt = (v: number | null | undefined, prefix = '$') =>
  v == null ? '—' : `${v >= 0 ? '+' : '-'}${prefix}${Math.abs(v).toFixed(2)}`

const pnlColor = (v: number | null | undefined) =>
  v == null ? '#888' : v >= 0 ? 'var(--green)' : 'var(--red)'

const STATUS_STYLE: Record<OptionsTradeStatus, { color: string; bg: string; label: string }> = {
  open:     { color: 'var(--teal)',   bg: 'var(--teal-dim)',   label: 'Open' },
  expired:  { color: 'var(--green)',  bg: 'var(--green-dim)',  label: 'Expired ✓' },
  closed:   { color: 'var(--yellow)', bg: 'var(--yellow-dim)', label: 'Closed early' },
  assigned: { color: 'var(--orange)', bg: 'var(--orange-dim)', label: 'Assigned' },
}

const inputCls = "w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-colors"
const inputStyle = { background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#e5e5e5' }

function AddForm({ onSave, onCancel }: { onSave: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    symbol: '', strategy: 'Cash-Secured Put', phase: '1',
    strike: '', long_strike: '', expiry_date: '', dte_at_entry: '', premium: '',
    contracts: '1', open_date: today(), notes: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isSpread = form.strategy === 'Bull Put Spread'

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.symbol || !form.strike || !form.expiry_date || !form.premium) {
      setError('Symbol, Strike, Expiry, and Premium are required')
      return
    }
    if (isSpread && !form.long_strike) {
      setError('Long strike is required for a spread')
      return
    }
    const strike = parseFloat(form.strike)
    const longStrike = isSpread ? parseFloat(form.long_strike) : undefined
    const premium = parseFloat(form.premium)
    const contracts = parseInt(form.contracts) || 1
    const dte = form.dte_at_entry ? parseInt(form.dte_at_entry) : undefined
    if (isNaN(strike) || isNaN(premium) || (isSpread && isNaN(longStrike as number))) {
      setError('Strike, long strike, and premium must be numbers'); return
    }
    setSaving(true); setError(null)
    try {
      await addOptionsTrade({
        symbol: form.symbol.toUpperCase().trim(),
        strategy: form.strategy,
        phase: parseInt(form.phase),
        strike, long_strike: longStrike, expiry_date: form.expiry_date,
        dte_at_entry: dte, premium, contracts,
        open_date: form.open_date,
        notes: form.notes || undefined,
      })
      onSave()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={e => void handleSubmit(e)}
      className="rounded-2xl border p-5 mb-5"
      style={{ background: 'var(--surface-2)', borderColor: 'var(--border-2)' }}>
      <div className="text-sm font-semibold text-white mb-4">Log Options Trade</div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
        {/* Symbol */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Symbol <span style={{ color: 'var(--red)' }}>*</span></label>
          <input type="text" value={form.symbol} onChange={set('symbol')} placeholder="NOK"
            className={inputCls} style={inputStyle} />
        </div>
        {/* Strategy */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Strategy</label>
          <select value={form.strategy} onChange={set('strategy')}
            className={inputCls} style={inputStyle}>
            <option>Cash-Secured Put</option>
            <option>Covered Call</option>
            <option>Bull Put Spread</option>
          </select>
        </div>
        {/* Phase auto-syncs with strategy */}
        {/* Strike */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>{isSpread ? 'Short strike' : 'Strike'} <span style={{ color: 'var(--red)' }}>*</span></label>
          <input type="text" value={form.strike} onChange={set('strike')} placeholder="14.00"
            className={inputCls} style={inputStyle} />
        </div>
        {/* Long strike — spreads only */}
        {isSpread && (
          <div>
            <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Long strike <span style={{ color: 'var(--red)' }}>*</span></label>
            <input type="text" value={form.long_strike} onChange={set('long_strike')} placeholder="9.00"
              className={inputCls} style={inputStyle} />
          </div>
        )}
        {/* Premium */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>{isSpread ? 'Net credit / contract' : 'Premium / share'} <span style={{ color: 'var(--red)' }}>*</span></label>
          <input type="text" value={form.premium} onChange={set('premium')} placeholder="0.96"
            className={inputCls} style={inputStyle} />
        </div>
        {/* Contracts */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Contracts</label>
          <input type="text" value={form.contracts} onChange={set('contracts')} placeholder="1"
            className={inputCls} style={inputStyle} />
        </div>
        {/* DTE */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>DTE at entry</label>
          <input type="text" value={form.dte_at_entry} onChange={set('dte_at_entry')} placeholder="42"
            className={inputCls} style={inputStyle} />
        </div>
        {/* Open date */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Open date</label>
          <input type="date" value={form.open_date} onChange={set('open_date')}
            className={inputCls} style={inputStyle} />
        </div>
        {/* Expiry */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Expiry date <span style={{ color: 'var(--red)' }}>*</span></label>
          <input type="date" value={form.expiry_date} onChange={set('expiry_date')}
            className={inputCls} style={inputStyle} />
        </div>
        {/* Notes */}
        <div>
          <label className="block text-xs mb-1.5" style={{ color: '#888' }}>Notes</label>
          <input type="text" value={form.notes} onChange={set('notes')} placeholder="IVR 45, RSI 42"
            className={inputCls} style={inputStyle} />
        </div>
      </div>

      {/* Preview */}
      {form.premium && form.contracts && (
        <div className="mb-4 px-3 py-2 rounded-lg text-xs font-mono" style={{ background: 'var(--surface)', color: 'var(--teal)' }}>
          Total {isSpread ? 'net credit' : 'premium'} collected: ${(parseFloat(form.premium || '0') * 100 * (parseInt(form.contracts) || 1)).toFixed(2)}
        </div>
      )}

      <div className="flex gap-2 items-center flex-wrap">
        <button type="submit" disabled={saving}
          className="px-5 py-2 rounded-lg text-sm font-semibold cursor-pointer disabled:opacity-40"
          style={{ background: 'var(--teal)', color: '#000' }}>
          {saving ? 'Saving…' : 'Log Trade'}
        </button>
        <button type="button" onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm cursor-pointer"
          style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#888' }}>
          Cancel
        </button>
        {error && <span className="text-xs" style={{ color: 'var(--red)' }}>{error}</span>}
      </div>
    </form>
  )
}

function CloseForm({ trade, onSave, onCancel }: { trade: OptionsTrade; onSave: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({ close_date: today(), close_premium: '0', status: 'expired', notes: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const close_premium = parseFloat(form.close_premium)
    if (isNaN(close_premium)) { setError('Close premium must be a number (0 if expired worthless)'); return }
    setSaving(true); setError(null)
    try {
      await closeOptionsTrade(trade.id, {
        close_date: form.close_date,
        close_premium,
        status: form.status,
        notes: form.notes || undefined,
      })
      onSave()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to close')
    } finally {
      setSaving(false)
    }
  }

  const openPremium = trade.premium * 100 * trade.contracts
  const closePremium = parseFloat(form.close_premium || '0') * 100 * trade.contracts
  const previewPnl = openPremium - closePremium

  return (
    <form onSubmit={e => void handleSubmit(e)}
      className="rounded-xl border p-4 mt-3"
      style={{ background: 'var(--surface)', borderColor: 'var(--border-2)' }}>
      <div className="text-xs font-medium mb-3" style={{ color: '#888' }}>
        Close {trade.symbol} {trade.strategy} — {trade.long_strike != null ? `$${trade.strike}/$${trade.long_strike} spread` : `$${trade.strike} strike`}
      </div>
      <div className="flex flex-wrap gap-3 mb-3">
        <div>
          <label className="block text-[11px] mb-1" style={{ color: '#666' }}>Close date</label>
          <input type="date" value={form.close_date} onChange={set('close_date')}
            className="rounded-lg px-3 py-1.5 text-xs focus:outline-none" style={inputStyle} />
        </div>
        <div>
          <label className="block text-[11px] mb-1" style={{ color: '#666' }}>Close premium / share (0 if expired)</label>
          <input type="text" value={form.close_premium} onChange={set('close_premium')} placeholder="0"
            className="rounded-lg px-3 py-1.5 text-xs focus:outline-none w-28" style={inputStyle} />
        </div>
        <div>
          <label className="block text-[11px] mb-1" style={{ color: '#666' }}>Outcome</label>
          <select value={form.status} onChange={set('status')}
            className="rounded-lg px-3 py-1.5 text-xs focus:outline-none" style={inputStyle}>
            <option value="expired">Expired worthless</option>
            <option value="closed">Closed early</option>
            <option value="assigned">Assigned (stock delivered)</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] mb-1" style={{ color: '#666' }}>Notes</label>
          <input type="text" value={form.notes} onChange={set('notes')} placeholder=""
            className="rounded-lg px-3 py-1.5 text-xs focus:outline-none w-36" style={inputStyle} />
        </div>
      </div>

      <div className="mb-3 text-xs font-mono" style={{ color: pnlColor(previewPnl) }}>
        P&L preview: {previewPnl >= 0 ? '+' : ''}${previewPnl.toFixed(2)}
      </div>

      <div className="flex gap-2 items-center">
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

export function OptionsTradeLog() {
  const [trades, setTrades] = useState<OptionsTrade[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [closingId, setClosingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchOptionsTrades()
      setTrades(res.trades)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const open = trades.filter(t => t.status === 'open')
  const closed = trades.filter(t => t.status !== 'open')

  const totalCollected = trades.reduce((s, t) => s + t.total_premium, 0)
  const totalPnl = closed.reduce((s, t) => s + (t.pnl ?? 0), 0)
  const wins = closed.filter(t => (t.pnl ?? 0) >= 0).length

  const handleDelete = async (id: number) => {
    if (!window.confirm('Delete this options trade?')) return
    await deleteOptionsTrade(id)
    await load()
  }

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Header */}
      <div className="px-5 py-4 border-b flex items-center justify-between flex-wrap gap-3"
        style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-5">
          <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Options Trade Log</h2>
          {closed.length > 0 && (
            <div className="flex gap-4 text-xs">
              <span>Realised <span className="font-mono font-semibold" style={{ color: pnlColor(totalPnl) }}>{fmt(totalPnl)}</span></span>
              <span style={{ color: '#666' }}>{wins}/{closed.length} profitable</span>
              <span style={{ color: '#555' }}>Total collected <span className="text-white font-mono">${totalCollected.toFixed(2)}</span></span>
            </div>
          )}
        </div>
        <button onClick={() => { setShowAdd(v => !v); setClosingId(null) }}
          className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer"
          style={{ background: 'var(--teal)', color: '#000' }}>
          + Log Trade
        </button>
      </div>

      <div className="p-5">
        {showAdd && (
          <AddForm onSave={async () => { setShowAdd(false); await load() }} onCancel={() => setShowAdd(false)} />
        )}

        {loading && <div className="text-sm py-6 text-center" style={{ color: '#555' }}>Loading…</div>}
        {error && <div className="text-sm py-6 text-center" style={{ color: 'var(--red)' }}>{error}</div>}

        {!loading && !error && trades.length === 0 && (
          <div className="text-sm py-8 text-center" style={{ color: '#555' }}>
            No options trades logged yet.{' '}
            <span style={{ color: 'var(--teal)' }}>Log your first trade when you sell a put or call.</span>
          </div>
        )}

        {/* Open positions */}
        {open.length > 0 && (
          <div className="mb-6">
            <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
              Open ({open.length})
            </div>
            <div className="space-y-3">
              {open.map(t => {
                const ss = STATUS_STYLE[t.status]
                return (
                  <div key={t.id}>
                    <div className="rounded-2xl border p-4" style={{ background: 'var(--surface-2)', borderColor: 'var(--border-2)' }}>
                      <div className="flex items-start justify-between gap-2 flex-wrap">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-white text-base">{t.symbol}</span>
                            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                              style={{ background: ss.bg, color: ss.color }}>{ss.label}</span>
                            <span className="text-xs" style={{ color: '#555' }}>{t.strategy}</span>
                          </div>
                          <div className="text-xs mt-1 font-mono" style={{ color: '#666' }}>
                            {t.long_strike != null ? `$${t.strike}/$${t.long_strike} spread` : `$${t.strike} strike`} · expires {t.expiry_date}
                            {t.dte_at_entry && <span> · {t.dte_at_entry} DTE at entry</span>}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-lg font-bold font-mono" style={{ color: 'var(--teal)' }}>
                            +${t.total_premium.toFixed(2)}
                          </div>
                          <div className="text-xs" style={{ color: '#555' }}>
                            ${t.premium.toFixed(2)}/sh × {t.contracts} contract{t.contracts > 1 ? 's' : ''}
                          </div>
                        </div>
                      </div>
                      {t.notes && <div className="mt-2 text-xs" style={{ color: '#555' }}>{t.notes}</div>}
                      <div className="mt-3 flex gap-2">
                        <button onClick={() => setClosingId(closingId === t.id ? null : t.id)}
                          className="px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer"
                          style={{ background: 'var(--surface)', border: '1px solid var(--border-2)', color: '#ccc' }}>
                          Close position
                        </button>
                        <button onClick={() => void handleDelete(t.id)}
                          className="px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition-colors"
                          style={{ color: '#555' }}
                          onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
                          onMouseLeave={e => (e.currentTarget.style.color = '#555')}>
                          ×
                        </button>
                      </div>
                    </div>
                    {closingId === t.id && (
                      <CloseForm trade={t}
                        onSave={async () => { setClosingId(null); await load() }}
                        onCancel={() => setClosingId(null)} />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Closed trades */}
        {closed.length > 0 && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#555' }}>
              Closed ({closed.length})
            </div>
            <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border-2)' }}>
              {closed.map(t => {
                const ss = STATUS_STYLE[t.status]
                return (
                  <div key={t.id}
                    className="flex items-center gap-3 px-4 py-3 border-b last:border-0 hover:bg-white/[0.02] transition-colors"
                    style={{ borderColor: 'var(--border)' }}>
                    <span className="font-bold text-sm w-12 flex-shrink-0 text-white">{t.symbol}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full flex-shrink-0"
                      style={{ background: ss.bg, color: ss.color }}>{ss.label}</span>
                    <span className="text-xs font-mono flex-1" style={{ color: '#555' }}>
                      {t.long_strike != null ? `$${t.strike}/$${t.long_strike}` : `$${t.strike}`} · {t.expiry_date}
                    </span>
                    <span className="text-xs hidden sm:block" style={{ color: '#555' }}>
                      +${t.total_premium.toFixed(2)} collected
                    </span>
                    <span className="text-sm font-mono font-semibold w-20 text-right" style={{ color: pnlColor(t.pnl) }}>
                      {fmt(t.pnl)}
                    </span>
                    <button onClick={() => void handleDelete(t.id)}
                      className="text-xs px-2 py-1 rounded cursor-pointer transition-colors"
                      style={{ color: '#444' }}
                      onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
                      onMouseLeave={e => (e.currentTarget.style.color = '#444')}>
                      ×
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
