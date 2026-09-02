# Text Linting Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `TextLintingTool` to the `codemie-tools` package that lints freeform text against Vale style packs and returns structured findings for the calling agent to reason over.

**Architecture:** The tool shells out to the Vale CLI binary (managed via the `vale` pip package). Style packs are downloaded once into a persistent cache directory; a fresh `.vale.ini` is generated per call pointing at that cache. The tool returns findings only — the agent uses its own LLM to rewrite text if needed.

**Tech Stack:** Python 3.12+, `vale` (pip), `pydantic` v2, `pytest`.

## Global Constraints

- All new files live in the `codemie-tools` repo at `/Users/Andriy_Lukashchuk/Dev/codemie-tools/`.
- Module root: `src/codemie_tools/text_linting/`.
- Tests live under `tests/codemie_tools/text_linting/`.
- Test runner: `poetry run pytest tests/` from the `codemie-tools` repo root.
- Follow existing toolkit pattern: `CodeMieTool` base, `DiscoverableToolkit`, `ToolMetadata` constants in `tool_vars.py`.
- No new `ToolSet` entry — use existing `ToolSet.CODE_QUALITY`.
- `vale` dep version: `^3.13`.
- Default enabled styles: `["proselint", "Readability", "ai-tells"]`.
- `ai-tells` pack URL: `https://github.com/tbhb/vale-ai-tells/releases/download/v1.29.0/ai-tells-commits.zip`, style directory name: `ai-tells`.
- Vale style cache default: `~/.cache/codemie/vale-styles`.
- Branch name in `codemie-tools`: `text-linting-tool`.

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `codemie-tools/src/codemie_tools/text_linting/__init__.py` | Create | Public re-exports |
| `codemie-tools/src/codemie_tools/text_linting/models.py` | Create | `TextLintingConfig` |
| `codemie-tools/src/codemie_tools/text_linting/tool_vars.py` | Create | `TEXT_LINTING_TOOL` metadata constant |
| `codemie-tools/src/codemie_tools/text_linting/vale_runner.py` | Create | `Finding`, `STYLE_PACK_MAP`, `run_vale()` |
| `codemie-tools/src/codemie_tools/text_linting/tool.py` | Create | `TextLintingTool`, `TextLintingInput` |
| `codemie-tools/src/codemie_tools/text_linting/toolkit.py` | Create | `TextLintingToolkit` (DiscoverableToolkit) |
| `codemie-tools/pyproject.toml` | Modify | Add `vale = "^3.13"` |
| `codemie-tools/tests/codemie_tools/text_linting/__init__.py` | Create | Test package marker |
| `codemie-tools/tests/codemie_tools/text_linting/test_vale_runner.py` | Create | Tests for `vale_runner` |
| `codemie-tools/tests/codemie_tools/text_linting/test_tool.py` | Create | Tests for `TextLintingTool` |
| `codemie-tools/tests/codemie_tools/text_linting/test_toolkit.py` | Create | Tests for `TextLintingToolkit` |
| `code-assistant/pyproject.toml` | Modify | Switch to path dep on `../codemie-tools` |

---

### Task 1: Branch setup and `vale` dependency

**Files:**
- Modify: `codemie-tools/pyproject.toml`

**Interfaces:**
- Produces: `vale` binary accessible as `vale --version` inside `poetry run`

- [ ] **Step 1: Pull latest main and create feature branch**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git fetch origin main
git checkout main
git pull --ff-only
git checkout -b text-linting-tool
```

- [ ] **Step 2: Add `vale` dependency**

Open `pyproject.toml` and add to `[tool.poetry.dependencies]` (keep alphabetical order):

```toml
vale = "^3.13"
```

- [ ] **Step 3: Install the new dependency**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry add vale@"^3.13"
```

- [ ] **Step 4: Verify the Vale binary is available**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run vale --version
```

Expected: prints a version string like `vale version 3.13.x`.

- [ ] **Step 5: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git add pyproject.toml poetry.lock
git commit -m "text-linting-tool: add vale dependency"
```

---

### Task 2: `models.py` and `tool_vars.py`

**Files:**
- Create: `src/codemie_tools/text_linting/models.py`
- Create: `src/codemie_tools/text_linting/tool_vars.py`

