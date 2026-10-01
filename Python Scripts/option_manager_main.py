from __future__ import annotations

from services.settings import load_process_settings

if __name__ == "__main__":
    # Before `main` imports services that still read configuration at import time.
    load_process_settings()

from main import LOGGER, _build_log_path, _configure_logging, option_manager_loop
from services.telemetry import send_startup_ping_async


if __name__ == "__main__":
    log_path = _build_log_path("option_manager")
    _configure_logging(log_path)

    LOGGER.info("Dedicated option manager log started at %s", log_path)
    send_startup_ping_async("option_manager", logger=LOGGER)
    option_manager_loop()
    print(f"Dedicated option manager log written to {log_path}")
