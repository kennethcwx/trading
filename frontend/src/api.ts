import type { MarketRegime, SignalsResponse, PortfolioResponse, OptionsResponse, TradesResponse } from './types'

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
export const fetchSignals = () => get<SignalsResponse>('/signals')
export const fetchPortfolio = () => get<PortfolioResponse>('/portfolio')
export const fetchOptions = () => get<OptionsResponse>('/options-opportunities')

export async function postRefreshCache() {
  await apiFetch('/refresh-cache', { method: 'POST' })
}

export async function postReconnect() {
  await apiFetch('/reconnect', { method: 'POST' })
}

export const fetchTrades = () => get<TradesResponse>('/trades')

export async function addTrade(data: {
  symbol: string; shares: number; entry_date: string; entry_price: number
  signal_reason?: string; notes?: string
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
