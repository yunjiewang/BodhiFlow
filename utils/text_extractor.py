"""
Text extraction for BodhiFlow via MarkItDown (Microsoft) and extract-msg for .msg.

Supports all formats supported by MarkItDown (use markitdown[all]), including: PDF, Word (.doc/.docx),
PowerPoint (.pptx), Excel (.xlsx/.xls), EPUB, Outlook (.msg), HTML, Markdown, plain text (.txt),
CSV, JSON, XML. For .msg files, we use extract_msg when available to reliably get email body;
MarkItDown alone often returns only the header. See: https://pypi.org/project/markitdown/
"""

import html
import re
from pathlib import Path
from typing import Optional

from utils.logger_config import get_logger

logger = get_logger(__name__)


def _html_to_plain(html_content: str) -> str:
    """
    Convert HTML to human-readable plain text.
    - Unescapes HTML entities (&lt; &gt; &amp; &nbsp; etc.)
    - Removes CSS/style fragments
    - Strips HTML tags
    - Normalizes whitespace (collapse excessive spaces/newlines)
    """
    if not html_content:
        return ""
    s = html.unescape(html_content)
    # Replace non-breaking space with regular space
    s = s.replace("\xa0", " ")
    # Remove CSS/style fragments (e.g. P{margin-top:0;margin-bottom:0;})
    s = re.sub(r"\{[^}]*;[^}]*\}", " ", s)
    # Strip HTML tags
    s = re.sub(r"<[^>]+>", " ", s)
    # Collapse multiple spaces to single space
    s = re.sub(r"[ \t]+", " ", s)
    # Normalize line endings
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Trim trailing space before newlines
    s = re.sub(r" +\n", "\n", s)
    # Collapse 3+ newlines to double newline (paragraph break)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _decode_html_body(html_bytes: bytes) -> str:
    """
    Decode HTML body bytes. Tries charset from meta tag, then gb2312/gbk (common for Chinese),
    then utf-8. Many Outlook emails from China use gb2312.
    """
    if not html_bytes:
        return ""
    # Try to extract charset from <meta charset=...> or content-type
    charset_match = re.search(
        rb'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)["\']?',
        html_bytes[:2000],
        re.IGNORECASE,
    )
    encodings = ["utf-8", "gb2312", "gbk", "cp936", "latin-1"]
    if charset_match:
        try:
            cs = charset_match.group(1).decode("ascii").lower()
            if cs not in encodings:
                encodings.insert(0, cs)
        except Exception:
            pass
    for enc in encodings:
        try:
            return html_bytes.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return html_bytes.decode("utf-8", errors="replace")


def _extract_text_from_msg_with_extract_msg(file_path: str) -> Optional[str]:
    """
    Extract full email content (header + body) from a .msg file using extract_msg.
    Do NOT use overrideEncoding: it breaks class-type detection for some .msg files
    (e.g. UnrecognizedMSGTypeError when class type is stored with UTF-16).
    When body is empty (HTML-only emails), use htmlBody and decode with charset detection.
    Returns None if extract_msg is not installed or conversion fails.
    """
    try:
        from extract_msg import openMsg
    except ImportError:
        return None
    msg = None
    try:
        # Do not use overrideEncoding - it corrupts class type detection for some files
        msg = openMsg(file_path)
        subject = getattr(msg, "subject", None) or ""
        sender = getattr(msg, "sender", None) or ""
        to = getattr(msg, "to", None) or ""
        body = getattr(msg, "body", None) or ""
        # When body is empty, many HTML-only emails have content in htmlBody
        if not body:
            html_body = getattr(msg, "htmlBody", None)
            if html_body:
                if isinstance(html_body, bytes):
                    html_body = _decode_html_body(html_body)
                body = _html_to_plain(html_body)
        # Body may be HTML; strip tags for plain-text use
        elif "<" in body and ">" in body:
            body = _html_to_plain(body)
        parts = [
            "# Email Message",
            "",
            f"**From:** {sender}",
            f"**To:** {to}",
            f"**Subject:** {subject}",
            "",
            "## Content",
            "",
            body,
        ]
        return "\n".join(parts).strip()
    except Exception as e:
        logger.debug(f"extract_msg failed for {file_path}: {e}")
        return None
    finally:
        if msg is not None:
            try:
                msg.close()
            except Exception:
                pass


def extract_text_from_file(file_path: str) -> str:
    """
    Extract plain text from a local file using MarkItDown (and for .msg, extract_msg when available).

    Supports .txt, .md, .pdf, .docx, .pptx, .xlsx, .xls, .epub, .msg, .html, .csv, .json, .xml and other formats supported by MarkItDown.

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        Extracted text content. Empty string on failure (log and return).
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        logger.warning(f"Text extract: path does not exist or is not a file: {file_path}")
        return ""

    # For .msg, prefer extract_msg so we get the email body (MarkItDown often returns only header).
    if path.suffix.lower() == ".msg":
        text = _extract_text_from_msg_with_extract_msg(str(path))
        if text:
            return text
        logger.info(f"Falling back to MarkItDown for .msg: {file_path}")

    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(path))
        text = (result.text_content or "").strip()
        if not text:
            logger.warning(f"Text extract: empty content for {file_path}")
        return text
    except Exception as e:
        logger.exception(f"Text extract failed for {file_path}: {e}")
        return ""


def extract_text_from_url(url: str, temp_dir: str) -> str:
    """
    Fetch a URL and extract text (e.g. webpage) using MarkItDown.

    If MarkItDown supports URL input directly, uses it; otherwise downloads
    to a temporary file then converts.

    Args:
        url: HTTP(S) URL to fetch.
        temp_dir: Directory for temporary file if download is needed.

    Returns:
        Extracted text content. Empty string on failure.
    """
    import os
    import tempfile

    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        # Try direct URL conversion if supported
        try:
            result = md.convert(url)
            text = (result.text_content or "").strip()
            if text:
                return text
        except Exception:
            pass

        # Fallback: download to temp file then convert
        import requests
        resp = requests.get(url, timeout=30, headers={"User-Agent": "BodhiFlow/1.0"})
        resp.raise_for_status()
        content = resp.content
        suffix = ".html"
        if "content-type" in resp.headers:
            ct = resp.headers["content-type"].lower()
            if "pdf" in ct:
                suffix = ".pdf"
            elif "xml" in ct or "rss" in ct:
                suffix = ".xml"

        os.makedirs(temp_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
        try:
            with open(fd, "wb") as f:
                f.write(content)
            result = md.convert(path)
            return (result.text_content or "").strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as e:
        logger.exception(f"Text extract from URL failed for {url}: {e}")
        return ""
