from __future__ import annotations

from main import LOGGER, _build_log_path, _configure_logging, option_manager_loop


if __name__ == "__main__":
    log_path = _build_log_path("option_manager")
    _configure_logging(log_path)

    LOGGER.info("Dedicated option manager log started at %s", log_path)
    option_manager_loop()
    print(f"Dedicated option manager log written to {log_path}")
