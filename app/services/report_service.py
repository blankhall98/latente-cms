# app/services/report_service.py
from __future__ import annotations

from fpdf import FPDF, XPos, YPos

# ── Palette ───────────────────────────────────────────────────────────────────
_Z900  = (24,  24,  27)
_Z600  = (82,  82,  91)
_Z500  = (113, 113, 122)
_Z400  = (161, 161, 170)
_Z200  = (228, 228, 231)
_Z100  = (244, 244, 245)
_Z50   = (250, 250, 250)
_WHITE = (255, 255, 255)
_GREEN = (22,  163, 74)
_RED   = (220, 38,  38)
_BLUE  = (37,  99,  235)

_ACCENT = _Z900   # left-page accent stripe colour
_STRIPE = 3       # mm — width of left accent stripe


def _s(text: str) -> str:
    """Strip characters outside Latin-1 so fpdf2 built-in fonts don't crash."""
    return (
        str(text)
        .replace("—", "-").replace("–", "-")   # em / en dash
        .replace("↑", "+").replace("↓", "-")   # arrows
        .replace("·", ".")                           # middle dot
    )


# ── FPDF subclass — header & footer print on every page automatically ─────────
class _Report(FPDF):
    def __init__(self, tenant_name: str, generated_on: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._tname = tenant_name
        self._date  = generated_on
        self.set_margins(left=20 + _STRIPE, top=18, right=18)
        self.set_auto_page_break(auto=True, margin=18)

    # called automatically before each page's content
    def header(self):
        # Left accent stripe
        self.set_fill_color(*_ACCENT)
        self.rect(0, 0, _STRIPE, 297, "F")

        # Top bar
        self.set_fill_color(*_Z900)
        self.rect(_STRIPE, 0, 210 - _STRIPE, 11, "F")

        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*_WHITE)
        self.set_xy(_STRIPE + 4, 3)
        self.cell(60, 5, "BLANK CMS")

        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(200, 200, 204)
        self.set_xy(_STRIPE + 4, 3)
        self.cell(210 - _STRIPE - 8, 5,
                  f"Generated {_s(self._date)}  |  Confidential", align="R")

        self.set_xy(self.l_margin, 15)

    # called automatically at the bottom of each page
    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*_Z200)
        self.set_line_width(0.15)
        self.line(self.l_margin, self.get_y(), 210 - self.r_margin, self.get_y())
        self.set_font("Helvetica", "", 6)
        self.set_text_color(*_Z400)
        self.set_xy(self.l_margin, self.get_y() + 1.5)
        self.cell(90, 4, _s(f"{self._tname} - Analytics Report"))
        self.cell(0, 4, f"Page {self.page_no()}", align="R")


# ── Layout helpers ────────────────────────────────────────────────────────────

def _section(pdf: _Report, title: str, badge: str = "", badge_col: tuple = _Z500) -> None:
    """Uppercase section header with hairline rule."""
    y = pdf.get_y() + 2
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(*_Z400)
    pdf.cell(0, 3.5, _s(title).upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.15)
    pdf.line(pdf.l_margin, pdf.get_y(), 210 - pdf.r_margin, pdf.get_y())
    if badge:
        bw = len(badge) * 1.7 + 4
        bx = 210 - pdf.r_margin - bw
        by = y - 0.5
        pdf.set_fill_color(*_Z100)
        pdf.set_draw_color(*_Z200)
        pdf.rect(bx, by, bw, 4.5, "FD")
        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*badge_col)
        pdf.set_xy(bx, by + 0.5)
        pdf.cell(bw, 3.5, badge.upper(), align="C")
    pdf.ln(3)


