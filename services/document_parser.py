"""
services/document_parser.py
----------------------------
Extracts plain text (and position metadata) from uploaded files.

Supported formats
-----------------
- .pdf   — PyMuPDF (fitz); preserves page numbers
- .docx  — python-docx; preserves paragraph numbers
- .txt   — plain UTF-8 / latin-1 / cp1252 read
- .xlsx — openpyxl; preserves worksheet numbers and names
- .png / .jpg / .jpeg — Tesseract OCR via pytesseract + Pillow
- Pasted text — returned as-is

Security notes
--------------
- Files are validated by extension AND magic bytes before processing.
- Files are NEVER executed.
- Max size enforced by caller (UI layer); parser also checks MAX_BYTES limit.
- Raw bytes are never forwarded to the AI.
- Graceful error handling for missing OCR / Tesseract binary dependencies without crashing.
"""

from __future__ import annotations

import io
import math
import os
import re
import zipfile
import csv
import shutil
from pathlib import Path

MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
MAX_ARCHIVE_ENTRIES = int(os.getenv("MAX_ARCHIVE_ENTRIES", "2000"))
MAX_ARCHIVE_UNCOMPRESSED_BYTES = int(os.getenv("MAX_ARCHIVE_UNCOMPRESSED_MB", "100")) * 1024 * 1024
MAX_ARCHIVE_ENTRY_BYTES = int(os.getenv("MAX_ARCHIVE_ENTRY_MB", "20")) * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = float(os.getenv("MAX_ARCHIVE_COMPRESSION_RATIO", "100"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "200"))
MAX_PDF_RENDER_PIXELS = int(os.getenv("MAX_PDF_RENDER_PIXELS", "200000000"))
PDF_RENDER_SCALE = float(os.getenv("PDF_RENDER_SCALE", "2.0"))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "40000000"))

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".csv", ".xlsx", ".png", ".jpg", ".jpeg",
}

# Magic byte signatures for type validation
_MAGIC: dict[bytes, str] = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "docx",          # ZIP-based (docx)
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
}


class ParseError(ValueError):
    """Raised when the document cannot be parsed safely or a parser dependency is unavailable."""


class ResourceLimitError(ParseError):
    """Raised when a valid document exceeds a configured processing budget."""


def _check_size(data: bytes) -> None:
    if len(data) > MAX_BYTES:
        mb = len(data) / (1024 * 1024)
        raise ParseError(
            f"File is {mb:.1f} MB which exceeds the {MAX_BYTES // (1024*1024)} MB limit."
        )


def _detect_magic(data: bytes) -> str | None:
    for sig, ftype in _MAGIC.items():
        if data.startswith(sig):
            return ftype
    return None


def _validate_office_archive(data: bytes, extension: str) -> set[str]:
    """Reject oversized or suspicious Office ZIP containers before XML parsing."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ParseError(
                    f"Office document contains too many archive entries ({len(entries)}; "
                    f"limit {MAX_ARCHIVE_ENTRIES})."
                )
            total_size = 0
            names: set[str] = set()
            for entry in entries:
                if entry.filename in names:
                    raise ParseError("Office document contains duplicate ZIP entry names.")
                names.add(entry.filename)
                path_parts = Path(entry.filename.replace("\\", "/")).parts
                if entry.filename.startswith(("/", "\\")) or ".." in path_parts:
                    raise ParseError("Office document contains an unsafe ZIP path.")
                if entry.flag_bits & 0x1:
                    raise ParseError("Encrypted Office ZIP entries are not supported.")
                if entry.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                    raise ParseError(
                        f"Office document entry '{entry.filename}' exceeds the uncompressed size limit."
                    )
                total_size += entry.file_size
                if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ParseError("Office document exceeds the total uncompressed size limit.")
                if entry.file_size and entry.file_size / max(entry.compress_size, 1) > MAX_ARCHIVE_COMPRESSION_RATIO:
                    raise ParseError(
                        f"Office document entry '{entry.filename}' exceeds the compression-ratio limit."
                    )
            required = (
                {"[Content_Types].xml", "word/document.xml"}
                if extension == ".docx"
                else {"[Content_Types].xml", "xl/workbook.xml"}
            )
            if not required.issubset(names):
                raise ParseError(f"ZIP file is not a valid {extension[1:].upper()} document.")
            return names
    except zipfile.BadZipFile as exc:
        raise ParseError(f"{extension[1:].upper()} file is not a valid ZIP container.") from exc


def validate_image_dimensions(data: bytes) -> tuple[int, int]:
    """Read image headers and reject excessive decoded pixel counts before processing."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception as exc:
        raise ParseError(f"Image could not be opened safely ({type(exc).__name__}).") from exc
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ParseError(
            f"Image dimensions ({width} x {height}) exceed the configured pixel limit "
            f"({MAX_IMAGE_PIXELS:,})."
        )
    return width, height


