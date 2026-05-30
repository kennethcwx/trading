import { useState, useEffect, useCallback } from 'react'
import { fetchMarketRegime, fetchSignals, fetchPortfolio, fetchOptions, postRefreshCache } from './api'
import { MarketBanner } from './components/MarketBanner'
import { ActionSummary } from './components/ActionSummary'
import { SignalsTable } from './components/SignalsTable'
import { OptionsPanel } from './components/OptionsPanel'
import { WeeklyChecklist } from './components/WeeklyChecklist'
import { WatchlistEditor } from './components/WatchlistEditor'
import { TradeLog } from './components/TradeLog'
import { PnLChart } from './components/PnLChart'
import type { MarketRegime, SignalsResponse, OptionsResponse, Trade } from './types'
import { fetchTrades } from './api'

const AUTO_REFRESH_MS = 5 * 60 * 1000

function marketStatus(): { label: string; color: string } {
  const now = new Date()
  const day = now.getUTCDay()
  const hour = now.getUTCHours()
  const min = now.getUTCMinutes()
  const mins = hour * 60 + min
  const isWeekday = day >= 1 && day <= 5
  const openMins = 13 * 60 + 30  // 09:30 ET = 13:30 UTC
  const closeMins = 20 * 60       // 16:00 ET = 20:00 UTC
  const preMarket = 9 * 60        // 05:00 ET = 09:00 UTC

  if (!isWeekday) return { label: 'Market Closed', color: 'text-slate-500' }
  if (mins >= openMins && mins < closeMins) return { label: 'Market Open', color: 'text-green-400' }
  if (mins >= preMarket && mins < openMins) return { label: 'Pre-Market', color: 'text-yellow-500' }
  return { label: 'Market Closed', color: 'text-slate-500' }
}

export default function App() {
  const [regime, setRegime] = useState<MarketRegime | null>(null)
  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [options, setOptions] = useState<OptionsResponse | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async (clearCache = false) => {
    setLoading(true)
    setError(null)
    try {
      if (clearCache) await postRefreshCache()
      const [r, s, o, t] = await Promise.all([
        fetchMarketRegime(),
        fetchSignals(),
        fetchOptions(),
        fetchTrades(),
      ])
      setRegime('error' in r ? null : r)
      setSignals('error' in s ? null : s)
      setOptions(o)
      setTrades(t.trades)
      setLastRefresh(new Date())
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
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 sm:px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2 sm:gap-3">
          <span className="text-teal-400 font-bold tracking-wider text-sm sm:text-base">TRADING DASHBOARD</span>
          <span className="text-xs bg-slate-800 text-slate-500 px-2 py-0.5 rounded border border-slate-700">PAPER</span>
        </div>
        <div className="flex items-center gap-2 sm:gap-4">
          <span className={`text-xs font-medium ${status.color} hidden sm:inline`}>
            ● {status.label}
          </span>
          {lastRefresh && (
            <span className="text-xs text-slate-600 hidden md:block">
              {lastRefresh.toLocaleTimeString('en-SG')}
            </span>
          )}
          <button
            onClick={() => void refresh(true)}
            disabled={loading}
            className="text-xs bg-slate-800 hover:bg-slate-700 disabled:opacity-40 px-4 py-2.5 sm:px-3 sm:py-1.5 rounded border border-slate-700 text-slate-300 transition-colors cursor-pointer min-w-[44px]"
          >
            {loading ? '…' : 'Refresh'}
          </button>
        </div>
      </header>

      {/* Error bar */}
      {error && (
        <div className="bg-red-950/60 border-b border-red-900 text-red-300 px-4 sm:px-6 py-2.5 text-xs">
          {error}
        </div>
      )}

      {/* Market regime banner */}
      {regime && <MarketBanner regime={regime} />}

      <div className="p-3 sm:p-6 space-y-4 sm:space-y-5 max-w-screen-2xl mx-auto">
        {/* Action summary */}
        {signals && <ActionSummary signals={signals} />}

        {/* Signals + right column */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 sm:gap-5">
          <div className="xl:col-span-2">
            {signals
              ? <SignalsTable signals={signals} />
              : !loading && <div className="bg-[#1a1d2e] rounded-lg border border-slate-800 p-6 text-slate-500 text-xs">No signal data</div>
            }
          </div>
          <div className="space-y-4 sm:space-y-5">
            <WeeklyChecklist />
            <WatchlistEditor onSave={() => void refresh()} />
          </div>
        </div>

        {/* P&L Chart */}
        <PnLChart trades={trades} />

        {/* Options */}
        {options && <OptionsPanel options={options} />}

        {/* Trade Log */}
        <TradeLog onTradesChange={setTrades} />

        {/* Footer */}
        <div className="text-xs text-slate-700 text-center pb-2">
          Auto-refreshes every 5 min · Data via Yahoo Finance · Not financial advice
        </div>
      </div>
    </div>
  )
}