def _kpi_row(pdf: _Report, kpis: list[tuple[str, str]]) -> None:
    """Render a row of KPI boxes: [(value, label), ...]"""
    n   = len(kpis)
    cw  = (210 - pdf.l_margin - pdf.r_margin) / n
    h   = 20
    y0  = pdf.get_y()

    for i, (val, lbl) in enumerate(kpis):
        x = pdf.l_margin + i * cw
        # Box
        pdf.set_fill_color(*_WHITE)
        pdf.set_draw_color(*_Z200)
        pdf.set_line_width(0.2)
        pdf.rect(x, y0, cw, h, "FD")
        # Thin top accent
        pdf.set_fill_color(*_Z900)
        pdf.rect(x, y0, cw, 0.8, "F")
        # Value
        fs = 17 if len(_s(val)) <= 6 else 12
        pdf.set_font("Helvetica", "B", fs)
        pdf.set_text_color(*_Z900)
        pdf.set_xy(x + 2, y0 + 3.5)
        pdf.cell(cw - 4, fs * 0.35, _s(val), align="C")
        # Label
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(*_Z500)
        pdf.set_xy(x + 2, y0 + h - 5)
        pdf.cell(cw - 4, 3.5, _s(lbl).upper(), align="C")

    pdf.set_xy(pdf.l_margin, y0 + h)
    pdf.ln(1)


def _insight_bar(pdf: _Report, items: list[tuple[str, str, tuple]]) -> None:
    """Compact strip: [(value, label, colour), ...]"""
    w  = 210 - pdf.l_margin - pdf.r_margin
    h  = 6.5
    y0 = pdf.get_y()
    pdf.set_fill_color(*_Z50)
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.15)
    pdf.rect(pdf.l_margin, y0, w, h, "FD")

    cw = w / len(items)
    for i, (val, lbl, col) in enumerate(items):
        cx = pdf.l_margin + i * cw
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*col)
        pdf.set_xy(cx + 2, y0 + 0.8)
        pdf.cell(cw - 4, 3, _s(val))
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(*_Z400)
        pdf.set_xy(cx + 2, y0 + 4)
        pdf.cell(cw - 4, 2, _s(lbl))
        if i < len(items) - 1:
            pdf.set_draw_color(*_Z200)
            pdf.line(cx + cw, y0 + 1.5, cx + cw, y0 + h - 1.5)

    pdf.set_xy(pdf.l_margin, y0 + h)
    pdf.ln(2)


def _bar_chart(pdf: _Report, series: list[dict], max_val: int) -> None:
    w  = 210 - pdf.l_margin - pdf.r_margin
    h  = 38
    y0 = pdf.get_y()
    bw = w / len(series)

    # Container
    pdf.set_fill_color(*_WHITE)
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.15)
    pdf.rect(pdf.l_margin, y0, w, h, "FD")

    # Subtle gridlines at 25%, 50%, 75%
    pdf.set_draw_color(240, 240, 241)
    pdf.set_line_width(0.1)
    for pct in (0.25, 0.5, 0.75):
        gy = y0 + h - (h - 4) * pct - 2
        pdf.line(pdf.l_margin + 1, gy, pdf.l_margin + w - 1, gy)

    # Bars
    for i, day in enumerate(series):
        pct = day["sessions"] / max_val if max_val else 0
        bh  = max((h - 4) * pct, 0.8)
        bx  = pdf.l_margin + i * bw + 0.6
        by  = y0 + h - bh - 2
        pdf.set_fill_color(*_Z900)
        pdf.rect(bx, by, max(bw - 1.2, 0.4), bh, "F")

    # Date labels
    pdf.set_font("Helvetica", "", 5)
    pdf.set_text_color(*_Z400)
    for idx, lbl in [(0, series[0]["label"]), (14, series[14]["label"]), (29, series[-1]["label"])]:
        pdf.set_xy(pdf.l_margin + idx * bw, y0 + h + 0.5)
        pdf.cell(bw * 4, 3, _s(lbl))

    pdf.set_xy(pdf.l_margin, y0 + h + 4)
    pdf.ln(2)


