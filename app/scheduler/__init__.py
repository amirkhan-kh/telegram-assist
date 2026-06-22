"""Scheduler layer.

Bridges the persisted APScheduler jobstore world (a module-level callable plus
JSON-serialisable args) to the live runtime services via ``app.registry``.

* :mod:`app.scheduler.factory` builds the (unstarted) ``AsyncIOScheduler``.
* :mod:`app.scheduler.jobs` defines the persisted ``execute_job`` callable and
  the ``schedule_at`` / ``cancel_job`` / ``make_job_id`` helpers used by services.
"""

from __future__ import annotations
