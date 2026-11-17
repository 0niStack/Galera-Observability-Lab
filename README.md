# Galera + MariaDB Observability with Filebeat and OpenSearch

A small deployment project for collecting **MariaDB and Galera database health** from three Galera nodes and forwarding it to a central OpenSearch server.

This intentionally leaves VM-level monitoring (CPU, memory, disk, operating-system security events, etc.) to your existing Wazuh deployment.

## What it collects

| Source | Data |
|---|---|
| MariaDB error log | Database startup/shutdown, InnoDB, authentication, and server errors |
| Galera status JSON | MariaDB availability, cluster membership/state, replication queues, flow control, connections, and counters |
| MariaDB slow query log | Slow SQL statements, grouped into multiline events by Filebeat |

Each node writes to the same OpenSearch index pattern: `galera-logs-YYYY.MM.dd`. Events are distinguished by `log_type` and `host_name`.

## Architecture

```text
galera01 ─┐
galera02 ─┼─ MariaDB logs + status collector + Filebeat ──HTTPS──> OpenSearch
galera03 ─┘                                                        |
                                                                  v
                                                       OpenSearch Dashboards
```

Deploy the node-side files on **every Galera node**. Configure the OpenSearch output to point to your OpenSearch endpoint.

## Project layout

```text
galera-mariadb-observability/
├── README.md
├── config/
│   ├── filebeat.yml.example
│   ├── mariadb-monitor.cnf.example
│   ├── mariadb-slow-log.cnf
│   ├── galera-status.service
│   ├── galera-status.timer
│   └── galera-status.logrotate
└── scripts/
    ├── galera_status.py
    └── install-node.sh
```

## Requirements

On each Galera node:

- Ubuntu/Debian Linux.
- MariaDB client (`mariadb-client` or compatible `mysql` client).
- Python 3.
- Filebeat **7.12.1** if you are matching the original Cluster-Sentinel setup. Confirm the version and OpenSearch compatibility in your environment before rollout.
- Network access from each node to OpenSearch TCP/9200 (or your configured endpoint).
- A MariaDB monitoring account, or a local socket-authenticated account, that can run `SHOW GLOBAL STATUS` and `SHOW GLOBAL VARIABLES`.

> Compatibility note: this project uses the classic Filebeat `log` input and `output.elasticsearch` syntax associated with Filebeat 7.x. OpenSearch compatibility depends on the OpenSearch version and the Beats distribution/version. Test against your actual OpenSearch server before rolling out to all nodes.

## 1. Configure MariaDB monitoring credentials

Copy the example:

```bash
sudo install -o root -g root -m 600 \
  config/mariadb-monitor.cnf.example \
  /etc/galera-monitor.cnf
sudoedit /etc/galera-monitor.cnf
```

Example:

```ini
[client]
user=galera_monitor
password=REPLACE_WITH_A_LONG_PASSWORD
host=localhost
protocol=socket
```

Create a least-privilege account using your MariaDB administrative account:

```sql
CREATE USER 'galera_monitor'@'localhost' IDENTIFIED BY 'REPLACE_WITH_A_LONG_PASSWORD';
GRANT PROCESS ON *.* TO 'galera_monitor'@'localhost';
```

The collector reads global status and variables. Validate the account on your MariaDB version:

```bash
mariadb --defaults-extra-file=/etc/galera-monitor.cnf \
  -NBe "SHOW GLOBAL STATUS LIKE 'wsrep_cluster_size';"
mariadb --defaults-extra-file=/etc/galera-monitor.cnf \
  -NBe "SHOW GLOBAL VARIABLES LIKE 'max_connections';"
```

If your environment uses socket authentication, configure the account and client file accordingly. Do not put database passwords in the collector script.

## 2. Install the health collector

From the project directory:

```bash
sudo install -o root -g root -m 0755 \
  scripts/galera_status.py /usr/local/bin/galera_status.py

sudo install -o root -g root -m 0644 \
  config/galera-status.service /etc/systemd/system/galera-status.service

sudo install -o root -g root -m 0644 \
  config/galera-status.timer /etc/systemd/system/galera-status.timer

sudo install -o root -g root -m 0644 \
  config/galera-status.logrotate /etc/logrotate.d/galera-status

sudo install -o root -g root -m 0750 -d /var/log/galera
sudo touch /var/log/galera/galera-status.json
sudo chown root:adm /var/log/galera/galera-status.json
sudo chmod 0640 /var/log/galera/galera-status.json

sudo systemctl daemon-reload
sudo systemctl enable --now galera-status.timer
```

The systemd timer runs the collector once per minute. Check it:

```bash
sudo systemctl list-timers galera-status.timer
sudo systemctl start galera-status.service
sudo journalctl -u galera-status.service -n 30 --no-pager
sudo tail -n 2 /var/log/galera/galera-status.json
```

If Filebeat runs as the `filebeat` user, add it to the `adm` group or use an equivalent narrowly scoped ACL so it can read the log:

```bash
sudo usermod -aG adm filebeat
sudo systemctl restart filebeat
```

Group membership takes effect after the service restarts.

## 3. Enable MariaDB slow query logging

