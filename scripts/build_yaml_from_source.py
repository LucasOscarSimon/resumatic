#!/usr/bin/env python3
"""Generate resume.yaml from a source CV document.

Primary path: deterministic Markdown parsing + keyword-based x-tag assignment.
Fallback to Claude API when parser confidence is low or --ai is passed.

Confidence thresholds (any one triggers the Claude fallback):
  - fewer than 2 experience entries found
  - summary is empty
  - fewer than 5 bullets across all experience
  - PDF source where pdftotext avg line length < 20 chars (scrambled output)
"""

from __future__ import annotations

import abc
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
HASH_FILE = ROOT / ".resume_source.hash"
OUTPUT_FILE = ROOT / "resume.yaml"

SOURCE_CANDIDATES: list[tuple[str, Path]] = [
    ("docx", ROOT / "resume.docx"),
    ("pdf", ROOT / "resume.pdf"),
    ("md", ROOT / "resume.md"),
]

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

# Pandoc smallcaps section heading: **[SECTION NAME]{.smallcaps}**
_SMALLCAPS = re.compile(r"^\*\*\[([^\]]+)\]\{\.smallcaps\}\*\*")

# Standard markdown headings
_H1 = re.compile(r"^#\s+(.+)")
_H2 = re.compile(r"^##\s+(.+)")
_H3 = re.compile(r"^###\s+(.+)")

# Bullet list item
_BULLET = re.compile(r"^[-*+]\s+(.+)")

# Inline italic span (single *, not double)
_ITALIC_SPAN = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")

# Contact info
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_LINKEDIN = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[^\s)>\]\"]+")

# Date range — handles pandoc's -- for en-dash, and real –/—
_DATE_RANGE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{4}"
    r"\s*(?:–|—|--|to)\s*"
    r"(?:Present|Current|Now"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{4}"
    r"|\d{4})",
    re.I,
)

# ---------------------------------------------------------------------------
# Section name → canonical key
# ---------------------------------------------------------------------------

_SECTION_MAP: dict[str, str] = {
    "professional summary": "summary",
    "summary": "summary",
    "profile": "summary",
    "about": "summary",
    "about me": "summary",
    "objective": "summary",
    "professional profile": "summary",
    "technical skills": "skills",
    "skills": "skills",
    "core skills": "skills",
    "key skills": "skills",
    "competencies": "skills",
    "work experience": "experience",
    "experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "career history": "experience",
    "education": "education",
    "education & certifications": "education",
    "education and certifications": "education",
    "certifications": "certifications",
    "certificates": "certifications",
    "languages": "languages",
    "language skills": "languages",
    "language proficiency": "languages",
}

# ---------------------------------------------------------------------------
# x-tag keyword matching
# ---------------------------------------------------------------------------

_XTAG_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bC#|\bASP\.NET\b|\.NET\b|Entity Framework\b|EF Core\b"), "csharp"),
    (re.compile(r"\bPython\b|pandas\b|PyArrow\b|mstrio\b", re.I), "python"),
    (re.compile(r"\bAzure\b", re.I), "azure"),
    (re.compile(r"\bETL\b|Parquet\b|MicroStrategy\b|eFront\b|data pipeline", re.I), "data"),
    (re.compile(r"\bREST\b|\bService Bus\b|Storage Queue|event.driven\b", re.I), "integration"),
    (re.compile(r"\bxUnit\b|\bMoq\b|unit test|test coverage", re.I), "testing"),
]


def assign_xtags(text: str) -> list[str]:
    tags = ["backend"]
    for pat, tag in _XTAG_RULES:
        if pat.search(text):
            tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# Bullet tagger — provider interface
# ---------------------------------------------------------------------------

_TAGGER_MODEL = "claude-haiku-4-5-20251001"

_TAGGER_SYSTEM = """\
You are a CV tagging assistant. Given a resume bullet point, assign one or more \
tags from this taxonomy:
- "python": the work itself is Python-specific (not just mentioned in stack)
- "csharp": the work itself is C#/.NET-specific
- "backend": general backend work, language-agnostic
- "common": appears in all variants regardless of target
Respond with only a JSON array of tags, e.g. ["backend"] or ["csharp"].
A bullet can have multiple tags if genuinely applicable to both variants.\
"""


class BulletTagger(abc.ABC):
    """Abstract base class for semantic bullet point taggers."""

    @abc.abstractmethod
    def tag(self, bullet_text: str, context: dict) -> list[str]:
        """Return a list of x-tags for the given bullet.

        context keys: company, role, period
        Returns [] to signal "no tags" — caller applies ["backend"] as fallback.
        """


