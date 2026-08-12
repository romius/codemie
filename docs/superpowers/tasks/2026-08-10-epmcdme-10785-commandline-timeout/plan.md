# EPMCDME-10785: Per-call timeout for CommandLineTool — Implementation Plan

**Goal:** Add an optional `timeout` parameter to `CommandLineTool` in the `codemie` repo
so callers can override the class-level default on a per-call basis.

**Scope:** `codemie` repo only. `codemie-plugins` is explicitly out of scope — the repo is
no longer maintained and does not accept new MRs.

**Architecture:** `CommandLineInput` gains an optional `timeout: int` field. `CommandLineTool.execute()`
computes `effective_timeout` by preferring the per-call value over the class-level default.
The class-level default is extracted from its magic integer into `DEFAULT_TIMEOUT = 60`.
No behavioral change when `timeout` is omitted.

**Tech Stack:** Python 3.12+, Pydantic v2, `subprocess.run`, pytest.

## Status: COMPLETE

All steps have been implemented and committed.

---

### Task 1: `codemie` — Per-call timeout in `CommandLineTool` ✅

**Files changed:**
- `src/codemie_tools/data_management/file_system/tools.py` — `DEFAULT_TIMEOUT` constant,
  extended `CommandLineInput`, updated `execute()`
- `tests/codemie_tools/data_management/file_system/test_file_system_tools.py` — five new
  test cases covering the per-call override and backward-compat path

**Commit:** `EPMCDME-10785: Add per-call timeout to CommandLineTool`

**Key invariants:**
- `DEFAULT_TIMEOUT = 60` equals the original hardcoded value (no behavioral regression)
- `effective_timeout = timeout if timeout is not None else self.timeout` — explicit `None`
  check so `timeout=0` would be respected if ever needed
- Backward compatible: existing callers that omit `timeout` behave identically to before

---

## Self-Review Checklist

- [x] Spec coverage: All four ACs satisfied
- [x] No codemie-plugins changes
- [x] `DEFAULT_TIMEOUT` replaces magic `60` literal
- [x] Per-call override tested with mock subprocess
- [x] Fallback to class default tested
- [x] Backward compatibility verified by tests
