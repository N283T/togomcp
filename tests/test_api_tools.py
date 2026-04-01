"""Tests for togo_mcp.api_tools module."""

import json

import httpx
import pytest
import respx

from togo_mcp.api_tools import (
    search_chembl_target,
    search_pdb_entity,
    search_rhea_entity,
    search_uniprot_entity,
)


@pytest.mark.asyncio
class TestSearchUniprotEntity:
    """Tests for the search_uniprot_entity function."""

    @respx.mock
    async def test_search_uniprot_entity(self):
        """Mock UniProt API and verify TSV response is returned."""
        tsv_response = (
            "Entry\tProtein names\tOrganism\n"
            "P04637\tCellular tumor antigen p53\tHomo sapiens (Human)\n"
        )
        respx.get("https://rest.uniprot.org/uniprotkb/search").mock(
            return_value=httpx.Response(200, text=tsv_response)
        )

        result = await search_uniprot_entity("TP53", limit=5)

        assert "P04637" in result
        assert "Cellular tumor antigen p53" in result
        assert "Homo sapiens" in result


@pytest.mark.asyncio
class TestSearchChemblTarget:
    """Tests for the search_chembl_target function."""

    @respx.mock
    async def test_search_chembl_target(self):
        """Mock ChEMBL API and verify parsed dict structure."""
        mock_response = {
            "page_meta": {"total_count": 1},
            "targets": [
                {
                    "target_chembl_id": "CHEMBL203",
                    "pref_name": "Epidermal growth factor receptor",
                    "organism": "Homo sapiens",
                    "target_type": "SINGLE PROTEIN",
                    "score": 15.0,
                }
            ],
        }
        respx.get("https://www.ebi.ac.uk/chembl/api/data/target/search.json").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        result = await search_chembl_target("EGFR", limit=5)

        assert result["total_count"] == 1
        assert len(result["results"]) == 1
        target = result["results"][0]
        assert target["chembl_id"] == "CHEMBL203"
        assert target["name"] == "Epidermal growth factor receptor"
        assert target["organism"] == "Homo sapiens"
        assert target["type"] == "SINGLE PROTEIN"
        assert target["score"] == 15.0


@pytest.mark.asyncio
class TestSearchPdbEntity:
    """Tests for the search_pdb_entity function."""

    @respx.mock
    async def test_search_pdb_entity(self):
        """Mock PDBj API and verify JSON parsing and limit application."""
        mock_response = {
            "total": 100,
            "results": [
                ["1ABC", "Crystal structure of protein X"],
                ["2DEF", "NMR structure of peptide Y"],
                ["3GHI", "Cryo-EM structure of complex Z"],
            ],
        }
        respx.get("https://pdbj.org/rest/newweb/search/pdb").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        result_str = await search_pdb_entity("pdb", "kinase", limit=2)
        result = json.loads(result_str)

        assert result["total"] == 100
        # limit=2 should restrict to first 2 results
        assert len(result["results"]) == 2
        assert result["results"][0] == {"1ABC": "Crystal structure of protein X"}
        assert result["results"][1] == {"2DEF": "NMR structure of peptide Y"}


@pytest.mark.asyncio
class TestSearchRheaEntity:
    """Tests for the search_rhea_entity function."""

    @respx.mock
    async def test_search_rhea_entity(self):
        """Mock Rhea API and verify TSV parsing into list of dicts."""
        tsv_response = (
            "Rhea ID\tEquation\n"
            "RHEA:10000\tATP + H2O = ADP + phosphate\n"
            "RHEA:10004\tGTP + H2O = GDP + phosphate\n"
        )
        respx.get("https://www.rhea-db.org/rhea").mock(
            return_value=httpx.Response(200, text=tsv_response)
        )

        result = await search_rhea_entity("ATP", limit=10)

        assert len(result) == 2
        assert result[0]["rhea_id"] == "RHEA:10000"
        assert result[0]["equation"] == "ATP + H2O = ADP + phosphate"
        assert result[1]["rhea_id"] == "RHEA:10004"

    @respx.mock
    async def test_search_rhea_entity_empty_response(self):
        """Empty TSV response returns empty list."""
        respx.get("https://www.rhea-db.org/rhea").mock(
            return_value=httpx.Response(200, text="Rhea ID\tEquation\n")
        )

        result = await search_rhea_entity("nonexistent_compound", limit=10)
        assert result == []
