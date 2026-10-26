"""Serve the collector's sanitized snapshot on the private Compose network."""

import argparse
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
STALE_AFTER_SECONDS = 180
HISTORY_WINDOW_HOURS = 24
MAX_HISTORY_FILE_BYTES = 32 * 1024 * 1024
BASE = Path(__file__).resolve().parents[1]


def load_dashboard(path):
    try:
        config = json.loads(Path(path).read_text())
    except FileNotFoundError:
        config = {}
    services = []
    for item in config.get('services', []):
        url = urlsplit(item.get('url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query:
            continue
        services.append({'name': str(item.get('name', 'Service'))[:120],
                         'description': str(item.get('description', ''))[:300], 'url': url.geturl()})
    return {'title': str(config.get('title', 'Homelab Control Room'))[:120], 'services': services}


def thermal_readings(host):
    """Keep the dashboard summary and its historical aggregates in the same order."""
    thermal = host.get('thermal') or {}
    if not isinstance(thermal, dict):
        thermal = {}
    sensors = [sensor for sensor in thermal.get('temperatures', []) if isinstance(sensor, dict)
               and isinstance(sensor.get('value_c'), (float, int))
               and not isinstance(sensor['value_c'], bool)
               and math.isfinite(sensor['value_c']) and -100 <= sensor['value_c'] <= 200]
    measured = thermal.get('measured_at')
    result = []
    hba = host.get('hba')
    if isinstance(hba, dict):
        result.append(({'id': 'hba:roc', 'label': 'HBA controller', 'source': 'StorCLI',
                        'kind': 'hba', 'value_c': hba.get('roc_temp_c')}, hba.get('measured_at')))
    cpu_groups = {}
    amd_sources = sorted({s.get('source', '') for s in sensors if s.get('label') in ('Tdie', 'Tctl')})
    for sensor in sensors:
        label = sensor.get('label', '')
        if label in ('Tdie', 'Tctl'):
            number, rank = amd_sources.index(sensor.get('source', '')) + 1, ('Tdie', 'Tctl').index(label)
            display = label
        elif match := re.fullmatch(r'Package id (\d+)', label):
            number, rank, display = int(match[1]) + 1, 2, 'Package'
        elif match := re.fullmatch(r'CPU(\d+) Temp', label):
            number, rank, display = int(match[1]), 3, 'BMC temperature'
        else:
            continue
        cpu_groups.setdefault(number, []).append((rank, dict(sensor, label=f'CPU #{number} · {display}')))
    for number in sorted(cpu_groups):
        cores = [s for s in sensors if re.fullmatch(r'Core \d+', s.get('label', ''))
                 and re.search(rf'coretemp\.{number - 1}(?:\D|$)', s.get('source', ''))]
        if cores:
            hottest = {'id': f'cpu:{number}:core-max', 'label': f'CPU #{number} · Hottest core',
                       'kind': 'cpu', 'source': f'Maximum of {len(cores)} reported core sensors',
                       'value_c': max(s['value_c'] for s in cores)}
            cpu_groups[number].append((4, hottest))
        result.extend((sensor, measured) for _, sensor in sorted(cpu_groups[number], key=lambda pair: pair[0])[:2])
    result.extend((dict(sensor, kind='gpu'), measured) for sensor in sensors
                  if sensor.get('kind') == 'gpu' or re.match(r'^GPU\d*', sensor.get('label', ''), re.I))
    for name in ('PCH', 'System', 'Peripheral'):
        candidates = [s for s in sensors if s.get('label', '').casefold() in (name.casefold(), name.casefold() + ' temp')]
        if name == 'PCH' and not candidates:
            candidates = [s for s in sensors if s.get('source', '').startswith('pch_')]
        if candidates:
            sensor = next((s for s in candidates if s.get('source') == 'BMC'), candidates[0])
            result.append((dict(sensor, label=name), measured))
    for key, label, pattern in (('dimm', 'DIMM average', r'DIMM'),
                                ('optane', 'Optane average', r'PMEM|Optane')):
        members = [s for s in sensors if re.search(pattern, s.get('label', ''), re.I)]
        if members:
            result.append(({'id': f'memory:{key}:average', 'label': label, 'kind': 'memory',
                            'source': f'Average of {len(members)} reported memory sensors',
                            'value_c': round(sum(s['value_c'] for s in members) / len(members), 1)}, measured))
    return result


def thermal_hosts(snapshot):
    result = []
    for host in snapshot.get('hosts', [snapshot.get('host')]):
        if not isinstance(host, dict):
            continue
        thermal = host.get('thermal') or {}
        result.append({'name': host.get('name'), 'error': host.get('error'),
                       'measured_at': thermal.get('measured_at'),
                       'temperatures': [dict(sensor, measured_at=at) for sensor, at in thermal_readings(host)],
                       'fans': thermal.get('fans', []),
                       'errors': [e for e in thermal.get('errors', []) if not e.startswith('SMART')]})
    return result


def load_thermal_history(data_dir, now=None):
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=HISTORY_WINDOW_HOURS)
    hosts = {}
    for day in sorted({cutoff.date(), current.date()}):
        path = Path(data_dir) / 'history' / f'{day.isoformat()}.jsonl'
        try:
            with path.open('rb') as source:
                consumed = 0
                while consumed < MAX_HISTORY_FILE_BYTES:
                    line = source.readline(MAX_SNAPSHOT_BYTES + 1)
                    if not line:
                        break
                    consumed += len(line)
                    if len(line) > MAX_SNAPSHOT_BYTES:
                        break
                    try:
                        record = json.loads(line)
                        snapshot = record['snapshot']
                        for host in snapshot.get('hosts', [snapshot.get('host')]):
                            if not isinstance(host, dict) or host.get('error') or not isinstance(host.get('name'), str):
                                continue
                            for sensor, timestamp in thermal_readings(host):
                                value = sensor.get('value_c')
                                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not -100 <= value <= 200:
                                    continue
                                if not all(isinstance(sensor.get(key), str) for key in ('id', 'label', 'source', 'kind')):
                                    continue
                                try:
                                    measured = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                except (ValueError, TypeError, AttributeError):
                                    continue
                                if measured.utcoffset() is None or not cutoff <= measured <= current:
                                    continue
                                series = hosts.setdefault(host['name'], {}).setdefault(sensor['id'],
                                    {key: sensor[key] for key in ('id', 'label', 'source', 'kind')})
                                series.setdefault('points', {})[measured.isoformat()] = value
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        except FileNotFoundError:
            continue
    return {'generated_at': current.isoformat(), 'window_hours': HISTORY_WINDOW_HOURS,
            'interval_seconds': 600,
            'hosts': [{'name': name, 'series': [dict(series, points=sorted(series['points'].items())[-256:])
                       for series in sensors.values()]} for name, sensors in hosts.items()]}


def load_snapshot(path, now=None):
    """Read one atomic snapshot; never change the collector's source file."""
    with Path(path).open("rb") as source:
        raw = source.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot too large")
    snapshot = json.loads(raw)
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be an object")
    if not isinstance(snapshot.get("host"), dict) or not isinstance(snapshot.get("vms"), list):
        raise ValueError("Snapshot must contain host and vms")
    generated_at = snapshot.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError("Snapshot timestamp required")
    generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if generated.utcoffset() is None:
        raise ValueError("Snapshot timestamp must include timezone")
    current = now if now is not None else datetime.now(timezone.utc)
    age = (current - generated).total_seconds()
    if age < -30:
        raise ValueError("Snapshot timestamp is in the future")
    snapshot["age_seconds"] = round(max(0, age), 1)
    snapshot["stale"] = age > STALE_AFTER_SECONDS
    return snapshot


def make_handler(snapshot_path, dashboard_path=None):
    dashboard_path = dashboard_path or BASE / 'config/dashboard.json'
    class TelemetryHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Do not log arbitrary request paths or user-provided query strings.
            pass

        def send_json(self, status, payload):
            body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
            except ValueError:
                self.send_json(404, {"error": "Not found"})
                return
            assets = {'/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}
            if path in assets:
                try:
                    body = (BASE / 'web' / assets[path]).read_bytes()
                except OSError:
                    self.send_json(503, {'error': 'Dashboard assets unavailable'})
                    return
                self.send_response(200)
                self.send_header('Content-Type', {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/styles.css': 'text/css; charset=utf-8'}[path])
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == '/dashboard.json':
                try:
                    self.send_json(200, load_dashboard(dashboard_path))
                except (OSError, ValueError, TypeError, AttributeError):
                    self.send_json(503, {'error': 'Dashboard configuration unavailable'})
                return
            if path not in ("/metrics.json", "/health", "/thermal-history.json"):
                self.send_json(404, {"error": "Not found"})
                return
            try:
                if path == '/thermal-history.json':
                    self.send_json(200, load_thermal_history(Path(snapshot_path).parent))
                    return
                snapshot = load_snapshot(snapshot_path)
                # Reject non-standard JSON values before writing response headers.
                json.dumps(snapshot, allow_nan=False)
            except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                self.send_json(503, {"status": "unavailable", "stale": True})
                return
            if path == "/health":
                self.send_json(503 if snapshot["stale"] else 200, {
                    "status": "stale" if snapshot["stale"] else "ok",
                    "generated_at": snapshot["generated_at"],
                    "age_seconds": snapshot["age_seconds"],
                    "stale": snapshot["stale"],
                })
            else:
                snapshot['thermal_hosts'] = thermal_hosts(snapshot)
                self.send_json(200, snapshot)

        def method_not_allowed(self):
            self.send_json(405, {"error": "Only GET is supported"})

        do_HEAD = method_not_allowed
        do_POST = method_not_allowed
        do_PUT = method_not_allowed
        do_PATCH = method_not_allowed
        do_DELETE = method_not_allowed
        do_OPTIONS = method_not_allowed

    return TelemetryHandler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Read-only dashboard. Bind loopback; use an authenticated TLS proxy for remote access.')
    parser.add_argument('--data-dir', type=Path, default=BASE / 'data')
    parser.add_argument('--dashboard-config', type=Path, default=BASE / 'config/dashboard.json')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.data_dir / 'metrics.json', args.dashboard_config))
    print(f'Dashboard listening at http://{server.server_address[0]}:{server.server_address[1]}', flush=True)
    server.serve_forever()
