# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.3.x   | Yes                |
| < 0.3   | No — upgrade; archives migrate in place on first open |

## Reporting a Vulnerability

If you discover a security vulnerability in MyChatArchive, please report it
responsibly. **Do not open a public issue.**

Use GitHub's [private vulnerability reporting](https://github.com/1ch1n/mychatarchive/security/advisories/new)
on this repository, or email
[channing@mychatarchive.com](mailto:channing@mychatarchive.com) with:

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
- **Imported content is untrusted input.** Conversation text is stored and
  indexed verbatim but never executed or evaluated. Anything that renders or
  forwards archive content — including MCP clients and LLMs consuming search
  results — is a trust boundary: imported text can contain adversarial
  instructions aimed at whatever reads it.

## Best Practices

- Run the MCP server over stdio (the default) for local-only access.
- If you use SSE transport, place it behind a VPN. Do not expose it to the
  public internet.
- Keep your `~/.mychatarchive/` directory permissions restricted to your user
  account.
- Review what you import. The tool ingests your chat data as-is.