**Interfaces:**
- Produces:
  - `TextLintingConfig(enabled_styles, autofix_enabled, styles_cache_dir, chat_model)`
  - `TEXT_LINTING_TOOL: ToolMetadata`

- [ ] **Step 1: Write the failing test**

Create `tests/codemie_tools/text_linting/__init__.py` (empty).

Create `tests/codemie_tools/text_linting/test_tool.py`:

```python
from codemie_tools.text_linting.models import TextLintingConfig
from codemie_tools.text_linting.tool_vars import TEXT_LINTING_TOOL


class TestTextLintingConfig:
    def test_defaults(self):
        config = TextLintingConfig()
        assert config.enabled_styles == ["proselint", "Readability", "ai-tells"]
        assert config.styles_cache_dir == "~/.cache/codemie/vale-styles"

    def test_custom_styles(self):
        config = TextLintingConfig(enabled_styles=["Google", "Microsoft"])
        assert config.enabled_styles == ["Google", "Microsoft"]


class TestTextLintingToolVars:
    def test_metadata_fields(self):
        assert TEXT_LINTING_TOOL.name == "text_linting"
        assert TEXT_LINTING_TOOL.label == "Text Linting"
        assert TEXT_LINTING_TOOL.settings_config is True
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_tool.py -v
```

Expected: `ModuleNotFoundError` — `text_linting` doesn't exist yet.

- [ ] **Step 3: Create `src/codemie_tools/text_linting/models.py`**

```python
from typing import Optional, Any
from pydantic import Field
from codemie_tools.base.models import CodeMieToolConfig


class TextLintingConfig(CodeMieToolConfig):
    enabled_styles: list[str] = Field(
        default=["proselint", "Readability", "ai-tells"],
        description="Vale style packs to enable. Options: proselint, write-good, Google, Microsoft, Readability, ai-tells",
    )
    styles_cache_dir: str = Field(
        default="~/.cache/codemie/vale-styles",
        description="Directory where Vale style packs are downloaded and cached. Shared across all calls.",
    )
```

- [ ] **Step 4: Create `src/codemie_tools/text_linting/tool_vars.py`**

```python
from codemie_tools.base.models import ToolMetadata

TEXT_LINTING_TOOL = ToolMetadata(
    name="text_linting",
    label="Text Linting",
    description=(
        "Lint freeform text against prose style guides (proselint, write-good, Google, "
        "Microsoft, Readability, ai-tells). Returns a structured list of style violations."
    ),
    user_description="Check text for style, readability, and AI-writing-pattern issues.",
    settings_config=True,
)
```

- [ ] **Step 5: Create `src/codemie_tools/text_linting/__init__.py`**

```python
from codemie_tools.text_linting.tool import TextLintingTool
from codemie_tools.text_linting.models import TextLintingConfig

__all__ = ["TextLintingTool", "TextLintingConfig"]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_tool.py::TestTextLintingConfig tests/codemie_tools/text_linting/test_tool.py::TestTextLintingToolVars -v
```

Expected: PASS (the imports will fail until `tool.py` exists — create a stub `tool.py` with `class TextLintingTool: pass` temporarily if needed to satisfy the `__init__.py` import).

