# SPARQL History Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in SPARQL-specific JSONL history logging so users can reproduce `run_sparql` executions with exact query text, resolved endpoint, outcome, and compact result metadata.

**Architecture:** Implement a failure-safe `_SparqlHistoryLogger` in `togo_mcp/server.py` and call it from `execute_sparql()` in a `finally` block. Keep the existing `TOGOMCP_QUERY_LOG` middleware unchanged and add documentation/config examples for the new `TOGOMCP_SPARQL_HISTORY` environment variable.

**Tech Stack:** Python 3.11+, FastMCP, httpx, stdlib `logging.handlers.RotatingFileHandler`, pytest, uv.

---

## File structure

- Modify `togo_mcp/server.py`
  - Add `_SparqlHistoryLogger` and `_sparql_history_logger` near the existing logging helpers.
  - Update `execute_sparql()` to build a reproducibility record and write it on success or failure.
- Modify `tests/test_server.py`
  - Add tests for disabled history logging, successful records, HTTP-error records, timeout records, and write-failure safety.
- Modify `README.md`
  - Document SPARQL history logging near the existing Tool-Call Logging section.
- Modify `.env.example`
  - Add optional Docker env examples for main and test SPARQL history files.
- Modify `compose.yaml`
  - Pass through `TOGOMCP_SPARQL_HISTORY` and `TOGOMCP_SPARQL_HISTORY_TEST` to the containers.

## Task 1: Add failing tests for SPARQL history logging

**Files:**
- Modify: `tests/test_server.py`

- [ ] **Step 1: Add helper classes and fixtures for fake SPARQL responses**

Append these helpers after `_make_logger()` in `tests/test_server.py`:

```python
class _FakeResponse:
    def __init__(self, text: str, status_code: int, url: str = "https://fake.example/sparql") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.content = text.encode("utf-8")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


class _FakeSparqlClient:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.timeout = SimpleNamespace(read=60.0)
        self.calls: list[dict] = []

    async def post(self, url: str, data: dict, headers: dict) -> _FakeResponse:
        self.calls.append({"url": url, "data": data, "headers": headers})
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        self.response.url = url
        return self.response


def _reload_server_with_sparql_history(monkeypatch, tmp_path: Path, enabled: bool):
    history_path = tmp_path / "sparql-history.jsonl"
    monkeypatch.delenv("TOGOMCP_QUERY_LOG", raising=False)
    if enabled:
        monkeypatch.setenv("TOGOMCP_SPARQL_HISTORY", str(history_path))
    else:
        monkeypatch.delenv("TOGOMCP_SPARQL_HISTORY", raising=False)
    import togo_mcp.server as srv
    importlib.reload(srv)
    return srv, history_path
```

- [ ] **Step 2: Add the disabled-history test**

Append this test class after `TestToolCallLogger`:

```python
class TestSparqlHistoryLogger:
    def test_disabled_history_does_not_write_file(self, monkeypatch, tmp_path: Path) -> None:
        srv, history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=False)
        fake_client = _FakeSparqlClient(_FakeResponse("x\n1\n", 200))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)

        out = asyncio.run(srv.execute_sparql("SELECT * WHERE { ?s ?p ?o } LIMIT 1", database="uniprot"))

        assert out == "x\n1\n"
        assert not history_path.exists()
```

- [ ] **Step 3: Add the successful-history test**

Add this method inside `TestSparqlHistoryLogger`:

```python
    def test_successful_sparql_writes_history_record(self, monkeypatch, tmp_path: Path) -> None:
        srv, history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=True)
        body = "protein\nP04637\nQ9Y6K9\n"
        fake_client = _FakeSparqlClient(_FakeResponse(body, 200))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)
        query = "SELECT ?protein WHERE { ?protein ?p ?o } LIMIT 2"

        out = asyncio.run(srv.execute_sparql(query, database="uniprot"))

        assert out == body
        records = _read_jsonl(history_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["database"] == "uniprot"
        assert rec["endpoint_name"] == ""
        assert rec["endpoint_url"] == srv.SPARQL_ENDPOINT["uniprot"]["url"]
        assert rec["sparql_query"] == query
        assert rec["status"] == "ok"
        assert rec["http_code"] == 200
        assert rec["n_rows"] == 2
        assert rec["n_bytes"] == len(body.encode("utf-8"))
        assert rec["query_sha256"]
        assert rec["result_sha256"]
        assert isinstance(rec["elapsed_ms"], (int, float))
```