def parse_file(filename: str, data: bytes) -> dict:
    """
    Parse *data* (bytes) for the given *filename*.

    Returns
    -------
    {
        "text":        str,          # full extracted text
        "page_map":    dict,         # {char_offset: page_or_paragraph_number}
        "source_type": str,          # 'pdf' | 'docx' | 'txt' | 'image'
        "word_count":  int,
        "page_records": list,        # PDF page-level method, confidence, and warnings
        "extraction_status": str,    # PDF human-attention status
    }

    Raises ParseError on validation or parsing failure.
    """
    _check_size(data)

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ParseError(
            f"File type '{ext}' is not allowed. "
            f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # For non-txt formats, check magic bytes and the DOCX container structure.
    if ext not in {".txt", ".csv"}:
        magic = _detect_magic(data)
        expected_magic = {
            ".pdf": "pdf",
            ".docx": "docx",
            ".xlsx": "docx",
            ".png": "png",
            ".jpg": "jpg",
            ".jpeg": "jpg",
        }[ext]
        if magic != expected_magic:
            raise ParseError("File content does not match its extension — upload rejected.")
        if ext in {".docx", ".xlsx"}:
            _validate_office_archive(data, ext)

    if ext == ".pdf":
        return _parse_pdf(data)
    elif ext == ".docx":
        return _parse_docx(data)
    elif ext == ".txt":
        return _parse_txt(data)
    elif ext == ".csv":
        return _parse_csv(data)
    elif ext == ".xlsx":
        return _parse_xlsx(data)
    else:  # png / jpg / jpeg
        validate_image_dimensions(data)
        return _parse_image(data)


def parse_for_onboarding(filename: str, data: bytes, visual_asset: bool = False) -> dict:
    """Parse a supported source into page/section records with quality diagnostics."""
    extension = Path(filename).suffix.lower()
    if extension in {".png", ".jpg", ".jpeg"} and visual_asset:
        _check_size(data)
        expected = "png" if extension == ".png" else "jpg"
        if _detect_magic(data) != expected:
            raise ParseError("File content does not match its extension — upload rejected.")
        validate_image_dimensions(data)
        note = "Image preserved as an asset source; text OCR is not used for visual-asset uploads."
        return {
            "text": "",
            "page_map": {},
            "source_type": "image",
            "word_count": 0,
            "page_records": [{
                "page_number": 1, "text": "", "method": "image",
                "quality": 0.0, "confidence": None, "warnings": [note],
            }],
            "extraction_status": "PROCESSED",
        }
    if extension == ".pdf":
        _check_size(data)
        if _detect_magic(data) != "pdf":
            raise ParseError("File content does not match its extension — upload rejected.")
        return _parse_pdf_detailed(data)
    parsed = parse_file(filename, data)
    if extension == ".xlsx":
        return {
            **parsed,
            "page_records": parsed["page_records"],
            "extraction_status": _aggregate_status(parsed["page_records"]),
        }

    text = parsed["text"]
    lines = text.splitlines()
    if parsed["source_type"] == "docx":
        parse_warnings = parsed.get("warnings", [])
        sections = [
            {"page_number": number, "text": line, "method": "text",
             "quality": 1.0 if not parse_warnings else 0.75,
             "confidence": None,
             "warnings": parse_warnings if number == 1 else []}
            for number, line in enumerate(lines, start=1) if line.strip()
        ]
        if not sections:
            sections = [{
                "page_number": 1, "text": "", "method": "text", "quality": 0.0,
                "confidence": None,
                "warnings": parse_warnings or ["No readable text was extracted."],
            }]
    else:
        sections = [{
            "page_number": 1, "text": text, "method": "text",
            "quality": _text_quality(text), "confidence": None,
            "warnings": [] if text.strip() else ["No readable text was extracted."],
        }]
    return {
        **parsed,
        "page_records": sections,
        "extraction_status": _aggregate_status(sections),
    }


def parse_text(raw: str) -> dict:
    """Wrap pasted plain text in the standard return dict."""
    return {
        "text": raw,
        "page_map": {},
        "source_type": "paste",
        "word_count": len(raw.split()),
        "unobserved_pages": [],
    }


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def _parse_pdf(data: bytes) -> dict:
    parsed = _parse_pdf_detailed(data)
    parts: list[str] = []
    page_map: dict[int, int] = {}
    ocr_failures: dict[int, str] = {}
    current_offset = 0
    for record in parsed["page_records"]:
        text = record["text"]
        if record["method"] in {"ocr", "text+ocr"} and any(
            warning.startswith(("Tesseract ", "English Tesseract", "OCR could not run", "OCR failed"))
            for warning in record["warnings"]
        ):
            ocr_failures[record["page_number"]] = "; ".join(record["warnings"])
        if text.strip():
            page_map[current_offset] = record["page_number"]
            parts.append(text)
            current_offset += len(text) + 1
    full_text = "\n".join(parts)
    return {
        "text": full_text,
        "page_map": page_map,
        "source_type": "pdf",
        "word_count": len(full_text.split()),
        "unobserved_pages": parsed["unobserved_pages"],
        "ocr_failures": ocr_failures,
        "page_records": parsed["page_records"],
        "extraction_status": parsed["extraction_status"],
    }


def _parse_docx(data: bytes) -> dict:
    try:
        from docx import Document
    except ImportError as exc:
        raise ParseError("python-docx is not installed (pip install python-docx).") from exc

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"Failed to open DOCX document: {exc}") from exc

    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def table_text(table) -> str:
        rows: list[str] = []
        for row in table.rows:
            cells: list[str] = []
            seen_cells = set()
            for cell in row.cells:
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                cell_text = " ".join(part.strip() for part in cell.text.splitlines() if part.strip())
                cells.append(cell_text)
            row_text = "\t".join(cells).strip()
            if row_text:
                rows.append(row_text)
        return "\n".join(rows)

    def block_texts(container, parent) -> list[str]:
        element = container._element if hasattr(container, "_element") else container
        values: list[str] = []
        for child in element.iterchildren():
            if child.tag == qn("w:p"):
                value = Paragraph(child, parent).text.strip()
            elif child.tag == qn("w:tbl"):
                value = table_text(Table(child, parent)).strip()
            else:
                continue
            if value:
                values.append(value)
        return values

    parts: list[str] = []
    for section in doc.sections:
        header_text = block_texts(section.header, section.header)
        parts.extend(f"[Header] {value}" for value in header_text)
    parts.extend(block_texts(doc.element.body, doc))
    for section in doc.sections:
        footer_text = block_texts(section.footer, section.footer)
        parts.extend(f"[Footer] {value}" for value in footer_text)

    full_text = "\n".join(parts)
    para_map = {
        match.start(): index
        for index, match in enumerate(re.finditer(r"[^\n]+", full_text), start=1)
    }
    unsupported_tags = {
        "w:txbxContent": "text boxes",
        "w:footnoteReference": "footnotes",
        "w:endnoteReference": "endnotes",
        "w:commentRangeStart": "comments",
        "w:altChunk": "embedded external content",
        "w:drawing": "embedded drawings or images",
    }
    present_tags = {element.tag.rsplit("}", 1)[-1] for element in doc.element.body.iter()}
    warning_labels = [
        label for tag, label in unsupported_tags.items()
        if tag.split(":", 1)[-1] in present_tags
    ]
    warnings = (
        ["Some Word content was not extracted (" + ", ".join(warning_labels) + "); verify the original document."]
        if warning_labels else []
    )
    return {
        "text": full_text,
        "page_map": para_map,  # paragraph index map for DOCX
        "source_type": "docx",
        "word_count": len(full_text.split()),
        "warnings": warnings,
    }


