import type { MarketRegime, SignalsResponse, PortfolioResponse, OptionsResponse, SpreadOpportunitiesResponse, TradesResponse, WatchlistResponse, OptionsTradesResponse, AlertsResponse, AlertDirection, NewsFeedResponse, TrackResponse } from './types'

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? ''

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${API_BASE}/api${path}`, init)
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`)
  return res
}

async function get<T>(path: string): Promise<T> {
  return (await apiFetch(path)).json() as Promise<T>
}

export const fetchMarketRegime = () => get<MarketRegime>('/market-regime')
export type SignalGroup = 'core' | 'long_term' | 'quantum' | 'covered_calls' | 'screener'
export const fetchSignals = (group: SignalGroup = 'core') => get<SignalsResponse>(`/signals?group=${group}`)
export const fetchPortfolio = () => get<PortfolioResponse>('/portfolio')
export const fetchOptions = () => get<OptionsResponse>('/options-opportunities')
export const fetchSpreads = () => get<SpreadOpportunitiesResponse>('/spread-opportunities')

export async function postRefreshCache() {
  await apiFetch('/refresh-cache', { method: 'POST' })
}

export async function postReconnect() {
  await apiFetch('/reconnect', { method: 'POST' })
}

export const fetchTrades = () => get<TradesResponse>('/trades')

export async function addTrade(data: {
  symbol: string; shares: number; entry_date: string; entry_price: number
  signal_reason?: string; notes?: string; strategy?: 'A' | 'B' | 'C' | 'D'
}) {
  await apiFetch('/trades', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function closeTrade(id: number, data: { exit_date: string; exit_price: number; notes?: string }) {
  await apiFetch(`/trades/${id}/close`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function deleteTrade(id: number) {
  await apiFetch(`/trades/${id}`, { method: 'DELETE' })
}

export const fetchWatchlist = () => get<WatchlistResponse>('/watchlist')
export const fetchOptionsTrades = () => get<OptionsTradesResponse>('/options-trades')

export async function addOptionsTrade(data: {
  symbol: string; strategy: string; phase: number
  strike: number; long_strike?: number; expiry_date: string; dte_at_entry?: number
  premium: number; contracts: number; open_date: string; notes?: string
}) {
  await apiFetch('/options-trades', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function closeOptionsTrade(id: number, data: {
  close_date: string; close_premium: number; status: string; notes?: string
}) {
  await apiFetch(`/options-trades/${id}/close`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function deleteOptionsTrade(id: number) {
  await apiFetch(`/options-trades/${id}`, { method: 'DELETE' })
}

export async function updateWatchlist(symbols: string[]) {
  await apiFetch('/watchlist', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols }),
  })
}

export const fetchAlerts = () => get<AlertsResponse>('/alerts')

export async function addAlert(data: { symbol: string; target: number; direction?: AlertDirection }) {
  await apiFetch('/alerts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function deleteAlert(id: number) {
  await apiFetch(`/alerts/${id}`, { method: 'DELETE' })
}

export const fetchNewsFeed = () => get<NewsFeedResponse>('/news-feed')

// Auto-logged S$10k US track. Read-only by design — the track fills itself, so
// there is no add/close/delete counterpart to the /trades mutations above.
export const fetchTrack = () => get<TrackResponse>('/track')
