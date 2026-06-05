import { useState, useEffect, useCallback } from 'react'
import { fetchMarketRegime, fetchSignals, fetchPortfolio, fetchOptions, postRefreshCache, fetchTrades } from './api'
import type { SignalGroup } from './api'
import { MarketBanner } from './components/MarketBanner'
import { ActionSummary } from './components/ActionSummary'
import { SignalsTable } from './components/SignalsTable'
import { OptionsPanel } from './components/OptionsPanel'
import { WeeklyChecklist } from './components/WeeklyChecklist'
import { WatchlistEditor } from './components/WatchlistEditor'
import { TradeLog } from './components/TradeLog'
import { PnLChart } from './components/PnLChart'
import { PortfolioSummary } from './components/PortfolioSummary'
import type { MarketRegime, SignalsResponse, OptionsResponse, PortfolioResponse, Trade } from './types'

const AUTO_REFRESH_MS = 5 * 60 * 1000

type Tab = 'dashboard' | 'signals' | 'trades'

function marketStatus(): { label: string; dot: string } {
  const now = new Date()
  const day = now.getUTCDay()
  const mins = now.getUTCHours() * 60 + now.getUTCMinutes()
  const isWeekday = day >= 1 && day <= 5
  const openMins = 13 * 60 + 30
  const closeMins = 20 * 60
  const preMarket = 9 * 60

  if (!isWeekday) return { label: 'Closed', dot: 'bg-zinc-600' }
  if (mins >= openMins && mins < closeMins) return { label: 'Open', dot: 'bg-[var(--green)]' }
  if (mins >= preMarket && mins < openMins) return { label: 'Pre-market', dot: 'bg-yellow-400' }
  return { label: 'Closed', dot: 'bg-zinc-600' }
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function IconGrid() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="1" y="1" width="6" height="6" rx="1" />
      <rect x="9" y="1" width="6" height="6" rx="1" />
      <rect x="1" y="9" width="6" height="6" rx="1" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
    </svg>
  )
}

