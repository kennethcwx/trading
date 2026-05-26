import { useState, useEffect, useCallback } from 'react'
import { fetchMarketRegime, fetchSignals, fetchPortfolio, fetchOptions, postRefreshCache } from './api'
import { MarketBanner } from './components/MarketBanner'
import { ActionSummary } from './components/ActionSummary'
import { SignalsTable } from './components/SignalsTable'
import { PortfolioSummary } from './components/PortfolioSummary'
import { OptionsPanel } from './components/OptionsPanel'
import { WeeklyChecklist } from './components/WeeklyChecklist'
import { TradeLog } from './components/TradeLog'
import { PnLChart } from './components/PnLChart'
import type { MarketRegime, SignalsResponse, PortfolioResponse, OptionsResponse, Trade } from './types'
import { fetchTrades } from './api'

const AUTO_REFRESH_MS = 5 * 60 * 1000

export default function App() {
  const [regime, setRegime] = useState<MarketRegime | null>(null)
  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null)
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
      const [r, s, p, o, t] = await Promise.all([
        fetchMarketRegime(),
        fetchSignals(),
        fetchPortfolio(),
        fetchOptions(),
        fetchTrades(),
      ])
      setRegime(r)
      setSignals(s)
      setPortfolio(p)
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

  const ibkrConnected = portfolio?.ibkr_connected ?? false

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 sm:px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2 sm:gap-3">
          <span className="text-teal-400 font-bold tracking-wider text-sm sm:text-base">TRADING DASHBOARD</span>
          <span className="text-xs bg-slate-800 text-slate-500 px-2 py-0.5 rounded border border-slate-700">PAPER</span>
        </div>
        <div className="flex items-center gap-2 sm:gap-4">
          <span className={`text-xs ${ibkrConnected ? 'text-green-400' : 'text-yellow-600'} hidden sm:inline`}>
            {ibkrConnected ? '● IBKR Connected' : '● IBKR Offline'}
          </span>
          {lastRefresh && (
            <span className="text-xs text-slate-600 hidden md:block">
              {lastRefresh.toLocaleTimeString('en-SG')}
            </span>
          )}
          <button
            onClick={() => void refresh(true)}
            disabled={loading}
            className="text-xs bg-slate-800 hover:bg-slate-700 disabled:opacity-40 px-3 py-2 sm:py-1.5 rounded border border-slate-700 text-slate-300 transition-colors cursor-pointer"
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
        {/* Portfolio */}
        {portfolio && <PortfolioSummary portfolio={portfolio} onReconnect={() => void refresh()} />}

        {/* Action summary — most important on mobile */}
        {signals && <ActionSummary signals={signals} />}

        {/* Signals + Checklist */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 sm:gap-5">
          <div className="xl:col-span-2">
            {signals
              ? <SignalsTable signals={signals} />
              : !loading && <div className="bg-[#1a1d2e] rounded-lg border border-slate-800 p-6 text-slate-500 text-xs">No signal data</div>
            }
          </div>
          <div>
            <WeeklyChecklist />
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
