"""Tests for togo_mcp.ncbi_tools module."""

from togo_mcp.ncbi_tools import _validate_query_field_tags


class TestValidateQueryFieldTags:
    """Tests for the _validate_query_field_tags function."""

    def test_no_tags_critical_db(self):
        """Gene db query without field tags: has_issues=True, is_critical=True."""
        result = _validate_query_field_tags("BRCA1 human", "gene")
        assert result["has_issues"] is True
        assert result["is_critical"] is True
        assert result["has_field_tags"] is False

    def test_with_proper_tags(self):
        """Gene db query with proper field tags: has_issues=False."""
        result = _validate_query_field_tags("BRCA1[Gene Name] AND Homo sapiens[Organism]", "gene")
        assert result["has_issues"] is False
        assert result["has_field_tags"] is True

    def test_organism_term_without_tag(self):
        """Detects organism terms like 'human' without [Organism] tag."""
        result = _validate_query_field_tags("BRCA1[Gene Name] AND human", "gene")
        assert result["has_issues"] is True
        # Should mention the untagged organism term
        issues_text = " ".join(result["issues"])
        assert "human" in issues_text.lower()
        assert "[Organism]" in issues_text or "Organism" in issues_text

    def test_non_critical_db_no_tags(self):
        """PubMed db query without field tags: is_critical=False."""
        result = _validate_query_field_tags("CRISPR gene editing", "pubmed")
        assert result["is_critical"] is False

    def test_gene_symbol_without_gene_name_tag(self):
        """Detects uppercase gene symbol-like terms without [Gene Name] tag."""
        result = _validate_query_field_tags("TP53 AND Homo sapiens[Organism]", "gene")
        assert result["has_issues"] is True
        issues_text = " ".join(result["issues"])
        assert "Gene Name" in issues_text

    def test_unknown_db_returns_non_critical(self):
        """Unknown database defaults to non-critical behavior."""
        result = _validate_query_field_tags("some query", "unknown_db")
        assert result["is_critical"] is False