def _two_col(
    pdf: _Report,
    left_title: str,  left_rows:  list[tuple[str, str]],
    right_title: str, right_rows: list[tuple[str, str]],
) -> None:
    gap = 4
    cw  = (210 - pdf.l_margin - pdf.r_margin - gap) / 2
    rh  = 5.5
    th  = 5
    y0  = pdf.get_y()

    for ci, (title, rows) in enumerate([(left_title, left_rows), (right_title, right_rows)]):
        cx = pdf.l_margin + ci * (cw + gap)

        # Subheading
        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_text_color(*_Z500)
        pdf.set_xy(cx, y0)
        pdf.cell(cw, 4, _s(title).upper())
        pdf.set_draw_color(*_Z200)
        pdf.set_line_width(0.15)
        pdf.line(cx, y0 + 4.2, cx + cw, y0 + 4.2)

        table_h = th + len(rows) * rh
        ty = y0 + 5.5

        # Table border
        pdf.set_fill_color(*_WHITE)
        pdf.set_draw_color(*_Z200)
        pdf.rect(cx, ty, cw, table_h, "FD")

        # Header
        pdf.set_fill_color(*_Z100)
        pdf.rect(cx, ty, cw, th, "F")
        pdf.set_draw_color(*_Z200)
        pdf.line(cx, ty + th, cx + cw, ty + th)
        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*_Z400)
        pdf.set_xy(cx + 2, ty + 1)
        pdf.cell(cw * 0.62, 3, "SOURCE" if ci == 0 else "DEVICE")
        pdf.set_xy(cx + cw * 0.62, ty + 1)
        pdf.cell(cw * 0.38 - 2, 3, "SESSIONS", align="R")
        pdf.line(cx + cw * 0.62, ty, cx + cw * 0.62, ty + table_h)

        # Rows
        for ri, (label, value) in enumerate(rows):
            ry = ty + th + ri * rh
            if ri > 0:
                pdf.set_draw_color(*_Z200)
                pdf.line(cx, ry, cx + cw, ry)
            if ri % 2 == 1:
                pdf.set_fill_color(*_Z50)
                pdf.rect(cx, ry, cw, rh, "F")
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_Z600)
            pdf.set_xy(cx + 2, ry + 1.2)
            pdf.cell(cw * 0.62 - 2, rh - 2, _s(label[:26]))
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*_Z900)
            pdf.set_xy(cx + cw * 0.62, ry + 1.2)
            pdf.cell(cw * 0.38 - 2, rh - 2, _s(value), align="R")

    max_rows = max(len(left_rows), len(right_rows))
    pdf.set_xy(pdf.l_margin, y0 + 5.5 + th + max_rows * rh + 3)


