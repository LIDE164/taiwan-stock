"""Conservative Taiwan-equity execution costs used for risk sizing.

The sizing model deliberately uses the full published commission rate rather
than a broker-specific discount.  It also models a worse fill at the stop.  It
is still an estimate: a price gap or insufficient liquidity can lose more than
the configured budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaiwanStockCostModel:
    """Rates used for a conservative cash-equity/odd-lot execution estimate."""

    buy_commission_rate: float = 0.001425
    sell_commission_rate: float = 0.001425
    sell_tax_rate: float = 0.003
    minimum_commission: float = 20.0
    stop_slippage_rate: float = 0.0005


@dataclass(frozen=True)
class StopLossEstimate:
    """All-in loss estimate for one planned stop execution."""

    shares: int
    entry_price: float
    planned_stop_price: float
    stop_execution_price: float
    price_loss: float
    buy_commission: float
    sell_commission: float
    sell_tax: float
    estimated_net_loss: float

    @property
    def transaction_cost(self) -> float:
        return self.buy_commission + self.sell_commission + self.sell_tax

    @property
    def effective_risk_per_share(self) -> float:
        if self.shares <= 0:
            return self.entry_price - self.stop_execution_price
        return self.estimated_net_loss / self.shares


DEFAULT_TAIWAN_STOCK_COST_MODEL = TaiwanStockCostModel()
DEFAULT_MAX_LOSS_PER_TRADE = 5000.0


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _valid_model(model: TaiwanStockCostModel) -> bool:
    values = (
        model.buy_commission_rate,
        model.sell_commission_rate,
        model.sell_tax_rate,
        model.minimum_commission,
        model.stop_slippage_rate,
    )
    if any(_finite_number(value) is None for value in values):
        return False
    return (
        model.buy_commission_rate >= 0
        and model.sell_commission_rate >= 0
        and model.sell_tax_rate >= 0
        and model.minimum_commission >= 0
        and 0 <= model.stop_slippage_rate < 1
    )


def estimate_stop_loss(
    entry_price: Any,
    stop_price: Any,
    shares: Any,
    *,
    model: TaiwanStockCostModel = DEFAULT_TAIWAN_STOCK_COST_MODEL,
) -> StopLossEstimate | None:
    """Estimate all-in loss when ``shares`` are sold at a slipped stop price."""
    entry = _finite_number(entry_price)
    stop = _finite_number(stop_price)
    share_number = _finite_number(shares)
    if (
        entry is None
        or stop is None
        or share_number is None
        or not share_number.is_integer()
        or not _valid_model(model)
        or not 0 < stop < entry
        or share_number < 0
    ):
        return None

    share_count = int(share_number)
    stop_execution = stop * (1 - model.stop_slippage_rate)
    if share_count == 0:
        return StopLossEstimate(
            shares=0,
            entry_price=entry,
            planned_stop_price=stop,
            stop_execution_price=stop_execution,
            price_loss=0.0,
            buy_commission=0.0,
            sell_commission=0.0,
            sell_tax=0.0,
            estimated_net_loss=0.0,
        )

    entry_notional = entry * share_count
    exit_notional = stop_execution * share_count
    buy_commission = (
        max(entry_notional * model.buy_commission_rate, model.minimum_commission)
        if model.buy_commission_rate > 0
        else 0.0
    )
    sell_commission = (
        max(exit_notional * model.sell_commission_rate, model.minimum_commission)
        if model.sell_commission_rate > 0
        else 0.0
    )
    sell_tax = exit_notional * model.sell_tax_rate
    price_loss = (entry - stop_execution) * share_count
    estimated_net_loss = price_loss + buy_commission + sell_commission + sell_tax
    return StopLossEstimate(
        shares=share_count,
        entry_price=entry,
        planned_stop_price=stop,
        stop_execution_price=stop_execution,
        price_loss=price_loss,
        buy_commission=buy_commission,
        sell_commission=sell_commission,
        sell_tax=sell_tax,
        estimated_net_loss=estimated_net_loss,
    )


def estimate_round_trip_net_profit(
    entry_price: Any,
    exit_price: Any,
    shares: Any,
    *,
    model: TaiwanStockCostModel = DEFAULT_TAIWAN_STOCK_COST_MODEL,
) -> float | None:
    """Return realized net profit after Taiwan stock commissions and sell tax.

    ``exit_price`` is the actual modeled execution price.  Slippage is therefore
    deliberately not applied here; callers use the stop execution price from
    :func:`estimate_stop_loss` when modeling a stopped trade.
    """
    entry = _finite_number(entry_price)
    exit_value = _finite_number(exit_price)
    share_number = _finite_number(shares)
    if (
        entry is None
        or exit_value is None
        or share_number is None
        or not share_number.is_integer()
        or share_number < 1
        or entry <= 0
        or exit_value <= 0
        or not _valid_model(model)
    ):
        return None

    share_count = int(share_number)
    entry_notional = entry * share_count
    exit_notional = exit_value * share_count
    buy_commission = (
        max(entry_notional * model.buy_commission_rate, model.minimum_commission)
        if model.buy_commission_rate > 0
        else 0.0
    )
    sell_commission = (
        max(exit_notional * model.sell_commission_rate, model.minimum_commission)
        if model.sell_commission_rate > 0
        else 0.0
    )
    sell_tax = exit_notional * model.sell_tax_rate
    return exit_notional - sell_commission - sell_tax - entry_notional - buy_commission


def calculate_max_odd_lot_position(
    entry_price: Any,
    stop_price: Any,
    max_loss: Any,
    *,
    model: TaiwanStockCostModel = DEFAULT_TAIWAN_STOCK_COST_MODEL,
) -> StopLossEstimate | None:
    """Return the largest integer share count whose modeled net loss fits the budget.

    The search is exact for the supplied cost model, including minimum brokerage
    commissions.  ``None`` means that the inputs are invalid; a valid result may
    contain zero shares when even one share exceeds the loss budget.
    """
    entry = _finite_number(entry_price)
    stop = _finite_number(stop_price)
    budget = _finite_number(max_loss)
    if (
        entry is None
        or stop is None
        or budget is None
        or budget <= 0
        or not _valid_model(model)
        or not 0 < stop < entry
    ):
        return None

    slipped_stop = stop * (1 - model.stop_slippage_rate)
    price_risk_per_share = entry - slipped_stop
    upper = math.floor(budget / price_risk_per_share)
    zero = estimate_stop_loss(entry, stop, 0, model=model)
    if upper < 1:
        return zero

    low, high = 1, upper
    best = zero
    while low <= high:
        candidate_shares = (low + high) // 2
        candidate = estimate_stop_loss(entry, stop, candidate_shares, model=model)
        if candidate is None:
            return None
        if candidate.estimated_net_loss <= budget:
            best = candidate
            low = candidate_shares + 1
        else:
            high = candidate_shares - 1
    return best


def estimate_risk_sized_net_reward_risk(
    entry_price: Any,
    stop_price: Any,
    target_price: Any,
    *,
    max_loss: Any = DEFAULT_MAX_LOSS_PER_TRADE,
    model: TaiwanStockCostModel = DEFAULT_TAIWAN_STOCK_COST_MODEL,
) -> float | None:
    """Return target net profit divided by the modeled all-in stop loss.

    The share count is the same odd-lot risk sizing used by production.  This
    keeps the entry list, backtest and tracker on one cost-adjusted definition
    instead of comparing a gross chart ratio with a net realized result.
    """
    estimate = calculate_max_odd_lot_position(
        entry_price,
        stop_price,
        max_loss,
        model=model,
    )
    if estimate is None or estimate.shares < 1 or estimate.estimated_net_loss <= 0:
        return None
    target_profit = estimate_round_trip_net_profit(
        entry_price,
        target_price,
        estimate.shares,
        model=model,
    )
    if target_profit is None:
        return None
    ratio = target_profit / estimate.estimated_net_loss
    return ratio if math.isfinite(ratio) else None
