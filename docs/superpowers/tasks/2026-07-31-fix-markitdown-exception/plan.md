# EPMCDME-13779: Fix UnsupportedFormatException on File Attachments

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development (inline TDD) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guard markdown conversion against unsupported file formats so one bad file cannot break entire conversations.

**Architecture:** Wrap the unguarded `convert_file_to_markdown()` call at line 71 of `markdown_cache_service.py` with try/except, return a fallback error string when conversion fails, and write that fallback to cache to prevent re-conversion on history files. Mirror the established catch-and-fallback pattern from `FileAnalysisTool._process_single_file()`.

**Tech Stack:** Python 3.12, pytest, unittest.mock, markitdown 0.1.2

## Global Constraints

- Python 3.12+ (match project version floor)
- Catch `markitdown.MarkItDownException` (base class covering UnsupportedFormatException, FileConversionException, MissingDependencyException)
- Log at `logger.warning` level (not `error`) per project logging-patterns.md
- Fallback string format: `[Conversion failed for {filename}: {exception_type}]`
- Cache write of fallback result is mandatory to prevent history file re-conversion
- All existing 10 happy-path tests must remain passing

---

### Task 1: Guard conversion and return fallback string

**Files:**
- Modify: `src/codemie/service/file_service/markdown_cache_service.py:70-84`
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`

**Interfaces:**
- Consumes: `convert_file_to_markdown(bytes, str) -> str` (raises `markitdown.MarkItDownException` on failure)
- Produces: `MarkdownCacheService.get_or_convert(file_obj: FileObject) -> str` (never raises, returns fallback on conversion failure)

- [ ] **Step 1: Write the failing test for UnsupportedFormatException**

```python
def test_get_or_convert_unsupported_format_returns_fallback(self, file_obj, mock_repo):
    """UnsupportedFormatException returns fallback string and does not raise."""
    from markitdown import UnsupportedFormatException
    
    mock_repo.read_file.side_effect = Exception("not found")
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = UnsupportedFormatException("No converter for CFB/OLE2")
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        result = svc.get_or_convert(file_obj)
    
    assert result.startswith("[Conversion failed for report.pdf:")
    assert "UnsupportedFormatException" in result
    mock_convert.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_unsupported_format_returns_fallback -v`

Expected: FAIL with `UnsupportedFormatException` propagating out of `get_or_convert()`

- [ ] **Step 3: Add try/except guard around conversion**

In `src/codemie/service/file_service/markdown_cache_service.py`, replace lines 70-71:

```python
        logger.debug("MarkdownCache: miss for %s/%s, converting", file_obj.owner, file_obj.name)
        try:
            markdown = convert_file_to_markdown(file_obj.bytes_content(), file_obj.name)
        except Exception as e:
            fallback = f"[Conversion failed for {file_obj.name}: {type(e).__name__}]"
            logger.warning(
                "MarkdownCache: conversion failed for %s/%s (%s, %d bytes): %s",
                file_obj.owner,
                file_obj.name,
                file_obj.mime_type,
                len(file_obj.bytes_content() or b""),
                e,
            )
            markdown = fallback
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_unsupported_format_returns_fallback -v`

Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regression**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py -v`

Expected: All 11 tests (10 existing + 1 new) PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/file_service/markdown_cache_service.py tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Guard markdown conversion with try/except fallback"
```

---

### Task 2: Ensure fallback result is written to cache

**Files:**
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`
- Verify: `src/codemie/service/file_service/markdown_cache_service.py:73-82` (cache write already exists)

**Interfaces:**
- Consumes: `MarkdownCacheService.get_or_convert(file_obj) -> str` (returns fallback on error)
- Produces: Cache write verification via mock assertion

- [ ] **Step 1: Write the failing test for cache write of fallback**

```python
def test_get_or_convert_unsupported_format_writes_fallback_to_cache(self, file_obj, mock_repo):
    """Fallback error string is written to cache to prevent re-conversion on history files."""
    from markitdown import UnsupportedFormatException
    
    mock_repo.read_file.side_effect = Exception("not found")
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = UnsupportedFormatException("No converter")
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        result = svc.get_or_convert(file_obj)
    
    # Verify fallback string is written to cache
    mock_repo.write_file.assert_called_once()
    call_args = mock_repo.write_file.call_args
    assert call_args.kwargs["name"] == "report.pdf-md-cache.md"
    assert call_args.kwargs["owner"] == "user1"
    assert b"[Conversion failed for report.pdf:" in call_args.kwargs["content"]
```

