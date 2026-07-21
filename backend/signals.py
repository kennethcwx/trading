from datetime import datetime
from config import (
    RSI_ENTRY, RSI_EXIT, MOMENTUM_DAYS, VOLUME_MULTIPLIER,
    MAX_HOLD_WEEKS, PROFIT_RATIO,
    RISK_PER_TRADE_PCT, MAX_POSITION_PCT, PORTFOLIO_SIZE_SGD,
    STOP_ATR_MULT, STOP_MAX_PCT,
)


def generate_signal(analysis: dict, position: dict | None, regime: dict,
                    fundamentals: dict | None = None,
                    rel_strength: dict | None = None,
                    rs_rank: int | None = None,
                    sector_ok: bool | None = None,
                    variant: str = "COMBINED") -> dict:
    regime_ok = regime.get("regime_ok", True)
    vix_elevated = regime.get("vix_status") != "NORMAL"

    rsi = analysis.get("rsi")
    above_200 = analysis.get("above_200sma")
    above_50 = analysis.get("above_50sma")
    sma20_above_50 = analysis.get("sma20_above_sma50", True)
    price = analysis.get("price", 0)
    stop = analysis.get("stop_loss", 0)
    target = analysis.get("profit_target", 0)
    earnings_warning = analysis.get("earnings_warning", False)
    days_to_earnings = analysis.get("days_to_earnings")
    vol_ratio = analysis.get("volume_ratio", 1.0)
    high_20d = analysis.get("high_20d", 0)

    # ── EXIT signals (held positions) ──────────────────────────────────────
    if position:
        avg_cost = position.get("avg_cost", price)
        pct_gain = ((price - avg_cost) / avg_cost) * 100 if avg_cost else 0
        entry_date_str = position.get("entry_date")

        # Exit levels must be anchored at entry. The analysis stop/target float
        # with the current price (stop is below it by construction), so using
        # them here would make `price <= stop` unreachable. Legacy rows without
        # stored levels fall back to the max-stop rule off avg_cost.
        stop = position.get("stop_loss") or (avg_cost * (1 - STOP_MAX_PCT) if avg_cost else stop)
        target = position.get("profit_target") or (
            avg_cost * (1 + PROFIT_RATIO * STOP_MAX_PCT) if avg_cost else target)

        if price <= stop:
            return _signal("SELL", "HIGH",
                           [f"Stop loss triggered — price ${price:.2f} ≤ stop ${stop:.2f}"],
                           "Close full position immediately")

        if earnings_warning:
            return _signal("REVIEW", "HIGH",
                           [f"Earnings in {days_to_earnings} days — decide: hold or close first"],
                           "Close or hedge before earnings if not intentional")

        if above_200 is False:
            return _signal("SELL", "HIGH",
                           ["Price broke below 200-day SMA — trend broken"],
                           "Close full position")

        if rsi and rsi > RSI_EXIT and pct_gain > 0:
            return _signal("SELL_HALF", "MEDIUM",
                           [f"RSI overbought at {rsi:.0f} (>{RSI_EXIT}) with open gain"],
                           "Sell 50% · move stop to breakeven · trail at 10% if position gains another 15%")

        if price >= target:
            return _signal("SELL_HALF", "MEDIUM",
                           [f"Profit target reached ${target:.2f} (2:1 R:R)"],
                           "Sell 50% · move stop to breakeven · trail at 10% if position gains another 15%")

        if entry_date_str:
            try:
                weeks = (datetime.today() - datetime.fromisoformat(entry_date_str)).days // 7
                if weeks >= MAX_HOLD_WEEKS and pct_gain < 5:
                    return _signal("SELL", "MEDIUM",
                                   [f"Time stop: {weeks}w held with under 5% gain — redeploy capital"],
                                   "Close and redeploy")
            except ValueError:
                pass

        return _signal("HOLD", "LOW",
                       ["No exit signals triggered — hold position"],
                       f"Monitor. Stop ${stop:.2f} | Target ${target:.2f}")

    # ── ENTRY signals (watchlist) ─────────────────────────────────────────
    if not regime_ok:
        basis = regime.get("basis", "SPY")
        return _signal("SKIP", "LOW",
                       [f"Market regime BEARISH ({basis} < 200 SMA) — no new longs"],
                       "Wait for regime to recover or trade with 50% size")

    if earnings_warning:
        return _signal("WATCH", "LOW",
                       [f"Earnings in {days_to_earnings} days — wait until after to enter"],
                       "Re-evaluate after earnings", watch_kind="blocked")

    # Strategy D uses the swing-low stop/target computed in analysis.py
    if variant == "SWING_LOW_NOCAP":
        stop = analysis.get("stop_loss_swing") or stop
        target = analysis.get("profit_target_swing") or target

    mean_rev = (rsi is not None and rsi < RSI_ENTRY and above_200)
    momentum_base = (
        price and high_20d and price >= high_20d
        and vol_ratio >= VOLUME_MULTIPLIER
        and rsi is not None
        and above_200
    )
    momentum = (
        momentum_base and RSI_ENTRY < rsi < RSI_EXIT
        and sma20_above_50   # 20 SMA > 50 SMA — short-term trend aligned (COMBINED variant)
    )
    # D: no RSI ceiling, no 20/50 SMA alignment — parity with backtest
    # _entry_swing_low_no_cap (walk-forward winner, 7/7 windows)
    momentum_nocap = momentum_base and rsi > RSI_ENTRY
    # BASELINE (crypto since 2026-07-13): RSI ceiling kept, 20/50 alignment
    # dropped — beat COMBINED by ~0.8pp CAGR in all five crypto backtest configs
    momentum_baseline = momentum_base and RSI_ENTRY < rsi < RSI_EXIT

    if variant in ("MEAN_REV", "MEAN_REV_NO_RS"):
        signal_condition = mean_rev
    elif variant == "SWING_LOW_NOCAP":
        signal_condition = mean_rev or momentum_nocap
    elif variant == "BASELINE":
        signal_condition = mean_rev or momentum_baseline
    else:
        signal_condition = mean_rev or momentum
    if signal_condition:
        label = "Mean-Reversion" if mean_rev else "Momentum Breakout"

        # ── Filter 1: RS rank — only top 25% within the current symbol set ──
        # Skipped for MEAN_REV_NO_RS: diagnostic showed RS rank filter hurts mean-rev entries
        if variant != "MEAN_REV_NO_RS" and rs_rank is not None and rs_rank < 75:
            return _signal("WATCH", "LOW",
                           [f"{label} triggered but RS rank {rs_rank}th percentile — needs top 25%"],
                           f"Wait for relative strength to improve · currently ranked {rs_rank}th percentile vs watchlist",
                           watch_kind="blocked")

        # ── Filter 2: Sector confirmation — sector ETF must be above 200 SMA ──
        if sector_ok is False:
            return _signal("WATCH", "LOW",
                           [f"{label} triggered but sector ETF below 200 SMA — sector in downtrend"],
                           "Wait for sector ETF to reclaim 200 SMA before entering",
                           watch_kind="blocked")

        if mean_rev:
            tech_reasons = [f"RSI {rsi:.0f} below {RSI_ENTRY} + above 200 SMA"]
            if above_50:
                tech_reasons.append("Pullback within uptrend (above 50 SMA)")
        elif variant == "SWING_LOW_NOCAP":
            tech_reasons = [f"20-day high breakout · volume {vol_ratio:.1f}x average · no RSI ceiling · swing-low stop"]
        elif variant == "BASELINE":
            tech_reasons = [f"20-day high breakout · volume {vol_ratio:.1f}x average"]
        else:
            tech_reasons = [f"20-day high breakout · volume {vol_ratio:.1f}x average · 20 SMA above 50 SMA"]

        suffix = " (halve size — VIX elevated)" if vix_elevated else ""
        action_text = f"Enter position{suffix}. Stop ${stop:.2f} → Target ${target:.2f}"

        # ── Fundamental layer (stocks only) ───────────────────────────────
        if fundamentals and not fundamentals.get("is_etf"):
            score = fundamentals.get("score", 0) or 0
            grade = fundamentals.get("grade", "?")
            good = fundamentals.get("reasons_good", [])
            bad = fundamentals.get("reasons_bad", [])

            # Relative strength check
            rs_flag = ""
            if rel_strength:
                rs_3m = rel_strength.get("rs_3m")
                if rs_3m is not None and rs_3m < -10:
                    rs_flag = f" · underperforming SPY by {abs(rs_3m):.0f}% over 3m"

            if score <= 1:
                return _signal("SKIP", "LOW",
                               [f"Tech setup OK but fundamentals weak (grade {grade}){rs_flag}",
                                *bad[:2]],
                               "Avoid — fix your fundamentals filter or skip this name")

            if score == 2:
                return _signal("WATCH", "MEDIUM",
                               [f"Tech triggered, fundamentals mixed (grade {grade}){rs_flag}",
                                *tech_reasons, *bad[:1]],
                               "Possible entry at reduced size — verify fundamentals first",
                               watch_kind="blocked")

            # Grade B (3) or A (4-5)
            all_reasons = tech_reasons + [f"Fundamentals grade {grade}: {', '.join(good[:2])}"]
            if rs_flag:
                all_reasons.append(rs_flag.strip(" · "))
            priority = "HIGH" if score >= 4 else "MEDIUM"
            return _signal("BUY", priority, all_reasons, action_text, trade_type=label)

        # ETF or no fundamental data → technical only
        priority = "HIGH" if mean_rev else "MEDIUM"
        rs_note = []
        if rel_strength:
            rs_3m = rel_strength.get("rs_3m")
            if rs_3m is not None and rs_3m < -10:
                rs_note = [f"Underperforming SPY by {abs(rs_3m):.0f}% over 3m — caution"]
        return _signal("BUY", priority, tech_reasons + rs_note, action_text, trade_type=label)

    if above_200 and rsi is not None and rsi < 55:
        pts_away = round(rsi - RSI_ENTRY)

        if pts_away <= 4:
            proximity = "Almost there — could trigger in 1–2 days"
        elif pts_away <= 8:
            proximity = "Approaching — a few days of selling needed"
        else:
            proximity = "Early watch — not close yet"

        return _signal(
            "WATCH", "LOW",
            [f"RSI {rsi:.0f} — {pts_away} pts from the RSI-{RSI_ENTRY} pullback entry"],
            f"{proximity} · At current price: Stop ${stop:.2f} · Target ${target:.2f}",
            watch_kind="approaching",
        )

    reasons = []
    if rsi and rsi > RSI_EXIT:
        reasons.append(f"Overbought — RSI {rsi:.0f}, wait for pullback below {RSI_ENTRY}")
    elif not above_200:
        reasons.append("Below 200 SMA — not in uptrend")
    else:
        reasons.append("No entry signal")

    return _signal("SKIP", "LOW", reasons, "No action")


