# app/services/ga_service.py
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.oauth2 import service_account

from app.core.settings import settings

logger = logging.getLogger(__name__)

_GA_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def _load_credentials() -> service_account.Credentials | None:
    """Same resolution order as firebase_storage: file path first, then JSON env var."""
    cred_path = settings.FIREBASE_CREDENTIALS_PATH or ""
    if cred_path and os.path.exists(cred_path):
        return service_account.Credentials.from_service_account_file(
            cred_path, scopes=_GA_SCOPES
        )

    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if json_str:
        try:
            info = json.loads(json_str)
            return service_account.Credentials.from_service_account_info(
                info, scopes=_GA_SCOPES
            )
        except Exception as exc:
            logger.warning("GA4: could not parse FIREBASE_SERVICE_ACCOUNT_JSON: %s", exc)

    return None


def property_id_for_slug(slug: str) -> str | None:
    key = f"GA4_PROPERTY_ID_{slug.upper().replace('-', '_')}"
    return os.environ.get(key)


def fetch_ga4_report(tenant_slug: str) -> dict | None:
    """
    Pull a 30-day GA4 report for the tenant. Returns None if the property
    is not configured or if any API call fails — the caller should treat
    None as 'not connected' and render a placeholder.
    """
    property_id = property_id_for_slug(tenant_slug)
    if not property_id:
        return None

    credentials = _load_credentials()
    if not credentials:
        logger.warning("GA4: no credentials available")
        return None

    try:
        client = BetaAnalyticsDataClient(credentials=credentials)
        prop = f"properties/{property_id}"
        date_range = DateRange(start_date="30daysAgo", end_date="yesterday")

        # ── Overview metrics ────────────────────────────────────────────────
        overview = client.run_report(RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="newUsers"),
                Metric(name="screenPageViews"),
                Metric(name="averageSessionDuration"),
            ],
        ))
        row = overview.rows[0] if overview.rows else None
        sessions   = int(float(row.metric_values[0].value)) if row else 0
        users      = int(float(row.metric_values[1].value)) if row else 0
        new_users  = int(float(row.metric_values[2].value)) if row else 0
        returning  = max(users - new_users, 0)
        pageviews  = int(float(row.metric_values[3].value)) if row else 0
        raw_dur    = float(row.metric_values[4].value) if row else 0
        mins, secs = int(raw_dur // 60), int(raw_dur % 60)
        avg_duration = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

        # ── Top 5 pages ─────────────────────────────────────────────────────
        pages_resp = client.run_report(RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                desc=True,
            )],
            limit=5,
        ))
        top_pages = [
            {
                "path": r.dimension_values[0].value,
                "views": int(r.metric_values[0].value),
            }
            for r in pages_resp.rows
        ]

        # ── Daily sessions time series (last 30 days) ───────────────────────
        series_resp = client.run_report(RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(
                dimension=OrderBy.DimensionOrderBy(dimension_name="date"),
            )],
        ))
        raw_series = {
            r.dimension_values[0].value: int(r.metric_values[0].value)
            for r in series_resp.rows
        }

        today = date.today()
        series = [
            {
                "label": (today - timedelta(days=i)).strftime("%b %d"),
                "weekday": (today - timedelta(days=i)).strftime("%A"),
                "sessions": raw_series.get(
                    (today - timedelta(days=i)).strftime("%Y%m%d"), 0
                ),
            }
            for i in range(30, 0, -1)
        ]

        # ── Chart insights (computed from series, no extra API call) ─────────
        first_half  = sum(d["sessions"] for d in series[:15])
        second_half = sum(d["sessions"] for d in series[15:])
        if first_half > 0:
            trend_pct = round((second_half - first_half) / first_half * 100)
            trend_dir = "up" if trend_pct >= 0 else "down"
        else:
            trend_pct, trend_dir = None, None

        peak = max(series, key=lambda d: d["sessions"])

        daily_avg = round(sum(d["sessions"] for d in series) / 30)

        weekday_totals: dict[str, list[int]] = {}
        for d in series:
            weekday_totals.setdefault(d["weekday"], []).append(d["sessions"])
        busiest_weekday = max(
            weekday_totals,
            key=lambda w: sum(weekday_totals[w]) / len(weekday_totals[w]),
        ) if weekday_totals else None

        insights = {
            "trend_pct": abs(trend_pct) if trend_pct is not None else None,
            "trend_dir": trend_dir,
            "daily_avg": daily_avg,
            "peak_label": peak["label"],
            "peak_sessions": peak["sessions"],
            "busiest_weekday": busiest_weekday,
        }

        # ── Traffic sources ─────────────────────────────────────────────────
        sources_resp = client.run_report(RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="sessionDefaultChannelGrouping")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                desc=True,
            )],
            limit=6,
        ))
        sources = [
            {
                "channel": r.dimension_values[0].value,
                "sessions": int(r.metric_values[0].value),
            }
            for r in sources_resp.rows
        ]

        # ── Device split ────────────────────────────────────────────────────
        devices_resp = client.run_report(RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=[Dimension(name="deviceCategory")],
            metrics=[Metric(name="sessions")],
        ))
        devices = {
            r.dimension_values[0].value.capitalize(): int(r.metric_values[0].value)
            for r in devices_resp.rows
        }

        return {
            "sessions": sessions,
            "users": users,
            "new_users": new_users,
            "returning": returning,
            "pageviews": pageviews,
            "avg_duration": avg_duration,
            "top_pages": top_pages,
            "series": series,
            "max_sessions": max((d["sessions"] for d in series), default=1) or 1,
            "insights": insights,
            "sources": sources,
            "devices": devices,
        }

    except Exception as exc:
        logger.warning("GA4 fetch failed for %s: %s", tenant_slug, exc)
        return None
