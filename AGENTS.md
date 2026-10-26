# Agent instructions

Read `docs/AI_SETUP.md` before adapting or deploying this monitor. This is reusable public source;
machine-specific configuration, credentials, addresses, inventory and runtime data stay local and ignored.

- Verify actual nodes and capabilities. Use config for host names/VM identities; no personal constants.
- Default to strict-host-key, dedicated, forced-command SSH access and loopback web binding.
- Do not change host/guest lifecycle, storage, backup jobs, firewalls or authentication as part of setup.
- Preserve unrelated files, keys, configurations and running services. Make deployment changes reviewable.
- Prefer the existing Python standard-library/plain-JS approach; no framework or dependency for minor work.
- Never fabricate readings, relabel dissimilar CPU sensors, or turn unavailable values into zero.
- Tests: `python -m unittest discover -s telemetry -v`, `node --test test_*.cjs`.
- Before any public commit/push: `python3 scripts/audit_public.py --history`, review staged files and Git history.
- Never publish real config, private keys, credential output, deployment backups or live-data screenshots.
