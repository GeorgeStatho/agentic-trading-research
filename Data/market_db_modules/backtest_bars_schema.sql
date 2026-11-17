-- Option 1-minute trade bars for backtests (stock minute bars live in historical_underlying_bars, timeframe '1m').
CREATE TABLE IF NOT EXISTS historical_option_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    option_symbol TEXT NOT NULL,
    bar_at TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume REAL,
    vwap REAL,
    trade_count INTEGER,
    provider TEXT NOT NULL,
    UNIQUE (option_symbol, bar_at, timeframe, provider)
);

-- One row per symbol or contract per completed session fetched, including sessions with no trades,
-- so a fetch never repeats.
CREATE TABLE IF NOT EXISTS historical_bar_fetches (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    bar_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date, timeframe, provider)
);
