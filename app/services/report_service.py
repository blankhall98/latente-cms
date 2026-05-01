# app/services/report_service.py
from __future__ import annotations

from fpdf import FPDF, XPos, YPos

# ── Palette (zinc) ────────────────────────────────────────────────────────────
_Z900 = (24,  24,  27)   # #18181b  — primary text / dark fill
_Z500 = (113, 113, 122)  # #71717a  — secondary text
_Z400 = (161, 161, 170)  # #a1a1aa  — muted text
_Z200 = (228, 228, 231)  # #e4e4e7  — borders
_Z100 = (244, 244, 245)  # #f4f4f5  — light fill
_WHITE = (255, 255, 255)
_GREEN = (22,  163, 74)  # #16a34a
_RED   = (220, 38,  38)  # #dc2626
_BLUE  = (37,  99,  235) # #2563eb


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_head(pdf: FPDF, label: str, badge: str = "", badge_color: tuple = _Z500) -> None:
    y = pdf.get_y()
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)

    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(*_Z500)
    pdf.set_xy(pdf.l_margin, y)
    pdf.cell(0, 4, label.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_draw_color(*_Z200)
    pdf.line(pdf.l_margin, pdf.get_y(), 210 - pdf.r_margin, pdf.get_y())
    pdf.ln(2.5)

    if badge:
        bx = 210 - pdf.r_margin - 22
        by = y
        pdf.set_fill_color(*_Z100)
        pdf.set_draw_color(*_Z200)
        pdf.rect(bx, by, 22, 4.5, "FD")
        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*badge_color)
        pdf.set_xy(bx, by + 0.5)
        pdf.cell(22, 3.5, badge.upper(), align="C")
        pdf.set_xy(pdf.l_margin, pdf.get_y())


def _kpi_row(pdf: FPDF, cols: list[tuple[str, str, str]]) -> None:
    """cols = [(value, sublabel, value_size), ...]  value_size: 'lg'|'md'|'sm'"""
    cw = (210 - pdf.l_margin - pdf.r_margin) / len(cols)
    row_h = 18
    x0 = pdf.l_margin
    y0 = pdf.get_y()

    # Border box
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)
    pdf.rect(x0, y0, cw * len(cols), row_h)

    # Header row
    th = 4
    pdf.set_fill_color(*_Z100)
    for i, (_, sublabel, _) in enumerate(cols):
        cx = x0 + i * cw
        pdf.rect(cx, y0, cw, th, "F")
        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_text_color(*_Z400)
        pdf.set_xy(cx + 2, y0 + 0.8)
        pdf.cell(cw - 2, 2.5, sublabel.upper())
        if i < len(cols) - 1:
            pdf.set_draw_color(*_Z200)
            pdf.line(cx + cw, y0, cx + cw, y0 + row_h)

    # Header bottom rule
    pdf.set_draw_color(*_Z200)
    pdf.line(x0, y0 + th, x0 + cw * len(cols), y0 + th)

    # Values
    for i, (value, _, size) in enumerate(cols):
        cx = x0 + i * cw
        fs = {"lg": 16, "md": 11, "sm": 9}.get(size, 14)
        pdf.set_font("Helvetica", "B", fs)
        pdf.set_text_color(*_Z900)
        pdf.set_xy(cx + 2, y0 + th + 1.5)
        pdf.cell(cw - 4, row_h - th - 2, value, align="L")

    pdf.set_xy(pdf.l_margin, y0 + row_h)
    pdf.ln(1)


