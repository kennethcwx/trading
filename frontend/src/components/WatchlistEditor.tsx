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
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Watchlist</h2>
        <span className="text-xs text-slate-600">{watchlist.length} stocks · US tickers only</span>
      </div>

      <div className="p-4">
        {loading ? (
          <div className="text-xs text-slate-500 py-2">Loading…</div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 mb-4 min-h-[32px]">
              {watchlist.length === 0 && (
                <span className="text-xs text-slate-600">No stocks — add some below</span>
              )}
              {watchlist.map(sym => (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1.5 bg-slate-800 text-slate-200 text-xs px-2.5 py-1 rounded border border-slate-700"
                >
                  <span className="font-bold font-mono">{sym}</span>
                  <button
                    onClick={() => remove(sym)}
                    disabled={saving}
                    className="text-slate-500 hover:text-red-400 disabled:opacity-40 transition-colors cursor-pointer leading-none"
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
                placeholder="AAPL, VOO, TSLA…"
                maxLength={6}
                className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-200 font-mono w-36 focus:outline-none focus:border-teal-500"
              />
              <button
                onClick={add}
                disabled={saving || !input.trim()}
                className="text-xs bg-teal-700 hover:bg-teal-600 disabled:opacity-40 px-3 py-1.5 rounded text-white transition-colors cursor-pointer"
              >
                {saving ? '…' : 'Add'}
              </button>
            </div>

            {error && <div className="text-xs text-red-400 mt-2">{error}</div>}

            <div className="text-xs text-slate-700 mt-3">
              Changes take effect on next Refresh
            </div>
          </>
        )}
      </div>
    </div>
  )
}
