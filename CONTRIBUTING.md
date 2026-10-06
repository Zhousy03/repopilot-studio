# Contributing

Use Python 3.11+ and an isolated environment:

```bash
python -m pip install -e ".[ai,dev,ui-test]"
python -m pytest -q
python scripts/check_ui.py
```

The UI is served directly from `repopilot/static`; no frontend build is required. Browser checks use an installed Chromium browser or Playwright Chromium. Test workspaces and model fixtures live in temporary directories.

Add regressions for file filtering, model validation, configuration, cancellation, version checks, publication or sandbox changes. Keep UI wording focused on what the user can do; put detailed runtime information in Settings, run details and documentation.

Keep credentials, private source, generated databases and test output out of commits. Review changes to publishing and test execution against the boundaries in `SECURITY.md`.

After reviewing source and documentation changes, run `python scripts/verify_release.py --update`. The release verifier uses normalized text hashes so Windows and Unix line endings do not cause false failures. Use `--archive PATH` to generate a clean source ZIP.
