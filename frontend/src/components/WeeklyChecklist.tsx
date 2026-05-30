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

  return (
    <div className="bg-[#1a1d2e] rounded-lg border border-slate-800">
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <h2 className="text-xs font-bold text-slate-300 tracking-widest uppercase">Weekly Checklist</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">{done}/{total}</span>
          <div className="w-16 h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-teal-500 rounded-full motion-safe:transition-all motion-safe:duration-300"
              style={{ width: `${(done / total) * 100}%` }}
            />
          </div>
        </div>
      </div>

      <div className="divide-y divide-slate-800/50">
        {ITEMS.map(item => {
          const isChecked = checked.has(item.id)
          return (
            <button
              key={item.id}
              onClick={() => toggle(item.id)}
              className="w-full px-4 py-3 flex items-start gap-3 text-left hover:bg-slate-800/30 transition-colors cursor-pointer group"
            >
              <div className={`mt-0.5 w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition-colors ${
                isChecked
                  ? 'bg-teal-500 border-teal-500'
                  : 'border-slate-600 group-hover:border-slate-500'
              }`}>
                {isChecked && (
                  <svg className="w-2.5 h-2.5 text-white" viewBox="0 0 10 10" fill="none">
                    <path d="M1.5 5l3 3 4-6" stroke="currentColor" strokeWidth="1.5"
                      strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </div>
              <span className={`text-xs leading-relaxed ${
                isChecked ? 'text-slate-600 line-through' : 'text-slate-300'
              }`}>
                {item.text}
              </span>
            </button>
          )
        })}
      </div>

      {done === total && (
        <div className="px-4 py-2.5 border-t border-slate-800 text-xs text-teal-400 text-center">
          Weekly review complete
        </div>
      )}

      <div className="px-4 py-2 border-t border-slate-800 text-xs text-slate-700">
        Resets every Monday · execute signals at 9:30 AM ET
      </div>
    </div>
  )
}
