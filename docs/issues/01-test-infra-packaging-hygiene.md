# Test infrastructure, packaging, and repo hygiene

## Problems
- `pip install -e .` (no extras) crashed on `devkit --help`: `memory_cmd.py` imported `sqlite_backend`, which imported numpy at module load (numpy is only in `[memory]`).
- No tests existed, although the README said `pytest tests/ -v`. There was no CI.
- All state paths were `Path.home()` constants, so tests could not be isolated from the real `~/.devkit`.
- 38 committed `__pycache__/*.pyc` files and `devkit_cli.egg-info/`; no `.gitignore`.
- Models were hardcoded (`claude-sonnet-4-6`, `claude-haiku-4-5`), and eval ignored the `fast_model` config key.
- `devkit config set auto_learn false` stored the string `"false"`, which is truthy.

## Acceptance criteria
- [x] `devkit --help` works on a base install (tests/test_infra.py)
- [x] `DEVKIT_HOME` relocates all state; tests run in a temp home
- [x] `pytest` runs offline with no API key; CI covers Linux and Windows on Python 3.10–3.12
- [x] `.pyc` files and egg-info are removed and ignored
- [x] Config values are coerced to the type of their default; models are configurable
