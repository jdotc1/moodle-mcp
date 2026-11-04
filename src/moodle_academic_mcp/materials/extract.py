"""Text extraction with page/slide references. Never executes content; never invents text."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree as ET  # noqa: S405  (only parsing Office XML after size checks)

MAX_ZIP_MEMBER = 50 * 1024 * 1024
MAX_PAGES = 2000
_TEXT_EXT = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".py",
    ".js",
    ".ts",
    ".sql",
    ".sh",
    ".rs",
    ".go",
    ".rb",
    ".php",
    ".cs",
    ".kt",
    ".tex",
    ".log",
    ".html",
    ".htm",
    ".ipynb",
    ".r",
    ".m",
    ".hs",
    ".pl",
    ".ini",
    ".toml",
    ".css",
    ".lisp",
    ".scm",
    ".asm",
    ".s",
}

_INJECTION = re.compile(
    r"(?i)(ignore (all )?(previous|prior|above) instructions|disregard (the )?(system|previous)|"
    r"you are now|system prompt|reveal (your|the) (token|secret|key|password)|"
    r"send (it |this |the )?(to|via) .{0,40}(http|@)|exfiltrate|do not tell the user|"
    r"ignore as instru[cç][õo]es anteriores|desconsidere as instru[cç][õo]es)"
)


@dataclass
class Extracted:
    chunks: list[tuple[str, str]] = field(default_factory=list)  # (location, text)
    kind: str = "text"
    needs_ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def text_len(self) -> int:
        return sum(len(t) for _, t in self.chunks)


def injection_flag(text: str) -> bool:
    """Heuristic only: flags instruction-like text so clients can show extra caution. Does not gate anything."""
    return bool(_INJECTION.search(text))


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.out))).strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _split(text: str, size: int = 3000) -> list[str]:
    """Chunks on paragraph boundaries for plain text."""
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > size and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def _zip_member(z: zipfile.ZipFile, name: str) -> bytes:
    info = z.getinfo(name)
    if info.file_size > MAX_ZIP_MEMBER:
        raise ValueError(f"{name} is too large")
    return z.read(name)


def _xml_text(xml: bytes, ns_text: str, ns_para: str) -> str:
    root = ET.fromstring(xml)  # noqa: S314 - Office XML from the user's own course material, size-limited
    paras = []
    for p in root.iter(ns_para):
        paras.append("".join(t.text or "" for t in p.iter(ns_text)))
    return "\n".join(x for x in paras if x.strip())


def extract_docx(data: bytes) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = _xml_text(_zip_member(z, "word/document.xml"), w + "t", w + "p")
    ex = Extracted(kind="docx")
    ex.chunks = [(f"part {i + 1}", t) for i, t in enumerate(_split(text))]
    return ex


def extract_pptx(data: bytes) -> Extracted:
    ex = Extracted(kind="pptx")
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slides = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]),
        )
        for n in slides:
            num = int(re.findall(r"\d+", n)[0])
            ex.chunks.append((f"slide {num}", _xml_text(_zip_member(z, n), a + "t", a + "p")))
        for n in sorted(x for x in z.namelist() if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", x)):
            num = int(re.findall(r"\d+", n)[0])
            t = _xml_text(_zip_member(z, n), a + "t", a + "p")
            if t.strip():
                ex.chunks.append((f"slide {num} notes", t))
    return ex


def _odf_paragraphs(el: ET.Element) -> list[str]:
    t = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

    def inline_text(node: ET.Element) -> str:
        if node.tag == t + "tab":
            content = "\t"
        elif node.tag == t + "line-break":
            content = "\n"
        elif node.tag == t + "s":
            count = int(node.get(t + "c", "1"))
            if count < 1 or count > MAX_ZIP_MEMBER:
                raise ValueError("ODF repeated-space count is outside the extraction limit")
            content = " " * count
        else:
            content = (node.text or "") + "".join(inline_text(child) for child in node)
        return content + (node.tail or "")

    def paragraph_text(node: ET.Element) -> str:
        return (node.text or "") + "".join(inline_text(child) for child in node)

    return [paragraph_text(p) for p in el.iter() if p.tag in (t + "p", t + "h")]


def extract_odf(data: bytes) -> Extracted:
    """ODT (paragraphs), ODP (one chunk per slide) and ODS (one chunk per sheet) from content.xml."""
    office = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
    table = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    draw = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(_zip_member(z, "content.xml"))  # noqa: S314 - ODF from the user's own course material
    found = root.find(office + "body")
    if found is None or len(found) == 0:
        raise ValueError("content.xml has no document body")
    body = found[0]
    if body.tag == office + "presentation":
        ex = Extracted(kind="odp")
        for i, page in enumerate(body.findall(draw + "page"), 1):
            ex.chunks.append((f"slide {i}", "\n".join(x for x in _odf_paragraphs(page) if x.strip())))
    elif body.tag == office + "spreadsheet":
        ex = Extracted(kind="ods")
        for i, sheet in enumerate(body.findall(table + "table"), 1):
            ex.chunks.append((f"sheet {i}", "\n".join(x for x in _odf_paragraphs(sheet) if x.strip())))
    else:
        ex = Extracted(kind="odt")
        text = "\n".join(_odf_paragraphs(body))
        ex.chunks = [(f"part {i + 1}", t) for i, t in enumerate(_split(text))]
    return ex


def extract_pdf(data: bytes) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "PDF extraction needs the optional dependency: pip install 'moodle-academic-mcp[pdf]'"
        ) from None
    ex = Extracted(kind="pdf")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                ex.warnings.append("PDF is password-protected; not extracted.")
                return ex
        except Exception:
            ex.warnings.append("PDF is encrypted; not extracted.")
            return ex
    empty = 0
    n = min(len(reader.pages), MAX_PAGES)
    for i in range(n):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception as e:  # damaged page
            ex.warnings.append(f"page {i + 1}: extraction failed ({type(e).__name__})")
            t = ""
        if len(t.strip()) < 3:
            empty += 1
        ex.chunks.append((f"page {i + 1}", t))
    if len(reader.pages) > n:
        ex.warnings.append(f"Only the first {n} pages were extracted.")
    if n and empty / n > 0.6:
        ex.needs_ocr = True
        ex.warnings.append(
            "Most pages contain no text layer: this looks like a scanned PDF. OCR is required "
            "(optional adapter: install the 'ocr' extra and tesseract). No text was invented."
        )
    return ex


def ocr_pdf(data: bytes, lang: str = "por+eng") -> Extracted:
    """Optional OCR adapter (pytesseract + pdf2image + system tesseract/poppler). Not run in CI."""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        raise ImportError(
            "OCR needs: pip install 'moodle-academic-mcp[ocr]' plus system packages tesseract-ocr "
            "and poppler-utils"
        ) from None
    ex = Extracted(kind="pdf-ocr")
    for i, img in enumerate(convert_from_bytes(data, dpi=200)[:MAX_PAGES]):
        ex.chunks.append((f"page {i + 1} (ocr)", pytesseract.image_to_string(img, lang=lang)))
    ex.warnings.append("Text obtained by OCR: may contain recognition errors.")
    return ex


def extract(filename: str, data: bytes, mimetype: str | None = None, *, ocr: bool = False) -> Extracted:
    name = filename.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == ".pdf" or mimetype == "application/pdf":
        ex = extract_pdf(data)
        if ex.needs_ocr and ocr:
            return ocr_pdf(data)
        return ex
    if ext == ".docx":
        return extract_docx(data)
    if ext == ".pptx":
        return extract_pptx(data)
    if ext in (".odt", ".ods", ".odp"):
        return extract_odf(data)
    if ext in (".html", ".htm") or (mimetype or "").startswith("text/html"):
        ex = Extracted(kind="html")
        ex.chunks = [(f"part {i + 1}", t) for i, t in enumerate(_split(html_to_text(_decode(data))))]
        return ex
    if ext in _TEXT_EXT or (mimetype or "").startswith("text/"):
        ex = Extracted(kind="text")
        ex.chunks = [(f"part {i + 1}", t) for i, t in enumerate(_split(_decode(data)))]
        return ex
    ex = Extracted(kind="unsupported")
    ex.warnings.append(
        f"No extractor for {ext or mimetype or 'this file type'} "
        "(supported: pdf, docx, pptx, odt, ods, odp, html, txt/code). Binary files are not decoded."
    )
    return ex
