"""Draw the component map (docs/component-map.svg): actors as rows, the six stages as columns.

    python docs/component_map.py

Arrows run down the gutters between columns and enter a box from its side, so they never cross a box.
"""
import os

W, ACTOR_W, HEAD_H, ROW_H = 1320, 150, 46, 70
STAGES = ["Trigger", "Input", "Context", "Processing", "AI", "Output"]
COL_W = (W - ACTOR_W - 4) / 6

# actor, role, band tint, box fill, box border
ACTORS = [
    ("Growth Lead", "(User, Sprinto)", "#eef0fe", "#e3e6fd", "#5b5bd6"),
    ("Scheduler", "(every 5 minutes)", "#f1f3f6", "#e6e9ee", "#5b6777"),
    ("Dashboard / API", "(Vercel)", "#eef5fe", "#dfeafd", "#3b73d9"),
    ("News crawler", "(5 RSS feeds)", "#eefaf6", "#d6f3e9", "#22966f"),
    ("Web research", "(Scraper)", "#eefaf2", "#dcf5e4", "#3aa55c"),
    ("Neon", "(Memory)", "#eef9f8", "#d8f2ef", "#2b9c90"),
    ("Feedback repo", "(Lessons)", "#f2f8ec", "#e3f1d3", "#5e9a2c"),
    ("Qualifier", "(Code + Gemini)", "#fffbe8", "#fff4c4", "#c99a12"),
    ("Hunter", "(Contact finder)", "#fdf6ea", "#fbebcf", "#b7791f"),
    ("Gemini", "(LLM writer)", "#fdf0f5", "#fbe3ec", "#c8457a"),
    ("Quality checks", "(Code + critic)", "#f4f0fd", "#e9e2fc", "#7651c9"),
    ("Review gate", "(Growth Lead)", "#fff8ec", "#fff8f0", "#e07b1f"),
    ("Gmail", "(Email service)", "#eef4fb", "#dde9f7", "#2f6db5"),
    ("Reply handler", "(LangGraph + Gemini)", "#fbf0f8", "#f5e0f0", "#a3457f"),
]
GATE_ROW = 11

# steps: key -> (row, stage, lines)
STEPS = {
    "add":      (0, 0, ["Adds a contact or", "runs a test case"]),
    "rulings":  (0, 2, ["Feedback, rulings,", "portfolio (Questions,", "Offerings pages)"]),
    "cron":     (1, 0, ["Every 5 min: replies;", "hourly: news feeds;", "daily: re-research"]),
    "input":    (2, 1, ["Receives domain", "+ contact"]),
    "news":     (3, 1, ["Funding news from", "TechCrunch, Inc42 ...", "matched to companies"]),
    "research": (4, 2, ["Site, sitemap, security", "and press pages;", "digs deeper if unknown"]),
    "memory":   (5, 2, ["Rubric, past runs,", "news, portfolio"]),
    "lessons":  (6, 2, ["Ranked lessons", "+ winning emails"]),
    "qualify":  (7, 3, ["Scores fit, certificates,", "stage; stops dormant;", "applies your rulings"]),
    "hunter":   (8, 3, ["Finds the decision-maker", "(if none supplied)"]),
    "write":    (9, 4, ["Pain points + email", "in house style", "+ offerings deck"]),
    "checks":   (10, 5, ["15 draft checks,", "critic (not generic),", "fact check"]),
    "gate":     (11, 5, ["Reviews flagged", "drafts; Send"]),
    "send":     (12, 5, ["Sends to own inbox", "with the deck"]),
    "reply":    (12, 0, ["Inbox checked every", "5 min: prospect replies", "(or stays silent)"]),
    "read":     (13, 3, ["Reads the reply within", "5 min, decides; follow-up", "timers 3-3-2 days"]),
    "followup": (13, 4, ["Follow-ups, meeting", "times, Calendly booking"]),
    "learn":    (13, 5, ["Learns from outcomes:", "lessons to the repo,", "rubric tunes itself"]),
}
# arrows: (from, to, gutter x offset, entry y offset, mode); mode "left" = same column, down the left gutter
ARROWS = [("cron", "reply", -2, 0, "left"), ("add", "input", 0, 0, ""), ("cron", "news", 0, 0, ""), ("input", "research", 2, -8, ""),
          ("rulings", "lessons", -6, 0, "left"),
          ("news", "qualify", -8, -14, ""), ("research", "qualify", -2, 0, ""), ("memory", "qualify", 4, 14, ""),
          ("qualify", "hunter", 0, 0, ""), ("hunter", "write", -4, -10, ""), ("lessons", "write", 4, 10, ""),
          ("write", "checks", 0, 0, ""), ("checks", "gate", 0, 0, ""), ("gate", "send", 0, 0, ""),
          ("reply", "read", 0, 0, ""), ("read", "followup", 0, 0, ""), ("followup", "send", -4, 0, ""),
          ("followup", "learn", 4, 0, "")]

