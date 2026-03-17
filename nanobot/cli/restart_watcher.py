"""Restart watcher for nanobot gateway.

Runs as an independent background process. Monitors the gateway process and
restarts it if it dies. Also responds to a signal file for manual restarts.
Started automatically by `nanobot gateway`.

Signal file: /tmp/nanobot_restart
  - Create this file to trigger a restart.
  - Optional: write a message into the file (shown in logs).
  - The watcher deletes the file, kills the old gateway, and starts a new one.

PID file: /tmp/nanobot_watcher.pid
  - Written on startup so a new gateway can kill the previous watcher.
"""
import os
import signal
import subprocess
import sys
import time

SIGNAL_FILE = "/tmp/nanobot_restart"
WATCHER_PID_FILE = "/tmp/nanobot_watcher.pid"
CHECK_INTERVAL = 2  # seconds


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: restart_watcher.py <gateway_pid> <log_file> <cmd> [args...]", file=sys.stderr)
        sys.exit(1)

    gateway_pid = int(sys.argv[1])
    log_file = sys.argv[2]
    gateway_cmd = sys.argv[3:]

    # Write own PID so the next gateway launch can kill us before spawning a replacement.
    try:
        with open(WATCHER_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    def _popen_gateway() -> subprocess.Popen:
        log_fh = open(log_file, "a")
        return subprocess.Popen(gateway_cmd, start_new_session=True, stdout=log_fh, stderr=log_fh)

    while True:
        time.sleep(CHECK_INTERVAL)

        # Check for manual restart signal first.
        if os.path.exists(SIGNAL_FILE):
            try:
                msg = open(SIGNAL_FILE).read().strip()
            except OSError:
                msg = ""
            try:
                os.remove(SIGNAL_FILE)
            except OSError:
                pass

            if msg:
                print(f"[restart_watcher] restart requested: {msg}", flush=True)
            else:
                print("[restart_watcher] restart signal received", flush=True)

            # Kill current gateway if still running.
            if _is_alive(gateway_pid):
                try:
                    os.kill(gateway_pid, signal.SIGTERM)
                    for _ in range(10):
                        time.sleep(0.5)
                        if not _is_alive(gateway_pid):
                            break
                    else:
                        try:
                            os.kill(gateway_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                except ProcessLookupError:
                    pass

            proc = _popen_gateway()
            gateway_pid = proc.pid
            print(f"[restart_watcher] new gateway PID {gateway_pid}", flush=True)
            continue

        # Restart gateway if it crashed.
        if not _is_alive(gateway_pid):
            print("[restart_watcher] gateway crashed, restarting...", flush=True)
            proc = _popen_gateway()
            gateway_pid = proc.pid
            print(f"[restart_watcher] new gateway PID {gateway_pid}", flush=True)


if __name__ == "__main__":
    main()

