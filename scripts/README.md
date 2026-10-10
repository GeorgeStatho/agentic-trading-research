# Developer Commands

Use `project.py` as the canonical local entrypoint dispatcher. It delegates to the existing scripts, so Docker Compose and compatibility imports continue to work.

```bash
# Start individual services
python3 scripts/project.py run api
python3 scripts/project.py run worker
python3 scripts/project.py run news
python3 scripts/project.py run option-manager

# Run the offline evaluator with its normal arguments
python3 scripts/project.py run offline-evaluation --historical-outcome-audit --limit 100

# Grid-search backtest exit settings; tune before --split-at, validate after it
python3 scripts/project.py run backtest-sweep --db path/to.db --split-at 2026-09-01 \
    --setting take_profit_pct=25,50,100 --setting stop_loss_pct=-25,-50 --csv sweep.csv

# Validate Python or the dashboard
python3 scripts/project.py test
python3 scripts/project.py test tests.test_exposure_map tests.test_demo_mode
python3 scripts/project.py dashboard build

# Share the deterministic demo walkthrough
python3 scripts/project.py demo-url
```

Use `docker compose up --build` for the integrated containerized stack.
