"""APScheduler execution with the actual planned instant, without listener work."""
from apscheduler.executors.base import run_job
from apscheduler.executors.pool import ThreadPoolExecutor

from .run_contracts import TriggerContext
from .schedule_runtime import wake


def run_planned_job(job, jobstore_alias, run_times, logger_name):
    events = []
    for planned in run_times:
        research = job.id in {"daily_cycle", "weekly_review"} or job.id.startswith(("pead_score_", "phase_e:"))
        if research:
            with wake(TriggerContext(kind="schedule", schedule_id=job.id,
                                     scheduled_for=planned.isoformat())):
                events.extend(run_job(job, jobstore_alias, [planned], logger_name))
        else:
            events.extend(run_job(job, jobstore_alias, [planned], logger_name))
    return events


class PlannedThreadPoolExecutor(ThreadPoolExecutor):
    def _do_submit_job(self, job, run_times):
        def callback(future):
            exc = future.exception()
            if exc:
                self._run_job_error(job.id, exc, exc.__traceback__)
            else:
                self._run_job_success(job.id, future.result())
        future = self._pool.submit(run_planned_job, job, job._jobstore_alias,
                                   run_times, self._logger.name)
        future.add_done_callback(callback)
