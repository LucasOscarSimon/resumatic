# CV Pipeline — Project Context

## Problem Being Solved

Software engineers maintaining multiple tailored CV variants (e.g., C#/.NET-focused,
Python/system-integration-focused) face a fan-out maintenance problem: shared content —
skills, experience bullets, project descriptions — must be manually duplicated and kept in sync
across separate documents. Every typo fix, improved phrasing, or new skill requires tracking
down and editing every file individually. This introduces data drift, copy-paste errors, and
significant overhead exactly when the cost is highest (active job search).

The solution is to treat the CV as infrastructure: a single YAML source of truth with content
tagged by role variant, compiled deterministically into targeted Markdown files and then into
ATS-compliant PDFs via Pandoc + WeasyPrint. Git tracks the source. Generated artifacts are
never committed.

## Architecture

```
resume.yaml          # Single canonical source — the database
  └── x-tags: [csharp, python, common]   # per-entry and per-bullet tagging

generate.py          # Orchestration script — reads YAML, renders via Jinja2
resume.md.j2         # Jinja2 template — conditional filtering logic lives here
style.css            # WeasyPrint CSS — ATS-safe typography, @page rules

build/               # Generated artifacts — gitignored
  resume-csharp.md
  resume-python.md
  resume-csharp.pdf
  resume-python.pdf

Makefile             # Task runner — `make all`, `make clean`, `make variant V=csharp`
.github/workflows/   # Optional CI — builds and publishes PDFs as release assets
```

## Key Design Decisions

**YAML schema**: Extends JSON Resume with `x-tags` arrays on both entry level (entire job block)
and granular level (individual bullet points). `x-hidden: true` suppresses legacy entries
without deleting them. If no `x-tags` is present on a node, it appears in all variants.

**Tag inheritance**: A bullet with no `x-tags` inherits from its parent entry's tags. An entry
with no `x-tags` is treated as `[common]` and appears in every variant.

**Jinja2 filtering**: The template handles intersection logic — a bullet is included if
`target_tag in bullet.get('x-tags', parent_tags)`. This keeps the Python script minimal
and the filtering logic inspectable in the template itself.

**PDF engine**: Pandoc → WeasyPrint (not LaTeX, not wkhtmltopdf). WeasyPrint produces
clean HTML-to-vector PDF streams that ATS parsers can extract text from reliably. LaTeX
produces aesthetically superior output but can generate fragmented text streams that break
ATS extraction. CSS `@page` rules handle margins and pagination.

**Git strategy**: Only `resume.yaml`, `resume.md.j2`, `style.css`, `generate.py`, and `Makefile`
are committed. The `build/` directory is in `.gitignore`. Pre-commit hooks lint YAML only —
they never compile PDFs (binary blobs bloat Git history permanently).

## Stack

- Python 3.x, pyyaml, jinja2
- Pandoc + WeasyPrint (Fedora: `sudo dnf install pandoc weasyprint`)
- Git, Make (or justfile)
- Optional: GitHub Actions for CI artifact publishing

## Variants Defined

- `csharp` — C#/.NET, Azure, event-driven architecture, DCE platform work
- `python` — Python, system integration, data pipelines, scripting
- `common` — appears in all variants (contact info, education, shared achievements)

## Research Findings (Summary)

Investigation confirmed no existing tool implements per-bullet variant tagging with a
Python-native pipeline and Markdown intermediate layer. The closest tools:

- **RenderCV** (github.com/rendercv/rendercv): 15k+ stars, active, but uses Typst engine
  and bypasses Markdown. Draft PR #654 adds tag filtering but is unmerged as of Feb 2026.
- **resume-remixer**: Uses `x-hidden` boolean per-entry filtering via Lua + Pandoc.
  Closest architectural match but no role-based tag arrays and not Python-native.
- **JSON Resume standard**: Explicitly advises separate files per variant — not single-source.

Verdict: build from scratch using pyyaml + jinja2 + pandoc/weasyprint. Estimated ~50-80
lines of Python. Total dependency footprint: two dnf packages + two pip packages.

## Subagent Roles

| Subagent | Invoked For |
|---|---|
| `cv-schema-validator` | Validating and linting resume.yaml structure |
| `cv-variant-builder` | Implementing or modifying generate.py and resume.md.j2 |
| `cv-pdf-pipeline` | Configuring Pandoc + WeasyPrint, style.css, ATS compliance |
| `cv-content-editor` | Editing content in resume.yaml — bullets, phrasing, tags |