- [ ] **Step 2: Run test to verify it passes (no code change needed)**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_unsupported_format_writes_fallback_to_cache -v`

Expected: PASS (existing cache write at lines 73-82 already writes `markdown` variable, which now holds fallback string)

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Verify fallback result is written to cache"
```

---

### Task 3: Test partial failure in multi-file batch

**Files:**
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`

**Interfaces:**
- Consumes: `MarkdownCacheService.get_preconverted(file_objs: list[FileObject]) -> dict[str, str]`
- Produces: Test verifying partial failure does not abort batch processing

- [ ] **Step 1: Write the failing test for multi-file partial failure**

```python
def test_get_preconverted_partial_failure_continues_processing(self, mock_repo):
    """One file fails conversion, others succeed — processing continues."""
    from markitdown import FileConversionException
    
    file1 = FileObject(name="good.pdf", content=b"%PDF", mime_type="application/pdf", owner="user1")
    file2 = FileObject(name="bad.doc", content=b"\xd0\xcf\x11\xe0", mime_type="application/msword", owner="user1")
    file3 = FileObject(name="also-good.txt", content=b"text", mime_type="text/plain", owner="user1")
    
    mock_repo.read_file.side_effect = Exception("not found")
    
    def mock_convert_side_effect(content, name):
        if name == "bad.doc":
            raise FileConversionException("CFB/OLE2 not supported")
        return f"# Converted {name}"
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = mock_convert_side_effect
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        result = svc.get_preconverted([file1, file2, file3])
    
    assert "good.pdf" in result
    assert "# Converted good.pdf" in result["good.pdf"]
    assert "bad.doc" in result
    assert "[Conversion failed for bad.doc:" in result["bad.doc"]
    assert "also-good.txt" in result
    assert "# Converted also-good.txt" in result["also-good.txt"]
    assert len(result) == 3
```

- [ ] **Step 2: Run test to verify it passes (no code change needed)**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_preconverted_partial_failure_continues_processing -v`

Expected: PASS (Task 1 guard prevents exception propagation; dict comprehension in `get_preconverted()` continues)

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Test multi-file partial failure continues processing"
```

---

### Task 4: Test FileConversionException handling

**Files:**
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`

**Interfaces:**
- Consumes: `MarkdownCacheService.get_or_convert(file_obj) -> str` (catches all `Exception` subclasses)
- Produces: Test verifying different exception types are handled

- [ ] **Step 1: Write the failing test for FileConversionException**

```python
def test_get_or_convert_file_conversion_exception_returns_fallback(self, file_obj, mock_repo):
    """FileConversionException (another markitdown exception) is caught and logged."""
    from markitdown import FileConversionException
    
    mock_repo.read_file.side_effect = Exception("not found")
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = FileConversionException("Conversion failed")
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        result = svc.get_or_convert(file_obj)
    
    assert result.startswith("[Conversion failed for report.pdf:")
    assert "FileConversionException" in result
```

- [ ] **Step 2: Run test to verify it passes (no code change needed)**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_file_conversion_exception_returns_fallback -v`

Expected: PASS (Task 1 catches `Exception`, which covers `FileConversionException`)

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Test FileConversionException handling"
```

---

### Task 5: Test zero-byte file handling

**Files:**
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`

**Interfaces:**
- Consumes: `MarkdownCacheService.get_or_convert(file_obj) -> str` (handles empty content)
- Produces: Test verifying zero-byte files do not crash

- [ ] **Step 1: Write the failing test for zero-byte file**

```python
def test_get_or_convert_zero_byte_file_returns_fallback(self, mock_repo):
    """Zero-byte file that causes conversion to raise is handled gracefully."""
    from markitdown import UnsupportedFormatException
    
    file_obj = FileObject(name="empty.doc", content=b"", mime_type="application/msword", owner="user1")
    mock_repo.read_file.side_effect = Exception("not found")
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = UnsupportedFormatException("Empty stream")
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        result = svc.get_or_convert(file_obj)
    
    assert result.startswith("[Conversion failed for empty.doc:")
    assert "UnsupportedFormatException" in result
