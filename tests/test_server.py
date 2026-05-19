"""Tests for togo_mcp.server module."""

import asyncio
import csv
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from togo_mcp.server import load_sparql_endpoints, resolve_endpoint_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(tmp_dir: Path, rows: list[list[str]]) -> str:
    """Write a CSV file with a header and return its path."""
    csv_path = tmp_dir.joinpath("endpoints.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["db_name", "endpoint_url", "endpoint_name", "keyword_search_api"])
        for row in rows:
            writer.writerow(row)
    return str(csv_path)


# ---------------------------------------------------------------------------
# load_sparql_endpoints
# ---------------------------------------------------------------------------


class TestLoadSparqlEndpoints:
    """Tests for load_sparql_endpoints CSV parsing and key normalization."""

    def test_basic_loading(self, tmp_path: Path) -> None:
        """CSV rows are loaded with correct keys and values."""
        path = _write_csv(
            tmp_path,
            [
                ["UniProt", "https://uniprot.example.com/sparql", "uniprot_ep", "kw_api"],
            ],
        )
        result = load_sparql_endpoints(path)
        assert "uniprot" in result
        assert result["uniprot"]["url"] == "https://uniprot.example.com/sparql"
        assert result["uniprot"]["endpoint_name"] == "uniprot_ep"
        assert result["uniprot"]["keyword_search"] == "kw_api"

    def test_key_normalization_spaces(self, tmp_path: Path) -> None:
        """Spaces in db_name are replaced with underscores."""
        path = _write_csv(
            tmp_path,
            [
                ["NCBI Gene", "https://example.com/sparql", "ep", "kw"],
            ],
        )
        result = load_sparql_endpoints(path)
        assert "ncbi_gene" in result

    def test_key_normalization_hyphens(self, tmp_path: Path) -> None:
        """Hyphens in db_name are removed."""
        path = _write_csv(
            tmp_path,
            [
                ["rdf-config", "https://example.com/sparql", "ep", "kw"],
            ],
        )
        result = load_sparql_endpoints(path)
        assert "rdfconfig" in result

    def test_key_normalization_mixed(self, tmp_path: Path) -> None:
        """Mixed case, spaces, and hyphens are all normalized."""
        path = _write_csv(
            tmp_path,
            [
                ["My-DB Name", "https://example.com/sparql", "ep", "kw"],
            ],
        )
        result = load_sparql_endpoints(path)
        assert "mydb_name" in result

    def test_multiple_rows(self, tmp_path: Path) -> None:
        """Multiple CSV rows produce multiple dictionary entries."""
        path = _write_csv(
            tmp_path,
            [
                ["db1", "https://a.example.com/sparql", "ep1", "kw1"],
                ["db2", "https://b.example.com/sparql", "ep2", "kw2"],
            ],
        )
        result = load_sparql_endpoints(path)
        assert len(result) == 2
        assert "db1" in result
        assert "db2" in result

    def test_empty_csv(self, tmp_path: Path) -> None:
        """An empty CSV (header only) produces an empty dict."""
        path = _write_csv(tmp_path, [])
        result = load_sparql_endpoints(path)
        assert result == {}


# ---------------------------------------------------------------------------
# resolve_endpoint_url
# ---------------------------------------------------------------------------


class TestResolveEndpointUrl:
    """Tests for resolve_endpoint_url priority logic and error cases."""

    def test_endpoint_url_has_highest_priority(self) -> None:
        """When endpoint_url is provided, it is returned regardless of other args."""
        url = resolve_endpoint_url(
            database="chembl",
            endpoint_name="ebi",
            endpoint_url="https://custom.example.com/sparql",
        )
        assert url == "https://custom.example.com/sparql"

    def test_endpoint_name_over_database(self) -> None:
        """endpoint_name takes priority over database when endpoint_url is empty."""
        from togo_mcp.server import ENDPOINT_NAME_TO_URL, ENDPOINT_NAMES

        if not ENDPOINT_NAMES:
            pytest.skip("No endpoint names configured")
        ep_name = ENDPOINT_NAMES[0]
        expected_url = ENDPOINT_NAME_TO_URL[ep_name]
        url = resolve_endpoint_url(database="", endpoint_name=ep_name, endpoint_url="")
        assert url == expected_url

    def test_database_fallback(self) -> None:
        """database is used when both endpoint_url and endpoint_name are empty."""
        from togo_mcp.server import SPARQL_ENDPOINT, SPARQL_ENDPOINT_KEYS

        if not SPARQL_ENDPOINT_KEYS:
            pytest.skip("No databases configured")
        db = SPARQL_ENDPOINT_KEYS[0]
        expected_url = SPARQL_ENDPOINT[db]["url"]
        url = resolve_endpoint_url(database=db, endpoint_name="", endpoint_url="")
        assert url == expected_url

    def test_invalid_database_raises(self) -> None:
        """An unknown database raises ValueError."""
        with pytest.raises(ValueError, match="Unknown database"):
            resolve_endpoint_url(database="nonexistent_db_xyz", endpoint_name="", endpoint_url="")

    def test_endpoint_name_as_database_gives_hint(self) -> None:
        """Passing an endpoint_name as database raises with a specific hint."""
        from togo_mcp.server import ENDPOINT_NAMES

        if not ENDPOINT_NAMES:
            pytest.skip("No endpoint names configured")
        with pytest.raises(ValueError, match="is an endpoint_name"):
            resolve_endpoint_url(
                database=ENDPOINT_NAMES[0], endpoint_name="", endpoint_url=""
            )

    def test_invalid_endpoint_name_raises(self) -> None:
        """An unknown endpoint_name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown endpoint_name"):
            resolve_endpoint_url(database="", endpoint_name="nonexistent_ep_xyz", endpoint_url="")

    def test_none_provided_raises(self) -> None:
        """Passing all empty strings raises ValueError."""
        with pytest.raises(ValueError, match="Missing required argument"):
            resolve_endpoint_url(database="", endpoint_name="", endpoint_url="")


# ---------------------------------------------------------------------------
# _ToolCallLogger middleware
# ---------------------------------------------------------------------------


def _build_ctx(tool: str, args: dict | None = None) -> SimpleNamespace:
    """Minimal MiddlewareContext stand-in covering the attrs the logger reads."""
    return SimpleNamespace(
        message=SimpleNamespace(name=tool, arguments=args or {}),
        fastmcp_context=SimpleNamespace(
            session_id="sess-1",
            request_id="req-1",
            origin_request_id=None,
            client_id="client-1",
            transport="stdio",
        ),
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _make_logger(monkeypatch, tmp_path: Path, enabled: bool):
    """Re-import server with TOGOMCP_QUERY_LOG set/unset, return (_ToolCallLogger, log_path)."""
    log_path = tmp_path / "calls.jsonl"
    if enabled:
        monkeypatch.setenv("TOGOMCP_QUERY_LOG", str(log_path))
    else:
        monkeypatch.delenv("TOGOMCP_QUERY_LOG", raising=False)
    import togo_mcp.server as srv
    importlib.reload(srv)
    return srv._ToolCallLogger(), srv, log_path


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


class TestToolCallLogger:
    def test_disabled_short_circuits(self, monkeypatch, tmp_path: Path) -> None:
        mw, _srv, log_path = _make_logger(monkeypatch, tmp_path, enabled=False)
        assert mw._enabled is False

        async def call_next(_ctx):
            return "result"

        out = asyncio.run(mw.on_call_tool(_build_ctx("any_tool"), call_next))
        assert out == "result"
        assert not log_path.exists()

    def test_logs_success(self, monkeypatch, tmp_path: Path) -> None:
        mw, _srv, log_path = _make_logger(monkeypatch, tmp_path, enabled=True)
        assert mw._enabled is True

        async def call_next(_ctx):
            return "ok"

        out = asyncio.run(mw.on_call_tool(_build_ctx("find_databases", {"keywords": ["x"]}), call_next))
        assert out == "ok"

        for h in mw._log.handlers:  # type: ignore[union-attr]
            h.flush()
        records = _read_jsonl(log_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["tool"] == "find_databases"
        assert rec["args"] == {"keywords": ["x"]}
        assert rec["status"] == "ok"
        assert rec["session_id"] == "sess-1"
        assert rec["transport"] == "stdio"
        assert isinstance(rec["elapsed_ms"], (int, float))
        assert "extra" not in rec  # non-SPARQL call

    def test_logs_error(self, monkeypatch, tmp_path: Path) -> None:
        mw, _srv, log_path = _make_logger(monkeypatch, tmp_path, enabled=True)

        async def call_next(_ctx):
            raise ValueError("boom")

        with pytest.raises(ValueError):
            asyncio.run(mw.on_call_tool(_build_ctx("run_sparql"), call_next))

        for h in mw._log.handlers:  # type: ignore[union-attr]
            h.flush()
        rec = _read_jsonl(log_path)[0]
        assert rec["status"] == "error"
        assert rec["error_class"] == "ValueError"
        assert "boom" in rec["error_message"]

    def test_sparql_extra_merged(self, monkeypatch, tmp_path: Path) -> None:
        mw, srv, log_path = _make_logger(monkeypatch, tmp_path, enabled=True)

        async def call_next(_ctx):
            srv._sparql_extra_var.set(
                {"endpoint_url": "https://x/sparql", "sparql_status": "ok", "n_rows": 3}
            )
            return "csv body"

        asyncio.run(mw.on_call_tool(_build_ctx("run_sparql"), call_next))

        for h in mw._log.handlers:  # type: ignore[union-attr]
            h.flush()
        rec = _read_jsonl(log_path)[0]
        assert rec["extra"]["endpoint_url"] == "https://x/sparql"
        assert rec["extra"]["sparql_status"] == "ok"
        assert rec["extra"]["n_rows"] == 3

class TestSparqlHistoryLogger:
    def test_disabled_history_does_not_write_file(self, monkeypatch, tmp_path: Path) -> None:
        srv, history_path = _reload_server_with_sparql_history(monkeypatch, tmp_path, enabled=False)
        fake_client = _FakeSparqlClient(_FakeResponse("x\n1\n", 200))
        monkeypatch.setattr(srv, "_sparql_client", fake_client)

        out = asyncio.run(srv.execute_sparql("SELECT * WHERE { ?s ?p ?o } LIMIT 1", database="uniprot"))

        assert out == "x\n1\n"
        assert not history_path.exists()

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
