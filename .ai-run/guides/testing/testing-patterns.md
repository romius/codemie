# Testing Patterns

## Test Location

Place tests beside the behavior area under `tests/codemie/`, `tests/codemie_tools/`, `tests/unit/`, or `tests/enterprise/`.

| Avoid | Prefer |
|---|---|
| A broad test file unrelated to the changed package | Match the nearest existing test directory |
| Creating a new top-level `tests/` subtree for a new area | Mirror `src/` under an existing root (e.g. `tests/codemie/<package path>/`) and reuse its fixtures |
| Testing external services live by default | Mock provider boundaries unless integration is required |

Evidence: tests are organized under package-specific directories in `tests/`; pytest dependencies are declared at `pyproject.toml:157`.

## Running Tests

Run the narrowest relevant scope unless the user asks for all tests or full verification.

| Avoid | Prefer |
|---|---|
| Reporting the suite passed after a partial run | State the exact command and scope |
| Running tests when user policy says not requested | Report tests skipped by policy |

Evidence: Makefile test target runs all tests at `Makefile:27`.

## Seam Tests for Policy Helpers

When a helper encapsulates a decision (default vs custom, normalized vs rejected), unit-testing it in isolation is not enough — each callsite needs a test observing what reaches the outer boundary (SDK call, emitted request, constructed object) for each branch. Otherwise a callsite guard duplicating the same check stays silent.

| Avoid | Prefer |
|---|---|
| Only unit-testing the helper in isolation | One callsite test per branch, asserting the boundary value |
| `if x == default: skip; else: forward(x)` when the callee already handles `x == default` | Forward unconditionally; seam test proves the boundary call stays correct |

Red flag: deleting a callsite guard leaves tests green → the guard was dead code and no seam test existed to catch it.
