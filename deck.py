"""A mock offerings deck (PDF) attached to cold emails, built from your live portfolio.

Mock: made for this case study, not an official Sprinto document (it says so on every slide). Every
claim comes from writer.SPRINTO_CONTEXT, the same description the fact check accepts, plus the
frameworks in your portfolio (Offerings page). No numbers or customers are invented.

A tiny PDF writer (built-in Helvetica, rectangles, text) so no PDF library has to ship to Vercel.

    python deck.py out.pdf
"""
import datetime as dt

BLUE = (0.114, 0.306, 0.847)        # #1d4ed8
INK = (0.051, 0.122, 0.271)
MUTED = (0.30, 0.38, 0.53)
PALE = (0.86, 0.92, 1.0)
W, H = 842, 595                     # A4 landscape, points

WHAT_IT_DOES = [
    "Connects to your cloud, HR, code and identity tools",
    "Monitors your controls continuously",
    "Collects audit evidence automatically",
    "Ships ready-made policies and security training",
    "Gets companies audit-ready in weeks instead of months, with far less engineering time",
    "Helps answer security questionnaires, and keeps compliance always on after the first audit",
]
HOW = [("Connect", "Link your cloud, HR, code and identity tools."),
       ("Close the gaps", "Adopt ready policies and controls; train the team."),
       ("Evidence, automatically", "Controls are monitored and evidence is collected for you."),
       ("Audit-ready, then always on", "Go into the audit prepared, and stay compliant after it.")]


def _esc(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("·", "-").replace("•", "-")
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, size: float, width: float) -> list[str]:
    per = max(10, int(width / (size * 0.5)))          # Helvetica averages about half the font size per character
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > per and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


class _Page:
    def __init__(self):
        self.ops = []

    def rect(self, x, y, w, h, rgb):
        self.ops.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def text(self, x, y, s, size=14, bold=False, rgb=INK):
        font = "F2" if bold else "F1"
        self.ops.append(f"BT {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg /{font} {size} Tf {x:.1f} {y:.1f} Td ({_esc(s)}) Tj ET")

    def para(self, x, y, s, size=13, width=600, lead=1.35, **kw) -> float:
        for line in _wrap(s, size, width):
            self.text(x, y, line, size, **kw)
            y -= size * lead
        return y


def _frame(p: _Page, title: str, n: int, total: int):
    p.rect(0, 0, W, H, (0.97, 0.98, 1.0))
    p.rect(0, H - 8, W, 8, BLUE)
    p.text(48, H - 70, title, 28, bold=True)
    p.text(48, 26, "Mock deck for the MESA case study - not an official Sprinto document", 9, rgb=MUTED)
    p.text(W - 80, 26, f"{n} / {total}", 9, rgb=MUTED)


def build(portfolio_core: list[str], added: list[dict], company: str | None = None) -> bytes:
    """portfolio_core: e.g. ["SOC 2", "ISO 27001", ...]; added: accepted or your own frameworks
    ({name, who, pitch}). company: who the deck was prepared for (cover slide), optional."""
    pages, total = [], 5

    p = _Page()                                     # 1. cover
    p.rect(0, 0, W, H, BLUE)
    p.text(56, 380, "Compliance on autopilot", 40, bold=True, rgb=(1, 1, 1))
    p.text(56, 340, "Sprinto - what we cover and how it works", 20, rgb=PALE)
    if company:
        p.text(56, 300, f"Prepared for {company}", 16, rgb=(1, 1, 1))
    p.text(56, 60, f"{dt.date.today():%B %Y}  -  Mock deck for the MESA case study, not an official Sprinto document", 10, rgb=PALE)
    pages.append(p)

    p = _Page()                                     # 2. what it does
    _frame(p, "What Sprinto does", 2, total)
    y = H - 130
    for item in WHAT_IT_DOES:
        p.rect(52, y - 2, 8, 8, BLUE)
        y = p.para(72, y, item, 16, width=680) - 10
    pages.append(p)

    p = _Page()                                     # 3. frameworks
    _frame(p, "Frameworks we cover", 3, total)
    x, y = 48, H - 130
    for name in portfolio_core:
        p.rect(x, y - 10, 140, 34, PALE)
        p.text(x + 12, y + 2, name, 14, bold=True)
        x += 152
        if x > W - 170:
            x, y = 48, y - 48
    y -= 70
    if added:
        p.text(48, y, "New in our portfolio", 16, bold=True, rgb=BLUE)
        y -= 28
        for f in added[:5]:
            p.text(48, y, f.get("name", ""), 14, bold=True)
            y = p.para(48, y - 18, f"For: {f.get('who', '')}  Pitch: {f.get('pitch', '')}", 11, width=740, rgb=MUTED) - 12
    pages.append(p)

    p = _Page()                                     # 4. how it works
    _frame(p, "How it works", 4, total)
    x = 48
    for i, (head, body) in enumerate(HOW, 1):
        p.rect(x, 170, 176, 250, (1, 1, 1))
        p.rect(x, 412, 176, 8, BLUE)
        p.text(x + 14, 380, str(i), 30, bold=True, rgb=BLUE)
        p.para(x + 14, 340, head, 15, width=150, bold=True)
        p.para(x + 14, 280, body, 12, width=150, rgb=MUTED)
        x += 190
    pages.append(p)

    p = _Page()                                     # 5. next step
    _frame(p, "Next step", 5, total)
    p.para(48, H - 150, "A 15-minute call to map your path to the frameworks your customers ask for.", 22, width=740, bold=True)
    p.para(48, H - 240, "Just reply to the email this deck came with, and we will suggest a few times.", 16, width=740, rgb=MUTED)
    pages.append(p)
    return _pdf(pages)


def _pdf(pages: list[_Page]) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for pg in pages:
        stream = "\n".join(pg.ops).encode("latin-1")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        content_id = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Contents {content_id} 0 R "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        body = o if isinstance(o, bytes) else o.encode("latin-1")
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def for_portfolio(company: str | None = None) -> bytes:
    """The deck for your current portfolio (core + accepted + your own)."""
    import frameworks
    return build(frameworks.CORE, [{"name": f["name"], "who": f.get("who", ""), "pitch": f.get("pitch", "")}
                                   for f in frameworks.accepted()], company)


if __name__ == "__main__":
    import sys
    open(sys.argv[1] if len(sys.argv) > 1 else "offerings-deck.pdf", "wb").write(for_portfolio("Example Ltd"))
