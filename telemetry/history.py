"""Append thermal readings to a daily JSON Lines file and prune old days."""
import argparse
import datetime
import json
from pathlib import Path

RETENTION_DAYS = 90
HOST_FIELDS = ('name', 'error', 'thermal', 'hba')


def log_snapshot(data_dir, now=None, retention_days=RETENTION_DAYS):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    snapshot = json.loads((data_dir / 'metrics.json').read_text())
    hosts = snapshot.get('hosts', [snapshot.get('host')])
    thermal_snapshot = {key: snapshot[key] for key in ('schema_version', 'generated_at') if key in snapshot}
    thermal_snapshot['hosts'] = [
        {key: host[key] for key in HOST_FIELDS if key in host}
        for host in hosts if isinstance(host, dict)
    ]
    history = data_dir / 'history'
    history.mkdir(exist_ok=True)
    line = json.dumps({'logged_at': now.isoformat(), 'snapshot': thermal_snapshot},
                      separators=(',', ':'), allow_nan=False)
    with open(history / f'{now:%Y-%m-%d}.jsonl', 'a') as handle:
        handle.write(line + '\n')
    cutoff = (now - datetime.timedelta(days=retention_days)).date()
    for path in history.glob('????-??-??.jsonl'):
        try:
            day = datetime.date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    log_snapshot(parser.parse_args().data_dir)
