# start_imports
import os
from datetime import date, datetime, timedelta

from dagster import (
    In,
    Nothing,
    RetryPolicy,
    RunRequest,
    ScheduleDefinition,
    ScheduleEvaluationContext,
    job,
    op,
    repository,
    schedule,
)


# start_ops
@op
def print_date():
    os.system('date')

@op(
    retry_policy=RetryPolicy(
        max_retries=3
    ),
    ins={"start": In(Nothing)}
)
def sleep():
    os.system('sleep 5')

@op(ins={"start": In(Nothing)})
def templated(context):
    ds = context.get_tag("date")
    for i in range(5):
        os.system(f'echo {ds}')
        os.system(f'echo {datetime.strptime(ds, "%Y-%m-%d") + timedelta(days=7)}')
# end_ops

# start_job
@job(tags={"dagster/max_retries": 1, "dag_name": "example"})
def tutorial_job():
    _dt = print_date()
    sleep(_dt)
    templated(_dt)
# end_job

# start_schedule
@schedule(job=tutorial_job, cron_schedule="@daily")
def tutorial_job_schedule(context: ScheduleEvaluationContext):
    scheduled_date = context.scheduled_execution_time
    return RunRequest(
        run_key=None,
        tags={"date": scheduled_date},
    )
# end_schedule


# start_repo
@repository
def tutorial_repo():
    return [tutorial_job, tutorial_job_schedule]
# end_repo