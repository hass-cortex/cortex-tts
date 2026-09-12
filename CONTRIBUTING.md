# Contributing

## Setup

```bash
uv sync
```

## Before opening a pull request

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q
```

Home Assistant is not installed here. The tests run against a stand-in
`homeassistant` module tree built in `tests/conftest.py`, which covers exactly
the surface the integration imports — so a new core import fails loudly until
it is added there, rather than passing against a mock that answers everything.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
`docs:`, `refactor:`, `test:`, `chore:`.

## Releases

```bash
uv run cz bump
```

The tag, `pyproject.toml` and `manifest.json` must carry the same version;
`cz bump` writes all three and the release workflow refuses a tag that does not
match.
