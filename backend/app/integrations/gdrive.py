"""
Create a Google Doc in the signed-in user's Drive. Scope: drive.file, which only lets
Kacy touch files it created itself (it cannot read the rest of their Drive).

Drive converts uploaded HTML into a real Google Doc when the metadata asks for the
Docs mime type, so headings and bullets survive.
"""
from __future__ import annotations

import html
import json
import re
import uuid

import httpx

UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


def markdown_to_html(src: str) -> str:
    """Just enough Markdown for an AI-drafted document: headings, bullets, bold, paragraphs."""
    def inline(text: str) -> str:
        return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html.escape(text))

    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for line in src.replace("\r", "").split("\n"):
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
        elif re.match(r"^\s*[-*]\s+", line):
            items.append(inline(re.sub(r"^\s*[-*]\s+", "", line)))
        elif line.strip():
            flush()
            out.append(f"<p>{inline(line)}</p>")
        else:
            flush()
    flush()
    return "<html><body>" + "".join(out) + "</body></html>"


def create_doc(token: str, title: str, markdown: str) -> dict:
    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": title, "mimeType": "application/vnd.google-apps.document"})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n{markdown_to_html(markdown)}\r\n"
        f"--{boundary}--"
    ).encode()
    resp = httpx.post(
        UPLOAD_URL, params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
        content=body, timeout=30)
    if resp.status_code == 403:
        raise PermissionError("Drive permission not granted; sign out and sign in again, and allow Drive file access")
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "link": data.get("webViewLink", "")}