def _parse_txt(data: bytes) -> dict:
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ParseError("Cannot decode text file — please save it as UTF-8 or latin-1.")

    return {
        "text": text,
        "page_map": {},
        "source_type": "txt",
        "word_count": len(text.split()),
    }


def _parse_csv(data: bytes) -> dict:
    decoded = _decode_text(data)
    if "\x00" in decoded:
        raise ParseError("CSV contains binary data and could not be decoded safely.")
    try:
        rows = list(csv.reader(io.StringIO(decoded)))
    except csv.Error as exc:
        raise ParseError(f"CSV could not be read: {exc}") from exc
    if not rows:
        raise ParseError("CSV contains no rows.")
    return {
        "text": decoded,
        "page_map": {0: 1},
        "source_type": "csv",
        "word_count": len(decoded.split()),
    }


def _parse_xlsx(data: bytes) -> dict:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ParseError("openpyxl is required for XLSX files (pip install openpyxl).") from exc

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        formula_workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as exc:
        raise ParseError(f"XLSX workbook could not be opened: {exc}") from exc

    page_records: list[dict] = []
    parts: list[str] = []
    page_map: dict[int, int] = {}
    offset = 0
    try:
        for sheet_number, (sheet, formula_sheet) in enumerate(
            zip(workbook.worksheets, formula_workbook.worksheets), start=1
        ):
            rows: list[str] = []
            missing_cached_formula = False
            for value_row, formula_row in zip(sheet.iter_rows(), formula_sheet.iter_rows()):
                values: list[str] = []
                for value_cell, formula_cell in zip(value_row, formula_row):
                    value = value_cell.value
                    formula = formula_cell.value
                    if formula_cell.data_type == "f":
                        if value is None:
                            values.append(
                                f"[formula {formula_cell.coordinate}: {formula}; calculated value unavailable]"
                            )
                            missing_cached_formula = True
                        else:
                            values.append(
                                f"[formula {formula_cell.coordinate}: {formula}; calculated value: {value}]"
                            )
                    else:
                        values.append("" if value is None else str(value))
                rows.append("\t".join(values).rstrip())
            text = "\n".join(row for row in rows if row.strip())
            warnings = (
                ["Formula cells without cached results were preserved as formulas; calculated values could not be verified."]
                if missing_cached_formula else []
            )
            if text.strip():
                page_map[offset] = sheet_number
                page_records.append({
                    "page_number": sheet_number,
                    "section_name": sheet.title,
                    "text": text,
                    "method": "spreadsheet",
                    "quality": 0.6 if missing_cached_formula else 1.0,
                    "confidence": None,
                    "warnings": warnings,
                })
                parts.append(text)
                offset += len(text) + 1
            else:
                page_records.append({
                    "page_number": sheet_number,
                    "section_name": sheet.title,
                    "text": "",
                    "method": "spreadsheet",
                    "quality": 0.0,
                    "confidence": None,
                    "warnings": ["No populated cells were found in this worksheet."],
                })
    finally:
        workbook.close()
        formula_workbook.close()

    text = "\n".join(parts)
    return {
        "text": text,
        "page_map": page_map,
        "source_type": "xlsx",
        "word_count": len(text.split()),
        "page_records": page_records,
    }


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("Cannot decode text file — please save it as UTF-8 or latin-1.")


