import { useState, useEffect, useCallback } from 'react'
import { fetchNewsFeed } from '../api'
import type { NewsDigestItem } from '../types'

export function NewsFeed() {
  const [items, setItems] = useState<NewsDigestItem[]>([])
  const [threshold, setThreshold] = useState(3)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const res = await fetchNewsFeed()
      setItems(res.items)
      setThreshold(res.threshold_pct)
    } catch {
      // keep last known list on transient failure
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>News Feed</h2>
        <span className="text-xs" style={{ color: '#555' }}>±{threshold}% moves · headlines only, no cost</span>
      </div>

      <div className="p-5">
        {loading ? (
          <div className="text-sm py-2" style={{ color: '#555' }}>Loading…</div>
        ) : items.length === 0 ? (
          <span className="text-sm" style={{ color: '#555' }}>No notable moves yet today</span>
        ) : (
          <div className="flex flex-col gap-3">
            {items.map(item => (
              <div key={item.id}
                className="rounded-lg px-3 py-3"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                <div className="flex items-center gap-2 text-sm font-mono mb-2">
                  <span className="font-bold" style={{ color: '#e5e5e5' }}>{item.symbol}</span>
                  <span style={{ color: item.pct_change >= 0 ? 'var(--teal)' : 'var(--red)' }}>
                    {item.pct_change >= 0 ? '+' : ''}{item.pct_change.toFixed(1)}%
                  </span>
                  <span style={{ color: '#888' }}>${item.price.toFixed(2)}</span>
                  <span className="ml-auto text-xs" style={{ color: '#555' }}>{item.move_date}</span>
                </div>
                <div className="flex flex-col gap-1">
                  {item.headlines.map((h, i) => (
                    <a key={i} href={h.link ?? undefined} target="_blank" rel="noreferrer"
                      className="text-xs hover:underline"
                      style={{ color: h.link ? '#aaa' : '#666', pointerEvents: h.link ? 'auto' : 'none' }}>
                      {h.title}
                      {h.publisher && <span style={{ color: '#555' }}> — {h.publisher}</span>}
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
