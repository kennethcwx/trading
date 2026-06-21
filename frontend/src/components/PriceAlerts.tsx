import { useState, useEffect, useCallback } from 'react'
import { fetchAlerts, addAlert, deleteAlert } from '../api'
import type { PriceAlert } from '../types'

export function PriceAlerts() {
  const [alerts, setAlerts] = useState<PriceAlert[]>([])
  const [symbol, setSymbol] = useState('')
  const [target, setTarget] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchAlerts()
      setAlerts(res.alerts)
    } catch {
      // keep last known list on transient failure
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function add() {
    const sym = symbol.trim().toUpperCase()
    const t = parseFloat(target)
    if (!sym) { setError('Enter a ticker'); return }
    if (isNaN(t) || t <= 0) { setError('Enter a valid target price'); return }
    setError(null)
    setSaving(true)
    try {
      await addAlert({ symbol: sym, target: t })
      setSymbol(''); setTarget('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add alert')
    } finally {
      setSaving(false)
    }
  }

  async function remove(id: number) {
    setAlerts(a => a.filter(x => x.id !== id))
    await deleteAlert(id)
  }

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Price Alerts</h2>
        <span className="text-xs" style={{ color: '#555' }}>{alerts.length} active · checked every 5 min</span>
      </div>

      <div className="p-5">
        {loading ? (
          <div className="text-sm py-2" style={{ color: '#555' }}>Loading…</div>
        ) : (
          <>
            <div className="flex flex-col gap-2 mb-4 min-h-[36px]">
              {alerts.length === 0 && (
                <span className="text-sm" style={{ color: '#555' }}>No alerts set — add one below</span>
              )}
              {alerts.map(a => (
                <div key={a.id}
                  className="flex items-center justify-between gap-3 rounded-lg px-3 py-2"
                  style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                  <div className="flex items-center gap-2 text-sm font-mono">
                    <span className="font-bold" style={{ color: '#e5e5e5' }}>{a.symbol}</span>
                    <span style={{ color: '#666' }}>{a.direction === 'above' ? '↑' : '↓'}</span>
                    <span style={{ color: '#888' }}>${a.target.toFixed(2)}</span>
                  </div>
                  <button
                    onClick={() => void remove(a.id)}
                    className="flex items-center justify-center w-6 h-6 rounded-full text-sm transition-colors cursor-pointer"
                    style={{ color: '#555' }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
                    onMouseLeave={e => (e.currentTarget.style.color = '#555')}
                    aria-label={`Remove alert for ${a.symbol}`}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>

            <div className="flex gap-2">
              <input
                type="text"
                value={symbol}
                onChange={e => { setSymbol(e.target.value.toUpperCase()); setError(null) }}
                placeholder="Ticker…"
                maxLength={6}
                className="w-28 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#e5e5e5' }}
              />
              <input
                type="text"
                value={target}
                onChange={e => { setTarget(e.target.value); setError(null) }}
                onKeyDown={e => e.key === 'Enter' && void add()}
                placeholder="Target price…"
                className="flex-1 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#e5e5e5' }}
              />
              <button
                onClick={() => void add()}
                disabled={saving || !symbol.trim() || !target.trim()}
                className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer disabled:opacity-40 transition-colors"
                style={{ background: 'var(--teal)', color: '#000' }}
              >
                {saving ? '…' : 'Add'}
              </button>
            </div>

            {error && <div className="text-xs mt-2" style={{ color: 'var(--red)' }}>{error}</div>}
            <div className="text-xs mt-3" style={{ color: '#444' }}>
              Direction (above/below) is set automatically from the current price. Telegram notifies you when triggered.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
