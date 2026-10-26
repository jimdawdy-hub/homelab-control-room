# Homelab Control Room

A small, read-only dashboard for Linux and Proxmox homelabs: host resources, guests, ZFS, optional
Proxmox Backup Server status, and temperature history. Python on the collector, plain HTML/CSS/JS
in the browser. No frontend build, cloud account, or external telemetry service required.

**[Try the public demo](https://homelab-control-room-demo.legalteam-5526.chatgpt.site)** ·
**[AI-assisted setup guide](docs/AI_SETUP.md)** · **[Manual setup](docs/SETUP.md)**

The demo uses fictional infrastructure and simulated data. A real installation uses your own host
configuration and stays bound to localhost by default. Your machine names, keys and readings do not
belong in this repository.

## Give this to your AI assistant

Clone or download this repository, then paste the following into the assistant that can access your lab:

```text
Help me install Homelab Control Room on my own systems.

First read README.md, AGENTS.md, docs/AI_SETUP.md, docs/SETUP.md, and SECURITY.md in this
repository. Follow the AI setup guide. Inspect the code and my actual environment before
making changes. Ask me only for the missing inventory and preferences you cannot discover safely.

Start with the local synthetic demo. Then propose my collector host, monitored nodes, SSH
identities, available sensors, and an exact file/service change plan. Keep the real dashboard
on localhost behind my existing authenticated access path. Do not open firewall ports,
disable SSH host-key verification, expose the live dashboard publicly, change backup jobs,
or reboot a host. Get my approval for the concrete deployment plan before installing it.

Use local, ignored configuration files for my details. Never put secrets, real addresses,
machine identifiers, or live metrics in commits, screenshots, public issues, or chat output.
Use the fixed read-only SSH probe and preserve my existing authorized_keys entries.
Guest-agent resource probes and optional controller tools require explicit opt-in.

Verify my host identities, current/stale readings, temperature labels, 10-minute history,
and the actual page in a browser. Give me exact start/stop/update/rollback commands and a
sanitized handoff. Say clearly what could not be verified. Do not publish or push my local
configuration or modify this upstream repository on my behalf.
```

## Try it locally first

```sh
git clone https://github.com/jimdawdy-hub/homelab-control-room.git
cd homelab-control-room
python3 -m http.server 8070 --bind 127.0.0.1 --directory demo
```

Open **http://127.0.0.1:8070**. This serves only the synthetic demo folder; it does not connect to
anything in your lab. Never serve the repository root: it may contain your ignored local configuration.

## What the real monitor shows

- Any number of configured host nodes: CPU utilization, physical core/thread counts, available memory,
  uptime, optional ZFS pools and volumes.
- Proxmox guest inventory; optional Linux guest filesystem/memory/CPU data through an already-working
  QEMU guest agent and Python 3. Stopped guests stay stopped. Configured capacity is distinguished
  from live measurements.
- Optional local PBS service/datastore/task summary. It does **not** verify backup coverage or restores.
- Ordered thermal cards: HBA when available; up to two CPU readings per socket; GPU; PCH; System;
  Peripheral; DIMM average; Optane average. Actual Tdie/Tctl are preferred; Intel fallbacks use package
  and BMC readings, then hottest core. Averages use the available reported memory-bank sensors.
- Fan RPM without graphs. Missing sensors are unavailable, never invented. Drive cards are omitted;
  raw temperature history can still include SMART readings for separate analysis.
- Resource snapshots every minute and thermal/history sampling every ten minutes, with 24-hour
  charts and 90-day daily JSONL retention. Cached readings retain their original timestamps; outages
  do not become fabricated chart samples.
- Local service bookmarks configured in `config/dashboard.json`. No discovery of arbitrary LAN services.

## Real installation

Use **[docs/SETUP.md](docs/SETUP.md)** or the AI instructions above. The moving parts are intentionally small:

```text
Linux/PVE/PBS nodes                         Collector / dashboard machine
  fixed read-only SSH probe  ------------>  telemetry/collect.py  (1 minute)
  thermal cache             ------------>  data/metrics.json
    hwmon / IPMI / SMART                    telemetry/history.py  (10 minutes)
  optional StorCLI HBA cache                data/history/*.jsonl
                                           telemetry/server.py -> localhost:8080
                                           your authenticated TLS proxy -> browser
```

Python **3.11+**, Linux `/proc` and `/sys`, and SSH are required for live collection. Install the pinned
collector dependency from `requirements.txt`. Sensor/PBS utilities are optional and must be available
on the host that actually owns the hardware. The demo needs only Python's static file server.
Windows guest metrics and automatic machine provisioning are not included.

## Source layout

| Path | Purpose |
| --- | --- |
| `demo/` | Self-contained, synthetic public showcase |
| `web/` | Real dashboard, using the same-origin read-only API |
| `telemetry/` | Collector, probes, server, history and Python tests |
| `config/*.example.json` | Documentation-only hostnames and configuration templates |
| `scripts/build_probe.py` | Generates a fixed host probe without installing or contacting anything |
| `scripts/audit_public.py` | Redacted secret/address audit of release files and optional Git history |
| `systemd/` | Reviewed service/timer templates; no automatic installation |
| `docs/AI_SETUP.md` | Detailed instructions for adapting the monitor to another lab |

## Development and verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s telemetry -v
node --test test_*.cjs
python3 scripts/audit_public.py --history
```

Node is needed only for the built-in JavaScript checks, not to run the monitor. Before submitting a
PR, follow [CONTRIBUTING.md](CONTRIBUTING.md). Report security concerns according to [SECURITY.md](SECURITY.md).
Keep local configs, runtime data, known-host files, deployment receipts and private keys out of Git.

MIT licensed. This is a community starting point, not a replacement for backups, hardware safety
limits, or a supported infrastructure monitoring stack. Review changes before installing on a hypervisor.
