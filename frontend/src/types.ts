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

export interface SectorEtf {
  etf_symbol: string
  above_200sma: boolean
  etf_price: number
  etf_sma200: number
}

export interface SignalItem {
  symbol: string
  in_portfolio: boolean
  analysis: TickerAnalysis
  fundamentals: Fundamentals | null
  rel_strength: RelativeStrength | null
  sector_etf: SectorEtf | null
  signal: Signal
  position_size: PositionSize | null
}

export interface SignalsResponse {
  signals: SignalItem[]
  regime: MarketRegime
  generated_at: string
  total_scanned?: number
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

export interface OptionsSignal {
  action: 'SELL PUT' | 'SELL CALL' | 'WATCH' | 'AVOID'
  priority: 'HIGH' | 'MEDIUM' | 'LOW'
  reason: string
  suggested_action: string
}

export interface LivePremium {
  expiry: string
  dte: number
  strike: number
  bid: number
  ask: number
  mid: number
  total_contract: number
  iv_pct: number | null
}

export interface OptionsOpportunity {
  symbol: string
  phase: 1 | 2
  strategy: string
  price: number
  suggested_strike: number
  dte_target: string
  collateral_sgd: number
  avg_cost: number | null
  options_signal: OptionsSignal
  live_premium: LivePremium | null
  rsi: number | null
  days_to_earnings: number | null
  above_200sma: boolean | null
}

export interface OptionsResponse {
  opportunities: OptionsOpportunity[]
  regime: MarketRegime
  portfolio_size_sgd: number
  note: string
}

export interface SpreadSignal {
  action: 'OPEN SPREAD' | 'WATCH' | 'AVOID'
  priority: 'HIGH' | 'MEDIUM' | 'LOW'
  reason: string
  suggested_action: string
}

export interface BullPutSpread {
  expiry: string
  dte: number
  short_strike: number
  long_strike: number
  width: number
  net_credit: number
  max_profit: number
  max_loss: number
  breakeven: number
  roi_pct: number | null
  short_iv_pct: number | null
}

export interface SpreadOpportunity {
  symbol: string
  strategy: string
  price: number
  spread: BullPutSpread | null
  fits_account: boolean | null
  signal: SpreadSignal
  rsi: number | null
  above_200sma: boolean | null
  days_to_earnings: number | null
}

export interface SpreadOpportunitiesResponse {
  opportunities: SpreadOpportunity[]
  regime: MarketRegime
  account_size_sgd: number
  account_size_usd: number
  spread_width: number
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
  strategy?: 'A' | 'B'
  half_sold?: number
  peak_price?: number | null
  current_price?: number | null
  unrealized_pnl?: number | null
  unrealized_pnl_pct?: number | null
  realized_pnl?: number | null
  realized_pnl_pct?: number | null
}

export interface TradesResponse {
  trades: Trade[]
}

export interface WatchlistResponse {
  watchlist: string[]
}

export type OptionsTradeStatus = 'open' | 'expired' | 'closed' | 'assigned'

export interface OptionsTrade {
  id: number
  symbol: string
  strategy: string
  phase: 1 | 2
  strike: number
  long_strike: number | null
  expiry_date: string
  dte_at_entry: number | null
  premium: number
  contracts: number
  open_date: string
  close_date: string | null
  close_premium: number | null
  status: OptionsTradeStatus
  notes: string | null
  created_at: string
  total_premium: number
  pnl: number | null
}

export interface OptionsTradesResponse {
  trades: OptionsTrade[]
}

export type AlertDirection = 'above' | 'below'

export interface PriceAlert {
  id: number
  symbol: string
  target: number
  direction: AlertDirection
  created_at: string
}

export interface AlertsResponse {
  alerts: PriceAlert[]
}
