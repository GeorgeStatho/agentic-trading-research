-- Caps new telemetry install IDs per network per day. The address is stored only as an HMAC under a random
-- salt for that day; both rows are deleted once the day is over, so past days can't be linked to an address.
CREATE TABLE IF NOT EXISTS telemetry_daily_salts (
    day TEXT PRIMARY KEY,
    salt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_network_days (
    day TEXT NOT NULL,
    network_hash TEXT NOT NULL,
    new_install_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, network_hash)
);