class NullTagger(BulletTagger):
    """No-op tagger — returns [] for all bullets (triggers ["backend"] fallback)."""

    def tag(self, bullet_text: str, context: dict) -> list[str]:
        return []


class ClaudeTagger(BulletTagger):
    """Semantic tagger that calls claude-haiku to assign x-tags per bullet."""

    def __init__(self) -> None:
        try:
            import anthropic as _mod

            self._mod = _mod
        except ImportError:
            self._mod = None
            self._client = None
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._client = self._mod.Anthropic(api_key=api_key) if api_key else None

    def tag(self, bullet_text: str, context: dict) -> list[str]:
        if self._client is None:
            return []
        user_msg = (
            f"Company: {context.get('company', '')}, Role: {context.get('role', '')}\n"
            f"Bullet: {bullet_text}"
        )
        try:
            resp = self._client.messages.create(
                model=_TAGGER_MODEL,
                max_tokens=64,
                system=_TAGGER_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            tags = json.loads(raw)
            if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
                return tags
        except Exception:
            pass
        return []


def get_tagger(provider: str) -> BulletTagger | None:
    """Factory — return a BulletTagger for the given provider name.

    Returns None to signal "use built-in keyword matching" (assign_xtags).

    Supported providers:
        "none"   — None (offline keyword matching via assign_xtags)
        "claude" — ClaudeTagger (calls claude-haiku-4-5-20251001)
                   Falls back to None if ANTHROPIC_API_KEY is not set.

    Adding a new provider is a one-class addition.
    """
    if provider == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "warning: ANTHROPIC_API_KEY not set — falling back to keyword matching.",
                file=sys.stderr,
            )
            return None
        return ClaudeTagger()
    return None


# ---------------------------------------------------------------------------
# YAML serialisation — flow style for flat string lists (x-tags, skills)
# ---------------------------------------------------------------------------


class FlowList(list):
    """A list subclass that yaml.dump renders in inline flow style: [a, b, c]."""


class _CVDumper(yaml.Dumper):
    pass


def _flow_representer(dumper: yaml.Dumper, data: FlowList) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


_CVDumper.add_representer(FlowList, _flow_representer)


def to_yaml(data: dict) -> str:
    return yaml.dump(
        data,
        Dumper=_CVDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    )


# ---------------------------------------------------------------------------
# Hash helpers (unchanged)
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_up_to_date(source_path: Path) -> bool:
    if not HASH_FILE.exists() or not OUTPUT_FILE.exists():
        return False
    stored = HASH_FILE.read_text().strip()
    return stored == sha256_file(source_path)


# ---------------------------------------------------------------------------
# Source resolution (unchanged)
# ---------------------------------------------------------------------------


