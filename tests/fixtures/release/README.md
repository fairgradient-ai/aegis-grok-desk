# Release-check fixtures

These files are for the deny checks in [docs/RELEASE.md](../../../docs/RELEASE.md). They are not part of the desk.

- `blackhole_mcp.py` is a stub MCP server with one tool, `place_order`. The tool does nothing. It exists only to
  prove that the desk's hook refuses the call. If the hook ever let the call through, the server would answer
  `blackhole: nothing was done` and change nothing. It uses no network, runs no command and writes no file.
- `mcp-config.json` connects that server to Claude Code as `blackhole`, so the tool is named
  `mcp__blackhole__place_order` there. Pass it with `--mcp-config` from the repository root; its path is relative
  to that root.

`tests/gates/test_release_stub_mcp.py` checks both files.