def _text_quality(text: str) -> float:
    if not text.strip():
        return 0.0
    visible = sum(character.isprintable() for character in text)
    return round(visible / max(len(text), 1), 2)


def _aggregate_status(pages: list[dict]) -> str:
    if not pages:
        return "FAILED"
    if not any(page["text"].strip() for page in pages):
        return "NEEDS_ATTENTION"
    if any(page["warnings"] or page["quality"] < 0.55 for page in pages):
        return "NEEDS_ATTENTION"
    return "PROCESSED"


def _has_usable_pdf_text(text: str) -> bool:
    tokens = [token for token in text.split() if any(char.isalnum() for char in token)]
    return len(tokens) >= 3 and sum(char.isalnum() for char in text) >= 12


def _pdf_image_coverage(page) -> float:
    """Estimate visible raster-image coverage without rendering the page."""
    try:
        import fitz

        page_area = max(float(page.rect.width * page.rect.height), 1.0)
        image_area = 0.0
        for info in page.get_image_info(xrefs=True):
            bbox = info.get("bbox")
            if not bbox:
                continue
            rect = fitz.Rect(bbox) & page.rect
            if not rect.is_empty:
                image_area += max(0.0, rect.width * rect.height)
        return min(image_area / page_area, 1.0)
    except Exception:
        return 0.0


def _ocr_completeness_warnings(text: str, confidence: float) -> list[str]:
    words = [token for token in text.split() if any(char.isalnum() for char in token)]
    if not words:
        return []
    if len(words) < 5 and confidence < 0.55:
        return [
            "OCR did not recover enough reliable text to support proposals; verify the original page."
        ]
    if len(words) < 12:
        return [
            "OCR recovered very little text; the page may be incomplete. Verify it against the original."
        ]
    return []


