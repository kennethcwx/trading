export interface MarketRegime {
  spy_price: number
  spy_sma200: number
  regime: 'BULLISH' | 'BEARISH'
  regime_ok: boolean
  vix: number
  vix_status: 'NORMAL' | 'ELEVATED' | 'EXTREME'
  new_position_size_multiplier: number
  sgd_to_usd: number
  usd_to_sgd: number
}

export interface TickerAnalysis {
  symbol: string
  price: number
  rsi: number | null
  sma50: number | null
  sma200: number | null
  atr: number | null
  above_200sma: boolean | null
  above_50sma: boolean | null
  volume_ratio: number
  high_20d: number
  high_52w: number
  low_52w: number
  pct_from_52w_high: number
  stop_loss: number
  stop_pct: number
  profit_target: number
  pe_ratio: number | null
  sector: string
  earnings_date: string | null
  days_to_earnings: number | null
  earnings_warning: boolean
}

export type SignalAction = 'BUY' | 'SELL' | 'SELL_HALF' | 'HOLD' | 'WATCH' | 'SKIP' | 'REVIEW'
export type Priority = 'HIGH' | 'MEDIUM' | 'LOW'

export interface Signal {
  action: SignalAction
  priority: Priority
  reasons: string[]
  suggested_action: string
}

export interface PositionSize {
  shares: number
  position_value_sgd: number
  position_value_usd: number
  risk_sgd: number
  note: string | null
}

export interface Fundamentals {
  is_etf: boolean
  score: number | null
  grade: string | null
  revenue_growth: number | null
  earnings_growth: number | null
  profit_margin: number | null
  debt_equity: number | null
  roe: number | null
  forward_pe: number | null
  analyst_rating: string | null
  target_price: number | null
  upside_pct: number | null
  num_analysts: number
  reasons_good: string[]
  reasons_bad: string[]
}

export interface RelativeStrength {
  return_1m: number | null
  return_3m: number | null
  rs_1m: number | null
  rs_3m: number | null
  outperforming_3m: boolean
}

export interface SignalItem {
  symbol: string
  in_portfolio: boolean
  analysis: TickerAnalysis
  fundamentals: Fundamentals | null
  rel_strength: RelativeStrength | null
  signal: Signal
  position_size: PositionSize | null
}

export interface SignalsResponse {
  signals: SignalItem[]
  regime: MarketRegime
  generated_at: string
}

export interface IBKRPosition {
  symbol: string
  shares: number
  avg_cost: number
  market_price: number
  market_value: number
  unrealized_pnl: number
  asset_type: string
  analysis?: TickerAnalysis
}

export interface Account {
  cash: number | null
  total_value: number | null
  currency: string
  connected: boolean
}

export interface PortfolioResponse {
  positions: IBKRPosition[]
  account: Account
  ibkr_connected: boolean
}

export interface OptionsOpportunity {
  symbol: string
  strategy: string
  price: number
  suggested_strike: number
  dte_target: string
  collateral_usd: number
  collateral_sgd: number
  feasible: boolean
  note: string
  rsi: number | null
  days_to_earnings: number | null
}

export interface OptionsResponse {
  opportunities: OptionsOpportunity[]
  regime: MarketRegime
  portfolio_size_sgd: number
  note: string
}

export interface Trade {
  id: number
  symbol: string
  shares: number
  entry_date: string
  entry_price: number
  exit_date: string | null
  exit_price: number | null
  signal_reason: string | null
  notes: string | null
  created_at: string
  current_price?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  realized_pnl?: number | null
  realized_pnl_pct?: number | null
}

export interface TradesResponse {
  trades: Trade[]
}
