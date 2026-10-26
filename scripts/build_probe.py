"""Build a fixed host probe locally; nothing is installed or contacted."""
import argparse
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1]


def build(node, guest_metrics=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', node):
        raise ValueError('Use the exact hostname, containing letters, numbers, dots or hyphens')
    return (f'NODE_NAME = {node!r}\nCOLLECT_GUESTS = {guest_metrics!r}\n'
            + 'GUEST_SOURCE = ' + repr((BASE / 'telemetry/guest_probe.py').read_text()) + '\n'
            + (BASE / 'telemetry/host_probe.py').read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True)
    parser.add_argument('--guest-metrics', action='store_true', help='Opt in to fixed read-only QEMU guest-agent Python probes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = build(args.node, args.guest_metrics)
    compile(source, '<fixed host probe>', 'exec')
    args.output.write_text(source)
    print('Fixed probe built; copy it to the intended host after reviewing it.')
