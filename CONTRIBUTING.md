# Contributing

fleetkeeper is pure standard library at runtime. Keep it that way: no third-party
import in `src/`, ever. Development-only dependencies live in the `[dev]` extra.

## Set up

```bash
git clone https://github.com/muguangyeshang/fleetkeeper
cd fleetkeeper
python -m pip install -e ".[dev]"
python -m pytest -q
```

Working from a fork? Substitute your fork's clone URL above.

Optional local hooks:

```bash
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Before opening a PR

1. `python -m pytest -q` passes on your platform. Current baseline: 51 tests.
2. New behaviour gets a test. Bug fixes get a regression test that fails on the
   old code — see `tests/test_regressions.py` for the expected shape.
3. No machine-specific paths, hostnames, ports from a real deployment, account
   names, keys or tokens in config examples, tests or docs. Use neutral values
   such as `1234`, `example.invalid`, `PAUSE_LLM`.
4. Relative config paths are resolved against the config file's directory; do
   not reintroduce module-level path state (see `cli._hb_dir`).
5. Probes must stay bounded. Any new HTTP read caps the body at 64 KiB and must
   not issue a second request for `json_path` inspection.
6. The evidence ladder never claims a level it did not prove. A new shortcut
   that could report `UP` without a business proof gets rejected.

## PR format

- One logical change per PR.
- Title: `area: short imperative summary` (e.g. `watchdog: fix cooldown key type`).
- Body: what broke, how it was reproduced, what the test locks down.
- Update `CHANGELOG.md` under `Unreleased`.

## Reporting a security issue

Use the repository's private security advisory form, not a public issue.
See [SECURITY.md](SECURITY.md).
