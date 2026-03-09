"""Restart watcher for nanobot gateway.

Runs as an independent background process. Polls for a signal file and
restarts the gateway when it appears. Started automatically by `nanobot gateway`.

Signal file: /tmp/nanobot_restart
  - Create this file to trigger a restart.
  - Optional: write a message into the file (shown in logs).
  - The watcher deletes the file, kills the old gateway, and starts a new one.
"""
import os
import signal
import subprocess
import sys
import time

SIGNAL_FILE = "/tmp/nanobot_restart"
CHECK_INTERVAL = 2  # seconds


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: restart_watcher.py <gateway_pid> <cmd> [args...]", file=sys.stderr)
        sys.exit(1)

    gateway_pid = int(sys.argv[1])
    gateway_cmd = sys.argv[2:]

    while True:
        time.sleep(CHECK_INTERVAL)
        if not os.path.exists(SIGNAL_FILE):
            continue

        # Read optional message then remove signal file
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

        # Terminate the current gateway gracefully, then force-kill if needed
        try:
            os.kill(gateway_pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.5)
                try:
                    os.kill(gateway_pid, 0)  # check if still alive
                except ProcessLookupError:
                    break
            else:
                try:
                    os.kill(gateway_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except ProcessLookupError:
            pass  # already dead

        print(f"[restart_watcher] starting: {' '.join(gateway_cmd)}", flush=True)
        proc = subprocess.Popen(gateway_cmd, start_new_session=True)
        gateway_pid = proc.pid
        print(f"[restart_watcher] new gateway PID {gateway_pid}", flush=True)


if __name__ == "__main__":
    main()