def _configure_tesseract(pytesseract) -> str:
    configured_command = os.getenv("TESSERACT_CMD", "").strip()
    default_command = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    command = configured_command or (
        str(default_command) if default_command.is_file() else shutil.which("tesseract")
    )
    if not command or not Path(command).expanduser().is_file():
        target = configured_command or "the default Windows installation"
        raise ParseError(
            f"Tesseract executable was not found at {target}. Set TESSERACT_CMD "
            "to the installed tesseract.exe path."
        )

    tessdata_setting = os.getenv("TESSDATA_PREFIX", "").strip()
    tessdata = (
        Path(tessdata_setting).expanduser()
        if tessdata_setting else Path(command).parent / "tessdata"
    )
    if (
        not (tessdata / "eng.traineddata").is_file()
        and (tessdata / "tessdata" / "eng.traineddata").is_file()
    ):
        tessdata /= "tessdata"
    if not (tessdata / "eng.traineddata").is_file():
        raise ParseError(
            f"English Tesseract language data (eng.traineddata) was not found in "
            f"{tessdata}. Set TESSDATA_PREFIX to the directory containing it."
        )

    pytesseract.pytesseract.tesseract_cmd = str(Path(command).expanduser().resolve())
    os.environ["TESSDATA_PREFIX"] = str(tessdata.resolve())
    return ""


def _is_tesseract_unavailable(error: Exception) -> bool:
    return (
        isinstance(error, (ImportError, OSError, ParseError))
        or type(error).__name__ == "TesseractNotFoundError"
    )