def _table(pdf: _Report, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    rh = 5.5
    th = 5
    y0 = pdf.get_y()
    w  = sum(widths)
    table_h = th + len(rows) * rh

    pdf.set_fill_color(*_WHITE)
    pdf.set_draw_color(*_Z200)
    pdf.set_line_width(0.15)
    pdf.rect(pdf.l_margin, y0, w, table_h, "FD")

    # Header
    pdf.set_fill_color(*_Z100)
    pdf.rect(pdf.l_margin, y0, w, th, "F")
    pdf.line(pdf.l_margin, y0 + th, pdf.l_margin + w, y0 + th)

    x = pdf.l_margin
    for i, (hdr, cw) in enumerate(zip(headers, widths)):
        pdf.set_font("Helvetica", "B", 5)
        pdf.set_text_color(*_Z400)
        align = "R" if i == len(headers) - 1 else "L"
        pdf.set_xy(x + 2, y0 + 1)
        pdf.cell(cw - 4, 3, _s(hdr).upper(), align=align)
        if i < len(headers) - 1:
            pdf.line(x + cw, y0, x + cw, y0 + table_h)
        x += cw

    # Rows
    for ri, row in enumerate(rows):
        ry = y0 + th + ri * rh
        if ri > 0:
            pdf.set_draw_color(*_Z200)
            pdf.line(pdf.l_margin, ry, pdf.l_margin + w, ry)
        if ri % 2 == 1:
            pdf.set_fill_color(*_Z50)
            pdf.rect(pdf.l_margin, ry, w, rh, "F")
        x = pdf.l_margin
        for ci, (cell, cw) in enumerate(zip(row, widths)):
            is_last = ci == len(row) - 1
            pdf.set_text_color(*_Z900 if is_last else _Z600)
            pdf.set_font("Helvetica", "B" if is_last else "", 7)
            if ci == 0 and cell.startswith("/"):
                pdf.set_font("Courier", "", 6.5)
                pdf.set_text_color(*_Z600)
            align = "R" if is_last else "L"
            pdf.set_xy(x + 2, ry + 1.2)
            pdf.cell(cw - 4, rh - 2, _s(cell[:54] if not is_last else cell), align=align)
            x += cw

    pdf.set_xy(pdf.l_margin, y0 + table_h)
    pdf.ln(3)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_analytics_pdf(
    tenant: dict,
    stats: dict,
    activity: dict,
    ga: dict | None,
    generated_on: str,
) -> bytes:
    pdf = _Report(tenant_name=tenant["name"], generated_on=generated_on)
    pdf.add_page()

    # ── Title block ───────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*_Z400)
    pdf.cell(0, 4, "ANALYTICS REPORT - LAST 30 DAYS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*_Z900)
    pdf.cell(0, 12, _s(tenant["name"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_Z500)
    pdf.cell(0, 5, f"Project: /{_s(tenant['slug'])}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    # Title rule
    pdf.set_draw_color(*_Z900)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), 210 - pdf.r_margin, pdf.get_y())
    pdf.set_line_width(0.15)
    pdf.ln(5)

    # ── Traffic & Engagement ──────────────────────────────────────────────────
    if ga:
        _section(pdf, "Traffic & Engagement - Last 30 Days", "Google Analytics", _BLUE)
        _kpi_row(pdf, [
            (f"{ga['sessions']:,}",   "Sessions"),
            (f"{ga['new_users']:,}",  "New Users"),
            (f"{ga['returning']:,}",  "Returning"),
            (f"{ga['pageviews']:,}",  "Page Views"),
            (ga["avg_duration"],      "Avg. Session"),
        ])

        # Insights strip
        ins = ga.get("insights", {})
        items: list[tuple[str, str, tuple]] = []
        if ins.get("trend_pct") is not None:
            arrow = "+" if ins["trend_dir"] == "up" else "-"
            col   = _GREEN if ins["trend_dir"] == "up" else _RED
            items.append((f"{arrow}{ins['trend_pct']}%", "vs previous 15 days", col))
        items.append((f"~{ins.get('daily_avg', 0)}", "avg sessions / day", _Z900))
        items.append((str(ins.get("peak_sessions", 0)), f"peak on {ins.get('peak_label', '')}", _Z900))
        if ins.get("busiest_weekday"):
            items.append((f"{ins['busiest_weekday']}s", "busiest day of week", _Z900))
        if items:
            _insight_bar(pdf, items)

        # Chart
        _section(pdf, "Sessions - Last 30 Days")
        _bar_chart(pdf, ga["series"], ga["max_sessions"])

        # Sources + Devices side by side
        src_rows = [(s["channel"], f"{s['sessions']:,}") for s in ga.get("sources", [])]
        dev_rows = [(n, f"{c:,}") for n, c in ga.get("devices", {}).items()]
        if src_rows or dev_rows:
            _section(pdf, "Acquisition")
            _two_col(pdf, "Traffic Sources", src_rows, "Device Split", dev_rows)

        # Top pages
        if ga.get("top_pages"):
            _section(pdf, "Top Pages by Views")
            _table(
                pdf,
                headers=["Page", "Views"],
                rows=[[p["path"], f"{p['views']:,}"] for p in ga["top_pages"]],
                widths=[148, 21],
            )

    # ── Content health ────────────────────────────────────────────────────────
    if stats:
        _section(pdf, "Content Health", "Live", _GREEN)
        _kpi_row(pdf, [
            (str(stats["published"]),  "Published"),
            (str(stats["drafts"]),     "Drafts"),
            (str(stats["sections"]),   "Sections"),
            (_s(stats["last_published"]), "Last Published"),
        ])

    # ── Editorial activity ────────────────────────────────────────────────────
    if activity:
        _section(pdf, "Editorial Activity - Last 30 Days", "Live", _GREEN)
        _kpi_row(pdf, [
            (str(activity["publishes_30d"]),  "Publishes"),
            (str(activity["edits_30d"]),      "Edits"),
            (str(activity["editors_30d"]),    "Active Editors"),
            (_s(activity["top_section"]),     "Most Edited Section"),
        ])

    return bytes(pdf.output())
