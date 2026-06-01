import { useState, useEffect } from 'react'

const ITEMS = [
  { id: 'regime',   text: 'Market banner: is the regime bullish? No new buys if bearish.' },
  { id: 'signals',  text: 'Action Plan: any BUY or SELL signals to execute at Monday open?' },
  { id: 'stops',    text: 'Check open positions — still above their stop prices?' },
  { id: 'earnings', text: 'Any earnings in the next 7 days? Decide to hold or exit beforehand.' },
  { id: 'trailing', text: 'Any position up 15%+? Move stop up to lock in gains.' },
  { id: 'ivr',      text: 'Check Market Chameleon: IVR > 30 on any watchlist stock? Covered call opportunity.' },
  { id: 'journal',  text: 'Log any trades executed this week in the Trade Log.' },
]

function getWeekKey() {
  const now = new Date()
  const start = new Date(now.getFullYear(), 0, 1)
  const week = Math.ceil(((now.getTime() - start.getTime()) / 86400000 + start.getDay() + 1) / 7)
  return `checklist-${now.getFullYear()}-W${week}`
}

export function WeeklyChecklist() {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const key = getWeekKey()

  useEffect(() => {
    try {
      const saved = localStorage.getItem(key)
      if (saved) setChecked(new Set(JSON.parse(saved) as string[]))
    } catch { /* ignore */ }
  }, [key])

  function toggle(id: string) {
    setChecked(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      localStorage.setItem(key, JSON.stringify([...next]))
      return next
    })
  }

  const done = checked.size
  const total = ITEMS.length
  const pct = (done / total) * 100

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: '#888' }}>Weekly Checklist</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs" style={{ color: '#555' }}>{done}/{total}</span>
          <div className="w-20 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border-2)' }}>
            <div
              className="h-full rounded-full motion-safe:transition-all motion-safe:duration-300"
              style={{ width: `${pct}%`, background: 'var(--green)' }}
            />
          </div>
        </div>
      </div>

      <div>
        {ITEMS.map(item => {
          const isChecked = checked.has(item.id)
          return (
            <button
              key={item.id}
              onClick={() => toggle(item.id)}
              className="w-full px-5 py-3.5 flex items-start gap-3 text-left border-b last:border-0 transition-colors cursor-pointer hover:bg-white/[0.02]"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className={`mt-0.5 w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition-colors`}
                style={isChecked
                  ? { background: 'var(--green)', borderColor: 'var(--green)' }
                  : { borderColor: '#444' }
                }>
                {isChecked && (
                  <svg className="w-2.5 h-2.5" viewBox="0 0 10 10" fill="none">
                    <path d="M1.5 5l3 3 4-6" stroke="#000" strokeWidth="1.5"
                      strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </div>
              <span className="text-sm leading-relaxed" style={{
                color: isChecked ? '#555' : '#ccc',
                textDecoration: isChecked ? 'line-through' : 'none',
              }}>
                {item.text}
              </span>
            </button>
          )
        })}
      </div>

      {done === total && (
        <div className="px-5 py-3 text-xs text-center font-medium" style={{ color: 'var(--green)' }}>
          Weekly review complete
        </div>
      )}

      <div className="px-5 py-2.5 border-t text-xs" style={{ borderColor: 'var(--border)', color: '#444' }}>
        Resets every Monday · execute signals at 9:30 AM ET
      </div>
    </div>
  )
}
