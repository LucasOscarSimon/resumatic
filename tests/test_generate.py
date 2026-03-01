"""Tests for generate.py — variant rendering and theme resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

import generate


# ---------------------------------------------------------------------------
# Fixture: minimal resume data with tagged bullets
# ---------------------------------------------------------------------------

FIXTURE_DATA = {
    "meta": {
        "name": "TEST USER",
        "title": "Engineer",
        "email": "test@example.com",
        "phone": "+1 555 000 0000",
        "location": "Remote",
        "linkedin": "https://linkedin.com/in/test",
        "open_to": "Full-time",
    },
    "summary": "A test summary.",
    "skills": {"languages": ["Python", "C#"]},
    "experience": [
        {
            "company": "AlphaCorp",
            "role": "Senior Dev",
            "period": "Jan 2023 – Present",
            "location": "Remote",
            "x-tags": ["common"],
            "projects": [],
            "bullets": [
                {"text": "Built a shared library.", "x-tags": ["backend"]},
                {"text": "Wrote C# microservices.", "x-tags": ["backend", "csharp"]},
                {"text": "Built Python ETL.", "x-tags": ["backend", "python"]},
            ],
        },
        {
            "company": "BetaInc",
            "role": "Python Dev",
            "period": "2021 – 2022",
            "location": "NYC",
            "x-tags": ["python"],
            "projects": [],
            "bullets": [
                {"text": "Python-only bullet.", "x-tags": ["backend", "python"]},
            ],
        },
        {
            "company": "HiddenCorp",
            "role": "Old Role",
            "period": "2010",
            "location": "",
            "x-hidden": True,
            "projects": [],
            "bullets": [
                {"text": "Should never appear.", "x-tags": ["backend"]},
            ],
        },
    ],
    "education": [{"institution": "MIT", "credential": "BSc CS", "year": "2020"}],
    "certifications": [],
    "languages": [{"language": "English", "level": "Native"}],
}


TEMPLATE_PATH = Path(__file__).parent.parent / "resume.md.j2"


# ---------------------------------------------------------------------------
# Test: csharp variant filtering
# ---------------------------------------------------------------------------


class TestRenderMarkdownFiltering:
    def test_csharp_includes_csharp_bullet(self):
        md = generate.render_markdown(FIXTURE_DATA, "csharp", TEMPLATE_PATH)
        assert "Wrote C# microservices." in md

    def test_csharp_excludes_python_bullet(self):
        md = generate.render_markdown(FIXTURE_DATA, "csharp", TEMPLATE_PATH)
        assert "Built Python ETL." not in md

    def test_csharp_includes_backend_only_bullet(self):
        """A bullet tagged only ["backend"] appears in all variants."""
        md = generate.render_markdown(FIXTURE_DATA, "csharp", TEMPLATE_PATH)
        assert "Built a shared library." in md

    def test_python_includes_python_bullet(self):
        md = generate.render_markdown(FIXTURE_DATA, "python", TEMPLATE_PATH)
        assert "Built Python ETL." in md

    def test_python_excludes_csharp_bullet(self):
        md = generate.render_markdown(FIXTURE_DATA, "python", TEMPLATE_PATH)
        assert "Wrote C# microservices." not in md

    def test_python_includes_python_only_entry(self):
        """BetaInc entry is tagged [python] — should appear in python variant."""
        md = generate.render_markdown(FIXTURE_DATA, "python", TEMPLATE_PATH)
        assert "BetaInc" in md

    def test_csharp_excludes_python_only_entry(self):
        """BetaInc entry is tagged [python] — should NOT appear in csharp variant."""
        md = generate.render_markdown(FIXTURE_DATA, "csharp", TEMPLATE_PATH)
        assert "BetaInc" not in md

    def test_hidden_entry_excluded(self):
        """Entry with x-hidden: true should not appear in any variant."""
        for variant in ["csharp", "python"]:
            md = generate.render_markdown(FIXTURE_DATA, variant, TEMPLATE_PATH)
            assert "HiddenCorp" not in md
            assert "Should never appear." not in md

    def test_common_entry_appears_in_all(self):
        """AlphaCorp is tagged [common] — appears in all variants."""
        for variant in ["csharp", "python"]:
            md = generate.render_markdown(FIXTURE_DATA, variant, TEMPLATE_PATH)
            assert "AlphaCorp" in md


# ---------------------------------------------------------------------------
# Test: project-level filtering
# ---------------------------------------------------------------------------


FIXTURE_WITH_PROJECTS = {
    "meta": {
        "name": "TEST",
        "title": "Dev",
        "email": "t@t.com",
        "phone": "",
        "location": "",
        "linkedin": "",
        "open_to": "",
    },
    "summary": "Summary.",
    "skills": {},
    "experience": [
        {
            "company": "ProjCorp",
            "role": "Lead",
            "period": "2024",
            "location": "",
            "x-tags": ["common"],
            "projects": [
                {
                    "name": "CSharp Project",
                    "period": "2024",
                    "x-tags": ["csharp"],
                    "bullets": [
                        {"text": "C# project bullet.", "x-tags": ["csharp"]},
                    ],
                },
                {
                    "name": "Python Project",
                    "period": "2024",
                    "x-tags": ["python"],
                    "bullets": [
                        {"text": "Python project bullet.", "x-tags": ["python"]},
                    ],
                },
            ],
            "bullets": [],
        },
    ],
    "education": [],
    "certifications": [],
    "languages": [],
}


class TestProjectFiltering:
    def test_csharp_variant_shows_csharp_project(self):
        md = generate.render_markdown(FIXTURE_WITH_PROJECTS, "csharp", TEMPLATE_PATH)
        assert "CSharp Project" in md
        assert "C# project bullet." in md

    def test_csharp_variant_hides_python_project(self):
        md = generate.render_markdown(FIXTURE_WITH_PROJECTS, "csharp", TEMPLATE_PATH)
        assert "Python Project" not in md
        assert "Python project bullet." not in md

    def test_python_variant_shows_python_project(self):
        md = generate.render_markdown(FIXTURE_WITH_PROJECTS, "python", TEMPLATE_PATH)
        assert "Python Project" in md

    def test_python_variant_hides_csharp_project(self):
        md = generate.render_markdown(FIXTURE_WITH_PROJECTS, "python", TEMPLATE_PATH)
        assert "CSharp Project" not in md


# ---------------------------------------------------------------------------
# Test: resolve_theme
# ---------------------------------------------------------------------------


class TestResolveTheme:
    def test_valid_theme_returns_path(self):
        path = generate.resolve_theme("classic")
        assert path.exists()
        assert path.name == "classic.css"

    def test_invalid_theme_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            generate.resolve_theme("nonexistent_theme")
        assert exc_info.value.code == 1
