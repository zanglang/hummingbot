"""
Unit tests for hummingbot.strategy.uniswap_v3_lp.uniswap_v3_lp
"""

from decimal import Decimal
import pandas as pd
import numpy as np
from typing import Dict, List
import unittest.mock
import asyncio

from hummingbot.core.clock import Clock, ClockMode
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from hummingbot.strategy.uniswap_v3_lp.uniswap_v3_lp import UniswapV3LpStrategy
from hummingbot.connector.connector.uniswap_v3.uniswap_v3_in_flight_position import UniswapV3InFlightPosition

from hummingsim.backtest.backtest_market import BacktestMarket
from hummingsim.backtest.market import QuantizationParams
from hummingsim.backtest.mock_order_book_loader import MockOrderBookLoader


class ExtendedBacktestMarket(BacktestMarket):
    def __init__(self):
        super().__init__()
        self._trading_pairs = ["ETH-USDT"]
        np.random.seed(123456789)
        self._in_flight_positions = {}

    async def get_price_by_fee_tier(self, trading_pair: str, tier: str, seconds: int = 1, twap: bool = False):
        if twap:
            original_price = 100
            volatility = 0.1
            return np.random.normal(original_price, volatility, 3599)
        else:
            return Decimal("100")

    def add_position(self,
                     trading_pair: str,
                     fee_tier: str,
                     base_amount: Decimal,
                     quote_amount: Decimal,
                     lower_price: Decimal,
                     upper_price: Decimal,
                     token_id: int = 0):
        self._in_flight_positions["pos1"] = UniswapV3InFlightPosition(hb_id="pos1",
                                                                      token_id=token_id,
                                                                      trading_pair=trading_pair,
                                                                      fee_tier=fee_tier,
                                                                      base_amount=base_amount,
                                                                      quote_amount=quote_amount,
                                                                      lower_price=lower_price,
                                                                      upper_price=upper_price)

    def remove_position(self, hb_id: str, token_id: str = "1", reducePercent: Decimal = Decimal("100.0")):
        self._in_flight_positions.pop(hb_id)


