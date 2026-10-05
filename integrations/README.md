# Connecting agents to Sweet

`sweet mcp` is Sweet's MCP server. By default it attaches to the most recently started
viewer (`sweet <data>`), so the person sees everything the agent does and approves its
changes. With no viewer running, it works in a private, headless session.

```bash
sweet mcp                    # attach to the latest viewer, else headless
sweet mcp --attach sales     # a specific session (see `sweet sessions`)
sweet mcp --headless         # never attach
sweet mcp --labs             # add the older, larger tool set (headless only)
```

## Claude Code

Install the plugin from this repository (it adds the MCP server and a `sweet` skill that
teaches the inspect → propose → verify workflow):

```bash
claude plugin marketplace add rich-iannone/sweet-data
claude plugin install sweet@sweet-data
```

Or add only the MCP server to a project with a `.mcp.json`:

```json
{
  "mcpServers": {
    "sweet": { "type": "stdio", "command": "sweet", "args": ["mcp", "--client", "claude"] }
  }
}
```

## Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sweet": { "command": "sweet", "args": ["mcp", "--client", "claude-desktop"] }
  }
}
```

## Cursor

In `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "sweet": { "command": "sweet", "args": ["mcp", "--client", "cursor"] }
  }
}
```

## VS Code

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "sweet": { "type": "stdio", "command": "sweet", "args": ["mcp", "--client", "vscode"] }
  }
}
```

## Live data

Agents can open a feed with `open` (`follow: true`, or a `ws://` URL), add checks with
`alerts` (`{"sql": "temp_c < 60"}`, or `{"column": "temp_c", "stat": "null_rate",
"threshold": 0.1}`), and call `watch` in a loop to be woken when one fires. Each call
returns alerts the agent hasn't seen yet, then waits.

## What the person controls

- **Agent mode** (`M` in the viewer): read-only → propose → auto. The default is propose.
- **Masking**: `m` masks the column under the cursor from agents, and the palette's "Mask
  detected PII" masks every column that looks like personal data. Start with
  `sweet --mask-pii`, set `SWEET_MASK_PII=1`, or commit a `.sweet/policy.yaml`:

  ```yaml
  mode: propose
  mask_pii: true
  masks:
    salary: hash      # redact | hash | partial | null
  ```

- **Stop** (`ctrl+x`): agents drop to read-only and any demo ends.
- **Take over**: during a demo, any key gives you control. `ctrl+r` resumes the agent.

Every agent action is recorded in the session's tamper-evident audit log with the agent's
name.
