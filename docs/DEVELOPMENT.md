# Development

Use Python 3.12 or later. Keep examples synthetic and do not check in generated output or private source rows.

Suggested contributor checks:

```sh
python -m compileall -q app
python -m pytest
```

Add focused checks for invalid schemas, empty categories, numeric range errors, SDV fit failures, PII scan interpretation, finite bounds, and repeated DP releases before expanding behavior. Commands above are the intended workflow, not a claim that checks were run for a change.
