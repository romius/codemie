# Text Linting Tool — Spec

## Goal

Add a `TextLintingTool` to the `codemie-tools` package that allows agents to lint freeform text against configurable prose style guides and, optionally, auto-fix violations using an LLM.

## Scope

- New module: `src/codemie_tools/text_linting/` inside the `codemie-tools` repo
- New dependency in `codemie-tools`: `vale = "^3.13"` (pip package that auto-downloads the Vale binary)
- `code-assistant` switches from the embedded `src/codemie_tools/` to a path dependency on `../codemie-tools`

## Style Guides

All packs are installed via Vale's `Packages` directive in `.vale.ini`. Registry packs are referenced by name; `ai-tells` is referenced by GitHub release URL (Vale supports both).

| Pack | Source | Coverage |
|---|---|---|
| `proselint` | Vale registry | Orwell-aligned: hedging, jargon, clichés, passive voice, misused words |
| `write-good` | Vale registry | Passive voice, weasel words, lexical illusions |
| `Google` | Vale registry | Google developer documentation style |
| `Microsoft` | Vale registry | Microsoft writing style guide |
| `Readability` | Vale registry | Flesch-Kincaid and other readability metrics |
| `ai-tells` | [GitHub release ZIP](https://github.com/tbhb/vale-ai-tells/releases/download/v1.29.0/ai-tells-commits.zip) | Detects AI-generated writing patterns (hedging, filler phrases, over-explanation) |

Default enabled styles: `["proselint", "Readability", "ai-tells"]`. Users add or remove packs via config.

## Module Layout

```
src/codemie_tools/text_linting/
├── __init__.py
├── toolkit.py       — DiscoverableToolkit, ToolSet.CODE_QUALITY
├── tool.py          — TextLintingTool (CodeMieTool)
├── tool_vars.py     — ToolMetadata constants
├── models.py        — TextLintingConfig (CodeMieToolConfig, settings_config=True)
└── vale_runner.py   — Vale subprocess execution and JSON parsing
```

## Configuration

`TextLintingConfig` exposes user-configurable fields shown in the UI settings panel (`settings_config=True`):

```python
class TextLintingConfig(CodeMieToolConfig):
    enabled_styles: list[str] = Field(
        default=["proselint", "Readability", "ai-tells"],
        description="Vale style packs to enable. Options: proselint, write-good, Google, Microsoft, Readability, ai-tells"
    )
    styles_cache_dir: str = Field(
        default="~/.cache/codemie/vale-styles",
        description="Directory where Vale style packs are downloaded and cached. Shared across all calls."
    )
```

## Tool Input Schema

```python
class TextLintingInput(BaseModel):
    text: str = Field(description="Text to lint.")
    styles: Optional[list[str]] = Field(
        default=None,
        description="Override enabled style packs for this call. If omitted, uses config defaults."
    )
```

## Execution Flow

Style pack downloads and `.vale.ini` generation are handled separately to avoid redundant network calls.

**Persistent cache** — `StylesPath` points to a global directory (default `~/.cache/codemie/vale-styles/`, configurable via `TextLintingConfig.styles_cache_dir`). Pack directories inside it survive between calls and across restarts.

**Per-call steps (`vale_runner.py`)**:

1. Resolve effective styles (`styles` arg → config `enabled_styles`).
2. For each requested pack, check whether its directory already exists under `styles_cache_dir`. If any are missing, run `vale sync` with a `.vale.ini` that lists only the missing packs (and `StylesPath` pointing at the cache). This downloads once and never again for that pack.
3. Create a per-call temp directory. Write `input.md` (the text) and a fresh `.vale.ini` that sets `StylesPath = <styles_cache_dir>` and `BasedOnStyles` to the effective styles list.
4. Run `vale --output=JSON input.md` and capture stdout.
5. Parse the JSON output into a list of `Finding` objects (line, column, rule, severity, message, context).
6. Delete the per-call temp directory (`styles_cache_dir` is left intact).
7. Return the formatted findings string. The calling agent uses its own LLM to rewrite the text if needed.

**Cache layout**:
```
~/.cache/codemie/vale-styles/   ← persistent across calls
    proselint/
    Readability/
    ai-tells/
    write-good/
    ...

<tempdir>/                      ← created and deleted per call
    .vale.ini                   ← StylesPath → cache above
    input.md
```

## Output Format

```
Found N style issues:

[SEVERITY] RuleName (line L, col C): message
  > "offending excerpt"

...
```

When no issues are found: `"No style issues found."`

## pyproject Changes

**`codemie-tools/pyproject.toml`** — add:
```toml
vale = "^3.13"
```

**`code-assistant/pyproject.toml`** — switch from embedded to path dependency:
```toml
# Remove from packages list:
{ include = "codemie_tools", from = "src" }

# Add to [tool.poetry.dependencies]:
codemie-tools = {path = "../codemie-tools", develop = true}
```

After both changes: `poetry install` in `code-assistant`.

## Acceptance Criteria

- `TextLintingTool` appears under `CODE_QUALITY` in the toolkit registry.
- Calling the tool with a text string containing passive voice returns at least one `write-good` finding.
- Calling with `styles=["Google"]` only applies Google rules regardless of config defaults.
- `vale sync` is called once per unique style set; Vale's own file-based caching means pack files are not re-downloaded if already present.
- All fields in `TextLintingConfig` are visible in the UI settings panel.

## Out of Scope

- STE-100 custom rules (dropped).
- File path input (raw text string only).
- LLM autofix inside the tool — the calling agent handles rewriting using its own model.
- Merging diverged modules from `code-assistant/src/codemie_tools/` into `codemie-tools`.
