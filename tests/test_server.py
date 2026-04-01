"""Tests for togo_mcp.server module."""

import os
import tempfile

import pytest

from togo_mcp.server import load_sparql_endpoints, resolve_endpoint_url


class TestLoadSparqlEndpoints:
    """Tests for the load_sparql_endpoints function."""

    def test_load_sparql_endpoints_parses_csv(self):
        """Verify CSV parsing returns correct structure with normalized keys."""
        csv_content = (
            "database,endpoint_url,endpoint_name,keyword_search_api\n"
            "UniProt,https://example.com/sparql,sib,search_uniprot\n"
            "My-DB,https://example.com/mydb,ebi,search_mydb\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(csv_content)
            f.flush()
            tmp_path = f.name

        try:
            endpoints = load_sparql_endpoints(tmp_path)

            # Key normalization: lowercase, no spaces, no dashes
            assert "uniprot" in endpoints
            assert "mydb" in endpoints  # "My-DB" -> "mydb"

            # Verify structure
            assert endpoints["uniprot"]["url"] == "https://example.com/sparql"
            assert endpoints["uniprot"]["endpoint_name"] == "sib"
            assert endpoints["uniprot"]["keyword_search"] == "search_uniprot"

            assert endpoints["mydb"]["url"] == "https://example.com/mydb"
        finally:
            os.unlink(tmp_path)

    def test_load_sparql_endpoints_key_normalization(self):
        """Verify that keys are lowercased and spaces/dashes are removed."""
        csv_content = (
            "database,endpoint_url,endpoint_name,keyword_search_api\n"
            "Gene Expression,https://example.com/ge,ebi,search_ge\n"
            "ChEMBL-Target,https://example.com/ct,ebi,search_ct\n"
            "UPPER CASE,https://example.com/uc,sib,search_uc\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(csv_content)
            f.flush()
            tmp_path = f.name

        try:
            endpoints = load_sparql_endpoints(tmp_path)

            assert "gene_expression" in endpoints
            assert "chembltarget" in endpoints
            assert "upper_case" in endpoints
        finally:
            os.unlink(tmp_path)


class TestResolveEndpointUrl:
    """Tests for the resolve_endpoint_url function."""

    def test_endpoint_url_has_highest_priority(self):
        """When endpoint_url is provided, it takes priority over everything."""
        result = resolve_endpoint_url(
            dbname="uniprot",
            endpoint_name="sib",
            endpoint_url="https://custom.example.com/sparql",
        )
        assert result == "https://custom.example.com/sparql"

    def test_endpoint_name_has_second_priority(self):
        """When endpoint_url is empty but endpoint_name is given, use endpoint_name."""
        from togo_mcp.server import ENDPOINT_NAME_TO_URL

        # Pick a real endpoint name from the loaded data
        if not ENDPOINT_NAME_TO_URL:
            pytest.skip("No endpoint names loaded")
        ep_name = next(iter(ENDPOINT_NAME_TO_URL))
        expected_url = ENDPOINT_NAME_TO_URL[ep_name]

        result = resolve_endpoint_url(
            dbname="",
            endpoint_name=ep_name,
            endpoint_url="",
        )
        assert result == expected_url

    def test_dbname_has_lowest_priority(self):
        """When only dbname is given, resolve from SPARQL_ENDPOINT."""
        from togo_mcp.server import SPARQL_ENDPOINT

        if not SPARQL_ENDPOINT:
            pytest.skip("No SPARQL endpoints loaded")
        db = next(iter(SPARQL_ENDPOINT))
        expected_url = SPARQL_ENDPOINT[db]["url"]

        result = resolve_endpoint_url(dbname=db, endpoint_name="", endpoint_url="")
        assert result == expected_url

    def test_invalid_dbname_raises_valueerror(self):
        """Unknown dbname raises ValueError with helpful message."""
        with pytest.raises(ValueError, match="Unknown database"):
            resolve_endpoint_url(
                dbname="nonexistent_db_xyz",
                endpoint_name="",
                endpoint_url="",
            )

    def test_invalid_endpoint_name_raises_valueerror(self):
        """Unknown endpoint_name raises ValueError with helpful message."""
        with pytest.raises(ValueError, match="Unknown endpoint name"):
            resolve_endpoint_url(
                dbname="",
                endpoint_name="nonexistent_endpoint_xyz",
                endpoint_url="",
            )

    def test_none_provided_raises_valueerror(self):
        """When all arguments are empty, ValueError is raised."""
        with pytest.raises(ValueError, match="At least one of"):
            resolve_endpoint_url(dbname="", endpoint_name="", endpoint_url="")