- [ ] **Step 4: Add HTTP error and timeout tests**

Add these methods inside `TestSparqlHistoryLogger`:

```python
    def test_http_error_writes_history_record(self, monkeypatch, tmp_path: Path) -> None:
        srv, history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=True)
        fake_client = _FakeSparqlClient(_FakeResponse("bad sparql", 400))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)

        with pytest.raises(ValueError, match="HTTP 400"):
            asyncio.run(srv.execute_sparql("BROKEN", database="uniprot"))

        rec = _read_jsonl(history_path)[0]
        assert rec["status"] == "http_4xx"
        assert rec["http_code"] == 400
        assert rec["n_bytes"] == len(b"bad sparql")
        assert rec["sparql_query"] == "BROKEN"
        assert rec["error_class"] == "ValueError"
        assert "HTTP 400" in rec["error_message"]

    def test_timeout_writes_history_record(self, monkeypatch, tmp_path: Path) -> None:
        srv, history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=True)
        fake_client = _FakeSparqlClient(exc=srv.httpx.TimeoutException("slow"))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)

        with pytest.raises(ValueError, match="timed out"):
            asyncio.run(srv.execute_sparql("SELECT * WHERE { ?s ?p ?o }", database="uniprot"))

        rec = _read_jsonl(history_path)[0]
        assert rec["status"] == "timeout"
        assert rec["database"] == "uniprot"
        assert rec["endpoint_url"] == srv.SPARQL_ENDPOINT["uniprot"]["url"]
        assert rec["error_class"] == "ValueError"
        assert "timed out" in rec["error_message"]
```

- [ ] **Step 5: Add write-failure safety test**

Add this method inside `TestSparqlHistoryLogger`:

```python
    def test_history_write_failure_does_not_fail_sparql(self, monkeypatch, tmp_path: Path) -> None:
        srv, _history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=True)
        body = "x\n1\n"
        fake_client = _FakeSparqlClient(_FakeResponse(body, 200))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)

        def fail_write(_record: dict) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(srv._sparql_history_logger, "write", fail_write)

        out = asyncio.run(srv.execute_sparql("SELECT * WHERE { ?s ?p ?o } LIMIT 1", database="uniprot"))

        assert out == body
```

- [ ] **Step 6: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_server.py::TestSparqlHistoryLogger -v
```

Expected: tests fail with `AttributeError` or missing-file assertions because `_SparqlHistoryLogger` and history writing are not implemented yet.

## Task 2: Implement `_SparqlHistoryLogger`

**Files:**
- Modify: `togo_mcp/server.py`

- [ ] **Step 1: Add a helper class before `execute_sparql()`**

Insert this class after `raise_for_status_with_body()` and before `execute_sparql()`:

```python
class _SparqlHistoryLogger:
    """Append reproducibility-focused SPARQL execution records to JSONL.

    Enabled by setting TOGOMCP_SPARQL_HISTORY to a filesystem path. Logging is
    best-effort and must never break SPARQL execution.
    """

    def __init__(self) -> None:
        log_path = os.getenv("TOGOMCP_SPARQL_HISTORY", "").strip()
        self._enabled = bool(log_path)
        self._log: logging.Logger | None = None
        if not self._enabled:
            return
        try:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path, maxBytes=50_000_000, backupCount=10, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            log = logging.getLogger("togomcp.sparql_history")
            log.setLevel(logging.INFO)
            log.propagate = False
            log.handlers = [handler]
            self._log = log
        except Exception as exc:
            self._enabled = False
            logger.warning("SPARQL history logging disabled: %s", exc)

    def write(self, record: dict[str, Any]) -> None:
        if not self._enabled or self._log is None:
            return
        try:
            self._log.info(json.dumps(record, default=str))
        except Exception as exc:
            logger.warning("Failed to write SPARQL history record: %s", exc)


