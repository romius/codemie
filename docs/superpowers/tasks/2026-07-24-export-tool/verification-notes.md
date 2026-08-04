# Task 5 — Download-chain verification (EPMCDME-12313)

Manual/static verification of the one-click chat download for tool-generated files.
A live browser e2e was not possible in this environment (no running app instance;
colima/docker daemon not running), so the chain was verified statically end-to-end.

## Chain

1. **Tool output** — `ExportTablesTool.execute` returns
   `FileExportService.store_exported_bytes(...)`, i.e.
   `` File '<name>', URL `sandbox:/v1/files/<encoded>` ``.
   Unit tests assert the correct MIME (`text/csv` /
   `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) and that
   the returned string contains a `sandbox:/v1/files/...` URL.

2. **Extraction** — `SANDBOX_FILE_RE`
   (`src/codemie/agents/tools/agent.py:36`) matches the URL. It captures the
   trailing markdown backtick (`...<encoded>\``), but this is harmless:
   `FileObject.from_encoded_url(encoded + "`")` round-trips correctly
   (verified: `out.csv` / `text/csv`), because the serializer discards the
   non-alphabet character on decode. In the rendered chat the backticks are
   markdown inline-code delimiters, so the linkified URL is clean.

3. **Download endpoint** — `GET /v1/files/{file_name}`
   (`src/codemie/rest_api/routers/files.py:157`): the xlsx and csv MIME types are
   not in `READ_FILE_MIME_TYPE_HANDLERS` and do not match the inline-safe
   prefixes, so `read_file` falls through to `get_attachment_response`
   (`Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`) — a
   one-click download.

## Result

- No router or frontend change required.
- This is the same `store_exported_bytes` path the Code Executor already ships in
  production (`export_files_from_execution` → `store_exported_bytes`), confirming
  the mechanism end-to-end.
