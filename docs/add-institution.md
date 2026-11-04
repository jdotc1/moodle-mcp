# Adding a university or plugin

**Institution** — a profile is just data:
```toml
[profiles.myuni]
base_url = "https://moodle.myuni.edu"
timezone = "Europe/Lisbon"
# attendance_min_percent = "75%"          # only with a documented source:
# attendance_rule_source = "Regulation X art. 5, 2026-03-01"
```
1. `moodle-academic-mcp configure --profile myuni --url ...`, `doctor`, `capabilities`.
2. If the site's service lists functions not in the bundled catalog, export its `wsdoc.php` page to PDF (`pdftotext -layout`) and run `scripts/build_catalog.py` — only shapes are extracted; do **not** commit the PDF or any header with a user name.
3. Add presets in `config.builtin_profile_presets()` (URL + timezone only; never rules).
4. Institution rules (attendance %, grade formulas) are never baked in: they are arguments with a required `source`.

**Plugin adapter** — add the function entries (with upstream URL/version and `Effect`) to `moodle/catalog.py:_UPSTREAM`, a domain module that detects availability and returns a *specific* limitation + activity link when the API is missing, an `@op` in `server/ops.py`, `permissions.py` entry, mock support in `tests/fake_moodle.py`, and a row in the feature matrix. Never use admin/teacher functions as a fallback for student actions.