_sparql_history_logger = _SparqlHistoryLogger()
```

- [ ] **Step 2: Run the disabled test**

Run:

```bash
uv run pytest tests/test_server.py::TestSparqlHistoryLogger::test_disabled_history_does_not_write_file -v
```

Expected: still fails because `execute_sparql()` does not call `_sparql_history_logger.write()` yet.

## Task 3: Write SPARQL history records from `execute_sparql()`

**Files:**
- Modify: `togo_mcp/server.py`

- [ ] **Step 1: Replace the body of `execute_sparql()` with history-aware code**

Keep the function signature and docstring. Replace the implementation body with this code:

```python
    start = time.perf_counter()
    status = "error"
    error_class: str | None = None
    error_message: str | None = None
    response: httpx.Response | None = None
    url: str | None = None

    extra: dict[str, Any] = {
        "query_sha256": hashlib.sha256(sparql_query.strip().encode("utf-8")).hexdigest(),
    }
    _sparql_extra_var.set(extra)

    try:
        url = resolve_endpoint_url(database, endpoint_name, endpoint_url)
        extra["endpoint_url"] = url

        response = await _sparql_client.post(
            url, data={"query": sparql_query}, headers={"Accept": "text/csv"}
        )
    except httpx.TimeoutException as exc:
        status = "timeout"
        extra["sparql_status"] = status
        error_class = "ValueError"
        error_message = (
            f"SPARQL endpoint at {url} timed out after {_sparql_client.timeout.read}s. "
            "The query is likely too heavy. Add LIMIT, narrow with specific IRIs or GRAPH "
            "clauses, or split into smaller queries. Do not retry the same query without "
            f"changes. ({exc.__class__.__name__})"
        )
        raise ValueError(error_message) from exc
    except httpx.HTTPError as exc:
        status = "network_error"
        extra["sparql_status"] = status
        error_class = "ValueError"
        error_message = (
            f"SPARQL endpoint at {url} could not be reached: "
            f"{exc.__class__.__name__}: {exc}"
        )
        raise ValueError(error_message) from exc
    except BaseException as exc:
        status = "error"
        extra["sparql_status"] = status
        error_class = exc.__class__.__name__
        error_message = str(exc)
        raise
    else:
        extra["http_code"] = response.status_code
        extra["n_bytes"] = len(response.content)
        if response.is_success:
            status = "ok"
            extra["sparql_status"] = status
            extra["n_rows"] = max(response.text.count("\n") - 1, 0)
            extra["result_sha256"] = hashlib.sha256(response.content).hexdigest()
        elif 400 <= response.status_code < 500:
            status = "http_4xx"
            extra["sparql_status"] = status
        else:
            status = "http_5xx"
            extra["sparql_status"] = status

        try:
            raise_for_status_with_body(
                response,
                context="SPARQL endpoint",
                client_error_hint=(
                    "The endpoint diagnostic above usually names the exact line/column. "
                    "Common causes: syntax error (missing brace/comma), undefined namespace "
                    "prefix, unsupported function. Fix the query — do not retry the same text."
                ),
                server_error_hint=(
                    "This may be transient or indicate the query is too heavy. Consider "
                    "adding LIMIT, stronger filters (specific IRIs, GRAPH clauses), or "
                    "splitting the query."
                ),
            )
        except BaseException as exc:
            error_class = exc.__class__.__name__
            error_message = str(exc)
            raise
        return response.text
    finally:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "endpoint_name": endpoint_name,
            "endpoint_url": url,
            "sparql_query": sparql_query,
            "query_sha256": extra["query_sha256"],
            "status": status,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
        }
        for key in ("http_code", "n_rows", "n_bytes", "result_sha256"):
            if key in extra:
                record[key] = extra[key]
        if error_class is not None:
            record["error_class"] = error_class
        if error_message is not None:
            record["error_message"] = error_message[:500]
        try:
            _sparql_history_logger.write(record)
        except Exception as exc:
            logger.warning("Failed to write SPARQL history record: %s", exc)
