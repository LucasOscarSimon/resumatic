"""Unit tests for scripts/build_yaml_from_source.py"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml

import build_yaml_from_source as bys


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_MD = """\
**JOHN DOE**

*Software Engineer*

john@example.com \\| +1 555 123 4567 \\| New York, NY

*Open to: Full-time Remote*

**[PROFESSIONAL SUMMARY]{.smallcaps}**

Experienced software engineer specialising in C# .NET and Python backends.

**[WORK EXPERIENCE]{.smallcaps}**

**Acme Corp** \\| Senior Software Engineer*January 2022 -- Present \\| Remote*

- Designed event-driven microservices using C# and Azure Service Bus.
- Built ETL pipelines in Python with Azure Data Lake.
- Wrote xUnit tests achieving 90% code coverage.

**Globex Systems** \\| Python Engineer*March 2020 -- December 2021 \\| New York, NY*

- Built REST API integrations with Python.
- Implemented Azure Functions for event-driven processing.

**[SKILLS]{.smallcaps}**

**Languages:** C#, Python, SQL
**Cloud:** Azure, Azure Functions, Azure Service Bus

**[EDUCATION]{.smallcaps}**

Universidade Federal --- Bachelor of Science in Computer Science --- 2019

**[LANGUAGES]{.smallcaps}**

