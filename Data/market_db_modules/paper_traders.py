"""Positions held by the paper traders that mirror production entries."""


def record_plan(*, trader, account, production_order_id, production_option_symbol, underlying, decision,
                budget, plan, db_path=None):
    raise NotImplementedError


def mark_entry_order(row_id, *, order_id, db_path=None):
    raise NotImplementedError


def mark_failed(row_id, *, error, db_path=None):
    raise NotImplementedError


def mark_entry_fill(row_id, *, fill_price, filled_at, db_path=None):
    raise NotImplementedError


def mark_production_seen(row_id, *, at, db_path=None):
    raise NotImplementedError


def mark_closing(row_id, *, order_id, reason, db_path=None):
    raise NotImplementedError


def reopen(row_id, *, error, db_path=None):
    raise NotImplementedError


def mark_closed(row_id, *, fill_price, closed_at, db_path=None):
    raise NotImplementedError


def active_positions(db_path=None):
    raise NotImplementedError


def trader_summary(*, since=None, db_path=None):
    raise NotImplementedError