def _deskew_image(image):
    """Correct small scan skew when NumPy is available; preserve the image otherwise."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return image
    sample = image.copy()
    sample.thumbnail((800, 800))
    resampling = getattr(Image, "Resampling", Image)
    best_angle, best_score = 0.0, -1.0
    for angle_step in range(-10, 11):
        angle = angle_step * 0.5
        rotated = sample.rotate(angle, resample=resampling.BILINEAR, fillcolor=255)
        binary = np.asarray(rotated.convert("L")) < 160
        projection = binary.sum(axis=1)
        score = float(projection.var())
        if score > best_score:
            best_angle, best_score = angle, score
    if best_angle:
        return image.rotate(
            best_angle, resample=resampling.BICUBIC, expand=True, fillcolor=255
        )
    return image


def _parse_pdf_detailed(data: bytes) -> dict:
    """Extract each PDF page; OCR image pages locally and disclose uncertainty."""
    try:
        import fitz
    except ImportError as exc:
        raise ParseError("PyMuPDF is not installed (pip install PyMuPDF).") from exc

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ParseError(f"Failed to open PDF document: {exc}") from exc
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise ParseError(
            "This PDF is password-protected. Remove the password and upload it again."
        )
    if doc.page_count > MAX_PDF_PAGES:
        page_count = doc.page_count
        doc.close()
        raise ResourceLimitError(
                f"PDF page count is {page_count}; the configured limit is {MAX_PDF_PAGES}."
        )

    page_records: list[dict] = []
    rendered_pixels = 0
    render_scale = min(max(PDF_RENDER_SCALE, 0.5), 3.0)
    try:
        for page_number, page in enumerate(doc, start=1):
            page_text = page.get_text("text") or ""
            native_text = page_text.strip()
            image_coverage = _pdf_image_coverage(page)
            native_tokens = sum(1 for token in native_text.split() if any(ch.isalnum() for ch in token))
            needs_ocr = not _has_usable_pdf_text(native_text) or (
                image_coverage >= 0.18 and native_tokens < 12
            )
            method = "text"
            confidence = None
            warnings: list[str] = []
            if needs_ocr:
                method = "ocr" if not native_text else "text+ocr"
                try:
                    import pytesseract
                    from PIL import Image, ImageOps

                    tesseract_config = _configure_tesseract(pytesseract)
                    render_width = math.ceil(page.rect.width * render_scale)
                    render_height = math.ceil(page.rect.height * render_scale)
                    rendered_pixels += render_width * render_height
                    if rendered_pixels > MAX_PDF_RENDER_PIXELS:
                        raise ResourceLimitError(
                            "PDF OCR would exceed the configured total rendering-pixel budget. "
                            "Split the document or reduce its page dimensions before uploading."
                        )
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(render_scale, render_scale), alpha=False
                    )
                    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
                    image = ImageOps.autocontrast(image)
                    try:
                        orientation = pytesseract.image_to_osd(
                            image,
                            output_type=pytesseract.Output.DICT,
                            config=tesseract_config,
                        )
                        rotation = int(orientation.get("rotate", 0)) % 360
                        if rotation:
                            image = image.rotate(360 - rotation, expand=True)
                    except Exception as exc:
                        if _is_tesseract_unavailable(exc):
                            raise
                        warnings.append("Page rotation could not be detected.")
                    image = _deskew_image(image)
                    data_result = pytesseract.image_to_data(
                        image,
                        lang="eng",
                        output_type=pytesseract.Output.DICT,
                        config=f"{tesseract_config} --psm 6",
                    )
                    words = data_result.get("text", [])
                    line_groups: dict[tuple[int, int, int], list[str]] = {}
                    for index, word in enumerate(words):
                        if not isinstance(word, str) or not word.strip():
                            continue
                        key = tuple(
                            int(data_result.get(field, [0] * len(words))[index])
                            for field in ("block_num", "par_num", "line_num")
                        )
                        line_groups.setdefault(key, []).append(word.strip())
                    ocr_lines = [" ".join(line) for line in line_groups.values()]
                    confidences = [
                        float(value) for value in data_result.get("conf", [])
                        if str(value).strip() not in {"", "-1"}
                    ]
                    ocr_text = " ".join(ocr_lines)
                    page_text = "\n".join(
                        text for text in (native_text, ocr_text) if text
                    )
                    if image_coverage >= 0.18:
                        warnings.append(
                            "OCR was used for substantial image content; reconstructed text may not preserve table columns or layout."
                        )
                    confidence = (
                        round(sum(confidences) / (100 * len(confidences)), 2)
                        if confidences else 0.0
                    )
                    warnings.extend(_ocr_completeness_warnings(ocr_text, confidence))
                    if confidence < 0.55:
                        warnings.append(
                            "OCR confidence is low; verify numbers and wording against the original."
                        )
                except ResourceLimitError:
                    raise
                except Exception as exc:
                    page_text = native_text
                    if isinstance(exc, ParseError):
                        warning = str(exc)
                    elif _is_tesseract_unavailable(exc):
                        warning = (
                            "OCR could not run; install the Tesseract executable "
                            "to process scanned pages."
                        )
                    else:
                        warning = f"OCR failed ({type(exc).__name__}); review this page manually."
                    warnings.append(warning)
            if not page_text.strip():
                warnings.append("No readable text was extracted from this page.")
            if page.rotation:
                warnings.append(f"PDF page has a stored rotation of {page.rotation} degrees.")
            page_records.append({
                "page_number": page_number,
                "text": page_text,
                "method": method,
                "quality": confidence if confidence is not None else _text_quality(page_text),
                "confidence": confidence,
                "warnings": list(dict.fromkeys(warnings)),
                "ocr_lines": ocr_lines if method in {"ocr", "text+ocr"} and "ocr_lines" in locals() else [],
            })
    finally:
        doc.close()

    full_text = "\n".join(record["text"] for record in page_records if record["text"].strip())
    return {
        "text": full_text,
        "page_map": {},
        "source_type": "pdf",
        "word_count": len(full_text.split()),
        "page_records": page_records,
        "unobserved_pages": [
            item["page_number"] for item in page_records if not item["text"].strip()
        ],
        "extraction_status": _aggregate_status(page_records),
    }


def _parse_image(data: bytes) -> dict:
    """
    Parse image files (.png, .jpg, .jpeg) using Pillow and pytesseract OCR.
    Catches missing Tesseract binary or missing library gracefully with a helpful message.
    """
    try:
        from PIL import Image
        import pytesseract
        tesseract_config = _configure_tesseract(pytesseract)
    except Exception as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError(
            "Tesseract OCR engine is not installed or not found on PATH. "
            "Please upload text, PDF, or DOCX, or install Tesseract."
        ) from exc

    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ParseError(
                f"Image dimensions ({width} x {height}) exceed the configured pixel limit "
                f"({MAX_IMAGE_PIXELS:,})."
            )
        text = pytesseract.image_to_string(img, lang="eng", config=tesseract_config)
    except Exception as exc:
        err_msg = str(exc).lower()
        if (
            "tesseract is not installed" in err_msg
            or "not found" in err_msg
            or "tesseractnotfound" in err_msg
            or "no such file" in err_msg
            or isinstance(exc, FileNotFoundError)
        ):
            raise ParseError(
                "Tesseract OCR engine is not installed or not found on PATH. "
                "Please upload text, PDF, or DOCX, or install Tesseract."
            ) from exc
        raise ParseError(
            "Tesseract OCR engine is not installed or not found on PATH. "
            "Please upload text, PDF, or DOCX, or install Tesseract."
        ) from exc

    return {
        "text": text,
        "page_map": {},
        "source_type": "image",
        "word_count": len(text.split()),
    }
