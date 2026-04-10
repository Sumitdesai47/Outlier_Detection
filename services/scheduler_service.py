"""Background 5-minute scheduler (daily catch-up), gated on DATABASE_URL."""
from __future__ import annotations

import atexit
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db_config import is_configured
from .scheduled_anomaly_runner import five_minute_tick, start_backfill_thread

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


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
        sched.add_job(
            five_minute_tick,
            CronTrigger(minute="*/5", timezone="UTC"),
            id="scheduled_anomaly_5m",
            replace_existing=True,
        )
        sched.start()
        _scheduler = sched
        atexit.register(lambda s=sched: s.shutdown(wait=False))
        start_backfill_thread()
        logger.info("Scheduled anomaly scheduler started (every 5 minutes, daily catch-up).")
    except Exception:
        logger.exception("Failed to start APScheduler")