function IconSignal() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M1 12 L4 8 L7 10 L10 5 L13 7 L15 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconTrades() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 8h12M10 4l4 4-4 4M6 12l-4-4 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function IconRefresh({ spinning }: { spinning: boolean }) {
  return (
    <svg
      className={`w-3.5 h-3.5 ${spinning ? 'animate-spin' : ''}`}
      viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
    >
      <path d="M13.5 8A5.5 5.5 0 1 1 8 2.5a5.5 5.5 0 0 1 4 1.7L14 6" strokeLinecap="round" />
      <path d="M14 2.5V6h-3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ── Sidebar (desktop) ─────────────────────────────────────────────────────────

function Sidebar({
  tab, setTab, status, loading, lastRefresh, onRefresh,
}: {
  tab: Tab
  setTab: (t: Tab) => void
  status: ReturnType<typeof marketStatus>
  loading: boolean
  lastRefresh: Date | null
  onRefresh: () => void
}) {
  const nav: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'dashboard', label: 'Dashboard', icon: <IconGrid /> },
    { id: 'signals',   label: 'Signals',   icon: <IconSignal /> },
    { id: 'trades',    label: 'Trades',    icon: <IconTrades /> },
  ]

  return (
    <aside className="hidden md:flex flex-col fixed left-0 top-0 h-full w-52 border-r z-20"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {/* Logo */}
      <div className="px-5 py-5 border-b flex items-center gap-2.5" style={{ borderColor: 'var(--border)' }}>
        <div className="w-7 h-7 rounded-lg flex items-center justify-center text-black text-xs font-bold"
          style={{ background: 'var(--teal)' }}>T</div>
        <div>
          <div className="text-sm font-semibold text-white leading-none">Trading</div>
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--teal)' }}>PAPER ACCOUNT</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {nav.map(item => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all cursor-pointer relative"
            style={tab === item.id
              ? { background: 'var(--surface-2)', color: 'var(--teal)' }
              : { color: '#888' }
            }
          >
            {tab === item.id && (
              <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-r"
                style={{ background: 'var(--teal)' }} />
            )}
            {item.icon}
            {item.label}
          </button>
        ))}
      </nav>

      {/* Bottom: status + refresh */}
      <div className="px-4 py-4 border-t space-y-3" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-1.5 h-1.5 rounded-full ${status.dot}`} />
          <span className="text-zinc-400">Market {status.label}</span>
        </div>
        {lastRefresh && (
          <div className="text-[11px] text-zinc-600">
            Updated {lastRefresh.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' })}
          </div>
        )}
        <button
          onClick={onRefresh}
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-40"
          style={{ background: 'var(--surface-2)', color: '#aaa' }}
        >
          <IconRefresh spinning={loading} />
          Refresh data
        </button>
      </div>
    </aside>
  )
}

// ── Bottom nav (mobile) ───────────────────────────────────────────────────────

function BottomNav({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const nav: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'dashboard', label: 'Dashboard', icon: <IconGrid /> },
    { id: 'signals',   label: 'Signals',   icon: <IconSignal /> },
    { id: 'trades',    label: 'Trades',    icon: <IconTrades /> },
  ]

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 z-20 border-t flex"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}>
      {nav.map(item => (
        <button
          key={item.id}
          onClick={() => setTab(item.id)}
          className="flex-1 flex flex-col items-center gap-1 py-3 text-[10px] font-medium transition-colors cursor-pointer"
          style={tab === item.id ? { color: 'var(--teal)' } : { color: '#666' }}
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </nav>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  const [signalGroup, setSignalGroup] = useState<SignalGroup>('core')
  const [regime, setRegime] = useState<MarketRegime | null>(null)
  const [signalsByGroup, setSignalsByGroup] = useState<Partial<Record<SignalGroup, SignalsResponse>>>({})
  const [options, setOptions] = useState<OptionsResponse | null>(null)
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [groupLoading, setGroupLoading] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)

  const signals = signalsByGroup[signalGroup] ?? null

  const fetchGroup = useCallback(async (group: SignalGroup) => {
    if (signalsByGroup[group]) return
    setGroupLoading(true)
    try {
      const s = await fetchSignals(group)
      setSignalsByGroup(prev => ({ ...prev, [group]: 'error' in s ? undefined : s }))
    } catch {
      // silently fail — error bar already shown from main refresh
    } finally {
      setGroupLoading(false)
    }
  }, [signalsByGroup])

  const handleGroupChange = useCallback((group: SignalGroup) => {
    setSignalGroup(group)
    void fetchGroup(group)
  }, [fetchGroup])

  const refresh = useCallback(async (clearCache = false) => {
    setLoading(true)
    setError(null)
    try {
      if (clearCache) await postRefreshCache()
      const [r, s, o, t] = await Promise.all([
        fetchMarketRegime(),
        fetchSignals('core'),
        fetchOptions(),
        fetchTrades(),
      ])
      setRegime('error' in r ? null : r)
      setSignalsByGroup({ core: 'error' in s ? undefined : s })
      setSignalGroup('core')
      setOptions(o)
      setTrades(t.trades)
      setLastRefresh(new Date())
      // Portfolio fetch is best-effort — IBKR offline is the normal state
      try {
        const p = await fetchPortfolio()
        setPortfolio(p)
      } catch {
        setPortfolio({ positions: [], account: { cash: null, total_value: null, currency: 'USD', connected: false }, ibkr_connected: false })
      }
    } catch {
      setError('Cannot reach backend. Make sure the Python server is running.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), AUTO_REFRESH_MS)
    return () => clearInterval(interval)
  }, [refresh])

  const status = marketStatus()

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar tab={tab} setTab={setTab} status={status} loading={loading} lastRefresh={lastRefresh} onRefresh={() => void refresh(true)} />
      <BottomNav tab={tab} setTab={setTab} />

      {/* Main content — offset by sidebar on desktop, pad bottom for mobile nav */}
      <main className="md:ml-52 pb-20 md:pb-0">
        {/* Error bar */}
        {error && (
          <div className="px-6 py-3 text-xs text-red-300 border-b"
            style={{ background: 'rgba(255,59,48,0.08)', borderColor: 'rgba(255,59,48,0.2)' }}>
            {error}
          </div>
        )}

        <div className="px-4 md:px-8 py-6 space-y-5 max-w-screen-xl mx-auto">
          {/* Dashboard tab */}
          {tab === 'dashboard' && (
            <>
              {regime && <MarketBanner regime={regime} />}
              {portfolio && <PortfolioSummary portfolio={portfolio} onReconnect={() => void refresh()} />}
              {signals && <ActionSummary signals={signals} />}
              <PnLChart trades={trades} />

                <WeeklyChecklist />

              {options && <OptionsPanel options={options} />}
            </>
          )}

          {/* Signals tab */}
          {tab === 'signals' && (
            <>
              {regime && <MarketBanner regime={regime} />}

              {/* List group selector */}
              <div className="flex gap-1 p-1 rounded-xl w-fit" style={{ background: 'var(--surface)' }}>
                {([
                  { id: 'core' as SignalGroup,          label: 'Core' },
                  { id: 'quantum' as SignalGroup,        label: 'Quantum' },
                  { id: 'covered_calls' as SignalGroup,  label: 'Covered Calls' },
                  { id: 'screener' as SignalGroup,       label: 'Screener' },
                ] as { id: SignalGroup; label: string }[]).map(g => (
                  <button
                    key={g.id}
                    onClick={() => handleGroupChange(g.id)}
                    className="px-4 py-1.5 rounded-lg text-xs font-medium transition-all cursor-pointer"
                    style={signalGroup === g.id
                      ? { background: 'var(--surface-2)', color: 'white' }
                      : { color: '#666' }
                    }
                  >
                    {g.label}
                  </button>
                ))}
              </div>

              {(loading || groupLoading)
                ? null
                : signals
                  ? <SignalsTable signals={signals} group={signalGroup} />
                  : (
                    <div className="rounded-2xl border p-8 text-sm text-center"
                      style={{ background: 'var(--surface)', borderColor: 'var(--border)', color: '#555' }}>
                      No signal data available
                    </div>
                  )
              }
              <WatchlistEditor onSave={() => void refresh()} />
            </>
          )}

          {/* Trades tab */}
          {tab === 'trades' && (
            <>
              <PnLChart trades={trades} />
              <TradeLog onTradesChange={setTrades} />
            </>
          )}

          <div className="text-xs text-center pb-2" style={{ color: '#333' }}>
            Auto-refreshes every 5 min · Data via Yahoo Finance · Not financial advice
          </div>
        </div>
      </main>
    </div>
  )
}