Review `config/mariadb-slow-log.cnf`. It enables a one-second slow-query threshold and writes to `/var/log/mysql/mariadb-slow.log`.

Install it:

```bash
sudo install -o root -g root -m 0644 \
  config/mariadb-slow-log.cnf \
  /etc/mysql/mariadb.conf.d/60-slow-query-log.cnf

sudo systemctl restart mariadb
```

Check the effective settings:

```bash
sudo mariadb -NBe "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','slow_query_log_file','long_query_time','log_output');"
```

The slow query log can contain SQL literals and sensitive values. Restrict access, define an appropriate retention period, and review privacy requirements before enabling it in production.

## 4. Configure Filebeat

Copy the example configuration:

```bash
sudo cp config/filebeat.yml.example /etc/filebeat/filebeat.yml
sudoedit /etc/filebeat/filebeat.yml
```

Replace:

- `OPENSEARCH_PRIVATE_IP` with the OpenSearch hostname/IP.
- `OPENSEARCH_USERNAME` with an account permitted to create/write the target indices.
- `OPENSEARCH_PASSWORD` with its password.

The example disables TLS certificate verification for a lab/self-signed setup. For production, configure your CA certificate and use `ssl.certificate_authorities`; do not leave `verification_mode: none`.

Check file permissions and validate:

```bash
sudo chown root:root /etc/filebeat/filebeat.yml
sudo chmod 0600 /etc/filebeat/filebeat.yml
sudo filebeat test config -e
sudo filebeat test output -e
sudo systemctl enable --now filebeat
sudo systemctl status filebeat --no-pager
sudo journalctl -u filebeat -f
```

### Filebeat event types

- `mariadb_error`
- `galera_status`
- `mariadb_slow_query`

All events are sent to `galera-logs-YYYY.MM.dd`.

## 5. Verify data in OpenSearch

Check indices:

```bash
curl -k -u 'OPENSEARCH_USERNAME:OPENSEARCH_PASSWORD' \
  'https://OPENSEARCH_PRIVATE_IP:9200/_cat/indices/galera-logs-*?v'
```

Search recent events:

```bash
curl -k -u 'OPENSEARCH_USERNAME:OPENSEARCH_PASSWORD' \
  -H 'Content-Type: application/json' \
  'https://OPENSEARCH_PRIVATE_IP:9200/galera-logs-*/_search?pretty' \
  -d '{
    "size": 10,
    "sort": [{"@timestamp": "desc"}],
    "query": {"match_all": {}}
  }'
```

In OpenSearch Dashboards, create a data view/index pattern `galera-logs-*` and use `@timestamp` as the time field.

Useful filters:

```text
log_type: galera_status
log_type: mariadb_error
log_type: mariadb_slow_query
```

## 6. Suggested initial alert conditions

Tune these against your normal workload; they are starting points, not universal limits.

| Signal | Initial condition |
|---|---|
| MariaDB unavailable | `mariadb_status: down` |
| Cluster membership reduced | `wsrep_cluster_size < expected cluster size` |
| Cluster not Primary | `wsrep_cluster_status != Primary` |
| Node not ready | `wsrep_ready != ON` |
| Node not connected | `wsrep_connected != ON` |
| Node not synchronized | `wsrep_local_state_comment != Synced` (allow expected maintenance/joining states) |
| Replication queue growth | `wsrep_local_recv_queue` persistently above workload baseline |
| Flow control pressure | `wsrep_flow_control_paused` persistently above baseline |
| Connection pressure | `threads_connected / max_connections` above 0.8 |
| Slow queries | Increase in slow-query events over a defined window |

**Important:** `Slow_queries`, `Aborted_connects`, `wsrep_local_cert_failures`, and `wsrep_local_bf_aborts` are cumulative counters since MariaDB startup. Alert on their rate/change over time, not merely on a non-zero value.

A missing heartbeat is different from a VM-down event. Wazuh can monitor VM/agent availability; OpenSearch can also alert when a Galera node has stopped sending `galera_status` events.

## 7. Troubleshooting

### Collector cannot connect to MariaDB

```bash
sudo mariadb --defaults-extra-file=/etc/galera-monitor.cnf \
  -NBe "SHOW GLOBAL STATUS LIKE 'wsrep_cluster_size';"
```

### Collector timer is not running

```bash
sudo systemctl status galera-status.timer
sudo systemctl status galera-status.service
sudo journalctl -u galera-status.service --since today
```

### Filebeat cannot read a log

```bash
sudo -u filebeat test -r /var/log/galera/galera-status.json && echo readable
sudo -u filebeat test -r /var/log/mysql/error.log && echo readable
sudo -u filebeat test -r /var/log/mysql/mariadb-slow.log && echo readable
```

### No OpenSearch events

```bash
sudo filebeat test output -e
sudo journalctl -u filebeat -n 100 --no-pager
```

## Operational notes

- Deploy and test on one non-production Galera node first.
- Ensure all three nodes use synchronized clocks (NTP/chrony).
- Restrict access to error and slow-query logs; they may contain sensitive data.
- Use a dedicated OpenSearch writer account instead of an administrator account.
- Configure certificate verification before production use.
- The collector emits one JSON snapshot per minute. It does not replace a dedicated metrics system or perform automatic database failover.
