"""Positions held by trading accounts. In v1 only follower accounts write rows."""


def record_plan(account, *, source_account, source_order_id, source_option_symbol, underlying, decision,
                budget, plan, db_path=None):
    raise NotImplementedError


def mark_entry_order(row_id, *, order_id, db_path=None):
    raise NotImplementedError


def mark_failed(row_id, *, error, db_path=None):
    raise NotImplementedError


def mark_entry_fill(row_id, *, fill_price, filled_at, db_path=None):
    raise NotImplementedError


def mark_source_seen(row_id, *, at, db_path=None):
    raise NotImplementedError


def mark_closing(row_id, *, order_id, reason, db_path=None):
    raise NotImplementedError


def reopen(row_id, *, error, db_path=None):
    raise NotImplementedError


def mark_closed(row_id, *, fill_price, closed_at, db_path=None):
    raise NotImplementedError


def active_positions(db_path=None):
    raise NotImplementedError


def account_summary(*, since=None, db_path=None):
    raise NotImplementedError