class UniswapV3LpStrategyTest(unittest.TestCase):
    start: pd.Timestamp = pd.Timestamp("2019-01-01", tz="UTC")
    end: pd.Timestamp = pd.Timestamp("2019-01-01 01:00:00", tz="UTC")
    start_timestamp: float = start.timestamp()
    end_timestamp: float = end.timestamp()
    market_infos: Dict[str, MarketTradingPairTuple] = {}

    @staticmethod
    def create_market(trading_pairs: List[str], mid_price, balances: Dict[str, int]) -> (BacktestMarket, Dict[str, MarketTradingPairTuple]):
        """
        Create a BacktestMarket and marketinfo dictionary to be used by the liquidity mining strategy
        """
        market: ExtendedBacktestMarket = ExtendedBacktestMarket()
        market_infos: Dict[str, MarketTradingPairTuple] = {}

        for trading_pair in trading_pairs:
            base_asset = trading_pair.split("-")[0]
            quote_asset = trading_pair.split("-")[1]

            book_data: MockOrderBookLoader = MockOrderBookLoader(trading_pair, base_asset, quote_asset)
            book_data.set_balanced_order_book(mid_price=mid_price,
                                              min_price=1,
                                              max_price=200,
                                              price_step_size=1,
                                              volume_step_size=10)
            market.add_data(book_data)
            market.set_quantization_param(QuantizationParams(trading_pair, 6, 6, 6, 6))
            market_infos[trading_pair] = MarketTradingPairTuple(market, trading_pair, base_asset, quote_asset)

        for asset, value in balances.items():
            market.set_balance(asset, value)

        return market, market_infos

    def setUp(self) -> None:
        self.loop = asyncio.get_event_loop()
        self.clock_tick_size = 1
        self.clock: Clock = Clock(ClockMode.BACKTEST, self.clock_tick_size, self.start_timestamp, self.end_timestamp)

        self.mid_price = 100
        self.bid_spread = 0.01
        self.ask_spread = 0.01
        self.order_refresh_time = 1

        trading_pairs = list(map(lambda quote_asset: "ETH-" + quote_asset, ["USDT", "BTC"]))
        market, market_infos = self.create_market(trading_pairs, self.mid_price, {"USDT": 5000, "ETH": 500, "BTC": 100})
        self.market = market
        self.market_infos = market_infos

        self.default_strategy = UniswapV3LpStrategy(
            self.market_infos[trading_pairs[0]],
            "MEDIUM",
            True,
            Decimal('144'),
            Decimal('2'),
            Decimal('0.01'),
            Decimal('0.01'),
            Decimal('1'),
            Decimal('1'),
            Decimal('0.05')
        )
        self.default_strategy._last_price = Decimal("100")

    def test_in_range_sell(self):
        """
        Test in_range_sell function.
        """

        self.default_strategy.tick(1)
        self.assertFalse(self.default_strategy.in_range_sell())
        self.default_strategy._market_info.market._in_flight_positions["pos1"] = UniswapV3InFlightPosition(hb_id="pos1",
                                                                                                           token_id=1,
                                                                                                           trading_pair="ETH-USDT",
                                                                                                           fee_tier="MEDIUM",
                                                                                                           base_amount=Decimal("0"),
                                                                                                           quote_amount=Decimal("100"),
                                                                                                           lower_price=Decimal("98"),
                                                                                                           upper_price=Decimal("101"))
        self.assertTrue(self.default_strategy.in_range_sell())
        self.default_strategy._market_info.market._in_flight_positions = {}

    def test_generate_proposal_with_volatility_above_zero(self):
        """
        Test generate proposal function works correctly when volatility is above zero
        """

        orders = self.loop.run_until_complete(self.default_strategy.propose_position_creation())
        self.assertEqual(orders[0][0], Decimal("0"))
        self.assertEqual(orders[0][1], Decimal("100"))
        self.assertEqual(orders[1][0], Decimal("100"))
        self.assertAlmostEqual(orders[1][1], Decimal("305.35"), 1)

    def test_generate_proposal_with_volatility_equal_zero(self):
        """
        Test generate proposal function works correctly when volatility is zero
        """

        for x in range(3600):
            self.default_strategy._volatility.add_sample(100)
        orders = self.loop.run_until_complete(self.default_strategy.propose_position_creation())
        self.assertEqual(orders[0], [])
        self.assertEqual(orders[1], [])

    def test_generate_proposal_without_volatility(self):
        """
        Test generate proposal function works correctly using user set spreads
        """

        self.default_strategy._use_volatility = False
        orders = self.loop.run_until_complete(self.default_strategy.propose_position_creation())
        self.assertEqual(orders[0][0], Decimal("99"))
        self.assertEqual(orders[0][1], Decimal("100"))
        self.assertEqual(orders[1][0], Decimal("100"))
        self.assertEqual(orders[1][1], Decimal("101"))

    def test_profitability_calculation(self):
        """
        Test profitability calculation function works correctly
        """

        pos = UniswapV3InFlightPosition(hb_id="pos1",
                                        token_id=1,
                                        trading_pair="HBOT-USDT",
                                        fee_tier="MEDIUM",
                                        base_amount=Decimal("0"),
                                        quote_amount=Decimal("100"),
                                        lower_price=Decimal("100"),
                                        upper_price=Decimal("101"))
        pos.current_base_amount = Decimal("1")
        pos.current_quote_amount = Decimal("0")
        pos.unclaimed_base_amount = Decimal("1")
        pos.unclaimed_quote_amount = Decimal("10")
        pos.gas_price = Decimal("5")
        result = self.default_strategy.calculate_profitability(pos)
        self.assertEqual(result["profitability"], (Decimal("110") - result["tx_fee"]) / Decimal("100"))

    def test_position_creation(self):
        """
        Test that positions are created properly.
        """
        self.assertEqual(len(self.default_strategy._market_info.market._in_flight_positions), 0)
        self.default_strategy.execute_proposal([[95, 100], []])
        self.assertEqual(len(self.default_strategy._market_info.market._in_flight_positions), 1)

    def test_range_position_removal(self):
        """
        Test that positions are removed when profitability is reached.
        """
        self.default_strategy._market_info.market._in_flight_positions["pos1"] = UniswapV3InFlightPosition(hb_id="pos1",
                                                                                                           token_id=1,
                                                                                                           trading_pair="ETH-USDT",
                                                                                                           fee_tier="MEDIUM",
                                                                                                           base_amount=Decimal("0"),
                                                                                                           quote_amount=Decimal("100"),
                                                                                                           lower_price=Decimal("90"),
                                                                                                           upper_price=Decimal("95"))
        self.default_strategy._market_info.market._in_flight_positions["pos1"].current_base_amount = Decimal("1")
        self.default_strategy._market_info.market._in_flight_positions["pos1"].current_quote_amount = Decimal("0")
        self.default_strategy._market_info.market._in_flight_positions["pos1"].unclaimed_base_amount = Decimal("1")
        self.default_strategy._market_info.market._in_flight_positions["pos1"].unclaimed_quote_amount = Decimal("100")
        self.default_strategy._market_info.market._in_flight_positions["pos1"].gas_price = Decimal("0")
        self.assertEqual(len(self.default_strategy._market_info.market._in_flight_positions), 1)
        self.default_strategy.range_position_remover()
        self.assertEqual(len(self.default_strategy._market_info.market._in_flight_positions), 0)
