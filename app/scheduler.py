"""Background scheduler wrapping APScheduler.

Runs :meth:`PRService.scan_all` on a fixed interval. The job is non-overlapping
(``max_instances=1``) so a slow scan never stacks up.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import SchedulerConfig
from app.pr_service import PRService

logger = logging.getLogger(__name__)


class ReviewScheduler:
    def __init__(self, config: SchedulerConfig, service: PRService) -> None:
        self._config = config
        self._service = service
        self._scheduler: Optional[BackgroundScheduler] = None

    def _job(self) -> None:
        logger.info("Scheduled scan starting")
        try:
            outcomes = self._service.scan_all()
            logger.info("Scheduled scan complete: %d PR(s) processed", len(outcomes))
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled scan failed")

    def start(self) -> None:
        if not self._config.enabled:
            logger.info("Scheduler disabled in config; not starting")
            return
        if self._scheduler is not None:
            return
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._scheduler.add_job(
            self._job,
            "interval",
            seconds=self._config.interval_seconds,
            id="pr_scan",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "Scheduler started: scanning every %d seconds",
            self._config.interval_seconds,
        )

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running