- [ ] **Step 7: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git add src/codemie_tools/text_linting/ tests/codemie_tools/text_linting/
git commit -m "text-linting-tool: add TextLintingConfig and tool_vars"
```

---

### Task 3: `vale_runner.py` — Finding, STYLE_PACK_MAP, run_vale()

**Files:**
- Create: `src/codemie_tools/text_linting/vale_runner.py`
- Create: `tests/codemie_tools/text_linting/test_vale_runner.py`

**Interfaces:**
- Consumes: nothing from prior tasks
- Produces:
  - `Finding(line: int, col: int, rule: str, severity: str, message: str, match: str)`
  - `STYLE_PACK_MAP: dict[str, dict[str, str]]` — maps style name → `{package, style_dir}`
  - `run_vale(text: str, styles: list[str], styles_cache_dir: str) -> list[Finding]`

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie_tools/text_linting/test_vale_runner.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.text_linting.vale_runner import (
    Finding,
    STYLE_PACK_MAP,
    run_vale,
    _generate_vale_ini,
    _missing_packs,
)


VALE_JSON_OUTPUT = {
    "input.md": [
        {
            "Check": "proselint.Hedging",
            "Line": 2,
            "Message": "'it seems' is a hedging phrase.",
            "Severity": "warning",
            "Span": [5, 13],
            "Match": "it seems",
        }
    ]
}


class TestFinding:
    def test_fields(self):
        f = Finding(line=2, col=5, rule="proselint.Hedging", severity="warning",
                    message="hedging phrase", match="it seems")
        assert f.line == 2
        assert f.col == 5
        assert f.rule == "proselint.Hedging"


class TestStylePackMap:
    def test_contains_all_supported_styles(self):
        for name in ["proselint", "write-good", "Google", "Microsoft", "Readability", "ai-tells"]:
            assert name in STYLE_PACK_MAP
            assert "package" in STYLE_PACK_MAP[name]
            assert "style_dir" in STYLE_PACK_MAP[name]

    def test_ai_tells_uses_url(self):
        assert STYLE_PACK_MAP["ai-tells"]["package"].startswith("https://")
        assert STYLE_PACK_MAP["ai-tells"]["style_dir"] == "ai-tells"


class TestGenerateValeIni:
    def test_contains_styles_path(self, tmp_path):
        ini = _generate_vale_ini(styles_cache_dir=str(tmp_path), styles=["proselint"])
        assert f"StylesPath = {tmp_path}" in ini

    def test_lists_styles_in_based_on(self, tmp_path):
        ini = _generate_vale_ini(styles_cache_dir=str(tmp_path), styles=["proselint", "write-good"])
        assert "proselint" in ini
        assert "write-good" in ini


class TestMissingPacks:
    def test_returns_packs_whose_dir_is_absent(self, tmp_path):
        (tmp_path / "proselint").mkdir()
        missing = _missing_packs(["proselint", "write-good"], str(tmp_path))
        assert missing == ["write-good"]

    def test_returns_empty_when_all_present(self, tmp_path):
        (tmp_path / "proselint").mkdir()
        assert _missing_packs(["proselint"], str(tmp_path)) == []


class TestRunVale:
    @patch("codemie_tools.text_linting.vale_runner.subprocess.run")
    def test_returns_findings_from_vale_json(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(VALE_JSON_OUTPUT), returncode=1
        )
        (tmp_path / "proselint").mkdir()

        findings = run_vale(
            text="It seems the system works.",
            styles=["proselint"],
            styles_cache_dir=str(tmp_path),
        )

        assert len(findings) == 1
        assert findings[0].rule == "proselint.Hedging"
        assert findings[0].severity == "warning"
        assert findings[0].line == 2

    @patch("codemie_tools.text_linting.vale_runner.subprocess.run")
    def test_returns_empty_list_when_no_findings(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"input.md": []}), returncode=0
        )
        (tmp_path / "proselint").mkdir()

        findings = run_vale(
            text="The system works.",
            styles=["proselint"],
            styles_cache_dir=str(tmp_path),
        )
        assert findings == []

    @patch("codemie_tools.text_linting.vale_runner.subprocess.run")
    def test_runs_vale_sync_for_missing_packs(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(stdout=json.dumps({}), returncode=0)

        run_vale(text="hello", styles=["proselint"], styles_cache_dir=str(tmp_path))

        calls = [str(c) for c in mock_run.call_args_list]
        assert any("sync" in c for c in calls)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_vale_runner.py -v
```

Expected: `ImportError` — `vale_runner` doesn't exist yet.

- [ ] **Step 3: Create `src/codemie_tools/text_linting/vale_runner.py`**

