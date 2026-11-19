"""Draw the component map (docs/component-map.svg): actors as rows, the six stages as columns.

    python docs/component_map.py
"""
import os

W, ACTOR_W, HEAD_H, ROW_H = 1320, 140, 46, 84
STAGES = ["Trigger", "Input", "Context", "Processing", "AI", "Output"]
COL_W = (W - ACTOR_W - 4) / 6

# (actor, role, band tint, box fill, box border, stage index, box lines)
ROWS = [
    ("Ravi", "Growth lead", "#eef0fe", "#e3e6fd", "#5b5bd6", 0, ["Adds a contact or", "runs a test case"]),
    ("Dashboard / API", "Vercel", "#eef5fe", "#dfeafd", "#3b73d9", 1, ["Receives domain + contact", "Daily cron starts runs"]),
    ("Web crawler", "Research", "#eefaf2", "#dcf5e4", "#3aa55c", 2, ["Reads site, sitemap,", "security pages", "+ news feeds"]),
    ("Neon", "Memory", "#eef9f8", "#d8f2ef", "#2b9c90", 2, ["Rubric, your rulings,", "lessons, past runs"]),
    ("Qualifier", "Code + Gemini", "#fffbe8", "#fff4c4", "#c99a12", 3, ["Scores fit 0-100", "Checks certificates", "Stops dormant / poor fit"]),
    ("Hunter", "Contact finder", "#f6faea", "#ecf5d2", "#7a9a20", 3, ["Finds the decision-maker", "(if none supplied)"]),
    ("Gemini", "Draft", "#fdf0f5", "#fbe3ec", "#c8457a", 4, ["Writes pain points + email", "in house style", "+ offerings deck"]),
    ("Quality checks", "Code + critic", "#f4f0fd", "#e9e2fc", "#7651c9", 5, ["15 draft checks", "Critic: specific, not generic", "Fact check (disagreement)"]),
    ("Review gate", "Ravi", "#fff8ec", "#fff8f0", "#e07b1f", 5, ["Reviews flagged drafts", "Send -> own inbox + deck", "Replies, follow-ups, Calendly"]),
]
# arrows: (from row, to row) - routed from the source box's side to the target box's top
ARROWS = [(0, 1), (1, 2), (2, 4), (4, 5), (3, 6), (5, 6), (6, 7), (7, 8)]

H = HEAD_H + ROW_H * len(ROWS) + 4


def col_x(c):
    return ACTOR_W + 2 + c * COL_W


def box(r):
    c = ROWS[r][5]
    x, y = col_x(c) + 10, HEAD_H + r * ROW_H + 10
    return x, y, COL_W - 20, ROW_H - 20


def svg():
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
         '<path d="M1 1L9 5L1 9z" fill="#6b7280"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<rect x="0" y="0" width="{W}" height="{HEAD_H}" fill="#1f2937"/>',
         f'<rect x="0" y="0" width="{ACTOR_W}" height="{H}" fill="#1f2937"/>',
         f'<rect x="0" y="0" width="{ACTOR_W}" height="{HEAD_H}" fill="#111827"/>',
         f'<text x="{ACTOR_W/2}" y="{HEAD_H/2+4}" fill="#e5e7eb" font-size="12" text-anchor="middle">Actor</text>']
    for i, s in enumerate(STAGES):
        o.append(f'<text x="{col_x(i)+COL_W/2}" y="{HEAD_H/2+4}" fill="#ffffff" font-size="13" font-weight="bold" text-anchor="middle">{s}</text>')
    for r, (name, role, band, fill, border, c, lines) in enumerate(ROWS):
        y = HEAD_H + r * ROW_H
        o.append(f'<rect x="{ACTOR_W+2}" y="{y}" width="{W-ACTOR_W-4}" height="{ROW_H}" fill="{band}"/>')
        o.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#374151" stroke-width="0.6" opacity="0.5"/>')
        o.append(f'<text x="{ACTOR_W/2}" y="{y+ROW_H/2-3}" fill="#ffffff" font-size="12.5" font-weight="bold" text-anchor="middle">{name}</text>')
        o.append(f'<text x="{ACTOR_W/2}" y="{y+ROW_H/2+14}" fill="#9ca3af" font-size="10" text-anchor="middle">{role}</text>')
    for i in range(1, 6):                              # faint column lines
        o.append(f'<line x1="{col_x(i)}" y1="{HEAD_H}" x2="{col_x(i)}" y2="{H}" stroke="#e5e7eb" stroke-width="1"/>')
    gy = HEAD_H + (len(ROWS) - 1) * ROW_H              # the review gate band
    o.append(f'<rect x="{ACTOR_W+3}" y="{gy+1}" width="{W-ACTOR_W-6}" height="{ROW_H-2}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-dasharray="8 5"/>')
    for r, (name, role, band, fill, border, c, lines) in enumerate(ROWS):
        x, y, w, h = box(r)
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.6"/>')
        top = y + h / 2 - (len(lines) - 1) * 7 + 4
        for k, line in enumerate(lines):
            o.append(f'<text x="{x+w/2}" y="{top+k*14}" fill="#111827" font-size="11" text-anchor="middle">{line.replace("->", "&#8594;")}</text>')
    for a, b in ARROWS:
        ax, ay, aw, ah = box(a)
        bx, by, bw, bh = box(b)
        tx = bx + bw / 2 + (14 if (a, b) in ((5, 6),) else (-14 if (a, b) == (3, 6) else 0))
        if ROWS[a][5] == ROWS[b][5]:                   # same column: straight down from the bottom
            sx, sy = ax + aw / 2, ay + ah
            d = f"M{sx} {sy} L{sx} {by-2}"
        else:                                          # from the right side, across, then down
            sx, sy = ax + aw, ay + ah / 2
            d = f"M{sx} {sy} L{tx} {sy} L{tx} {by-2}"
        o.append(f'<path d="{d}" fill="none" stroke="#6b7280" stroke-width="1.6" stroke-dasharray="6 4" marker-end="url(#a)"/>')
        o.append(f'<circle cx="{sx}" cy="{sy}" r="4" fill="#22c55e"/>')
    o.append("</svg>")
    return "\n".join(o)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "component-map.svg")
    open(out, "w").write(svg())
    print(out)