H = HEAD_H + ROW_H * len(ACTORS) + 4


def col_x(c):
    return ACTOR_W + 2 + c * COL_W


def box(key):
    r, c, _ = STEPS[key]
    return col_x(c) + 14, HEAD_H + r * ROW_H + 8, COL_W - 28, ROW_H - 16


def route(a, b, off, yoff=0, mode=""):
    ax, ay, aw, ah = box(a)
    bx, by, bw, bh = box(b)
    (ar, ac, _), (br, bc, _) = STEPS[a], STEPS[b]
    ty = by + bh / 2 + yoff
    if ac == bc and br == ar + 1:                       # next row, same column: straight down
        sx, sy = ax + aw / 2, ay + ah
        return (sx, sy), f"M{sx} {sy} L{sx} {by - 2}"
    if ac == bc and mode == "left":                     # same column, further down: the left gutter
        sx, sy = ax, ay + ah / 2
        gx = col_x(ac) + 6 + off
        return (sx, sy), f"M{sx} {sy} L{gx} {sy} L{gx} {ty} L{bx - 2} {ty}"
    sx, sy = ax + aw, ay + ah / 2                       # different column: along the row, down the target's left gutter
    if ar == br:
        return (sx, sy), f"M{sx} {sy} L{bx - 2} {sy}"
    gx = col_x(bc) + 6 + off
    return (sx, sy), f"M{sx} {sy} L{gx} {sy} L{gx} {ty} L{bx - 2} {ty}"


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
    for r, (name, role, band, fill, border) in enumerate(ACTORS):
        y = HEAD_H + r * ROW_H
        o.append(f'<rect x="{ACTOR_W+2}" y="{y}" width="{W-ACTOR_W-4}" height="{ROW_H}" fill="{band}"/>')
        o.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#374151" stroke-width="0.6" opacity="0.5"/>')
        o.append(f'<text x="{ACTOR_W/2}" y="{y+ROW_H/2-3}" fill="#ffffff" font-size="12.5" font-weight="bold" text-anchor="middle">{name}</text>')
        o.append(f'<text x="{ACTOR_W/2}" y="{y+ROW_H/2+13}" fill="#9ca3af" font-size="10" text-anchor="middle">{role}</text>')
    for i in range(1, 6):
        o.append(f'<line x1="{col_x(i)}" y1="{HEAD_H}" x2="{col_x(i)}" y2="{H}" stroke="#e5e7eb" stroke-width="1"/>')
    gy = HEAD_H + GATE_ROW * ROW_H
    o.append(f'<rect x="{ACTOR_W+3}" y="{gy+1}" width="{W-ACTOR_W-6}" height="{ROW_H-2}" fill="none" stroke="#f59e0b" stroke-width="2" stroke-dasharray="8 5"/>')
    for key, (r, c, lines) in STEPS.items():
        x, y, w, h = box(key)
        fill, border = ACTORS[r][3], ACTORS[r][4]
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.6"/>')
        top = y + h / 2 - (len(lines) - 1) * 6.5 + 4
        for k, line in enumerate(lines):
            o.append(f'<text x="{x+w/2}" y="{top+k*13}" fill="#111827" font-size="10.5" text-anchor="middle">{line}</text>')
    for a, b, off, yoff, mode in ARROWS:
        (sx, sy), d = route(a, b, off, yoff, mode)
        o.append(f'<path d="{d}" fill="none" stroke="#6b7280" stroke-width="1.5" stroke-dasharray="6 4" marker-end="url(#a)"/>')
        o.append(f'<circle cx="{sx}" cy="{sy}" r="3.8" fill="#22c55e"/>')
    o.append("</svg>")
    return "\n".join(o)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "component-map.svg")
    open(out, "w").write(svg())
    print(out)