```

- [ ] **Step 2: Run focused SPARQL history tests**

Run:

```bash
uv run pytest tests/test_server.py::TestSparqlHistoryLogger -v
```

Expected: all `TestSparqlHistoryLogger` tests pass.

- [ ] **Step 3: Run all server tests**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: all tests in `tests/test_server.py` pass.

- [ ] **Step 4: Commit implementation and tests**

Run:

```bash
git add togo_mcp/server.py tests/test_server.py
git commit -m "feat: add SPARQL history logging"
```

Expected: commit succeeds.

## Task 4: Document and configure SPARQL history logging

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `compose.yaml`

- [ ] **Step 1: Update `README.md` Tool-Call Logging section**

In `README.md`, after the paragraph that introduces `TOGOMCP_QUERY_LOG`, add:

```markdown
### SPARQL History Logging (Optional)

For reproducibility, TogoMCP can also record a SPARQL-only execution history.
Set `TOGOMCP_SPARQL_HISTORY` to a writable JSONL path. Each `run_sparql` call
appends the exact SPARQL text, original database/endpoint arguments, resolved
endpoint URL, status, HTTP code, elapsed time, row/byte counts, query SHA-256,
and result SHA-256. Full result bodies are not stored.

This is separate from `TOGOMCP_QUERY_LOG`: use `TOGOMCP_QUERY_LOG` for complete
MCP tool-call auditing, and `TOGOMCP_SPARQL_HISTORY` when you mainly need to
reproduce or debug SPARQL runs.
```

In the Docker subsection of Tool-Call Logging, add:

```markdown
To enable SPARQL history in Docker as well:

```bash
echo 'TOGOMCP_SPARQL_HISTORY=/var/log/togomcp/sparql-history.jsonl' >> .env
docker compose up -d togomcp-main
tail -f logs/sparql-history.jsonl
```
```

In the Claude Desktop local stdio example, extend the env block to include:

```json
"TOGOMCP_SPARQL_HISTORY": "/Users/you/togomcp-logs/sparql-history.jsonl"
```

- [ ] **Step 2: Update `.env.example`**

After the existing `TOGOMCP_QUERY_LOG` examples, add:

```dotenv
# Optional: enable SPARQL-only history logging for reproducibility.
# This stores exact query text and compact execution metadata, not full results.
# TOGOMCP_SPARQL_HISTORY=/var/log/togomcp/sparql-history.jsonl
# TOGOMCP_SPARQL_HISTORY_TEST=/var/log/togomcp/sparql-history-test.jsonl
```

- [ ] **Step 3: Update `compose.yaml`**

In service `togomcp-main.environment`, add:

```yaml
      TOGOMCP_SPARQL_HISTORY: ${TOGOMCP_SPARQL_HISTORY:-}
```

In service `togomcp-test.environment`, add:

```yaml
      TOGOMCP_SPARQL_HISTORY: ${TOGOMCP_SPARQL_HISTORY_TEST:-}
```

- [ ] **Step 4: Run documentation/config sanity checks**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: all tests in `tests/test_server.py` pass.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Commit documentation/config changes**

Run:

```bash
git add README.md .env.example compose.yaml
git commit -m "docs: document SPARQL history logging"
```

Expected: commit succeeds.

## Task 5: Final verification

**Files:**
- Inspect: `togo_mcp/server.py`
- Inspect: `tests/test_server.py`
- Inspect: `README.md`
- Inspect: `.env.example`
- Inspect: `compose.yaml`

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run pytest tests/test_server.py -v
```

Expected: all tests in `tests/test_server.py` pass.

- [ ] **Step 2: Run broader test suite if practical**

Run:

```bash
uv run pytest tests/test_server.py tests/test_ncbi_tools.py -v
```

Expected: all selected tests pass. Do not block on `tests/test_api_tools.py` unless explicitly requested because `CLAUDE.md` notes pre-existing FunctionTool-callability failures there.

- [ ] **Step 3: Verify final diff**

Run:

```bash
git diff --stat HEAD~2..HEAD
git status --short
```

Expected: only the intended files changed; working tree is clean after commits.

- [ ] **Step 4: Report completion**

Summarize:

- New environment variable: `TOGOMCP_SPARQL_HISTORY`
- What the JSONL records include
- Verification commands run and results
- Any tests intentionally not run