def _insights_strip(pdf: FPDF, insights: dict) -> None:
    w = 210 - pdf.l_margin - pdf.r_margin
    h = 6
    y0 = pdf.get_y()

    pdf.set_fill_color(*_Z100)
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)
    pdf.rect(pdf.l_margin, y0, w, h, "FD")

    items: list[tuple[str, str, tuple]] = []
    ins = insights
    if ins.get("trend_pct") is not None:
        arrow = "↑" if ins["trend_dir"] == "up" else "↓"
        color = _GREEN if ins["trend_dir"] == "up" else _RED
        items.append((f"{arrow} {ins['trend_pct']}%", "vs prev 15 days", color))
    items.append((f"~{ins.get('daily_avg', 0)}", "avg sessions / day", _Z900))
    peak = ins.get("peak_sessions", 0)
    plbl = ins.get("peak_label", "")
    items.append((str(peak), f"peak on {plbl}", _Z900))
    if ins.get("busiest_weekday"):
        items.append((f"{ins['busiest_weekday']}s", "busiest weekday", _Z900))

    cw = w / len(items)
    for i, (val, lbl, color) in enumerate(items):
        cx = pdf.l_margin + i * cw
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*color)
        pdf.set_xy(cx + 2, y0 + 0.8)
        pdf.cell(cw - 4, 2.5, val)
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(*_Z500)
        pdf.set_xy(cx + 2, y0 + 3.5)
        pdf.cell(cw - 4, 2, lbl)
        if i < len(items) - 1:
            pdf.set_draw_color(*_Z200)
            pdf.line(cx + cw, y0 + 1, cx + cw, y0 + h - 1)

    pdf.set_xy(pdf.l_margin, y0 + h)
    pdf.ln(3)


def _bar_chart(pdf: FPDF, series: list[dict], max_val: int) -> None:
    w   = 210 - pdf.l_margin - pdf.r_margin
    h   = 30
    y0  = pdf.get_y()
    bw  = w / len(series)

    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)
    pdf.rect(pdf.l_margin, y0, w, h)

    pdf.set_fill_color(*_Z900)
    for i, day in enumerate(series):
        pct = day["sessions"] / max_val if max_val else 0
        bh  = max((h - 3) * pct, 0.8)
        bx  = pdf.l_margin + i * bw + 0.5
        by  = y0 + h - bh - 1.5
        pdf.rect(bx, by, max(bw - 1, 0.5), bh, "F")

    # Date labels
    pdf.set_font("Helvetica", "", 5)
    pdf.set_text_color(*_Z400)
    for i, lbl in [(0, series[0]["label"]), (14, series[14]["label"]), (29, series[-1]["label"])]:
        tx = pdf.l_margin + i * bw
        pdf.set_xy(tx, y0 + h + 0.5)
        pdf.cell(bw * 3, 3, lbl)

    pdf.set_xy(pdf.l_margin, y0 + h + 4)
    pdf.ln(1)


def _two_col_tables(
    pdf: FPDF,
    left_title: str,  left_rows: list[tuple[str, str]],
    right_title: str, right_rows: list[tuple[str, str]],
) -> None:
    cw  = (210 - pdf.l_margin - pdf.r_margin - 5) / 2
    gap = 5
    y0  = pdf.get_y()
    rh  = 5.5

    for col_idx, (title, rows) in enumerate([(left_title, left_rows), (right_title, right_rows)]):
        cx = pdf.l_margin + col_idx * (cw + gap)

        # Section label
        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_text_color(*_Z500)
        pdf.set_xy(cx, y0)
        pdf.cell(cw, 3.5, title.upper())

        # Header rule
        pdf.set_draw_color(*_Z200)
        pdf.set_line_width(0.2)
        pdf.line(cx, y0 + 4, cx + cw, y0 + 4)

        # Table box
        th = 5  # header
        table_h = th + len(rows) * rh
        pdf.rect(cx, y0 + 5, cw, table_h)

        # Header
        pdf.set_fill_color(*_Z100)
        pdf.rect(cx, y0 + 5, cw, th, "F")
        pdf.set_draw_color(*_Z200)
        pdf.line(cx, y0 + 5 + th, cx + cw, y0 + 5 + th)

        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*_Z400)
        pdf.set_xy(cx + 2, y0 + 6)
        pdf.cell(cw * 0.65, 3.5, "CHANNEL" if col_idx == 0 else "DEVICE")
        pdf.set_xy(cx + cw * 0.65, y0 + 6)
        pdf.cell(cw * 0.35 - 2, 3.5, "SESSIONS", align="R")

        # Rows
        for j, (label, value) in enumerate(rows):
            ry = y0 + 5 + th + j * rh
            if j % 2 == 1:
                pdf.set_fill_color(250, 250, 250)
                pdf.rect(cx, ry, cw, rh, "F")
            if j > 0:
                pdf.set_draw_color(*_Z200)
                pdf.line(cx, ry, cx + cw, ry)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_Z900)
            pdf.set_xy(cx + 2, ry + 1)
            pdf.cell(cw * 0.65, rh - 2, label[:28])
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_xy(cx + cw * 0.65, ry + 1)
            pdf.cell(cw * 0.35 - 2, rh - 2, value, align="R")

        # Vertical divider
        pdf.set_draw_color(*_Z200)
        pdf.line(cx + cw * 0.65, y0 + 5, cx + cw * 0.65, y0 + 5 + table_h)

    max_rows = max(len(left_rows), len(right_rows))
    pdf.set_xy(pdf.l_margin, y0 + 5 + 5 + max_rows * rh + 3)


