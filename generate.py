#!/usr/bin/env python3
"""Generate targeted CV variants from resume.yaml via Jinja2 → Pandoc → WeasyPrint."""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent
BUILD = ROOT / "build"
VARIANTS = ["csharp", "python"]
FORMATS = ["md", "pdf", "docx", "all"]


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def render_markdown(data: dict, tag: str, template_path: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(template_path.name)
    return tmpl.render(data=data, target_tag=tag, all_variants=VARIANTS)


def compile_pdf(md_path: Path, css_path: Path, pdf_path: Path) -> None:
    cmd = [
        "pandoc",
        str(md_path),
        "--pdf-engine=weasyprint",
        f"--css={css_path}",
        "-o",
        str(pdf_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[error] pandoc failed for {md_path.name}:\n{result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)


def compile_docx(md_path: Path, docx_path: Path, reference_doc: Path | None = None) -> None:
    cmd = ["pandoc", str(md_path), "-o", str(docx_path)]
    if reference_doc is not None:
        cmd.append(f"--reference-doc={reference_doc}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[error] pandoc failed for {md_path.name}:\n{result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)


def build_variant(
    data: dict, tag: str, template: Path, css: Path,
    formats: set[str] | None = None, reference_doc: Path | None = None,
) -> None:
    if formats is None:
        formats = {"md", "pdf", "docx"}
    BUILD.mkdir(exist_ok=True)
    md_path = BUILD / f"resume-{tag}.md"

    md_content = render_markdown(data, tag, template)
    md_path.write_text(md_content)
    print(f"  wrote {md_path.relative_to(ROOT)}")

    if "pdf" in formats:
        pdf_path = BUILD / f"resume-{tag}.pdf"
        compile_pdf(md_path, css, pdf_path)
        print(f"  wrote {pdf_path.relative_to(ROOT)}")

    if "docx" in formats:
        docx_path = BUILD / f"resume-{tag}.docx"
        compile_docx(md_path, docx_path, reference_doc)
        print(f"  wrote {docx_path.relative_to(ROOT)}")


def resolve_theme(theme: str) -> Path:
    css_path = ROOT / "templates" / "themes" / f"{theme}.css"
    if not css_path.exists():
        available = sorted(p.stem for p in (ROOT / "templates" / "themes").glob("*.css"))
        print(
            f"[error] theme '{theme}' not found at {css_path}\n"
            f"        available themes: {', '.join(available)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return css_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CV variant PDFs.")
    parser.add_argument(
        "--variant",
        choices=VARIANTS,
        help="Build a single variant (default: all)",
    )
    parser.add_argument(
        "--theme",
        default="classic",
        help="CSS theme name from templates/themes/ (default: classic)",
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        default="all",
        dest="output_format",
        help="Output format: md, pdf, docx, or all (default: all)",
    )
    args = parser.parse_args()

    fmt = args.output_format
    formats = {"md", "pdf", "docx"} if fmt == "all" else {"md", fmt}

    data = load_yaml(ROOT / "resume.yaml")
    template = ROOT / "resume.md.j2"
    css = resolve_theme(args.theme)
    ref_doc = ROOT / "templates" / "reference.docx"
    reference_doc = ref_doc if ref_doc.exists() else None

    targets = [args.variant] if args.variant else VARIANTS
    for tag in targets:
        print(f"Building variant: {tag}")
        build_variant(data, tag, template, css, formats, reference_doc)

    print("Done.")


if __name__ == "__main__":
    main()
