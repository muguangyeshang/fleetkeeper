# Releasing

Version numbers follow `MAJOR.MINOR.PATCH`. While in 0.x, a minor bump may
contain breaking changes; say so explicitly in the changelog entry.

## Cadence

Patch releases are cut as soon as a fix lands. Minor releases are cut when a
new module or a new evidence level lands. There is no fixed calendar schedule —
this project ships small and often rather than batching changes.

## Repository home page (one-time, before the first push)

`README.md` **is** the GitHub home page. GitHub renders it below the file
list; everything a visitor sees before clicking a file comes from that one
file. The sections are ordered for that: badges, one-sentence positioning,
three commands, a real terminal transcript, documentation links, install,
project layout, mechanisms, quick start, config, CLI, comparison, FAQ,
roadmap, governance links, license.

The repository settings are not files and have to be filled in on GitHub
after `gh repo create`. Paste-ready values:

**About / Description** (GitHub allows 350 characters):

```
Self-healing watchdog for local AI service fleets. WAN-down gate, per-port cooldowns, pause markers, PID-validated process ops, L0-L4 evidence ladder, alerts and an incident timeline. Pure Python standard library, zero dependencies, with a standalone Windows exe.
```

**Website**: the GitHub repository URL until a docs site exists. Do not point
it at a URL that is not live.

**Topics**:

```
python watchdog self-healing monitoring homelab llmops reliability
zero-dependency standard-library service-health incident-timeline
```

**Social preview**: use a screenshot of `docs/assets/status-page.html`
(offline, no external assets). Do not commit generated screenshots into the
source tree unless they are needed for the preview image.

**First push** (requires a valid GitHub token; `gh auth status` currently
reports the default token as invalid):

```bash
gh auth login -h github.com
gh repo create fleetkeeper --public --source=. --remote=origin --push
```

**Draft release notes** for the first tag. `CHANGELOG.md` already records
0.1.0 and 0.1.1 as released, and the `Unreleased` section adds the console,
exe, alerts and timeline, so the next tag is `v0.2.0`. Bump `version` in
`pyproject.toml` and `__version__` in `src/fleetkeeper/__init__.py` together,
and move the `Unreleased` entries under `## 0.2.0` with the release date
before tagging. Paste these notes into the draft that `publish.yml` creates:

```
fleetkeeper watches local AI service fleets and heals them only when its
safety gates allow it: WAN down means record-only, per-port cooldowns, pause
markers, and PID-validated process operations.

Highlights
- Browser console and a standalone Windows exe: double-click, no Python, no terminal.
- Outbound alerts over webhook / command / file, with redaction before anything leaves the host.
- Append-only SQLite incident timeline, so "what happened at 4am" has an answer.
- Self-contained offline HTML status page, no CDN or JavaScript.
- 99 tests, pure standard library, zero runtime dependencies.

See README.md for the quick start, docs/USER_GUIDE.md for the full config
reference, and docs/说明书.md for the Chinese walkthrough.
```

Attach `dist/fleetkeeper.exe` to that release. The exe is a local build
artifact and is intentionally not committed.

## One-time setup (required before the first PyPI publish)

1. Push the repository to GitHub and confirm `.github/workflows/ci.yml` is green.
2. On PyPI, register a **pending trusted publisher** for `fleetkeeper`:
   - owner: `muguangyeshang`
   - repository: `fleetkeeper`
   - workflow: `publish.yml`
   - environment: `publish`
3. Without step 2, `publish-pypi` fails authentication. No token is stored in
   this repository by design.
4. Fill in `[project.urls]` in `pyproject.toml` (Source / Issues / Changelog)
   once the repository URL exists. Do not add it before then.

## Cutting a release

```bash
# 1. from a clean default branch
cd fleetkeeper                  # your checkout directory
git status --porcelain          # must be empty
python -m pytest -q             # 51 passed expected

# 2. bump the version in BOTH places (they must agree)
#    pyproject.toml          version = "0.1.2"
#    src/fleetkeeper/__init__.py   __version__ = "0.1.2"

# 3. move the Unreleased entries in CHANGELOG.md under the new version heading
#    and add the release date; leave an empty Unreleased section on top

# 4. verify the two version strings agree and the sdist builds
python - <<'PY'
import re, pathlib
p = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
i = pathlib.Path("src/fleetkeeper/__init__.py").read_text(encoding="utf-8")
a = re.search(r'version = "([^"]+)"', p).group(1)
b = re.search(r'__version__ = "([^"]+)"', i).group(1)
assert a == b, (a, b)
print("version OK:", a)
PY
python -m pip install -q build && python -m build
python -m pip install -q twine && python -m twine check dist/*

# 5. commit, tag, push (the tag triggers publish.yml)
git add -A && git commit -m "Release 0.1.2"
git tag v0.1.2
git push origin HEAD
git push origin v0.1.2
```

## What the tag triggers

`.github/workflows/publish.yml` runs three jobs:

1. `build` — `python -m build` with `SOURCE_DATE_EPOCH` from the commit, uploads
   `dist/` as an artifact.
2. `create-release` — `gh release create --draft --generate-notes`, attaches the
   artifacts. **Draft**: review the notes, then publish it by hand.
3. `publish-pypi` — `pypa/gh-action-pypi-publish` using `id-token: write`
   (Trusted Publishing). Only this job can publish; `build` has read-only
   permissions.

## Verify the release

```bash
gh run list --workflow=publish.yml --limit=3
gh release view v0.1.2
python -m pip index versions fleetkeeper
python -m pip install --no-deps fleetkeeper==0.1.2 --target ./_verify
PYTHONPATH=./_verify python -m fleetkeeper.cli --help
```

If `publish-pypi` fails but `build` and `create-release` succeeded, fix the
PyPI trusted-publisher configuration and re-run the workflow from the Actions
tab; do not re-tag.

## Known build warning

`python -m build` prints `project.license as a TOML table is deprecated`.
This is deliberate: `license = { text = "MIT" }` is the form understood by
`setuptools>=61`, which is the newest line that still supports Python 3.8.
The PEP 639 form (`license = "MIT"` + `license-files`) requires
`setuptools>=77`, which drops 3.8. setuptools says the deprecated form must be
removed by 2027-02-18. Migrate in the same release that raises
`requires-python` to `>=3.9`; until then the warning is expected and
`twine check` passes.

`MANIFEST.in` keeps LICENSE, the five root governance docs (CONTRIBUTING,
SECURITY, RELEASING, CHANGELOG, README), `docs/*.md` plus the sample HTML,
`config/*.json`, `scripts/`, `start.bat` and the tests in the sdist —
setuptools' defaults would otherwise ship only the package and README.

## Rollback

Delete the tag and the draft release, keep the commit. A PyPI release cannot be
reused — bump to the next patch version and publish that instead.
