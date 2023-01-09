import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import click

from dagster._serdes import serialize_dagster_namedtuple
from dagster._serdes.ipc import interrupt_ipc_subprocess, open_ipc_subprocess
from dagster._utils.log import configure_loggers

from .job import apply_click_params
from .utils import get_instance_for_service
from .workspace.cli_target import (
    get_workspace_load_target,
    python_file_option,
    python_module_option,
    workspace_option,
)


def _interrupt_after_grace_period(process, start_time, logger, grace_period, process_name):
    while True:
        if process.poll() is not None:
            # process died on its own
            return

        if time.time() - start_time >= grace_period:
            logger.info(f"Interrupting {process_name} process...")
            interrupt_ipc_subprocess(process)
            return

        time.sleep(1)


def dev_command_options(f):
    return apply_click_params(
        f,
        workspace_option(),
        python_file_option(),
        python_module_option(),
    )


@click.command(
    name="dev",
    help=(
        "Start a local deployment of Dagster, including dagit running on localhost and the"
        " dagster-daemon running in the background"
    ),
)
@dev_command_options
@click.option(
    "--code-server-log-level",
    help="Set the log level for code servers spun up by dagster services.",
    show_default=True,
    default="warning",
    type=click.Choice(
        ["critical", "error", "warning", "info", "debug", "trace"], case_sensitive=False
    ),
)
@click.option("--dagit-port", help="Port to use for the Dagit UI.", required=False)
def dev_command(code_server_log_level, dagit_port, **kwargs):
    # check if dagit installed, crash if not
    try:
        import dagit  #  # noqa: F401
    except ImportError:
        raise click.UsageError(
            "The dagit package must be installed in order to use the dagster dev command."
        )

    configure_loggers()
    logger = logging.getLogger("dagster")

    # Sanity check workspace args
    get_workspace_load_target(kwargs)

    dagster_home_path = os.getenv("DAGSTER_HOME")

    dagster_yaml_path = os.path.join(os.getcwd(), "dagster.yaml")

    has_local_dagster_yaml = os.path.exists(dagster_yaml_path)
    if dagster_home_path:
        if has_local_dagster_yaml and Path(os.getcwd()) != Path(dagster_home_path):
            logger.warning(
                "Found a dagster instance configuration value (dagster.yaml) in the current"
                " folder, but your DAGSTER_HOME environment variable is set to"
                f" {dagster_home_path}. The dagster.yaml file will not be used to configure Dagster"
                " unless it is placed in the same folder as DAGSTER_HOME."
            )

    with get_instance_for_service("dagster dev", logger_fn=logger.info) as instance:
        logger.info("Launching Dagster services...")

        args = [
            "--instance-ref",
            serialize_dagster_namedtuple(instance.get_ref()),
            "--code-server-log-level",
            code_server_log_level,
        ] + (["--workspace", kwargs["workspace"]] if kwargs.get("workspace") else [])

        if kwargs.get("python_file"):
            for python_file in kwargs["python_file"]:
                args.extend(["--python-file", python_file])

        if kwargs.get("module_name"):
            for module_name in kwargs["module_name"]:
                args.extend(["--module-name", module_name])

        dagit_process = open_ipc_subprocess(
            [sys.executable, "-m", "dagit"] + (["--port", dagit_port] if dagit_port else []) + args
        )
        daemon_process = open_ipc_subprocess(
            [sys.executable, "-m", "dagster._daemon", "run"] + args
        )
        try:
            while True:
                time.sleep(5)

                if dagit_process.poll() is not None:
                    raise Exception(
                        "Dagit process shut down unexpectedly with return code"
                        f" {dagit_process.returncode}"
                    )

                if daemon_process.poll() is not None:
                    raise Exception(
                        "dagster-daemon process shut down unexpectedly with return code"
                        f" {daemon_process.returncode}"
                    )

        except BaseException as e:
            logger.info("Shutting down Dagster services...")

            # If it's a KeyboardInterrupt / SIGINT that probably means that the whole
            # process group is being interrupted by a CTRL-C, so give the subprocesses a
            # grace period to stop on their own. Otherwise, interrupt them immediately
            grace_period = 30 if isinstance(e, KeyboardInterrupt) else 0

            start_time = time.time()
            _interrupt_after_grace_period(
                dagit_process, start_time, logger, grace_period=grace_period, process_name="dagit"
            )
            _interrupt_after_grace_period(
                daemon_process,
                start_time,
                logger,
                grace_period=grace_period,
                process_name="dagster-daemon",
            )

            try:
                dagit_process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                logger.warning("dagit process did not terminate cleanly, killing the process")
                dagit_process.kill()

            try:
                daemon_process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "dagster-daemon process did not terminate cleanly, killing the process"
                )
                daemon_process.kill()

            logger.info("Dagster services shut down.")