def _simple_table(pdf: FPDF, headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None) -> None:
    w   = 210 - pdf.l_margin - pdf.r_margin
    nc  = len(headers)
    cws = col_widths or [w / nc] * nc
    rh  = 5.5
    th  = 5
    y0  = pdf.get_y()

    # Outer border
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)
    pdf.rect(pdf.l_margin, y0, w, th + len(rows) * rh)

    # Header fill
    pdf.set_fill_color(*_Z100)
    pdf.rect(pdf.l_margin, y0, w, th, "F")
    pdf.set_draw_color(*_Z200)
    pdf.line(pdf.l_margin, y0 + th, pdf.l_margin + w, y0 + th)

    # Header text + vertical dividers
    x = pdf.l_margin
    for i, (hdr, cw) in enumerate(zip(headers, cws)):
        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*_Z400)
        pdf.set_xy(x + 2, y0 + 1)
        pdf.cell(cw - 4, 3, hdr.upper())
        if i < nc - 1:
            pdf.line(x + cw, y0, x + cw, y0 + th + len(rows) * rh)
        x += cw

    # Data rows
    for ri, row in enumerate(rows):
        ry = y0 + th + ri * rh
        if ri > 0:
            pdf.set_draw_color(*_Z200)
            pdf.line(pdf.l_margin, ry, pdf.l_margin + w, ry)
        if ri % 2 == 1:
            pdf.set_fill_color(250, 250, 250)
            pdf.rect(pdf.l_margin, ry, w, rh, "F")
        x = pdf.l_margin
        for ci, (cell, cw) in enumerate(zip(row, cws)):
            align = "R" if ci > 0 and ci == len(row) - 1 else "L"
            pdf.set_font("Helvetica", "B" if ci > 0 else "", 7)
            pdf.set_text_color(*_Z900 if ci > 0 else _Z500)
            pdf.set_xy(x + 2, ry + 1)
            # mono style for page paths
            if ci == 0 and len(cell) > 2 and cell.startswith("/"):
                pdf.set_font("Courier", "", 6.5)
                pdf.set_text_color(*_Z500)
            pdf.cell(cw - 4, rh - 2, cell[:52] if ci == 0 else cell, align=align)
            x += cw

    pdf.set_xy(pdf.l_margin, y0 + th + len(rows) * rh)
    pdf.ln(4)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_analytics_pdf(
    tenant: dict,
    stats: dict,
    activity: dict,
    ga: dict | None,
    generated_on: str,
) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.add_page()

    # ── Dark header bar ───────────────────────────────────────────────────────
    pdf.set_fill_color(*_Z900)
    pdf.rect(0, 0, 210, 14, "F")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*_WHITE)
    pdf.set_xy(20, 4.5)
    pdf.cell(85, 5, "BLANK CMS")
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(161, 161, 170)
    pdf.set_xy(20, 4.5)
    pdf.cell(170, 5, f"Generated {generated_on}  |  Confidential — internal use only", align="R")

    # ── Title block ───────────────────────────────────────────────────────────
    pdf.set_xy(20, 20)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*_Z400)
    pdf.cell(0, 4, "ANALYTICS REPORT — LAST 30 DAYS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*_Z900)
    pdf.cell(0, 11, tenant["name"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_Z500)
    pdf.cell(0, 5, f"Project slug: /{tenant['slug']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Title divider
    pdf.set_draw_color(*_Z900)
    pdf.set_line_width(0.5)
    pdf.line(20, pdf.get_y() + 1, 190, pdf.get_y() + 1)
    pdf.ln(5)
    pdf.set_line_width(0.2)

    # ── Traffic & Engagement (GA4) ────────────────────────────────────────────
    if ga:
        _section_head(pdf, "Traffic & Engagement — Last 30 Days", "Google Analytics", _BLUE)

        _kpi_row(pdf, [
            (f"{ga['sessions']:,}",  "Sessions",    "lg"),
            (f"{ga['new_users']:,}", "New Users",   "lg"),
            (f"{ga['returning']:,}", "Returning",   "lg"),
            (f"{ga['pageviews']:,}", "Page Views",  "lg"),
            (ga["avg_duration"],     "Avg. Session", "md"),
        ])

        if ga.get("insights"):
            _insights_strip(pdf, ga["insights"])

        _section_head(pdf, "Sessions — Last 30 Days")
        _bar_chart(pdf, ga["series"], ga["max_sessions"])

        # Sources + Devices side by side
        src_rows = [(s["channel"], f"{s['sessions']:,}") for s in ga.get("sources", [])]
        dev_rows = [(name, f"{count:,}") for name, count in ga.get("devices", {}).items()]
        if src_rows or dev_rows:
            _two_col_tables(pdf, "Traffic Sources", src_rows, "Device Split", dev_rows)

        # Top pages
        if ga.get("top_pages"):
            _section_head(pdf, "Top Pages by Views")
            _simple_table(
                pdf,
                headers=["Page", "Views"],
                rows=[[p["path"], f"{p['views']:,}"] for p in ga["top_pages"]],
                col_widths=[140, 30],
            )

        pdf.ln(1)

    # ── Content health ────────────────────────────────────────────────────────
    if stats:
        _section_head(pdf, "Content Health", "Live", _GREEN)
        _kpi_row(pdf, [
            (str(stats["published"]),   "Published",      "lg"),
            (str(stats["drafts"]),      "Drafts",         "lg"),
            (str(stats["sections"]),    "Sections",       "lg"),
            (stats["last_published"],   "Last Published", "md"),
        ])

    # ── Editorial activity ────────────────────────────────────────────────────
    if activity:
        _section_head(pdf, "Editorial Activity — Last 30 Days", "Live", _GREEN)
        _kpi_row(pdf, [
            (str(activity["publishes_30d"]), "Publishes",         "lg"),
            (str(activity["edits_30d"]),     "Edits",             "lg"),
            (str(activity["editors_30d"]),   "Active Editors",    "lg"),
            (activity["top_section"],        "Most Edited Section","sm"),
        ])

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.2)
    pdf.line(20, 277, 190, 277)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(*_Z400)
    pdf.set_xy(20, 279)
    pdf.cell(85, 4, f"{tenant['name']} — Analytics Report")
    pdf.set_xy(20, 279)
    pdf.cell(170, 4, f"Prepared by Blank CMS  ·  {generated_on}", align="R")

    return bytes(pdf.output())
