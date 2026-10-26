"""Audit release files and optional Git history. Findings never print matched values."""
import argparse
import ipaddress
from pathlib import Path
import re
import subprocess

PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'credential URL': re.compile(r'https?://[^\s/]+:[^\s/@]+@'),
    'credential assignment': re.compile(r'''(?i)(?:api[_-]?key|password|(?:aws_)?secret(?:_access_key)?|(?:access[_-]?)?token)["']?\s*[:=]\s*["'][A-Za-z0-9_+/=-]{16,}["']'''),
    'private IPv4': re.compile(r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|169\.254(?:\.\d{1,3}){2}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2})\b'),
    'private IPv6': re.compile(r'(?i)(?<![0-9a-f:])(?:f[cd][0-9a-f]{2}|fe80):[0-9a-f:]+'),
}
EXCLUDED = {'.git', '.venv', '__pycache__', 'node_modules', 'data', 'secrets', '.generated'}


def audit_text(text, label, forbidden=()):
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(f'{label}:{line_number}: {category}')
        if any(marker.lower() in line.lower() for marker in forbidden):
            findings.append(f'{label}:{line_number}: forbidden identifier')
        for candidate in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', line):
            try:
                address = ipaddress.IPv4Address(candidate)
            except ipaddress.AddressValueError:
                continue
            documentation = any(address in ipaddress.IPv4Network(network) for network in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'))
            if not address.is_loopback and not documentation and not PATTERNS['private IPv4'].fullmatch(candidate):
                findings.append(f'{label}:{line_number}: non-example IPv4 address')
        for mac in re.findall(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', line):
            if mac.lower() not in ('00:00:00:00:00:00', '02:00:00:00:00:01'):
                findings.append(f'{label}:{line_number}: non-example MAC address')
    return findings


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True).stdout


def audit_repository(root, history=False, forbidden=()):
    root = Path(root)
    try:
        paths = [Path(name) for name in git(root, 'ls-files', '-z').decode().split('\0') if name]
    except subprocess.CalledProcessError:
        paths = [p.relative_to(root) for p in root.rglob('*') if p.is_file()
                 and not set(p.relative_to(root).parts) & EXCLUDED]
    findings = []
    for relative in paths:
        path = root / relative
        if not path.is_file():
            continue
        name = relative.name
        if (name == '.env' or (name.startswith('.env.') and name != '.env.example')
                or name.endswith(('.pem', '.key', '.p12')) or name in ('hosts.json', 'dashboard.json', 'known_hosts')
                or set(relative.parts) & {'secrets', 'data', '.generated'}):
            findings.append(f'{relative}: private/runtime file is tracked')
        findings.extend(audit_text(path.read_bytes().decode('utf-8', errors='replace'), str(relative), forbidden))
    objects = 0
    if history:
        for entry in git(root, 'rev-list', '--objects', '--all').decode().splitlines():
            sha, _, path = entry.partition(' ')
            kind = git(root, 'cat-file', '-t', sha).decode().strip()
            if kind not in ('blob', 'commit'):
                continue
            objects += 1
            label = 'history/' + (path or sha[:12])
            findings.extend(audit_text(git(root, 'cat-file', '-p', sha).decode('utf-8', errors='replace'), label, forbidden))
            if path and (Path(path).name in ('.env', 'hosts.json', 'dashboard.json', 'known_hosts')
                         or set(Path(path).parts) & {'secrets', 'data', '.generated'}):
                findings.append(f'{label}: private/runtime file in history')
    return sorted(set(findings)), len(paths), objects


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--forbid', action='append', default=[], help='Additional private name/domain to reject (matched values stay redacted)')
    args = parser.parse_args()
    findings, files, objects = audit_repository(Path.cwd(), args.history, args.forbid)
    for finding in findings:
        print(finding)
    print(f'Checked {files} files and {objects} historical objects; {len(findings)} findings.')
    raise SystemExit(bool(findings))
