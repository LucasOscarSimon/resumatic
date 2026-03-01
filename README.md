# resumatic

Single-source CV compiler. One YAML file, multiple role-targeted PDFs.

## Problem

Maintaining separate CV documents per role (e.g. C#/.NET vs Python/data) creates a
fan-out maintenance problem: every phrasing improvement, typo fix, or new skill must be
applied to every file individually. Content drifts. Copy-paste errors accumulate. The cost
is highest exactly when you can least afford it — during an active job search.

**resumatic** treats the CV as infrastructure: a single YAML source of truth with content
tagged by role variant, compiled deterministically into targeted PDFs.

## Architecture

```
resume.yaml                    # single source — every job, bullet, and skill
    └── x-tags: [csharp, python, common]

generate.py                    # reads YAML, renders via Jinja2, calls Pandoc
resume.md.j2                   # filtering logic — which bullets survive per variant
style.css                      # WeasyPrint CSS — ATS-safe typography, @page margins

build/                         # generated artifacts (gitignored)
    resume-csharp.md
    resume-python.md
    resume-csharp.pdf
    resume-python.pdf
```

**Pipeline per variant:**

```
resume.yaml
    │
    ▼  (pyyaml)
Python dict
    │
    ▼  (Jinja2 + resume.md.j2)
build/resume-{variant}.md
    │
    ▼  (pandoc --pdf-engine=weasyprint + style.css)
build/resume-{variant}.pdf
```

WeasyPrint is used over LaTeX because it produces clean HTML-to-vector PDF streams
that ATS parsers can extract text from reliably. CSS `@page` rules handle margins and
pagination without preamble boilerplate.

## Quickstart

**System dependencies** (Fedora/RHEL):

```bash
sudo dnf install pandoc weasyprint
```

**Clone and set up:**

```bash
git clone <repo-url> resumatic
cd resumatic
python3 -m venv .venv
source .venv/bin/activate
make install
```

**Build all variants:**

```bash
make all
# → build/resume-csharp.pdf
# → build/resume-python.pdf
```

**Build a single variant:**

```bash
make csharp
make python
# or
make variant V=csharp
```

**Lint the YAML source:**

```bash
make lint
```

**Remove generated artifacts:**

```bash
make clean
```

## Bootstrapping resume.yaml

`resume.yaml` and all source documents are gitignored — personal data is never committed
to the repository. Use this workflow to generate `resume.yaml` locally from your CV.

### Setup

1. Place your CV in the project root as one of:
   - `resume.docx` — preferred; best text fidelity via pandoc
   - `resume.pdf`  — fallback (e.g. downloaded from a job site)
   - `resume.md`   — rare case

2. Set your Anthropic API key in the environment:

   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

3. Generate `resume.yaml`:

   ```bash
   make import
   ```

   The script auto-detects the source file (docx → pdf → md), extracts its text,
   and calls the Claude API to convert it into Resumatic-schema YAML with `x-tags`
   applied to every bullet. If the source file hasn't changed since the last run
   (checked via SHA-256 hash stored in `.resume_source.hash`), the step is skipped.

4. To force regeneration regardless of the hash:

   ```bash
   make import-force
   ```

5. Build all PDF variants:

   ```bash
   make all
   ```

### Manual invocation

```bash
python scripts/build_yaml_from_source.py [OPTIONS]

Options:
  --source {docx,pdf,md}   Force a specific source format
  --force                  Regenerate even if already up to date
  --dry-run                Print generated YAML to stdout, do not write files
  --model MODEL            Claude model (default: claude-sonnet-4-6)
```

## Variant tagging schema

Tags are declared as `x-tags` arrays on work entries, individual bullet points, and skill
keywords. The filtering rule is:

- A node with **no `x-tags`** is treated as `[common]` and appears in every variant.
- A bullet with **no `x-tags`** inherits its parent entry's tags.
- A node is included when `target_tag in x-tags` **or** `"common" in x-tags`.

### Example

```yaml
work:
  - company: Acme Corp
    position: Senior Software Engineer
    startDate: "2022-01"
    endDate: "2024-12"
    x-tags: [csharp]           # entire entry is csharp-only
    highlights:
      - bullet: Designed event-driven microservices on Azure Service Bus.
        x-tags: [csharp]       # explicit — csharp variant only
      - bullet: Improved CI/CD pipeline build times by 40%.
        x-tags: [common]       # appears in every variant

  - company: Globex Systems
    position: Python Engineer
    startDate: "2020-06"
    endDate: "2022-01"
    x-tags: [python]           # entire entry is python-only
    highlights:
      - bullet: Built ETL pipelines using Python and Apache Airflow.
        x-tags: [python]
      - bullet: Reduced data-processing latency by 60% via async refactor.
        x-tags: [common]
```

**`make csharp` produces:**

> **Senior Software Engineer — Acme Corp**
> - Designed event-driven microservices on Azure Service Bus.
> - Improved CI/CD pipeline build times by 40%.

**`make python` produces:**

> **Python Engineer — Globex Systems**
> - Built ETL pipelines using Python and Apache Airflow.
> - Reduced data-processing latency by 60% via async refactor.

### Suppressing legacy entries

Set `x-hidden: true` on an entry to exclude it from all variants without deleting it:

```yaml
  - company: Old Employer
    x-hidden: true
    # ... preserved for reference, never compiled
```

## Defined variants

| Variant | Focus |
|---------|-------|
| `csharp` | C#/.NET, Azure, event-driven architecture |
| `python` | Python, system integration, data pipelines |
| `common` | Appears in all variants (contact, education, shared achievements) |

## File reference

| File | Role |
|------|------|
| `resume.yaml` | Canonical source — edit this |
| `generate.py` | Orchestration script |
| `resume.md.j2` | Jinja2 template with filtering logic |
| `style.css` | WeasyPrint stylesheet |
| `Makefile` | Task runner |
| `requirements.txt` | Python dependencies (`pyyaml`, `jinja2`) |