```python
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

STYLE_PACK_MAP: dict[str, dict[str, str]] = {
    "proselint":   {"package": "proselint",   "style_dir": "proselint"},
    "write-good":  {"package": "write-good",  "style_dir": "write-good"},
    "Google":      {"package": "Google",      "style_dir": "Google"},
    "Microsoft":   {"package": "Microsoft",   "style_dir": "Microsoft"},
    "Readability": {"package": "Readability", "style_dir": "Readability"},
    "ai-tells": {
        "package": "https://github.com/tbhb/vale-ai-tells/releases/download/v1.29.0/ai-tells-commits.zip",
        "style_dir": "ai-tells",
    },
}


@dataclass
class Finding:
    line: int
    col: int
    rule: str
    severity: str
    message: str
    match: str


def _generate_vale_ini(styles_cache_dir: str, styles: list[str]) -> str:
    style_dirs = [STYLE_PACK_MAP[s]["style_dir"] for s in styles if s in STYLE_PACK_MAP]
    based_on = ", ".join(style_dirs)
    return (
        f"StylesPath = {styles_cache_dir}\n"
        "MinAlertLevel = suggestion\n\n"
        "[*.md]\n"
        f"BasedOnStyles = {based_on}\n"
    )


def _missing_packs(styles: list[str], styles_cache_dir: str) -> list[str]:
    cache = Path(styles_cache_dir)
    return [
        s for s in styles
        if s in STYLE_PACK_MAP and not (cache / STYLE_PACK_MAP[s]["style_dir"]).is_dir()
    ]


def _sync_packs(missing: list[str], styles_cache_dir: str) -> None:
    packages = ", ".join(STYLE_PACK_MAP[s]["package"] for s in missing if s in STYLE_PACK_MAP)
    cache = Path(styles_cache_dir).expanduser()
    cache.mkdir(parents=True, exist_ok=True)

    ini_content = (
        f"StylesPath = {cache}\n"
        f"Packages = {packages}\n\n"
        "[*.md]\n"
        "BasedOnStyles = Vale\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ini_path = Path(tmp) / ".vale.ini"
        ini_path.write_text(ini_content)
        subprocess.run(
            ["vale", "sync"],
            cwd=tmp,
            check=True,
            capture_output=True,
        )


def _parse_vale_json(raw: str) -> list[Finding]:
    data = json.loads(raw)
    findings = []
    for _path, alerts in data.items():
        for alert in alerts:
            findings.append(Finding(
                line=alert.get("Line", 0),
                col=alert.get("Span", [0])[0],
                rule=alert.get("Check", ""),
                severity=alert.get("Severity", ""),
                message=alert.get("Message", ""),
                match=alert.get("Match", ""),
            ))
    return findings


def run_vale(text: str, styles: list[str], styles_cache_dir: str) -> list[Finding]:
    resolved_cache = str(Path(styles_cache_dir).expanduser())
    missing = _missing_packs(styles, resolved_cache)
    if missing:
        _sync_packs(missing, resolved_cache)

    ini_content = _generate_vale_ini(resolved_cache, styles)

    with tempfile.TemporaryDirectory() as tmp:
        ini_path = Path(tmp) / ".vale.ini"
        ini_path.write_text(ini_content)
        input_path = Path(tmp) / "input.md"
        input_path.write_text(text)

        result = subprocess.run(
            ["vale", "--output=JSON", "input.md"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return []
        return _parse_vale_json(result.stdout)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_vale_runner.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git add src/codemie_tools/text_linting/vale_runner.py \
        tests/codemie_tools/text_linting/test_vale_runner.py
git commit -m "text-linting-tool: add vale_runner with Finding, STYLE_PACK_MAP, run_vale"
```

---

### Task 4: `tool.py` — TextLintingTool

**Files:**
- Create: `src/codemie_tools/text_linting/tool.py`
- Modify: `tests/codemie_tools/text_linting/test_tool.py` (add tool tests)

**Interfaces:**
- Consumes:
  - `run_vale(text, styles, styles_cache_dir) -> list[Finding]` from `vale_runner`
  - `TextLintingConfig` from `models`
  - `TEXT_LINTING_TOOL` from `tool_vars`
- Produces: `TextLintingTool(CodeMieTool)` with `execute(text, styles) -> str`

- [ ] **Step 1: Add tool tests to `tests/codemie_tools/text_linting/test_tool.py`**

Append to the existing file:

