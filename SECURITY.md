# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in MyChatArchive, please report it
responsibly. **Do not open a public issue.**

Email [channing@mychatarchive.com](mailto:channing@mychatarchive.com) with:

- A description of the vulnerability
- Steps to reproduce the issue
- The potential impact
- Any suggested fixes, if you have them

You should receive an acknowledgment within 48 hours. We will work with you to
understand the issue and coordinate a fix before any public disclosure.

## Scope

MyChatArchive is a local-first tool. The primary attack surface includes:

- **Import parsers.** Malformed export files could trigger unexpected behavior
  during parsing.
- **MCP server.** When running over SSE transport, the server is network-accessible.
  Users should secure it behind a VPN or private network (e.g., Tailscale,
  WireGuard).
- **Summarization API calls.** The `summarize` command sends conversation content
  to an external LLM API. Users should be aware of the privacy implications.
- **SQLite database.** The archive contains your full conversation history. Protect
  the database file with appropriate filesystem permissions.

## Best Practices

- Run the MCP server over stdio (the default) for local-only access.
- If you use SSE transport, place it behind a VPN. Do not expose it to the
  public internet.
- Keep your `~/.mychatarchive/` directory permissions restricted to your user
  account.
- Review what you import. The tool ingests your chat data as-is.
