"""
Template Previewer
===================
Renders application templates as styled HTML for review in a browser.

Usage:
    python -m scripts.preview_template open_procedure
    python -m scripts.preview_template restricted_sq
    python -m scripts.preview_template --all

Output:
    data/template_preview_<type>.html
"""

import argparse
import html as html_mod
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = ROOT / "templates"
REUSABLE_DIR = TEMPLATES_DIR / "reusable"
OUTPUT_DIR = ROOT / "data"

BRAND_BLUE = "#30475E"
BRAND_ORANGE = "#D08770"
BRAND_GREY = "#6b7785"


def load_template(template_type: str) -> dict:
    """Load a template JSON file."""
    path = TEMPLATES_DIR / f"{template_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_reusable_content(key: str) -> str | None:
    """Load reusable content by key. Returns markdown text or None."""
    # Check for direct file
    path = REUSABLE_DIR / f"{key}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Check for directory (e.g. case_studies/)
    dir_path = REUSABLE_DIR / key
    if dir_path.is_dir():
        parts = []
        for md_file in sorted(dir_path.glob("*.md")):
            parts.append(md_file.read_text(encoding="utf-8"))
        if parts:
            return "\n\n---\n\n".join(parts)

    return None


def render_template_html(template: dict) -> str:
    """Render a template as a styled HTML page for browser review."""
    t = template
    sections_html = ""

    for i, section in enumerate(t["sections"], 1):
        # Load reusable content if applicable
        reusable_html = ""
        if section.get("reusable_content_key"):
            content = load_reusable_content(section["reusable_content_key"])
            if content:
                # Convert markdown to basic HTML (paragraphs and headers)
                content_escaped = html_mod.escape(content)
                content_formatted = content_escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
                reusable_html = f"""
                    <details style="margin-top:10px; background:#f0f7f0; border:1px solid #c3e6c3; border-radius:6px; padding:10px;">
                        <summary style="cursor:pointer; font-weight:600; color:#1a7a3a; font-size:13px;">
                            View reusable content: {html_mod.escape(section['reusable_content_key'])}
                        </summary>
                        <div style="margin-top:8px; font-size:12px; color:#333; white-space:pre-wrap; font-family:monospace; max-height:400px; overflow-y:auto;">
                            <p>{content_formatted}</p>
                        </div>
                    </details>
                """

        # Section badges
        badges = ""
        if section.get("required"):
            badges += f'<span style="background:{BRAND_BLUE}; color:white; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:4px;">Required</span>'
        else:
            badges += '<span style="background:#e0e0e0; color:#555; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:4px;">Optional</span>'

        if section.get("reusable"):
            badges += '<span style="background:#2e9e50; color:white; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:4px;">Reusable</span>'
        else:
            badges += f'<span style="background:{BRAND_ORANGE}; color:white; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:4px;">Contract-specific</span>'

        word_limit = section.get("default_word_limit")
        word_str = f"{word_limit:,} words" if word_limit else "No limit"

        sections_html += f"""
        <div style="background:white; border:1px solid #e2e6ea; border-radius:8px; padding:20px; margin-bottom:16px;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <span style="color:{BRAND_GREY}; font-size:12px; font-weight:600;">Section {i}</span>
                    <h3 style="margin:4px 0 8px; color:{BRAND_BLUE}; font-size:16px;">
                        {html_mod.escape(section['name'])}
                    </h3>
                    <div>{badges}</div>
                </div>
                <div style="text-align:right; white-space:nowrap;">
                    <div style="font-size:12px; color:{BRAND_GREY};">Word limit</div>
                    <div style="font-size:14px; font-weight:600; color:{BRAND_BLUE};">{word_str}</div>
                </div>
            </div>
            <div style="margin-top:12px; padding:12px; background:#f8f9fa; border-radius:6px; border-left:3px solid {BRAND_ORANGE};">
                <div style="font-size:11px; font-weight:600; color:{BRAND_ORANGE}; text-transform:uppercase; margin-bottom:4px;">Claude Guidance</div>
                <div style="font-size:13px; color:#333; line-height:1.5;">
                    {html_mod.escape(section.get('guidance', ''))}
                </div>
            </div>
            {reusable_html}
        </div>
        """

    # Documents checklist
    docs_html = ""
    docs = t.get("documents_typically_required", [])
    if docs:
        items = "".join(
            f'<li style="padding:4px 0; font-size:13px;">{html_mod.escape(d)}</li>'
            for d in docs
        )
        docs_html = f"""
        <div style="background:white; border:1px solid #e2e6ea; border-radius:8px; padding:20px; margin-bottom:16px;">
            <h3 style="margin:0 0 12px; color:{BRAND_BLUE}; font-size:16px;">Documents Typically Required</h3>
            <ul style="margin:0; padding-left:20px;">{items}</ul>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Template Preview: {html_mod.escape(t['name'])}</title>
    <style>
        body {{ font-family: 'Segoe UI', Roboto, Arial, sans-serif; margin: 0; padding: 20px; background: #f0f2f6; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,{BRAND_BLUE},#3d5a73); padding:28px; border-radius:10px; margin-bottom:20px;">
            <div style="font-size:12px; color:#70BAD0; text-transform:uppercase; letter-spacing:1px;">Template Preview</div>
            <h1 style="color:white; margin:8px 0 0; font-size:24px; font-family:Georgia,serif;">
                {html_mod.escape(t['name'])}
            </h1>
            <p style="color:#b0c4d8; margin:10px 0 0; font-size:14px; line-height:1.5;">
                {html_mod.escape(t['description'])}
            </p>
        </div>

        <!-- Meta info -->
        <div style="display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap;">
            <div style="background:white; border:1px solid #e2e6ea; border-radius:8px; padding:14px 20px; flex:1; min-width:200px;">
                <div style="font-size:11px; color:{BRAND_GREY}; text-transform:uppercase;">Evaluation Method</div>
                <div style="font-size:13px; color:{BRAND_BLUE}; margin-top:4px; font-weight:500;">
                    {html_mod.escape(t.get('evaluation_method', 'Not specified'))}
                </div>
            </div>
            <div style="background:white; border:1px solid #e2e6ea; border-radius:8px; padding:14px 20px; flex:1; min-width:200px;">
                <div style="font-size:11px; color:{BRAND_GREY}; text-transform:uppercase;">Submission</div>
                <div style="font-size:13px; color:{BRAND_BLUE}; margin-top:4px; font-weight:500;">
                    {html_mod.escape(t.get('submission_notes', 'Not specified'))}
                </div>
            </div>
            <div style="background:white; border:1px solid #e2e6ea; border-radius:8px; padding:14px 20px; min-width:120px;">
                <div style="font-size:11px; color:{BRAND_GREY}; text-transform:uppercase;">Sections</div>
                <div style="font-size:22px; color:{BRAND_BLUE}; margin-top:4px; font-weight:700;">
                    {len(t['sections'])}
                </div>
            </div>
        </div>

        <!-- Sections -->
        <h2 style="color:{BRAND_BLUE}; font-size:18px; margin:24px 0 12px; border-bottom:2px solid {BRAND_ORANGE}; padding-bottom:8px;">
            Application Sections
        </h2>
        {sections_html}

        <!-- Documents -->
        {docs_html}

        <!-- Footer -->
        <div style="text-align:center; padding:20px; font-size:12px; color:{BRAND_GREY};">
            Inference Group &mdash; Application Template Preview &mdash; Review and approve before use
        </div>
    </div>
</body>
</html>"""