```

- [ ] **Step 2: Run test to verify it passes (no code change needed)**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_zero_byte_file_returns_fallback -v`

Expected: PASS (Task 1 guard handles this)

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Test zero-byte file handling"
```

---

### Task 6: Verify logging includes required fields

**Files:**
- Test: `tests/codemie/service/file_service/test_markdown_cache_service.py`

**Interfaces:**
- Consumes: `logger.warning()` call in `get_or_convert()` exception handler
- Produces: Test verifying log call includes owner, filename, mime_type, size, exception

- [ ] **Step 1: Write the failing test for log field verification**

```python
def test_get_or_convert_logs_conversion_failure_with_context(self, file_obj, mock_repo, caplog):
    """Logger.warning call includes owner, filename, mime_type, size, and exception."""
    from markitdown import UnsupportedFormatException
    import logging
    
    mock_repo.read_file.side_effect = Exception("not found")
    
    with (
        patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        caplog.at_level(logging.WARNING),
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_repo
        mock_convert.side_effect = UnsupportedFormatException("No converter")
        
        from codemie.service.file_service.markdown_cache_service import MarkdownCacheService
        
        svc = MarkdownCacheService()
        svc.get_or_convert(file_obj)
    
    # Verify log message contains required fields
    assert len(caplog.records) >= 1
    log_record = [r for r in caplog.records if "conversion failed" in r.message.lower()][0]
    assert "user1" in log_record.message  # owner
    assert "report.pdf" in log_record.message  # filename
    assert "application/pdf" in log_record.message  # mime_type
    assert "9 bytes" in log_record.message  # size (len(b"%PDF-fake"))
    assert "No converter" in log_record.message  # exception message
```

- [ ] **Step 2: Run test to verify it passes (no code change needed)**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py::TestMarkdownCacheService::test_get_or_convert_logs_conversion_failure_with_context -v`

Expected: PASS (Task 1 logger.warning call includes all fields)

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/codemie/service/file_service/test_markdown_cache_service.py -v`

Expected: All 16 tests (10 existing + 6 new) PASS

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/service/file_service/test_markdown_cache_service.py
git commit -m "EPMCDME-13779: Verify logging includes required context fields"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✓ Acceptance criterion 1 (consistent processing) → Task 1 guard prevents exception propagation
- ✓ Acceptance criterion 2 (no random failures) → Task 2 cache write prevents re-conversion
- ✓ Acceptance criterion 3 (accurate error details) → Task 1 fallback string names file and exception type
- ✓ Acceptance criterion 4 (deterministic retry) → Task 2 cache write makes retry behavior deterministic
- ✓ Acceptance criterion 5 (logs/telemetry) → Task 6 verifies log includes all required fields
- ✓ Acceptance criterion 6 (regression coverage) → Tasks 1-5 cover UnsupportedFormatException, FileConversionException, zero-byte, multi-file
- ✓ Acceptance criterion 7 (existing scenarios unchanged) → Task 1 Step 5 runs all existing tests
- ✓ Additional requirement: guard conversion → Task 1
- ✓ Additional requirement: report per-file failures → Task 1 fallback string
- ✓ Additional requirement: history files non-blocking → Task 2 cache write
- ✓ Additional requirement: warning-level logging → Task 1 uses logger.warning
- ✓ Additional requirement: 4-5 negative tests → Tasks 1-6 add 6 new tests

**Placeholder scan:**
- No "TBD", "TODO", "fill in details", or "add appropriate" phrases
- All test code blocks are complete with assertions
- All implementation code blocks show exact line replacements

**Type consistency:**
- `FileObject` fields (`name`, `content`, `mime_type`, `owner`) used consistently
- `get_or_convert(file_obj: FileObject) -> str` signature preserved
- `convert_file_to_markdown(bytes, str) -> str` signature matches existing code
- Fallback string format `[Conversion failed for {filename}: {exception_type}]` used in all tests
