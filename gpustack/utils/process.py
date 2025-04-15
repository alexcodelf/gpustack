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


def add_signal_handlers():
    signal.signal(signal.SIGTERM, handle_termination_signal)


def add_signal_handlers_in_loop():
    if platform.system() == "windows":
        # Windows does not support asyncio signal handlers.
        add_signal_handlers()
        return

    loop = asyncio.get_event_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        logger.debug(f"Adding signal handler for {sig}")
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown_event_loop(sig, loop))
        )


async def shutdown_event_loop(signal=None, loop=None):
    logger.debug(f"Received signal: {signal}. Shutting down gracefully...")

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
        logger.error(f"Error while terminating process tree: {e}")


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


def parent_process_monitor(parent_pid=None):
    """
    Monitor the parent process and exit if it is not running.

    Args:
        parent_pid: The PID of the parent process to monitor.

    Returns:
        thread: A thread that monitors the parent process.
    """
    if parent_pid is None:
        parent_pid = os.getppid()

    thread = threading.Thread(target=monitor_loop, args=(parent_pid,), daemon=True)
    thread.start()
    return thread


def monitor_loop(parent_pid: int, check_interval: float = 1) -> None:
    """
    Monitor the parent process and exit if it is not running.

    Args:
        parent_pid: The PID of the parent process to monitor.
        check_interval: Interval in seconds between checks. Must be positive.

    Raises:
        ValueError: If check_interval is not positive.
    """
    if not isinstance(check_interval, (int, float)) or check_interval <= 0:
        raise ValueError(f"check_interval must be positive, got {check_interval}")

    while not threading_stop_event.is_set():
        try:
            if not psutil.pid_exists(parent_pid):
                logger.warning(
                    f"Parent process with PID {parent_pid} not found, exiting"
                )
                os._exit(0)  # Force immediate exit without cleanup
        except psutil.ZombieProcess:
            logger.warning(
                f"Parent process with PID {parent_pid} is a zombie process, exiting"
            )
            os._exit(0)
        except psutil.NoSuchProcess:
            logger.warning(
                f"Parent process with PID {parent_pid} no longer exists, exiting"
            )
            os._exit(0)
        except psutil.AccessDenied:
            logger.warning(
                f"Access denied when checking parent process with PID {parent_pid}, exiting"
            )
            os._exit(0)
        except Exception as e:
            logger.error(
                f"Unexpected error checking parent process with PID {parent_pid}: {e}, exiting"
            )
            os._exit(1)  # Use exit code 1 for errors

        # Use event with timeout instead of sleep to allow for clean shutdown
        if threading_stop_event.wait(timeout=check_interval):
            break
