import { useState, useEffect } from 'react'
import { fetchWatchlist, updateWatchlist } from '../api'

export function WatchlistEditor({ onSave }: { onSave?: () => void }) {
  const [watchlist, setWatchlist] = useState<string[]>([])
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchWatchlist().then(data => {
      setWatchlist(data.watchlist)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  async function save(symbols: string[]) {
    setSaving(true)
    await updateWatchlist(symbols)
    setSaving(false)
    onSave?.()
  }

  function add() {
    const sym = input.trim().toUpperCase()
    if (!sym || !/^[A-Z0-9.]{1,6}$/.test(sym)) {
      setError('Enter a valid US ticker (e.g. AAPL, BRK.B)')
      return
    }
    if (watchlist.includes(sym)) {
      setError(`${sym} is already in your watchlist`)
      return
    }
    setError(null)
    const next = [...watchlist, sym]
    setWatchlist(next)
    setInput('')
    void save(next)
  }

  function remove(sym: string) {
    const next = watchlist.filter(s => s !== sym)
    setWatchlist(next)
    void save(next)
  }

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Watchlist</h2>
        <span className="text-xs" style={{ color: '#555' }}>{watchlist.length} stocks · US tickers only</span>
      </div>

      <div className="p-5">
        {loading ? (
          <div className="text-sm py-2" style={{ color: '#555' }}>Loading…</div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-4 min-h-[36px]">
              {watchlist.length === 0 && (
                <span className="text-sm" style={{ color: '#555' }}>No stocks — add some below</span>
              )}
              {watchlist.map(sym => (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1 pl-3 pr-1 py-1 rounded-full text-xs"
                  style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#ccc' }}
                >
                  <span className="font-bold font-mono">{sym}</span>
                  <button
                    onClick={() => remove(sym)}
                    disabled={saving}
                    className="flex items-center justify-center w-5 h-5 rounded-full text-sm transition-colors cursor-pointer disabled:opacity-40"
                    style={{ color: '#555' }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--red)')}
                    onMouseLeave={e => (e.currentTarget.style.color = '#555')}
                    aria-label={`Remove ${sym}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>

            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={e => { setInput(e.target.value.toUpperCase()); setError(null) }}
                onKeyDown={e => e.key === 'Enter' && add()}
                placeholder="Add ticker…"
                maxLength={6}
                className="flex-1 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)', color: '#e5e5e5' }}
              />
              <button
                onClick={add}
                disabled={saving || !input.trim()}
                className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer disabled:opacity-40 transition-colors"
                style={{ background: 'var(--teal)', color: '#000' }}
              >
                {saving ? '…' : 'Add'}
              </button>
            </div>

            {error && <div className="text-xs mt-2" style={{ color: 'var(--red)' }}>{error}</div>}
            <div className="text-xs mt-3" style={{ color: '#444' }}>Changes take effect on next Refresh</div>
          </>
        )}
      </div>
    </div>
  )
}
