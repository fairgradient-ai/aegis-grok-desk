# Aegis Desk contributor guide

This repository ships desk instructions, role profiles, routine manifests and standard-library
validators and tools. Editing it does not start a desk or authorize any brokerage action.

- Run `bash scripts/check.sh` before every commit (available from release 0.1.0's first pull request).
- The desk never places, modifies or cancels orders. Keep that boundary in every role, skill and tool.
- Every number a role quotes comes from a tool and carries its source, data delay and UTC time.
- Preserve the MIT notices in `LICENSE`, `LICENSES/` and each skill's `ATTRIBUTION.md`.
