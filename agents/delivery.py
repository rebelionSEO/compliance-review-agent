"""
DeliveryAgent — uploads revised articles to Google Drive as proper Google Docs.

Uses the Google Drive API with a service account to:
- Create a real Google Doc (application/vnd.google-apps.document)
- Import HTML content so bold headings and hyperlinks are preserved
- Save to the correct output folder based on content type

Requires: google-auth google-api-python-client
Credentials: knowledge/credentials/google_service_account.json
"""

import json
import re
from pathlib import Path

BASE = Path(__file__).parent.parent
CREDS_PATH = BASE / "knowledge" / "credentials" / "google_service_account.json"
DRIVE_CONFIG = BASE / "knowledge" / "memory" / "drive_config.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def _get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(CREDS_PATH), scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_folder_id(content_type: str) -> str | None:
    if not DRIVE_CONFIG.exists():
        return None
    config = json.loads(DRIVE_CONFIG.read_text(encoding="utf-8"))
    folder = config.get("output_folders", {}).get(content_type)
    return folder["id"] if folder else None


def md_to_html(text: str) -> str:
    """
    Convert markdown to clean HTML for Google Docs import.

    Critical ordering rule: hyperlinks MUST be processed BEFORE bold/italic,
    because **[text](url)** would be consumed by the bold regex first, breaking links.

    Handles: headings, bold, italic, hyperlinks, bullet lists, numbered lists,
    tables, visual placeholder blocks, CTA boxes, stats bars.
    """
    lines = text.split('\n')
    out = ['<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;font-size:11pt;line-height:1.6;max-width:900px;margin:0 auto;padding:20px;">']
    in_list = False
    in_ol = False
    in_table = False
    table_rows: list = []

    def process_inline(s: str) -> str:
        # 1. Links FIRST — must happen before bold eats the brackets
        s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', s)
        # 2. Bold
        s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
        # 3. Italic (single asterisk, not adjacent to another)
        s = re.sub(r'(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', s)
        return s

    def flush_list():
        nonlocal in_list, in_ol
        if in_list:
            out.append('</ul>')
            in_list = False
        if in_ol:
            out.append('</ol>')
            in_ol = False

    def flush_table():
        nonlocal in_table, table_rows
        if not table_rows:
            in_table = False
            return
        out.append('<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;margin:16px 0;font-size:10pt;">')
        header_done = False
        for row in table_rows:
            # Skip pure separator rows (---, :-:, etc.)
            if all(re.match(r'^[-: ]+$', c) for c in row if c):
                continue
            is_header = not header_done
            header_done = True
            bg = 'background-color:#1a3a5c;color:white;' if is_header else ('background-color:#f5f5f5;' if len(out) % 2 == 0 else '')
            out.append(f'<tr style="{bg}">')
            for cell in row:
                tag = 'th' if is_header else 'td'
                out.append(f'<{tag} style="padding:8px;">{process_inline(cell.strip())}</{tag}>')
            out.append('</tr>')
        out.append('</table>')
        in_table = False
        table_rows = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Table row detection
        if stripped.startswith('|') and stripped.endswith('|') and '|' in stripped[1:-1]:
            flush_list()
            if not in_table:
                in_table = True
                table_rows = []
            cells = [c.strip() for c in stripped.split('|')[1:-1]]
            table_rows.append(cells)
            i += 1
            continue
        elif in_table:
            flush_table()
            continue  # re-process current line

        # Empty line
        if not stripped:
            flush_list()
            out.append('<br>')
            i += 1
            continue

        # Visual/placeholder blocks
        if re.match(r'^\[?\\?\[?VISUAL:', stripped):
            flush_list()
            content = re.sub(r'^\[?\\?\[?VISUAL:\s*', '', stripped).rstrip(r'\]')
            out.append(f'<table width="100%" cellpadding="12" style="border:2px dashed #aaa;background:#f9f9f9;margin:12px 0;"><tr><td style="color:#666;font-style:italic;font-size:10pt;">[VISUAL: {content}]</td></tr></table>')
            i += 1
            continue

        # Headings: ## **Title** or ## Title
        heading_match = re.match(r'^(#{1,4})\s+\*?\*?(.+?)\*?\*?$', stripped)
        if heading_match:
            flush_list()
            level = min(len(heading_match.group(1)), 4)
            title_text = heading_match.group(2).strip('*').strip()
            sizes = {1: '20pt', 2: '16pt', 3: '13pt', 4: '12pt'}
            out.append(f'<h{level} style="font-size:{sizes[level]};margin-top:20px;margin-bottom:8px;"><b>{process_inline(title_text)}</b></h{level}>')
            i += 1
            continue

        # Bullet lists
        if stripped.startswith('- ') or stripped.startswith('• '):
            if in_ol:
                out.append('</ol>')
                in_ol = False
            if not in_list:
                out.append('<ul style="margin:8px 0;padding-left:24px;">')
                in_list = True
            out.append(f'<li style="margin-bottom:4px;">{process_inline(stripped[2:])}</li>')
            i += 1
            continue

        # Numbered lists
        if re.match(r'^\d+[\.\)]\s', stripped):
            if in_list:
                out.append('</ul>')
                in_list = False
            if not in_ol:
                out.append('<ol style="margin:8px 0;padding-left:24px;">')
                in_ol = True
            out.append(f'<li style="margin-bottom:4px;">{process_inline(re.sub(r"^\d+[\.\)]\s+", "", stripped))}</li>')
            i += 1
            continue

        flush_list()

        # Stats/pipe bars (not table format)
        if '|' in stripped and not stripped.startswith('|') and stripped.count('|') >= 2:
            out.append(f'<table width="100%" cellpadding="10" style="border:1px solid #ccc;background:#f0f4f8;margin:12px 0;text-align:center;"><tr><td>{process_inline(stripped)}</td></tr></table>')
            i += 1
            continue

        # Standalone bold lines that act as CTA boxes (long, start+end with **)
        if stripped.startswith('**') and stripped.endswith('**') and len(stripped) > 60 and '\n' not in stripped:
            inner = stripped[2:-2]
            out.append(f'<table width="100%" cellpadding="14" style="border:1px solid #1a3a5c;background:#f0f4f8;margin:12px 0;"><tr><td><b>{process_inline(inner)}</b></td></tr></table>')
            i += 1
            continue

        # Regular paragraph
        out.append(f'<p style="margin:8px 0;">{process_inline(stripped)}</p>')
        i += 1

    flush_list()
    if in_table:
        flush_table()

    out.append('</body></html>')
    return '\n'.join(out)


