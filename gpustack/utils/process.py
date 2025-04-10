import psutil
import asyncio
import logging
import signal
import os
import threading


from gpustack.utils import platform

logger = logging.getLogger(__name__)

threading_stop_event = threading.Event()

termination_signal_handled = False


# Job object manager class
class JobObjectManager:
    def __init__(self):
        self.job_object = None


job_manager = JobObjectManager()


def add_signal_handlers():
    signal.signal(signal.SIGTERM, handle_termination_signal)


def add_signal_handlers_in_loop():
    if platform.system() == "windows":
        # Windows does not support asyncio signal handlers.
        add_signal_handlers()
        return

    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        logger.debug("Adding signal handler for %s", sig)
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown_event_loop(sig, loop))
        )


async def shutdown_event_loop(signal=None, loop=None):
    logger.debug("Received signal: %s. Shutting down gracefully...", signal)

    threading_stop_event.set()

    try:
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()

        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    handle_termination_signal(signal=signal)


def handle_termination_signal(signal=None, frame=None):
    """
    Terminate the current process and all its children.
    """
    global termination_signal_handled
    if termination_signal_handled:
        return
    termination_signal_handled = True

    threading_stop_event.set()

    pid = os.getpid()
    terminate_process_tree(pid)


def terminate_process_tree(pid: int):
    try:
        process = psutil.Process(pid)
        children = process.children(recursive=True)

        # Terminate all child processes
        terminate_processes(children)

        # Terminate the parent process
        terminate_process(process)
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        logger.error("Error while terminating process tree: %s", e)


def terminate_processes(processes):
    """
    Terminates a list of processes, attempting graceful termination first,
    then forcibly killing remaining ones if necessary.
    """
    for proc in processes:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue

    # Wait for processes to terminate and kill if still alive
    _, alive = psutil.wait_procs(processes, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue


def terminate_process(process):
    """
    Terminates a single process, attempting graceful termination first,
    then forcibly killing it if necessary.
    """
    if process.is_running():
        try:
            process.terminate()
            process.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass


def setup_windows_job_object():
    """
    Set up a Job object for Windows to ensure that all child processes are terminated when the main process terminates.
    This is the best practice for managing process groups on Windows.
    """
    if platform.system() != "windows":
        return

    try:
        # Import win32 related modules only on Windows
        import win32job
        import win32api

        # Create a global Job object
        job_name = f"GPUStackJob_{os.getpid()}"
        h_job = win32job.CreateJobObject(None, job_name)

        # Set the critical parameter: terminate all child processes when the main process terminates
        extended_info = win32job.QueryInformationJobObject(
            h_job, win32job.JobObjectExtendedLimitInformation
        )
        extended_info['BasicLimitInformation'][
            'LimitFlags'
        ] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            h_job, win32job.JobObjectExtendedLimitInformation, extended_info
        )

        # Add the current process to the Job object
        h_process = win32api.GetCurrentProcess()
        win32job.AssignProcessToJobObject(h_job, h_process)

        # Store job object reference in the manager class
        job_manager.job_object = h_job

        logger.info("Process management initialized with Job object: %s", job_name)
    except ImportError:
        logger.warning(
            "win32job module not available. Process management with Job objects disabled."
        )
        logger.warning(
            "Install pywin32 package to enable advanced process management on Windows."
        )
    except (OSError, RuntimeError) as e:
        logger.error("Failed to initialize process management with Job object: %s", e)