```python
from unittest.mock import patch
from codemie_tools.text_linting.tool import TextLintingTool, TextLintingInput
from codemie_tools.text_linting.models import TextLintingConfig
from codemie_tools.text_linting.vale_runner import Finding


def _make_tool():
    return TextLintingTool(config=TextLintingConfig())


class TestTextLintingInput:
    def test_defaults(self):
        inp = TextLintingInput(text="hello")
        assert inp.styles is None

    def test_override(self):
        inp = TextLintingInput(text="hello", styles=["Google"])
        assert inp.styles == ["Google"]


class TestTextLintingToolExecute:
    @patch("codemie_tools.text_linting.tool.run_vale")
    def test_returns_no_issues_message_when_clean(self, mock_run_vale):
        mock_run_vale.return_value = []
        tool = _make_tool()
        result = tool.execute(text="Clean text.")
        assert result == "No style issues found."

    @patch("codemie_tools.text_linting.tool.run_vale")
    def test_formats_findings(self, mock_run_vale):
        mock_run_vale.return_value = [
            Finding(line=1, col=3, rule="proselint.Hedging",
                    severity="warning", message="hedging phrase", match="it seems")
        ]
        tool = _make_tool()
        result = tool.execute(text="It seems this works.")
        assert "Found 1 style" in result
        assert "proselint.Hedging" in result
        assert "[WARNING]" in result

    @patch("codemie_tools.text_linting.tool.run_vale")
    def test_per_call_styles_override_config(self, mock_run_vale):
        mock_run_vale.return_value = []
        tool = _make_tool()
        tool.execute(text="hello", styles=["Google"])
        call_kwargs = mock_run_vale.call_args
        assert call_kwargs[1].get("styles") == ["Google"] or call_kwargs[0][1] == ["Google"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_tool.py -v
```

Expected: `ImportError` — `tool.py` not yet created.

- [ ] **Step 3: Create `src/codemie_tools/text_linting/tool.py`**

```python
from typing import Optional, Type
from pydantic import BaseModel, Field
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.text_linting.models import TextLintingConfig
from codemie_tools.text_linting.tool_vars import TEXT_LINTING_TOOL
from codemie_tools.text_linting.vale_runner import Finding, run_vale


class TextLintingInput(BaseModel):
    text: str = Field(description="Text to lint.")
    styles: Optional[list[str]] = Field(
        default=None,
        description="Override enabled style packs for this call. If omitted, uses config defaults.",
    )


def _format_findings(findings: list[Finding]) -> str:
    lines = [f"Found {len(findings)} style issue{'s' if len(findings) != 1 else ''}:\n"]
    for f in findings:
        lines.append(f"[{f.severity.upper()}] {f.rule} (line {f.line}, col {f.col}): {f.message}")
        if f.match:
            lines.append(f'  > "{f.match}"\n')
    return "\n".join(lines)


class TextLintingTool(CodeMieTool):
    name: str = TEXT_LINTING_TOOL.name
    label: str = TEXT_LINTING_TOOL.label
    description: str = TEXT_LINTING_TOOL.description
    args_schema: Optional[Type[BaseModel]] = TextLintingInput
    config: TextLintingConfig

    def __init__(self, config: TextLintingConfig) -> None:
        super().__init__(config=config)

    def execute(self, text: str, styles: Optional[list[str]] = None) -> str:
        effective_styles = styles if styles is not None else self.config.enabled_styles
        findings = run_vale(
            text=text,
            styles=effective_styles,
            styles_cache_dir=self.config.styles_cache_dir,
        )
        if not findings:
            return "No style issues found."
        return _format_findings(findings)
```

- [ ] **Step 4: Run all tool tests**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_tool.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git add src/codemie_tools/text_linting/tool.py \
        tests/codemie_tools/text_linting/test_tool.py
git commit -m "text-linting-tool: add TextLintingTool and TextLintingInput"
```

---

### Task 5: `toolkit.py` — DiscoverableToolkit registration

**Files:**
- Create: `src/codemie_tools/text_linting/toolkit.py`
- Create: `tests/codemie_tools/text_linting/test_toolkit.py`

**Interfaces:**
- Consumes: `TextLintingTool`, `TEXT_LINTING_TOOL`, `ToolSet.CODE_QUALITY`
- Produces: `TextLintingToolkit(DiscoverableToolkit)`

- [ ] **Step 1: Write the failing test**

Create `tests/codemie_tools/text_linting/test_toolkit.py`:

```python
from codemie_tools.base.models import ToolSet
from codemie_tools.text_linting.toolkit import TextLintingToolkit, TextLintingToolkitUI