def preview_template(template_type: str) -> Path:
    """Load, render, and save a template preview."""
    template = load_template(template_type)
    html = render_template_html(template)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"template_preview_{template_type}.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def preview_all() -> list[Path]:
    """Preview all templates."""
    paths = []
    for template_file in sorted(TEMPLATES_DIR.glob("*.json")):
        template_type = template_file.stem
        try:
            path = preview_template(template_type)
            paths.append(path)
            print(f"  {template_type}: {path}")
        except Exception as e:
            print(f"  {template_type}: ERROR — {e}")
    return paths


def main():
    parser = argparse.ArgumentParser(description="Preview application templates as HTML")
    parser.add_argument("template_type", nargs="?", help="Template type (e.g. open_procedure)")
    parser.add_argument("--all", action="store_true", help="Preview all templates")
    args = parser.parse_args()

    if args.all:
        print("Generating previews for all templates...")
        paths = preview_all()
        print(f"\n{len(paths)} previews generated in {OUTPUT_DIR}/")
    elif args.template_type:
        print(f"Generating preview for: {args.template_type}")
        path = preview_template(args.template_type)
        print(f"Preview saved to: {path}")
    else:
        # List available templates
        templates = [f.stem for f in sorted(TEMPLATES_DIR.glob("*.json"))]
        print("Available templates:")
        for t in templates:
            print(f"  - {t}")
        print(f"\nUsage: python -m scripts.preview_template <template_type>")
        print(f"       python -m scripts.preview_template --all")


if __name__ == "__main__":
    main()