English (Fluent) • Portuguese (Native)
"""


@pytest.fixture
def sample_md_text() -> str:
    return SAMPLE_MD


@pytest.fixture
def sample_parsed_data() -> dict:
    return {
        "meta": {
            "name": "JOHN DOE",
            "title": "Software Engineer",
            "email": "john@example.com",
            "phone": "+1 555 123 4567",
            "location": "New York, NY",
            "linkedin": "",
            "open_to": "Full-time Remote",
        },
        "summary": "Experienced software engineer specialising in C# .NET and Python backends.",
        "skills": {
            "languages": ["C#", "Python", "SQL"],
            "backend": [],
            "cloud": ["Azure", "Azure Functions", "Azure Service Bus"],
            "messaging": [],
            "databases": [],
            "identity": [],
            "observability": [],
            "tools": [],
        },
        "experience": [
            {
                "company": "Acme Corp",
                "role": "Senior Software Engineer",
                "period": "January 2022 \u2013 Present",
                "location": "Remote",
                "note": "",
                "projects": [],
                "bullets": [
                    {
                        "text": "Designed event-driven microservices using C# and Azure Service Bus.",
                        "x-tags": bys.FlowList(["backend", "csharp", "azure", "integration"]),
                    },
                    {
                        "text": "Built ETL pipelines in Python with Azure Data Lake.",
                        "x-tags": bys.FlowList(["backend", "python", "azure", "data"]),
                    },
                    {
                        "text": "Wrote xUnit tests achieving 90% code coverage.",
                        "x-tags": bys.FlowList(["backend", "testing"]),
                    },
                ],
            },
            {
                "company": "Globex Systems",
                "role": "Python Engineer",
                "period": "March 2020 \u2013 December 2021",
                "location": "New York, NY",
                "note": "",
                "projects": [],
                "bullets": [
                    {
                        "text": "Built REST API integrations with Python.",
                        "x-tags": bys.FlowList(["backend", "python", "integration"]),
                    },
                    {
                        "text": "Implemented Azure Functions for event-driven processing.",
                        "x-tags": bys.FlowList(["backend", "azure", "integration"]),
                    },
                ],
            },
        ],
        "education": [
            {
                "institution": "Universidade Federal",
                "credential": "Bachelor of Science in Computer Science",
                "year": "2019",
            }
        ],
        "certifications": [],
        "languages": [
            {"language": "English", "level": "Fluent"},
            {"language": "Portuguese", "level": "Native"},
        ],
    }


# ---------------------------------------------------------------------------
# Test 1: Hash check — up to date → exits early
# ---------------------------------------------------------------------------


def test_up_to_date_exits_early(tmp_path):
    """When the hash matches, main() skips regeneration and exits with code 0."""
    source_path = tmp_path / "resume.docx"
    source_path.write_bytes(b"fake content")

    with (
        patch("sys.argv", ["prog"]),
        patch.object(bys, "resolve_source", return_value=source_path),
        patch.object(bys, "is_up_to_date", return_value=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        bys.main()

    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Test 2: Hash check — stale → proceeds to extraction
# ---------------------------------------------------------------------------


def test_stale_hash_proceeds(tmp_path, sample_parsed_data):
    """When the hash is stale, main() calls extract_text and does not exit early."""
    source_path = tmp_path / "resume.docx"
    source_path.write_bytes(b"fake")
    extract_mock = Mock(return_value="some extracted text")

    with (
        patch("sys.argv", ["prog", "--dry-run"]),
        patch.object(bys, "resolve_source", return_value=source_path),
        patch.object(bys, "is_up_to_date", return_value=False),
        patch.object(bys, "extract_text", extract_mock),
        patch.object(bys, "parse_markdown", return_value=sample_parsed_data),
        patch.object(bys, "check_confidence", return_value=(False, "")),
    ):
        bys.main()  # must NOT raise SystemExit

    extract_mock.assert_called_once_with(source_path)


# ---------------------------------------------------------------------------
# Test 3: Source auto-detection (docx / pdf / md / none)
# ---------------------------------------------------------------------------


class TestResolveSource:
    def test_docx_preferred_when_all_present(self, tmp_path):
        docx = tmp_path / "resume.docx"
        pdf = tmp_path / "resume.pdf"
        md = tmp_path / "resume.md"
        for p in (docx, pdf, md):
            p.write_bytes(b"x")

        candidates = [("docx", docx), ("pdf", pdf), ("md", md)]
        with patch.object(bys, "SOURCE_CANDIDATES", candidates):
            result = bys.resolve_source(None)
        assert result == docx

    def test_falls_back_to_pdf_without_docx(self, tmp_path):
        pdf = tmp_path / "resume.pdf"
        md = tmp_path / "resume.md"
        pdf.write_bytes(b"x")
        md.write_bytes(b"x")

        candidates = [("docx", tmp_path / "resume.docx"), ("pdf", pdf), ("md", md)]
        with patch.object(bys, "SOURCE_CANDIDATES", candidates):
            result = bys.resolve_source(None)
        assert result == pdf

    def test_falls_back_to_md_without_docx_or_pdf(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_bytes(b"x")

        candidates = [
            ("docx", tmp_path / "resume.docx"),
            ("pdf", tmp_path / "resume.pdf"),
            ("md", md),
        ]
        with patch.object(bys, "SOURCE_CANDIDATES", candidates):
            result = bys.resolve_source(None)
        assert result == md

    def test_no_source_file_exits(self, tmp_path):
        candidates = [
            ("docx", tmp_path / "resume.docx"),
            ("pdf", tmp_path / "resume.pdf"),
            ("md", tmp_path / "resume.md"),
        ]
        with (
            patch.object(bys, "SOURCE_CANDIDATES", candidates),
            pytest.raises(SystemExit) as exc_info,
        ):
            bys.resolve_source(None)
        assert exc_info.value.code == 1

    def test_explicit_source_missing_exits(self, tmp_path):
        with (
            patch.object(bys, "ROOT", tmp_path),
            pytest.raises(SystemExit) as exc_info,
        ):
            bys.resolve_source("docx")  # resume.docx does not exist in tmp_path
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test 4: DOCX extraction via pandoc mock
# ---------------------------------------------------------------------------


def test_extract_text_docx_via_pandoc(tmp_path):
    """extract_text_docx returns pandoc stdout when pandoc exits 0."""
    docx = tmp_path / "resume.docx"
    docx.write_bytes(b"fake")

    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "# JOHN DOE\n\nSoftware Engineer\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        text = bys.extract_text_docx(docx)

    mock_run.assert_called_once()
    assert text == "# JOHN DOE\n\nSoftware Engineer\n"


# ---------------------------------------------------------------------------
# Test 5: Pandoc unavailable → python-docx fallback
# ---------------------------------------------------------------------------


def test_extract_text_docx_python_docx_fallback(tmp_path):
    """When pandoc is not found, extract_text_docx falls back to python-docx."""
    docx = tmp_path / "resume.docx"
    docx.write_bytes(b"fake")

    mock_doc = Mock()
    mock_doc.paragraphs = [
        Mock(text="John Doe"),
        Mock(text="Software Engineer"),
        Mock(text=""),  # empty paragraph — should be filtered out
    ]

    mock_docx_module = MagicMock()
    mock_docx_module.Document.return_value = mock_doc

    with (
        patch("subprocess.run", side_effect=FileNotFoundError),
        patch.dict("sys.modules", {"docx": mock_docx_module}),
    ):
        text = bys.extract_text_docx(docx)

    assert text == "John Doe\nSoftware Engineer"


# ---------------------------------------------------------------------------
# Test 6: YAML fence stripping
# ---------------------------------------------------------------------------


class TestStripYamlFences:
    def test_removes_yaml_fence(self):
        raw = "```yaml\nmeta:\n  name: John\n```"
        assert bys.strip_yaml_fences(raw) == "meta:\n  name: John"

    def test_removes_plain_fence(self):
        raw = "```\nmeta:\n  name: John\n```"
        assert bys.strip_yaml_fences(raw) == "meta:\n  name: John"

    def test_passthrough_unfenced_yaml(self):
        raw = "meta:\n  name: John"
        assert bys.strip_yaml_fences(raw) == raw

    def test_strips_surrounding_whitespace(self):
        raw = "  ```yaml\nmeta:\n  name: John\n```  "
        assert bys.strip_yaml_fences(raw) == "meta:\n  name: John"


# ---------------------------------------------------------------------------
# Test 7: Invalid YAML from model → error exit
# ---------------------------------------------------------------------------


def test_call_claude_invalid_yaml_exits():
    """When the model returns a non-dict YAML value, call_claude exits with code 1."""

    # Define concrete exception classes so `except anthropic.XxxError:` works
    class _AuthErr(Exception):
        pass

    class _ConnErr(Exception):
        pass

    class _StatusErr(Exception):
        pass

    # "just a plain string" → yaml.safe_load returns a str, not a dict → sys.exit(1)
    mock_stream = MagicMock()
    mock_stream.__enter__.return_value = mock_stream
    mock_stream.text_stream = iter(["just a plain string"])
    mock_final = Mock()
    mock_final.usage.output_tokens = 5
    mock_stream.get_final_message.return_value = mock_final

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_stream

    mock_anthropic = MagicMock()
    mock_anthropic.AuthenticationError = _AuthErr
    mock_anthropic.APIConnectionError = _ConnErr
    mock_anthropic.APIStatusError = _StatusErr
    mock_anthropic.Anthropic.return_value = mock_client

    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
        patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        pytest.raises(SystemExit) as exc_info,
    ):
        bys.call_claude("some cv text", "claude-sonnet-4-6")

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test 8: Confidence — high → Claude NOT called
# ---------------------------------------------------------------------------


def test_high_confidence_skips_claude(tmp_path, sample_parsed_data):
    """When check_confidence returns False, call_claude must not be invoked."""
    source_path = tmp_path / "resume.docx"
    source_path.write_bytes(b"fake")

    with (
        patch("sys.argv", ["prog", "--dry-run"]),
        patch.object(bys, "resolve_source", return_value=source_path),
        patch.object(bys, "is_up_to_date", return_value=False),
        patch.object(bys, "extract_text", return_value="some text"),
        patch.object(bys, "parse_markdown", return_value=sample_parsed_data),
        patch.object(bys, "check_confidence", return_value=(False, "")),
        patch.object(bys, "call_claude") as mock_claude,
    ):
        bys.main()

    mock_claude.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9: Confidence — low → Claude IS called as fallback
# ---------------------------------------------------------------------------


def test_low_confidence_calls_claude(tmp_path, sample_parsed_data):
    """When check_confidence returns True, call_claude is invoked as fallback."""
    source_path = tmp_path / "resume.docx"
    source_path.write_bytes(b"fake")
    reason = "only 1 experience entry found"

    with (
        patch("sys.argv", ["prog", "--dry-run"]),
        patch.object(bys, "resolve_source", return_value=source_path),
        patch.object(bys, "is_up_to_date", return_value=False),
        patch.object(bys, "extract_text", return_value="some cv text"),
        patch.object(bys, "parse_markdown", return_value={}),
        patch.object(bys, "check_confidence", return_value=(True, reason)),
        patch.object(bys, "call_claude", return_value=sample_parsed_data) as mock_claude,
    ):
        bys.main()

    mock_claude.assert_called_once_with("some cv text", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Test 10: --dry-run → no files written
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_files(tmp_path, sample_parsed_data):
    """With --dry-run, neither resume.yaml nor the hash file are written."""
    source_path = tmp_path / "resume.docx"
    source_path.write_bytes(b"fake")
    output_yaml = tmp_path / "resume.yaml"
    hash_file = tmp_path / ".resume_source.hash"

    with (
        patch("sys.argv", ["prog", "--dry-run"]),
        patch.object(bys, "resolve_source", return_value=source_path),
        patch.object(bys, "is_up_to_date", return_value=False),
        patch.object(bys, "extract_text", return_value="some text"),
        patch.object(bys, "parse_markdown", return_value=sample_parsed_data),
        patch.object(bys, "check_confidence", return_value=(False, "")),
        patch.object(bys, "OUTPUT_FILE", output_yaml),
        patch.object(bys, "HASH_FILE", hash_file),
    ):
        bys.main()

    assert not output_yaml.exists()
    assert not hash_file.exists()


# ---------------------------------------------------------------------------
# Test 11: x-tags keyword matching
# ---------------------------------------------------------------------------


class TestAssignXtags:
    def test_always_includes_backend(self):
        tags = bys.assign_xtags("Deployed a Docker container.")
        assert "backend" in tags

    def test_csharp_matched(self):
        tags = bys.assign_xtags("Built APIs with C# and Entity Framework.")
        assert "csharp" in tags
        assert "backend" in tags

    def test_azure_functions_matched(self):
        tags = bys.assign_xtags("Deployed Azure Functions for serverless processing.")
        assert "azure" in tags
        assert "backend" in tags

    def test_csharp_and_azure_together(self):
        tags = bys.assign_xtags("C# services running on Azure with REST API.")
        assert "csharp" in tags
        assert "azure" in tags
        assert "integration" in tags  # REST keyword
        assert "backend" in tags

    def test_python_not_tagged_as_csharp(self):
        tags = bys.assign_xtags("Processed data with Python pandas.")
        assert "python" in tags
        assert "csharp" not in tags

    def test_testing_keywords(self):
        tags = bys.assign_xtags("Wrote xUnit and Moq tests to reach 90% unit test coverage.")
        assert "testing" in tags

    def test_data_keywords(self):
        tags = bys.assign_xtags("Built ETL pipeline reading Parquet files from MicroStrategy.")
        assert "data" in tags