def calculate_position_size(portfolio_sgd: float, price_usd: float,
                             stop_usd: float, sgd_to_usd: float,
                             size_mult: float = 1.0) -> dict | None:
    stop_dist = price_usd - stop_usd
    if stop_dist <= 0:
        return None

    risk_usd = portfolio_sgd * sgd_to_usd * RISK_PER_TRADE_PCT * size_mult
    shares = risk_usd / stop_dist

    pos_usd = shares * price_usd
    pos_sgd = pos_usd / sgd_to_usd

    # Cap at 10% of portfolio
    max_sgd = portfolio_sgd * MAX_POSITION_PCT
    capped = False
    if pos_sgd > max_sgd:
        pos_sgd = max_sgd
        pos_usd = max_sgd * sgd_to_usd
        shares = pos_usd / price_usd
        capped = True

    note = None
    if shares < 1:
        note = "Fractional shares — supported via IBKR"
    if capped:
        note = "Capped at 10% portfolio limit (not risk-based)"

    return {
        "shares": round(shares, 3),
        "position_value_sgd": round(pos_sgd, 2),
        "position_value_usd": round(pos_usd, 2),
        "risk_sgd": round(risk_usd / sgd_to_usd, 2),
        "note": note,
    }


def _signal(action: str, priority: str, reasons: list[str], suggested: str,
            trade_type: str | None = None, watch_kind: str | None = None) -> dict:
    result = {
        "action": action,
        "priority": priority,
        "reasons": reasons,
        "suggested_action": suggested,
    }
    if trade_type:
        result["trade_type"] = trade_type
    # "approaching" (near a trigger) vs "blocked" (triggered/near but held back by
    # a filter: earnings window, RS rank, sector downtrend, mixed fundamentals).
    # Presentation only — lets the briefings split the Watch list into two groups.
    if watch_kind:
        result["watch_kind"] = watch_kind
    return result