def upload_as_google_doc(
    content: str,
    title: str,
    content_type: str = "blog",
    folder_id: str = None,
    is_html: bool = False,
) -> dict:
    """
    Upload content as a proper Google Doc with formatting.

    content     — article text (markdown or HTML)
    title       — document title
    content_type — blog | email | paid_media | landing_page
    folder_id   — override folder (uses drive_config.json if None)
    is_html     — True if content is already HTML, False if markdown

    Returns: { "id": str, "url": str, "title": str }
    """
    from googleapiclient.http import MediaInMemoryUpload

    drive = _get_service()

    # Convert to HTML if needed
    html = content if is_html else md_to_html(content)

    # Get target folder
    if not folder_id:
        folder_id = _get_folder_id(content_type)

    file_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",  # OUTPUT = Google Doc
    }
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaInMemoryUpload(
        html.encode("utf-8"),
        mimetype="text/html",   # INPUT = HTML → imported with formatting
        resumable=False,
    )

    result = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()

    return {
        "id": result["id"],
        "url": result.get("webViewLink", f"https://docs.google.com/document/d/{result['id']}/edit"),
        "title": result["name"],
    }


def upload_feedback_doc(
    feedback_md: str,
    article_title: str,
    content_type: str = "blog",
    folder_id: str = None,
) -> dict:
    """Upload a compliance feedback document to the output folder."""
    title = f"{article_title} – Compliance Feedback – CCA"
    return upload_as_google_doc(feedback_md, title, content_type, folder_id)


def upload_revised_article(
    revised_content: str,
    article_title: str,
    content_type: str = "blog",
    folder_id: str = None,
    date_str: str = "",
) -> dict:
    """Upload the compliance-revised article to the correct output folder."""
    from datetime import datetime
    date = date_str or datetime.now().strftime("%m.%d.%Y")
    title = f"{article_title} – REVISED – {date} – CCA"
    return upload_as_google_doc(revised_content, title, content_type, folder_id)
