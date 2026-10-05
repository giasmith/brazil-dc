# Python environment (uv)

`pyproject.toml` at the repo root is the single source of truth for packages.

```bash
uv sync                 # create/refresh .venv from pyproject.toml (writes uv.lock)
uv add <package>        # add a package from now on (updates pyproject + lock + .venv)
uv run python scripts/<name>.py
```

Status as of 2026-10-04: `pyproject.toml` was written from an audit of `.venv` and every import in
`scripts/`, `map_explorer/`, `analysis/` and the two notebooks. All existing imports were already
satisfied; the additions are for the planned Sentinel-2/Landsat work. `uv.lock` has not been generated
yet (the audit ran where PyPI was unreachable) - run `uv sync` once on your Mac.

Installed in `.venv` but not declared (transitive or unknown origin): boto, fabric, paramiko, invoke,
blessings. Add them back with `uv add` if a script needs them.

Not pip-installable (separate tools): Sen2Cor (ESA), LaSRC (USGS/ESPA), 6S. Install per their docs.
