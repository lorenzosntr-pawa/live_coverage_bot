# UAT Issues: Phase 6 Plan 1

**Tested:** 2026-03-12
**Source:** .planning/phases/06-monitoring-loop/06-01-SUMMARY.md
**Tester:** User via /gsd:verify-work

## Open Issues

[None]

## Resolved Issues

### UAT-001: __main__.py doesn't load config.yaml

**Discovered:** 2026-03-12
**Phase/Plan:** 06-01
**Severity:** Major
**Feature:** CLI Entry Point
**Description:** The entry point in `__main__.py` uses `Settings()` directly instead of calling `load_config()`. This means the config.yaml file is ignored and only environment variables are read.
**Expected:** Running `python -m live_coverage_bot` should read config.yaml
**Actual:** Config.yaml is ignored; only environment variables work
**Repro:**
1. Create config.yaml with slack.webhook_url set
2. Run `python -m live_coverage_bot` without setting env var
3. Get "Field required" error for slack

**Resolved:** 2026-03-12 - Fixed in 06-01-FIX.md
**Fix:** Changed `Settings()` to `load_config()` in `__main__.py`

---

*Phase: 06-monitoring-loop*
*Plan: 01*
*Tested: 2026-03-12*
