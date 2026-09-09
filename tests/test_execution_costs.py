import unittest

from execution_costs import (
    DEFAULT_TAIWAN_STOCK_COST_MODEL,
    calculate_max_odd_lot_position,
    estimate_stop_loss,
)


class ExecutionCostTests(unittest.TestCase):
    def test_max_position_includes_fees_tax_and_stop_slippage(self):
        budget = 5_000
        result = calculate_max_odd_lot_position(100, 90, budget)

        self.assertIsNotNone(result)
        self.assertGreater(result.shares, 0)
        self.assertLessEqual(result.estimated_net_loss, budget)
        self.assertGreater(result.transaction_cost, 0)
        self.assertLess(result.stop_execution_price, result.planned_stop_price)
        next_share = estimate_stop_loss(100, 90, result.shares + 1)
        self.assertIsNotNone(next_share)
        self.assertGreater(next_share.estimated_net_loss, budget)

    def test_invalid_or_nonfinite_inputs_are_rejected(self):
        self.assertIsNone(calculate_max_odd_lot_position(float("nan"), 90, 5_000))
        self.assertIsNone(calculate_max_odd_lot_position(100, float("inf"), 5_000))
        self.assertIsNone(calculate_max_odd_lot_position(100, 100, 5_000))
        self.assertIsNone(calculate_max_odd_lot_position(100, 90, 0))

    def test_default_model_uses_full_taiwan_equity_cost_rates(self):
        self.assertEqual(DEFAULT_TAIWAN_STOCK_COST_MODEL.buy_commission_rate, 0.001425)
        self.assertEqual(DEFAULT_TAIWAN_STOCK_COST_MODEL.sell_commission_rate, 0.001425)
        self.assertEqual(DEFAULT_TAIWAN_STOCK_COST_MODEL.sell_tax_rate, 0.003)
        self.assertGreater(DEFAULT_TAIWAN_STOCK_COST_MODEL.stop_slippage_rate, 0)


if __name__ == "__main__":
    unittest.main()
