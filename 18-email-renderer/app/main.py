"""Email renderer: markdown in, email-client-safe HTML + plain text out."""
import html
import re
from urllib.parse import urlsplit

import markdown
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="email-renderer")

FONT = "font-family:Arial,Helvetica,sans-serif;"
TEXT_COLOR = "#1f2933"
ACCENT = "#2563eb"
UNSUBSCRIBE = "{{unsubscribe_url}}"  # left literally; the ESP substitutes it

# Gmail and Outlook drop <style> blocks in many cases, so every tag gets inline CSS.
TAG_STYLES = {
    "h1": f"{FONT}font-size:26px;line-height:32px;margin:0 0 16px;color:{TEXT_COLOR};",
    "h2": f"{FONT}font-size:21px;line-height:28px;margin:24px 0 12px;color:{TEXT_COLOR};",
    "h3": f"{FONT}font-size:17px;line-height:24px;margin:20px 0 8px;color:{TEXT_COLOR};",
    "p": f"{FONT}font-size:16px;line-height:24px;margin:0 0 16px;color:{TEXT_COLOR};",
    "ul": f"{FONT}font-size:16px;line-height:24px;margin:0 0 16px;padding-left:24px;color:{TEXT_COLOR};",
    "ol": f"{FONT}font-size:16px;line-height:24px;margin:0 0 16px;padding-left:24px;color:{TEXT_COLOR};",
    "li": "margin:0 0 6px;",
    "a": f"color:{ACCENT};text-decoration:underline;",
    "blockquote": "margin:0 0 16px;padding:0 0 0 12px;border-left:3px solid #cbd2d9;color:#52606d;",
    "code": "font-family:Consolas,Menlo,monospace;font-size:14px;background:#f1f5f9;",
    "pre": "margin:0 0 16px;padding:12px;background:#f1f5f9;white-space:pre-wrap;",
    "img": "display:block;max-width:100%;height:auto;border:0;",
    "hr": "border:0;border-top:1px solid #e4e7eb;margin:24px 0;",
}
SAFE_URL = re.compile(r"^(https?:|mailto:|\{\{)", re.I)


class RenderRequest(BaseModel):
    subject: str
    preheader: str | None = None
    body_markdown: str
    cta_text: str | None = None
    cta_url: str | None = None
    footer: str | None = None


def is_http_url(url: str) -> bool:
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def markdown_to_html(text: str) -> str:
    md = markdown.Markdown(extensions=["sane_lists"])
    # Raw HTML in the markdown is escaped, not passed through (it breaks email clients).
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    out = md.convert(text)
    # Neutralise javascript:/data: etc. links and image sources.
    out = re.sub(
        r'(href|src)="([^"]*)"',
        lambda m: m.group(0) if SAFE_URL.match(m.group(2)) else f'{m.group(1)}="#"',
        out,
    )
    for tag, style in TAG_STYLES.items():
        out = re.sub(rf"<{tag}(?=[\s>/])", f'<{tag} style="{style}"', out)
    return out


def markdown_to_text(text: str) -> str:
    t = text.replace("\r\n", "\n")
    t = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", r"\1", t)  # images -> alt
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", r"\1 (\2)", t)  # links -> text (url)
    t = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*(.+?)[ \t]*#*[ \t]*$", r"\1", t, flags=re.M)  # headings
    t = re.sub(r"^[ \t]{0,3}>[ \t]?", "", t, flags=re.M)  # blockquotes
    t = re.sub(r"^[ \t]*[*+][ \t]+", "- ", t, flags=re.M)  # bullets -> "- "
    t = re.sub(r"^[ \t]*([-*_][ \t]*){3,}$", "", t, flags=re.M)  # horizontal rules
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)  # bold
    t = re.sub(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])", r"\2", t)  # italic
    t = re.sub(r"`{1,3}", "", t)  # code marks
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def cta_button(text: str, url: str) -> str:
    """Bulletproof button: VML for Outlook desktop, padded table cell elsewhere."""
    t, u = html.escape(text), html.escape(url, quote=True)
    return f"""<table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:8px 0 24px;">
<tr><td align="center" bgcolor="{ACCENT}" style="border-radius:6px;">
<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{u}" style="height:44px;v-text-anchor:middle;width:240px;" arcsize="14%" stroke="f" fillcolor="{ACCENT}"><center style="color:#ffffff;{FONT}font-size:16px;font-weight:bold;">{t}</center></v:roundrect><![endif]-->
<!--[if !mso]><!--><a href="{u}" target="_blank" style="display:inline-block;padding:12px 28px;{FONT}font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:6px;background:{ACCENT};">{t}</a><!--<![endif]-->
</td></tr></table>"""


def render_html(req: RenderRequest, body_html: str) -> str:
    preheader = ""
    if req.preheader:
        # Hidden in the body, shown by inbox previews next to the subject.
        preheader = (
            '<span style="display:none!important;visibility:hidden;opacity:0;color:transparent;'
            'height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all;">'
            f"{html.escape(req.preheader)}</span>"
        )
    cta = cta_button(req.cta_text, req.cta_url) if req.cta_text and req.cta_url else ""
    footer_text = html.escape(req.footer).replace("\n", "<br>") + "<br>" if req.footer else ""
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{html.escape(req.subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;">
{preheader}
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background:#f4f5f7;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" border="0" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:#ffffff;border-radius:8px;">
<tr><td style="padding:32px 36px 8px;">
{body_html}
{cta}
</td></tr>
<tr><td style="padding:16px 36px 28px;border-top:1px solid #e4e7eb;{FONT}font-size:12px;line-height:18px;color:#7b8794;">
{footer_text}<a href="{UNSUBSCRIBE}" style="color:#7b8794;text-decoration:underline;">Unsubscribe</a>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def render_text(req: RenderRequest) -> str:
    parts = [markdown_to_text(req.body_markdown)]
    if req.cta_text and req.cta_url:
        parts.append(f"{req.cta_text}: {req.cta_url}")
    footer = [req.footer.strip()] if req.footer else []
    parts.append("\n".join(["--", *footer, f"Unsubscribe: {UNSUBSCRIBE}"]))
    return "\n\n".join(p for p in parts if p) + "\n"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/render")
def render(req: RenderRequest):
    if not req.body_markdown.strip():
        raise HTTPException(422, "body_markdown is empty")
    if req.cta_url and not is_http_url(req.cta_url):
        raise HTTPException(422, "cta_url must be an absolute http(s) URL")
    return {"html": render_html(req, markdown_to_html(req.body_markdown)), "text": render_text(req)}
