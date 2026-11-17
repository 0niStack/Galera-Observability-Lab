#!/usr/bin/env python3
\"\"\"Emit one JSON snapshot of MariaDB and Galera status.

Credentials are read from /etc/galera-monitor.cnf. The script uses the
MariaDB/MySQL CLI so it does not need a Python database driver.
\"\"\"

import json
import os
import socket
import subprocess
from datetime import datetime, timezone

CONFIG = "/etc/galera-monitor.cnf"
OUTPUT = "/var/log/galera/galera-status.json"
MYSQL = "/usr/bin/mariadb"
if not os.path.exists(MYSQL):
    MYSQL = "/usr/bin/mysql"

STATUS_KEYS = {
    "Threads_connected": "threads_connected",
    "Threads_running": "threads_running",
    "Max_used_connections": "max_used_connections",
    "Aborted_connects": "aborted_connects",
    "Slow_queries": "slow_queries",
    "wsrep_cluster_size": "wsrep_cluster_size",
    "wsrep_cluster_status": "wsrep_cluster_status",
    "wsrep_local_state_comment": "wsrep_local_state_comment",
    "wsrep_ready": "wsrep_ready",
    "wsrep_connected": "wsrep_connected",
    "wsrep_local_recv_queue": "wsrep_local_recv_queue",
    "wsrep_local_send_queue": "wsrep_local_send_queue",
    "wsrep_flow_control_paused": "wsrep_flow_control_paused",
    "wsrep_local_cert_failures": "wsrep_local_cert_failures",
    "wsrep_local_bf_aborts": "wsrep_local_bf_aborts",
}


def mysql_query(sql):
    command = [
        MYSQL,
        f"--defaults-extra-file={CONFIG}",
        "--batch",
        "--raw",
        "--skip-column-names",
        "-e",
        sql,
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout


def numeric(value, default=0):
    try:
        return float(value) if "." in str(value) else int(value)
    except (TypeError, ValueError):
        return default


def main():
    event = {
        "@timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host_name": socket.gethostname().split(".")[0],
        "mariadb_status": "down",
    }

    try:
        status_rows = mysql_query("SHOW GLOBAL STATUS")
        variables_rows = mysql_query(
            "SHOW GLOBAL VARIABLES LIKE 'max_connections'"
        )

        raw_status = {}
        for line in status_rows.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                raw_status[parts[0]] = parts[1]

        raw_variables = {}
        for line in variables_rows.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                raw_variables[parts[0]] = parts[1]

        event["mariadb_status"] = "up"

        for source_key, target_key in STATUS_KEYS.items():
            value = raw_status.get(source_key)
            if target_key in {
                "wsrep_cluster_status",
                "wsrep_local_state_comment",
                "wsrep_ready",
                "wsrep_connected",
            }:
                event[target_key] = value or "unknown"
            elif target_key == "wsrep_flow_control_paused":
                try:
                    event[target_key] = float(value or 0)
                except ValueError:
                    event[target_key] = 0.0
            else:
                event[target_key] = numeric(value)

        event["max_connections"] = numeric(
            raw_variables.get("max_connections")
        )

    except (subprocess.SubprocessError, OSError) as exc:
        event["collector_error"] = str(exc)[:500]

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "a", encoding="utf-8") as output:
        output.write(json.dumps(event, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