def resolve_source(fmt: str | None) -> Path:
    if fmt is not None:
        path = ROOT / f"resume.{fmt}"
        if not path.exists():
            print(
                f"error: --source {fmt} specified but {path.name} not found in {ROOT}",
                file=sys.stderr,
            )
            sys.exit(1)
        return path

    for _, path in SOURCE_CANDIDATES:
        if path.exists():
            return path

    names = ", ".join(p.name for _, p in SOURCE_CANDIDATES)
    print(
        f"error: no source file found. Place one of [{names}] in the project root.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Text extraction (unchanged)
# ---------------------------------------------------------------------------


def extract_text_docx(path: Path) -> str:
    """Extract text from DOCX using pandoc, falling back to python-docx."""
    try:
        result = subprocess.run(
            ["pandoc", str(path), "-t", "markdown"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
    except FileNotFoundError:
        pass

    try:
        from docx import Document  # type: ignore[import-untyped]

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        print(
            "error: cannot read DOCX — pandoc not found and python-docx not installed.\n"
            "       Install pandoc:     sudo dnf install pandoc\n"
            "       Or python-docx:    pip install python-docx",
            file=sys.stderr,
        )
        sys.exit(1)


def extract_text_pdf(path: Path) -> str:
    """Extract text from PDF using pdftotext, falling back to pandoc."""
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["pandoc", str(path), "-t", "markdown"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
        print(f"error: pandoc failed to read PDF:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(
            "error: cannot read PDF — pdftotext not found and pandoc not found.\n"
            "       Install poppler-utils: sudo dnf install poppler-utils\n"
            "       Or pandoc:             sudo dnf install pandoc",
            file=sys.stderr,
        )
        sys.exit(1)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_text_docx(path)
    if suffix == ".pdf":
        return extract_text_pdf(path)
    if suffix == ".md":
        return path.read_text()
    print(f"error: unsupported file extension: {suffix}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Markdown parser helpers
# ---------------------------------------------------------------------------


def _strip_md(text: str) -> str:
    """Remove common markdown formatting characters."""
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"`+", "", text)
    text = re.sub(r"^\s*#+\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\{[^}]+\}", r"\1", text)   # [text]{.attr} → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [text](url) → text
    text = re.sub(r"\\([|#\-])", r"\1", text)               # unescape \| \# \-
    return text.strip()


def _canonical_section(heading: str) -> str:
    """Map a heading string to its canonical section key."""
    key = re.sub(r"\s*\([^)]*\)\s*$", "", heading).lower().strip()
    return _SECTION_MAP.get(key, key)


def _blocks(lines: list[str]) -> list[str]:
    """
    Merge wrapped paragraph continuation lines into single blocks.

    Rules:
    - Blank line → flush current block.
    - Line starting with '- '/'* '/'+ ' → new bullet block (flush first).
    - Line with 2+ leading spaces → continuation of current block.
    - Other line → append to current paragraph block.
    """
    result: list[str] = []
    current: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            if current:
                result.append(" ".join(current))
                current = []
        elif re.match(r"^[ \t]{2,}", raw) and current:
            # Indented continuation — join into current block
            current.append(stripped)
        elif re.match(r"^[-*+]\s", stripped):
            # New bullet — flush paragraph, start bullet block
            if current:
                result.append(" ".join(current))
            current = [stripped]
        else:
            # Regular paragraph line — flush if previous was a bullet
            if current and re.match(r"^[-*+]\s", current[0]):
                result.append(" ".join(current))
                current = []
            current.append(stripped)

    if current:
        result.append(" ".join(current))
    return result


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    """Split markdown lines into named sections by pandoc smallcaps or ## headings."""
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for line in lines:
        m = _SMALLCAPS.match(line.strip()) or _H2.match(line.strip()) or _H1.match(line.strip())
        if m:
            current = _canonical_section(m.group(1))
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return sections


def _is_pure_italic(block: str) -> bool:
    """True if the entire block is a single italic span: *...*"""
    s = block.strip()
    return (
        len(s) > 2
        and s.startswith("*")
        and s.endswith("*")
        and not s.startswith("**")
    )


def _normalize_period(s: str) -> str:
    """Normalise pandoc -- to en-dash."""
    return s.replace("--", "–").strip()


def _split_company_role(heading: str) -> tuple[str, str]:
    """Split 'Company — Role' or 'Company | Role' into (company, role)."""
    for sep in ["—", "–", " — ", " – ", " | ", " - "]:
        if sep in heading:
            parts = heading.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return heading.strip(), ""


# ---------------------------------------------------------------------------
# Deterministic section parsers
# ---------------------------------------------------------------------------


def _parse_meta(header_lines: list[str], full_text: str) -> dict:
    meta = {
        "name": "",
        "title": "",
        "email": "",
        "phone": "",
        "location": "",
        "linkedin": "",
        "open_to": "",
    }

    # Email
    m = _EMAIL.search(full_text)
    if m:
        meta["email"] = m.group()

    # LinkedIn — prefer pandoc's ](url) form, then bare URL
    url_m = re.search(r"\]\((https?://[^)]*linkedin\.com[^)]*)\)", full_text)
    if url_m:
        meta["linkedin"] = url_m.group(1)
    else:
        url_m = _LINKEDIN.search(full_text)
        if url_m:
            meta["linkedin"] = url_m.group().rstrip(".,;)")

    # Phone — scan first 600 chars, joining lines so wrapped numbers aren't split
    contact_head = " ".join(ln.strip() for ln in full_text[:600].splitlines() if ln.strip())
    for m in re.finditer(
        r"(?:\+\d{1,3}[\s\-.]?)?\d{2,4}[\s\-.]?\d{3,5}[\s\-.]?\d{3,5}",
        contact_head,
    ):
        candidate = re.sub(r"\s+", " ", m.group()).strip()
        digits = re.sub(r"\D", "", candidate)
        # Require ≥7 digits and skip bare years (4-digit sequences)
        if len(digits) >= 7 and not re.fullmatch(r"(?:19|20)\d{2}", digits):
            meta["phone"] = candidate
            break

    blocks = _blocks(header_lines)

    # Name: first block that is pure bold text (e.g. **LUCAS OSCAR SIMON**)
    for block in blocks:
        m = re.match(r"^\*\*([^*\[]+)\*\*\s*$", block.strip())
        if m:
            meta["name"] = _strip_md(m.group(1)).strip()
            break

    # Title: first meaningful block that is not the name, not contact info, not open_to
    for block in blocks:
        clean = _strip_md(block).strip()
        if not clean or clean == meta["name"]:
            continue
        if _EMAIL.search(clean) or "linkedin" in clean.lower():
            continue
        if re.search(r"open to|full.time|CLT|EOR|B2B", clean, re.I):
            continue
        if len(clean) > 5:
            meta["title"] = clean
            break

    # Location: part of the contact line before the first |
    for block in blocks:
        if _EMAIL.search(block):
            parts = re.split(r"\\?\|", block)
            if parts:
                loc = _strip_md(parts[0]).strip()
                # Remove pandoc date-like artifacts and keep city-country text
                loc = re.sub(r"\s*--\s*", " — ", loc)
                if loc and not _EMAIL.search(loc):
                    meta["location"] = loc
            break

    # open_to: italic block with availability keywords
    for block in blocks:
        if _is_pure_italic(block):
            inner = block.strip().strip("*")
            if re.search(r"open to|full.time|CLT|EOR|B2B|remote", inner, re.I):
                # Strip common "Open to:" / "Open to" prefix
                value = _strip_md(inner).strip()
                value = re.sub(r"^Open\s+to\s*:\s*", "", value, flags=re.I)
                meta["open_to"] = value
                break

    return meta


def _parse_summary(sections: dict[str, list[str]]) -> str:
    lines = sections.get("summary", [])
    if not lines:
        return ""
    paras = []
    for block in _blocks(lines):
        if block.startswith("#") or _SMALLCAPS.match(block.strip()):
            continue
        clean = _strip_md(block).strip()
        if clean:
            paras.append(clean)
    return " ".join(paras)


def _parse_skills(lines: list[str]) -> dict:
    skills: dict[str, list] = {
        "languages": [],
        "backend": [],
        "cloud": [],
        "messaging": [],
        "databases": [],
        "identity": [],
        "observability": [],
        "tools": [],
    }

    # Map label variants → canonical category
    LABEL_MAP: dict[str, str] = {
        "language": "languages",
        "languages": "languages",
        "programming": "languages",
        "backend": "backend",
        "framework": "backend",
        "frameworks": "backend",
        "backend & frameworks": "backend",
        "backend & framework": "backend",
        "cloud": "cloud",
        "cloud & serverless": "cloud",
        "cloud platform": "cloud",
        "cloud platforms": "cloud",
        "infrastructure": "cloud",
        "messaging": "messaging",
        "messaging & integration": "messaging",
        "message broker": "messaging",
        "database": "databases",
        "databases": "databases",
        "databases & data": "databases",
        "data": "databases",
        "identity": "identity",
        "identity & security": "identity",
        "auth": "identity",
        "security": "identity",
        "authentication": "identity",
        "observability": "observability",
        "monitoring": "observability",
        "logging": "observability",
        "tool": "tools",
        "tools": "tools",
        "tools & practices": "tools",
        "tools & practice": "tools",
        "ci/cd": "tools",
        "devops": "tools",
        "other": "tools",
    }

    current_cat: str | None = None

    for block in _blocks(lines):
        # Detect "**Label:** items" or "Label: items"
        m = re.match(r"^\*{0,2}([^:*\n]{2,50}?)\*{0,2}:\s*(.*)", block.strip())
        if m:
            label = _strip_md(m.group(1)).strip().lower()
            items_str = _strip_md(m.group(2)).strip()
            canon = LABEL_MAP.get(label)
            if not canon:
                # Partial match: find any key that's a substring
                for key, cat in LABEL_MAP.items():
                    if key in label or label.startswith(key):
                        canon = cat
                        break
            if canon:
                current_cat = canon
                if items_str:
                    items = [i.strip() for i in re.split(r"[,;]", items_str) if i.strip()]
                    skills[canon].extend(items)
                continue

        # Continuation items for current category
        if current_cat and block.strip() and not block.strip().startswith("#"):
            clean = _strip_md(block).strip()
            if clean:
                items = [i.strip() for i in re.split(r"[,;]", clean) if i.strip()]
                skills[current_cat].extend(items)

    # Wrap in FlowList so yaml.dump renders inline
    return {k: FlowList(v) for k, v in skills.items()}


def _try_parse_entry(block: str) -> dict | None:
    """
    Try to parse a block as an experience entry header.

    Accepts both pandoc style ('**Company** \\| Role') and the mixed form
    where the italic date is inline ('**Company** \\| Role*Date | Loc*').
    Returns a partial entry dict (period/location may be empty) or None.
    """
    text = block.strip()

    # Must start with ** but not a section heading or project line
    if not text.startswith("**"):
        return None
    if "{.smallcaps}" in text:
        return None
    if re.match(r"^\*\*Project:", text, re.I):
        return None

    # Extract inline italic spans — these carry date + location info
    period = ""
    location = ""
    for span in _ITALIC_SPAN.finditer(text):
        inner = span.group(1)
        dm = _DATE_RANGE.search(inner)
        if dm and not period:
            period = _normalize_period(dm.group())
            # Location: the part after \| following the date
            remainder = inner[dm.end():]
            loc_m = re.search(r"\\?\|\s*(.+?)(?:\s*---.*)?$", remainder)
            if loc_m:
                location = _strip_md(loc_m.group(1)).strip().replace("---", "—")

    # Remove italic spans to isolate the bold header text
    header = _ITALIC_SPAN.sub("", text).strip()

    # Match **Company** \| Role
    m = re.match(r"^\*\*([^[*]+?)\*\*\s*(?:\\?\|)\s*(.*)", header, re.DOTALL)
    if not m:
        return None

    company = _strip_md(m.group(1)).strip()
    role = _strip_md(m.group(2)).strip()
    if not company:
        return None

    return {
        "company": company,
        "role": role,
        "period": period,
        "location": location,
        "note": "",
        "projects": [],
        "bullets": [],
    }


def _parse_experience(lines: list[str], tagger: BulletTagger | None = None) -> list[dict]:
    blocks = _blocks(lines)
    entries: list[dict] = []
    current: dict | None = None
    current_project: dict | None = None

    i = 0
    while i < len(blocks):
        block = blocks[i]
        i += 1

        # --- Try to detect an entry header ---
        entry = _try_parse_entry(block)
        if entry is not None:
            # Flush previous state
            if current_project is not None and current is not None:
                current["projects"].append(current_project)
                current_project = None
            if current is not None:
                entries.append(current)
            current = entry

            # If the entry didn't carry a date, look ahead in the next few
            # blocks for a pure-italic date line (e.g. Grupo GFT pattern)
            if not current["period"]:
                j = i
                while j < len(blocks) and j < i + 4:
                    nb = blocks[j]
                    if _is_pure_italic(nb):
                        inner = nb.strip().strip("*")
                        dm = _DATE_RANGE.search(inner)
                        if dm:
                            current["period"] = _normalize_period(dm.group())
                            rem = inner[dm.end():]
                            loc_m = re.search(r"\\?\|\s*(.+?)(?:\s*---.*)?$", rem)
                            if loc_m:
                                current["location"] = (
                                    _strip_md(loc_m.group(1)).strip().replace("---", "—")
                                )
                            i = j + 1  # consume this block
                            break
                    elif not nb.strip():
                        j += 1
                        continue
                    else:
                        break
                    j += 1
            continue

        # --- Standard markdown h3 entry (fallback for non-pandoc CVs) ---
        h3m = _H3.match(block)
        if h3m and current is None:
            heading = _strip_md(h3m.group(1))
            company, role = _split_company_role(heading)
            current = {
                "company": company,
                "role": role,
                "period": "",
                "location": "",
                "note": "",
                "projects": [],
                "bullets": [],
            }
            continue

        if current is None:
            continue

        # --- Pure italic line: date or note ---
        if _is_pure_italic(block):
            inner = block.strip().strip("*")
            dm = _DATE_RANGE.search(inner)
            if dm and not current["period"]:
                current["period"] = _normalize_period(dm.group())
                rem = inner[dm.end():]
                loc_m = re.search(r"\\?\|\s*(.+?)(?:\s*---.*)?$", rem)
                if loc_m:
                    current["location"] = (
                        _strip_md(loc_m.group(1)).strip().replace("---", "—")
                    )
            elif inner.strip():
                note_text = _strip_md(inner).strip()
                if current["note"]:
                    current["note"] += "\n" + note_text
                else:
                    current["note"] = note_text
            continue

        # --- Project header: **Project: Name (period)** ---
        pm = re.match(r"^\*\*Project:\s*(.+?)\*\*\s*$", block.strip(), re.I)
        if pm:
            if current_project is not None:
                current["projects"].append(current_project)
            raw_name = pm.group(1).strip()
            period_m = re.search(r"\(([^)]+)\)\s*$", raw_name)
            proj_period = _normalize_period(period_m.group(1)) if period_m else ""
            proj_name = (
                raw_name[: period_m.start()].strip(" —-") if period_m else raw_name
            )
            current_project = {"name": proj_name, "period": proj_period, "bullets": []}
            continue

        # --- Bullet point ---
        bm = _BULLET.match(block)
        if bm:
            text = _strip_md(bm.group(1)).strip()
            if text:
                if tagger is not None:
                    ctx = {
                        "company": current.get("company", "") if current else "",
                        "role": current.get("role", "") if current else "",
                        "period": current.get("period", "") if current else "",
                    }
                    raw = tagger.tag(text, ctx)
                    tags = raw if raw else ["backend"]
                else:
                    tags = assign_xtags(text)
                bullet = {"text": text, "x-tags": FlowList(tags)}
                if current_project is not None:
                    current_project["bullets"].append(bullet)
                else:
                    current["bullets"].append(bullet)

    # Flush final state
    if current_project is not None and current is not None:
        current["projects"].append(current_project)
    if current is not None:
        entries.append(current)

    return entries


def _parse_edu_cert_section(lines: list[str]) -> tuple[list[dict], list[dict]]:
    """Parse a combined Education & Certifications section.

    Classifies each block and returns (edu_entries, cert_entries).

    Three pandoc patterns handled:
      **Name** --- Certification            → cert entry (name only)
      **Credential** --- Institution (year) → education entry (2-part, credential first)
      **Continuous Learning:** A (X, Y), …  → individual cert entries
    """
    edu_entries: list[dict] = []
    cert_entries: list[dict] = []

    def _strip_year(s: str) -> str:
        return re.sub(r"\s*\(?\s*(?:19|20)\d{2}\s*\)?\s*", "", s).strip()

    for block in _blocks(lines):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or _SMALLCAPS.match(stripped):
            continue
        clean = _strip_md(stripped)
        parts = [p.strip() for p in re.split(r"\s+(?:---|—|–)\s+", clean) if p.strip()]

        # "**Name** --- Certification" → single cert entry with no issuer/year
        if parts and parts[-1].lower() in {"certification", "certificate", "cert"}:
            cert_entries.append({"name": " — ".join(parts[:-1]), "issuer": "", "year": ""})
            continue

        # "**Continuous Learning:** Name (Issuer, Year), …" → individual cert entries
        cl_m = re.match(r"\*{0,2}Continuous\s+Learning\*{0,2}:?\s*(.*)", stripped, re.I | re.DOTALL)
        if cl_m:
            remainder = _strip_md(cl_m.group(1)).strip()
            for raw_name, paren_content in re.findall(r"([^()]+)\(([^)]+)\)", remainder):
                name = raw_name.strip().lstrip(",").strip()
                if not name:
                    continue
                paren_parts = paren_content.rsplit(",", 1)
                year_str = paren_parts[-1].strip() if len(paren_parts) > 1 else ""
                if re.match(r"(?:19|20)\d{2}", year_str):
                    issuer = paren_parts[0].strip()
                else:
                    issuer = paren_content.strip()
                    year_str = ""
                cert_entries.append({"name": name, "issuer": issuer, "year": year_str})
            continue

        # Regular education entry
        if not parts:
            continue
        year_m = re.search(r"\b((?:19|20)\d{2})\b", clean)
        year = year_m.group(1) if year_m else ""

        if len(parts) == 2:
            # Pandoc format: **Credential** --- Institution (bold part is credential)
            credential = _strip_year(parts[0])
            institution = _strip_year(parts[1]).rstrip(",").strip()
        else:
            # Standard format: **Institution** --- Credential --- …
            institution = _strip_year(parts[0]).rstrip(":").strip()
            credential = " — ".join(_strip_year(p) for p in parts[1:])

        if institution:
            edu_entries.append({"institution": institution, "credential": credential, "year": year})

    return edu_entries, cert_entries


def _parse_certifications(lines: list[str]) -> list[dict]:
    entries = []
    for block in _blocks(lines):
        bm = _BULLET.match(block.strip())
        text = _strip_md(bm.group(1) if bm else block).strip()
        if not text:
            continue
        year_m = re.search(r"\b((?:19|20)\d{2})\b", text)
        year = year_m.group(1) if year_m else ""
        text_no_year = re.sub(r"\s*\(?\s*(?:19|20)\d{2}\s*\)?\s*", " ", text).strip()
        parts = [p.strip() for p in re.split(r"\s*(?:—|–|--|,)\s*", text_no_year) if p.strip()]
        if parts:
            entries.append({"name": parts[0], "issuer": parts[1] if len(parts) > 1 else "", "year": year})
    return entries


def _parse_languages(lines: list[str]) -> list[dict]:
    entries = []
    # Join all content and split on bullet separators (• or ·)
    full = " ".join(_strip_md(b) for b in _blocks(lines) if b.strip())
    for part in re.split(r"[•·]", full):
        part = part.strip()
        if not part:
            continue
        # "Language (Level)" or "Language — Level" or "Language: Level"
        m = re.match(
            r"^([A-Za-zÀ-ÿ\s]+?)\s*(?:\((.+?)\)|[—–\-:]\s*(.+))$", part.strip()
        )
        if m:
            entries.append(
                {
                    "language": m.group(1).strip(),
                    "level": (m.group(2) or m.group(3) or "").strip(),
                }
            )
        elif part:
            entries.append({"language": part, "level": ""})
    return entries


# ---------------------------------------------------------------------------
# Deterministic parser — main entry point
# ---------------------------------------------------------------------------


def parse_markdown(text: str, tagger: BulletTagger | None = None) -> dict:
    """Parse CV text (pandoc markdown or plain markdown) into the YAML schema."""
    lines = text.splitlines()
    sections = _split_sections(lines)
    edu_entries, cert_from_edu = _parse_edu_cert_section(sections.get("education", []))
    return {
        "meta": _parse_meta(sections.get("_header", []), text),
        "summary": _parse_summary(sections),
        "skills": _parse_skills(sections.get("skills", [])),
        "experience": _parse_experience(sections.get("experience", []), tagger),
        "education": edu_entries,
        "certifications": _parse_certifications(sections.get("certifications", [])) + cert_from_edu,
        "languages": _parse_languages(sections.get("languages", [])),
    }


# ---------------------------------------------------------------------------
# Confidence check
# ---------------------------------------------------------------------------


def check_confidence(
    data: dict, source_path: Path, raw_text: str
) -> tuple[bool, str]:
    """
    Return (needs_claude, reason).  True = low confidence, use Claude fallback.
    """
    experience = data.get("experience") or []
    if len(experience) < 2:
        n = len(experience)
        return True, f"only {n} experience entr{'y' if n == 1 else 'ies'} found"

    summary = data.get("summary") or ""
    if not summary.strip():
        return True, "summary is empty"

    bullets = count_bullets(data)
    if bullets < 5:
        return True, f"only {bullets} bullet{'s' if bullets != 1 else ''} found across all experience"

    if source_path.suffix.lower() == ".pdf":
        nonempty = [ln for ln in raw_text.splitlines() if ln.strip()]
        if nonempty:
            avg_len = sum(len(ln) for ln in nonempty) / len(nonempty)
            if avg_len < 20:
                return True, f"PDF text appears scrambled (avg line length {avg_len:.0f} chars)"

    return False, ""


# ---------------------------------------------------------------------------
# Claude API (fallback path)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a structured CV parser. Read raw CV text and output it as valid YAML.

RULES — follow exactly:
1. Output ONLY the YAML block. No preamble, no markdown fences (no ```yaml), no explanation.
2. Preserve every top-level key in the schema below, even if a section is empty (use [] or "").
3. Apply x-tags to EVERY bullet point based on these content conventions:
   - csharp      — C#, .NET, ASP.NET, Entity Framework, Azure Functions in C#
   - python      — Python, Durable Functions in Python, pandas, PyArrow, mstrio
   - azure       — any Azure service (Service Bus, Azure Storage, Functions, etc.)
   - backend     — ALL experience bullets get this tag; apply it broadly to every bullet
   - data        — ETL, Parquet, MicroStrategy, eFront, data pipelines
   - integration — REST API, Service Bus, Storage Queues, event-driven architectures
   - testing     — xUnit, Moq, unit testing, test coverage mentions
4. Every bullet must include at least [backend] in x-tags, plus all other applicable tags.
5. Use YAML literal block scalar (|) for the summary field when it spans multiple lines.
6. If a role has named sub-projects, use the projects list. Otherwise use bullets directly.
7. Omit optional fields (note, projects, certifications, etc.) if they have no content.

SCHEMA — output must conform to this structure:
meta:
  name: string
  title: string
  email: string
  phone: string
  location: string
  linkedin: string
  open_to: string

summary: |
  multi-line string

skills:
  languages: [string]
  backend: [string]
  cloud: [string]
  messaging: [string]
  databases: [string]
  identity: [string]
  observability: [string]
  tools: [string]

experience:
  - company: string
    role: string
    period: string
    location: string
    note: string
    projects:
      - name: string
        period: string
        bullets:
          - text: string
            x-tags: [string]
    bullets:
      - text: string
        x-tags: [string]

education:
  - institution: string
    credential: string
    year: string

certifications:
  - name: string
    issuer: string
    year: string

languages:
  - language: string
    level: string
"""

USER_PROMPT_TEMPLATE = """\
Parse the following CV document into YAML according to your schema and rules.

CV DOCUMENT:
---
{text}
---

Output ONLY valid YAML. No markdown fences. No explanation.\
"""


def strip_yaml_fences(text: str) -> str:
    """Remove ```yaml ... ``` wrapper if the model added one."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def call_claude(extracted_text: str, model: str) -> dict:
    """Send extracted CV text to Claude and return the parsed YAML as a dict."""
    try:
        import anthropic
    except ImportError:
        print(
            "error: anthropic package not installed. Run: pip install anthropic",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "error: ANTHROPIC_API_KEY environment variable not set",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    print(f"  Calling {model}...", end=" ", flush=True)

    chunks: list[str] = []
    try:
        with client.messages.stream(
            model=model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(text=extracted_text),
                }
            ],
        ) as stream:
            for chunk in stream.text_stream:
                chunks.append(chunk)
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        print("\nerror: invalid ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)
    except anthropic.APIConnectionError:
        print("\nerror: could not connect to Claude API", file=sys.stderr)
        sys.exit(1)
    except anthropic.APIStatusError as e:
        print(f"\nerror: API returned {e.status_code}: {e.message}", file=sys.stderr)
        sys.exit(1)

    tokens = final.usage.output_tokens
    print(f"done ({tokens} output tokens)")

    raw = "".join(chunks)
    clean = strip_yaml_fences(raw)

    try:
        data = yaml.safe_load(clean)
    except yaml.YAMLError as exc:
        print(f"error: Claude returned unparseable YAML:\n{exc}", file=sys.stderr)
        print("\n--- Raw output (first 2000 chars) ---", file=sys.stderr)
        print(clean[:2000], file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print(
            f"error: expected a YAML mapping at root, got {type(data).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)

    return data


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def count_bullets(data: dict) -> int:
    total = 0
    for entry in data.get("experience", []) or []:
        total += len(entry.get("bullets", []) or [])
        for project in entry.get("projects", []) or []:
            total += len(project.get("bullets", []) or [])
    return total


def print_summary(source_path: Path, data: dict, path_taken: str) -> None:
    sections = [k for k, v in data.items() if v]
    bullets = count_bullets(data)
    print("\n--- Import summary ---")
    print(f"  Source:   {source_path.name}")
    print(f"  Path:     {path_taken}")
    print(f"  Sections: {', '.join(sections)}")
    print(f"  Bullets:  {bullets}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate resume.yaml from a source CV document. "
            "Uses deterministic parsing by default; falls back to the Claude API "
            "when confidence is low."
        )
    )
    parser.add_argument(
        "--source",
        choices=["docx", "pdf", "md"],
        help="Force a specific source format (default: auto-detect docx > pdf > md)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if resume.yaml is already up to date",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated YAML to stdout; do not write files",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude model to use for the API fallback (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Force the Claude API path, skipping deterministic parsing",
    )
    parser.add_argument(
        "--tagger",
        choices=["claude", "none"],
        default="none",
        help="Bullet tagging provider for the deterministic parse path (default: none)",
    )
    args = parser.parse_args()

    # 1. Resolve source file
    source_path = resolve_source(args.source)
    print(f"Source: {source_path.name}")

    # 2. Check if already up to date (skipped by --force)
    if not args.force and is_up_to_date(source_path):
        print("resume.yaml is up to date — skipping regeneration.")
        sys.exit(0)

    # 3. Extract text from source document
    print(f"Extracting text from {source_path.name}...", end=" ", flush=True)
    text = extract_text(source_path)
    print(f"done ({len(text):,} chars)")

    # 4. Parse — deterministic primary, Claude fallback
    tagger = get_tagger(args.tagger)
    path_taken: str
    if args.ai:
        print("Parsing via Claude API (--ai flag)...")
        data = call_claude(text, args.model)
        path_taken = "used Claude API (--ai flag)"
    else:
        print("Parsing deterministically...", end=" ", flush=True)
        data = parse_markdown(text, tagger)
        low_confidence, reason = check_confidence(data, source_path, text)
        if low_confidence:
            print(f"low confidence ({reason})")
            print("Falling back to Claude API...")
            data = call_claude(text, args.model)
            path_taken = f"used Claude API fallback (reason: {reason})"
        else:
            print("done")
            path_taken = "parsed deterministically"

    # 5. Serialise to YAML
    yaml_str = to_yaml(data)

    # 6. Write or dry-run
    if args.dry_run:
        print("\n--- Generated YAML (dry-run) ---")
        print(yaml_str)
    else:
        OUTPUT_FILE.write_text(yaml_str)
        print(f"Wrote {OUTPUT_FILE.relative_to(ROOT)}")

        # 7. Update source hash
        new_hash = sha256_file(source_path)
        HASH_FILE.write_text(new_hash + "\n")
        print(f"Updated {HASH_FILE.relative_to(ROOT)}")

    # 8. Print summary
    print_summary(source_path, data, path_taken)


if __name__ == "__main__":
    main()
