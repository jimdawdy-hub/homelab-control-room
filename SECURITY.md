# Security

The live monitor exposes infrastructure inventory and sensor history. Read-only does not mean public.
Bind loopback and put remote access behind your own authenticated TLS proxy or SSH tunnel. There is
no application login in this edition. Do not publish live data or use the demo's public hosting for it.

The collector's SSH key must be dedicated and restricted to a fixed, root-owned probe. Retain strict
host-key validation. Protect the local key/config and the account that can edit probe code. Optional
guest-agent probes execute fixed Python inside running Linux guests; enable them deliberately.

Never commit real config, keys, known-host files, runtime snapshots, history, logs, or deployment backups.
The audit script detects common secret formats and private IP ranges; pattern checks cannot prove
that arbitrary text contains no secret. Review what will be public and the complete Git history.

For a suspected vulnerability, use GitHub's private vulnerability reporting if enabled. If unavailable,
open a minimal issue requesting a private contact method without publishing the exploit, credentials,
private addresses or real topology. Do not include live snapshots in public reports.
