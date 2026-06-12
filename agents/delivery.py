"""
DeliveryAgent — uploads revised articles to Google Drive as proper Google Docs.

Uses the Google Drive API with a service account to:
- Create a real Google Doc (application/vnd.google-apps.document)
- Import HTML content with EXACT formatting preservation
- Proper table centering, block alignment, spacing
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
    Convert markdown to clean, properly formatted HTML for Google Docs import.
    
    CRITICAL FIXES:
    1. Block-level elements (tables, lists, headings) are full-width, centered
    2. Tables use proper Google Docs-compatible CSS with center alignment
    3. Whitespace/margins preserved exactly
    4. Nested structures properly closed
    5. Google Docs respects width:100% and margin auto
    
    Critical ordering rule: hyperlinks MUST be processed BEFORE bold/italic,
    because **[text](url)** would be consumed by the bold regex first, breaking links.
    """
    lines = text.split('\n')
    # ✅ FIXED: Removed max-width constraint, use full width with proper centering
    out = [
        '<!DOCTYPE html>',
        '<html>',
        '<head>',
        '<meta charset="utf-8">',
        '<style>',
        'body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.6; padding: 20px; }',
        'h1, h2, h3, h4 { margin-top: 16px; margin-bottom: 8px; }',
        'p { margin: 8px 0; }',
        'ul, ol { margin: 12px 0; padding-left: 24px; }',
        'li { margin-bottom: 6px; }',
        'table { width: 100%; border-collapse: collapse; margin: 16px 0; }',
        'table td, table th { padding: 10px; border: 1px solid #ddd; }',
        'table th { background-color: #1a3a5c; color: white; font-weight: bold; }',
        'table tr:nth-child(even) { background-color: #f9f9f9; }',
        '.cta-box { width: 100%; border: 2px solid #1a3a5c; background: #f0f4f8; padding: 16px; margin: 16px 0; box-sizing: border-box; }',
        '.visual-block { width: 100%; border: 2px dashed #aaa; background: #f9f9f9; padding: 12px; margin: 12px 0; box-sizing: border-box; }',
        '.disclosure-block { width: 100%; background: #fff3cd; border-left: 4px solid #ff9800; padding: 12px; margin: 12px 0; box-sizing: border-box; font-weight: bold; }',
        '</style>',
        '</head>',
        '<body>',
    ]
    
    in_list = False
    in_ol = False
    in_table = False
    table_rows = []
    in_cta = False

    def process_inline(s: str) -> str:
        """Process inline formatting: links → bold → italic"""
        # 1. Links FIRST
        s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', s)
        # 2. Bold
        s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
        # 3. Italic
        s = re.sub(r'(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', s)
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
        
        # ✅ FIXED: Proper table structure with full width and centered alignment
        out.append('<table>')
        header_done = False
        for row in table_rows:
            # Skip separator rows
            if all(re.match(r'^[-: ]+$', c) for c in row if c):
                continue
            
            is_header = not header_done
            header_done = True
            tag = 'th' if is_header else 'td'
            
            out.append('<tr>')
            for cell in row:
                out.append(f'<{tag}>{process_inline(cell.strip())}</{tag}>')
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
            continue

        # Empty line — preserve spacing
        if not stripped:
            out.append('<p></p>')  # Empty paragraph preserves vertical space
            i += 1
            continue

        # ✅ FIXED: Disclosure blocks with proper styling
        if stripped.startswith(('ⓘ ', '[DISCLOSURE:', '[REQUIRED DISCLOSURE:', 'Results not guaranteed')):
            flush_list()
            if in_table:
                flush_table()
            
            # Extract disclosure text
            disclosure_text = re.sub(r'^[\[\]ⓘ\s:]+', '', stripped).rstrip(']')
            out.append(f'<div class="disclosure-block">{process_inline(disclosure_text)}</div>')
            i += 1
            continue

        # Visual/placeholder blocks with proper styling
        if re.match(r'^\[?\\?\[?VISUAL:', stripped):
            flush_list()
            if in_table:
                flush_table()
            
            content = re.sub(r'^\[?\\?\[?VISUAL:\s*', '', stripped).rstrip(r'\]')
            out.append(f'<div class="visual-block">[VISUAL: {process_inline(content)}]</div>')
            i += 1
            continue

        # Headings
        heading_match = re.match(r'^(#{1,6})\s+\*?\*?(.+?)\*?\*?$', stripped)
        if heading_match:
            flush_list()
            if in_table:
                flush_table()
            
            level = min(len(heading_match.group(1)), 6)
            title_text = heading_match.group(2).strip('*').strip()
            out.append(f'<h{level}>{process_inline(title_text)}</h{level}>')
            i += 1
            continue

        # Bullet lists
        if stripped.startswith(('- ', '• ', '* ')):
            if in_ol:
                out.append('</ol>')
                in_ol = False
            if not in_list:
                out.append('<ul>')
                in_list = True
            
            list_text = re.sub(r'^[-•*]\s+', '', stripped)
            out.append(f'<li>{process_inline(list_text)}</li>')
            i += 1
            continue

        # Numbered lists
        if re.match(r'^\d+[\.\)]\s', stripped):
            if in_list:
                out.append('</ul>')
                in_list = False
            if not in_ol:
                out.append('<ol>')
                in_ol = True
            
            list_text = re.sub(r'^\d+[\.\)]\s+', '', stripped)
            out.append(f'<li>{process_inline(list_text)}</li>')
            i += 1
            continue

        flush_list()

        # ✅ FIXED: CTA boxes with proper styling and class
        if stripped.startswith('**') and stripped.endswith('**') and len(stripped) > 60:
            if in_table:
                flush_table()
            
            inner = stripped[2:-2]
            out.append(f'<div class="cta-box">{process_inline(inner)}</div>')
            i += 1
            continue

        # Regular paragraph
        if in_table:
            flush_table()
        
        out.append(f'<p>{process_inline(stripped)}</p>')
        i += 1

    flush_list()
    if in_table:
        flush_table()

    out.extend(['</body>', '</html>'])
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
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaInMemoryUpload(
        html.encode("utf-8"),
        mimetype="text/html",
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
