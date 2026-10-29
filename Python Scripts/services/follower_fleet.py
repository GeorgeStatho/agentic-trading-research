"""Mirror the primary account's entries into follower accounts and close them when it exits."""
from __future__ import annotations


class FollowerFleet:
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

    def manage(self):
        raise NotImplementedError


def build_follower_fleet(accounts, *, logger, settings=None):
    raise NotImplementedError