class TestTextLintingToolkit:
    def test_get_definition_returns_ui_object(self):
        definition = TextLintingToolkit.get_definition()
        assert isinstance(definition, TextLintingToolkitUI)

    def test_toolkit_is_code_quality(self):
        definition = TextLintingToolkit.get_definition()
        assert definition.toolkit == ToolSet.CODE_QUALITY

    def test_contains_text_linting_tool(self):
        definition = TextLintingToolkit.get_definition()
        tool_names = [t.name for t in definition.tools]
        assert "text_linting" in tool_names

    def test_settings_config_enabled(self):
        definition = TextLintingToolkit.get_definition()
        tool = next(t for t in definition.tools if t.name == "text_linting")
        assert tool.settings_config is True
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/test_toolkit.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `src/codemie_tools/text_linting/toolkit.py`**

```python
from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from codemie_tools.text_linting.models import TextLintingConfig
from codemie_tools.text_linting.tool import TextLintingTool
from codemie_tools.text_linting.tool_vars import TEXT_LINTING_TOOL


class TextLintingToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.CODE_QUALITY
    tools: list[Tool] = [
        Tool.from_metadata(TEXT_LINTING_TOOL, tool_class=TextLintingTool,
                           config_class=TextLintingConfig, settings_config=True)
    ]
    label: str = ToolSet.CODE_QUALITY.value


class TextLintingToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls) -> ToolKit:
        return TextLintingToolkitUI()
```

- [ ] **Step 4: Run all text_linting tests**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
poetry run pytest tests/codemie_tools/text_linting/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/codemie-tools
git add src/codemie_tools/text_linting/toolkit.py \
        tests/codemie_tools/text_linting/test_toolkit.py
git commit -m "text-linting-tool: add TextLintingToolkit registered under CODE_QUALITY"
```

---

### Task 6: Wire `code-assistant` to path dependency and install

**Files:**
- Modify: `code-assistant/pyproject.toml`

**Interfaces:**
- Produces: `from codemie_tools.text_linting import TextLintingTool` works inside `code-assistant`'s venv

- [ ] **Step 1: Edit `code-assistant/pyproject.toml`**

Find the `packages` list and remove `{ include = "codemie_tools", from = "src" }`:

```toml
# Before:
packages = [{ include = "codemie", from = "src" }, { include = "codemie_tools", from = "src" }]

# After:
packages = [{ include = "codemie", from = "src" }]
```

Add the path dependency under `[tool.poetry.dependencies]`:

```toml
codemie-tools = {path = "../codemie-tools", develop = true}
```

- [ ] **Step 2: Run `poetry install`**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant
poetry install
```

Expected: resolves without errors. The `codemie_tools` package is now served from `../codemie-tools`.

- [ ] **Step 3: Smoke-test the import**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant
poetry run python -c "
from codemie_tools.text_linting import TextLintingTool, TextLintingConfig
from codemie_tools.text_linting.toolkit import TextLintingToolkit
from codemie_tools.base.models import ToolSet
d = TextLintingToolkit.get_definition()
assert d.toolkit == ToolSet.CODE_QUALITY
print('OK — TextLintingToolkit registered under', d.toolkit.value)
"
```

Expected: `OK — TextLintingToolkit registered under Code Quality`.

- [ ] **Step 4: Commit `code-assistant` change**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant
git add pyproject.toml poetry.lock
git commit -m "text-linting-tool: switch codemie_tools to path dep on ../codemie-tools"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Goal ✓, Style guides ✓, Module layout ✓, Config ✓, Input schema ✓, Execution flow ✓ (persistent cache, per-call .vale.ini, sync on miss, autofix), Output format ✓, pyproject changes ✓, Acceptance criteria ✓ (covered across Tasks 3–7).
- [x] **Placeholder scan:** No TBDs, no "handle edge cases", all steps show actual code.
- [x] **Type consistency:** `Finding` defined in Task 3, consumed in Tasks 4 and 5 with matching field names. `run_vale` signature consistent across Task 3 (definition) and Task 5 (usage). `autofix_text` signature consistent across Task 4 (definition) and Task 5 (usage).
