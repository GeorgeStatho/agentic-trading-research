from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce

from services.trading.trading_gateway import AlpacaTradingGateway

LOGGER = logging.getLogger('test_trading_gateway_paper_orders')


def submitted_client():
    client = MagicMock()
    client.submit_order.return_value = SimpleNamespace(id='order-1', symbol='', status='accepted', qty='2')
    return client


class GatewayCredentialsTests(unittest.TestCase):
    def test_explicit_keys_build_a_client_for_that_account(self):
        with patch('services.trading.trading_gateway.TradingClient') as client_class, \
                patch.dict('os.environ', {'PUBLIC_KEY': 'PROD-KEY', 'PRIVATE_KEY': 'PROD-SECRET'}):
            AlpacaTradingGateway(paper=True, logger=LOGGER, api_key='k2', api_secret='s2').create_client()
        self.assertEqual(client_class.call_args.kwargs['api_key'], 'k2')
        self.assertEqual(client_class.call_args.kwargs['secret_key'], 's2')
        self.assertIs(client_class.call_args.kwargs['paper'], True)

    def test_without_keys_production_env_is_used(self):
        with patch('services.trading.trading_gateway.TradingClient') as client_class, \
                patch.dict('os.environ', {'PUBLIC_KEY': 'PROD-KEY', 'PRIVATE_KEY': 'PROD-SECRET'}):
            AlpacaTradingGateway(paper=True, logger=LOGGER).create_client()
        self.assertEqual(client_class.call_args.kwargs['api_key'], 'PROD-KEY')


class MultilegOrderTests(unittest.TestCase):
    legs = [{'symbol': 'XYZ261113C00105000', 'side': 'buy', 'ratio_qty': 1},
            {'symbol': 'XYZ261113C00110000', 'side': 'sell', 'ratio_qty': 1}]

    def test_opening_spread_is_one_mleg_market_order(self):
        client = submitted_client()
        summary = AlpacaTradingGateway(paper=True, logger=LOGGER).submit_multileg_market_order(
            client, legs=self.legs, qty=2, opening=True)
        request = client.submit_order.call_args.kwargs['order_data']
        self.assertEqual(request.order_class, OrderClass.MLEG)
        self.assertEqual(float(request.qty), 2)
        self.assertEqual(request.time_in_force, TimeInForce.DAY)
        self.assertEqual([(leg.symbol, leg.side, leg.position_intent, leg.ratio_qty) for leg in request.legs], [
            ('XYZ261113C00105000', OrderSide.BUY, PositionIntent.BUY_TO_OPEN, 1),
            ('XYZ261113C00110000', OrderSide.SELL, PositionIntent.SELL_TO_OPEN, 1),
        ])
        self.assertEqual(summary['id'], 'order-1')

    def test_closing_legs_use_close_intents(self):
        client = submitted_client()
        closing = [{**self.legs[0], 'side': 'sell'}, {**self.legs[1], 'side': 'buy'}]
        AlpacaTradingGateway(paper=True, logger=LOGGER).submit_multileg_market_order(
            client, legs=closing, qty=2, opening=False)
        request = client.submit_order.call_args.kwargs['order_data']
        self.assertEqual([leg.position_intent for leg in request.legs],
                         [PositionIntent.SELL_TO_CLOSE, PositionIntent.BUY_TO_CLOSE])


class StockOrderTests(unittest.TestCase):
    def test_stock_market_order(self):
        client = submitted_client()
        summary = AlpacaTradingGateway(paper=True, logger=LOGGER).submit_stock_market_order(
            client, symbol='xyz', qty=4, side='sell')
        request = client.submit_order.call_args.kwargs['order_data']
        self.assertEqual((request.symbol, float(request.qty), request.side, request.time_in_force),
                         ('XYZ', 4, OrderSide.SELL, TimeInForce.DAY))
        self.assertEqual(summary['id'], 'order-1')

    def test_rejects_bad_side_and_quantity(self):
        gateway = AlpacaTradingGateway(paper=True, logger=LOGGER)
        with self.assertRaises(ValueError):
            gateway.submit_stock_market_order(submitted_client(), symbol='XYZ', qty=4, side='short')
        with self.assertRaises(ValueError):
            gateway.submit_stock_market_order(submitted_client(), symbol='XYZ', qty=0, side='buy')


if __name__ == '__main__':
    unittest.main()
