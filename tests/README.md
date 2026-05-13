# Tests

This folder contains the project's automated Python test suite.

The tests here mainly protect the backend areas that were cleaned up in the refactor roadmap:

- sector and industry ranking logic
- strategist and manager payload contracts
- split-module regression coverage for pipeline and strategist helpers
- API startup and health-route smoke checks
- option-manager behavior and compatibility wrappers

Most of these are lightweight unit tests with mocked dependencies so they run quickly and can be executed during Docker image builds.

Run the suite from the project root with:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```
