"""Background APScheduler for live-dashboard incremental catch-up (hourly by default)."""
from __future__ import annotations

import atexit
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db_config import is_configured
from .scheduled_anomaly_runner import live_dashboard_scheduler_tick, start_backfill_thread

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _cron_trigger_for_live_dashboard() -> CronTrigger:
    """
    LIVE_DASHBOARD_SCHEDULE_MINUTES (default 60): run at UTC minute 0 each hour when 60;
    otherwise */N minutes (e.g. 5 → every 5 minutes).
    """
    raw = (os.environ.get("LIVE_DASHBOARD_SCHEDULE_MINUTES") or "60").strip()
    try:
        mins = int(raw)
    except ValueError:
        mins = 60
    if mins < 1:
        mins = 60
    if mins >= 60:
        return CronTrigger(minute=0, second=0, timezone="UTC")
    return CronTrigger(minute=f"*/{mins}", timezone="UTC")


def init_scheduler_if_enabled(app) -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if not is_configured():
        return
    v = (os.environ.get("SCHEDULED_ANOMALY_ENABLED") or "1").strip().lower()
    if v in ("0", "false", "no", "off"):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    try:
        sched = BackgroundScheduler(timezone="UTC")
        trigger = _cron_trigger_for_live_dashboard()
        sched.add_job(
            live_dashboard_scheduler_tick,
            trigger,
            id="live_dashboard_catchup",
            replace_existing=True,
        )
        sched.start()
        _scheduler = sched
        atexit.register(lambda s=sched: s.shutdown(wait=False))
        start_backfill_thread()
        logger.info(
            "Live dashboard scheduler started (cron=%s, incremental per-plant daily catch-up).",
            trigger,
        )
    except Exception:
        logger.exception("Failed to start APScheduler")
