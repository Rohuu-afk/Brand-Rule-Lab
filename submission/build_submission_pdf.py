from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz


_INLINE_TOKEN = re.compile(
    r"(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*.+?\*\*|(?<!\*)\*[^*]+\*(?!\*))"
)


def _render_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text):
        parts.append(html.escape(text[cursor:match.start()]))
        token = match.group()
        if token.startswith("["):
            link_match = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)
            if not link_match:
                parts.append(html.escape(token))
            else:
                label, url = link_match.groups()
                parts.append(
                    f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
                )
        elif token.startswith("`"):
            parts.append(f"<code>{html.escape(token[1:-1])}</code>")
        elif token.startswith("**"):
            parts.append(f"<strong>{html.escape(token[2:-2])}</strong>")
        else:
            parts.append(f"<em>{html.escape(token[1:-1])}</em>")
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def _markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_render_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        if stripped == "<!-- pagebreak -->":
            flush_paragraph()
            blocks.append('<div class="page-break"></div>')
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            blocks.append(
                f"<h{level}>{_render_inline(heading.group(2))}</h{level}>"
            )
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            blocks.append(
                f'<blockquote><p>{_render_inline(" ".join(quote_lines))}</p></blockquote>'
            )
            continue
        list_match = re.match(r"^\s*((?:[-*])|(?:\d+\.))\s+(.+)$", line)
        if list_match:
            flush_paragraph()
            ordered = list_match.group(1)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                item_match = re.match(
                    r"^\s*((?:[-*])|(?:\d+\.))\s+(.+)$", lines[index]
                )
                if not item_match or item_match.group(1)[0].isdigit() != ordered:
                    break
                items.append(f"<li>{_render_inline(item_match.group(2))}</li>")
                index += 1
            blocks.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(
                f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
            continue
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    body = "\n".join(blocks)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 52pt 56pt 48pt; }}
* {{ box-sizing: border-box; }}
body {{ color: #263445; font-family: Arial, sans-serif; font-size: 9pt; line-height: 1.24; }}
h1 {{ color: #17385f; font-size: 25pt; margin: 12pt 0 7pt; border-bottom: 2pt solid #357b9b; padding-bottom: 6pt; }}
h2 {{ color: #17385f; font-size: 15pt; margin: 14pt 0 6pt; break-after: avoid; }}
h3 {{ color: #17385f; font-size: 11pt; margin: 10pt 0 4pt; break-after: avoid; }}
p {{ margin: 0 0 6pt; orphans: 2; widows: 2; }}
ul, ol {{ margin: 3pt 0 8pt; padding-left: 19pt; }}
li {{ margin: 0 0 3pt; orphans: 2; widows: 2; }}
blockquote {{ background: #edf4f7; border-left: 3pt solid #357b9b; margin: 8pt 0 10pt; padding: 8pt 10pt; }}
blockquote p {{ margin: 0; }}
code, pre {{ font-family: "Courier New", monospace; font-size: 8pt; }}
pre {{ white-space: pre-wrap; }}
a {{ color: #155e85; text-decoration: underline; }}
.page-break {{ break-before: page; page-break-before: always; height: 0; }}
</style></head><body>{body}</body></html>"""


def _find_edge() -> Path:
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Microsoft Edge was not found; install Edge or add msedge to PATH.")


def build_pdf(source: Path, destination: Path) -> None:
    edge = _find_edge()
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="brand-rule-lab-pdf-") as temp_name:
        temp = Path(temp_name)
        html_path = temp / "submission.html"
        rendered_pdf = temp / "rendered.pdf"
        html_path.write_text(
            _markdown_to_html(source.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        command = [
            str(edge),
            "--headless",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={temp / 'edge-profile'}",
            f"--print-to-pdf={rendered_pdf}",
            "--no-pdf-header-footer",
            html_path.as_uri(),
        ]
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=120
        )
        if result.returncode:
            raise RuntimeError(
                f"Edge PDF rendering failed ({result.returncode}): "
                f"{(result.stderr or result.stdout)[-3000:]}"
            )
        if not rendered_pdf.is_file():
            raise RuntimeError("Edge completed without producing a PDF.")

        document = fitz.open(rendered_pdf)
        for page_number, page in enumerate(document, start=1):
            page.insert_text(
                (56, 27),
                "CODENEXUS INDIA 2026 | INKLOOM AI",
                fontname="hebo",
                fontsize=7.5,
                color=(0.12, 0.39, 0.53),
            )
            footer = f"BRAND RULE LAB | ROUND 2 SUBMISSION | {page_number}"
            footer_width = fitz.get_text_length(
                footer, fontname="helv", fontsize=7.5
            )
            page.insert_text(
                (page.rect.width - 56 - footer_width, page.rect.height - 23),
                footer,
                fontname="helv",
                fontsize=7.5,
                color=(0.37, 0.43, 0.50),
            )
        document.save(destination, garbage=4, deflate=True)
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Round 2 PDF from its Markdown source."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("Brand_Rule_Lab_Round2_Submission.md"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("Brand_Rule_Lab_Round2_Submission.pdf"),
    )
    args = parser.parse_args()
    build_pdf(args.source, args.output)
    print(f"PDF written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
