"""Mirror production entries into paper-trader accounts and close them when production exits."""
from __future__ import annotations


class PaperTraderFleet:
    def __init__(self, *, accounts, settings, gateway_factory, chain_fetcher, logger, db_path=None, clock=None):
        self._accounts = list(accounts)
        self._settings = settings
        self._gateway_factory = gateway_factory
        self._chain_fetcher = chain_fetcher
        self._logger = logger
        self._db_path = db_path
        self._clock = clock

    def mirror_entry(self, candidate, execution):
        raise NotImplementedError

    def manage(self, *, production_gateway, production_client):
        raise NotImplementedError
