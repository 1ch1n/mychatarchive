# MyChatArchive Hermes Plugin -- Demo Transcript

**Date:** 2026-05-24
**Platform:** Telegram (Hermes-Chan bot)
**Model:** anthropic/claude-sonnet-4.6 via OpenRouter
**Archive:** 61,091 messages, 873 threads, 91,798 chunks across 5 platforms

---

## System prompt injection (automatic)

On session start, the plugin injects this into the system prompt:

```
# MyChatArchive
Active. 61091 messages across 873 threads (836 summarized), 91798 chunks, 51 thoughts.
Platforms: chatgpt (51228), anthropic (6184), cursor (1736), claude_code (1441), grok (502).
Relevant archive context is automatically injected. Use mca_search, mca_recall, mca_remember, mca_provenance for deeper queries.
```

---

## User query

> Recall what I've discussed about wanting to be an entrepreneur

## Tool calls (automatic)

```
mca_recall: {"topic": "entrepreneur entrepreneurship business"}
mca_search: {"query": "entrepreneur entrepreneurship business ideas startup"}
mca_search: {"query": "want to be entrepreneur business idea side hustle"}
mca_search: {"query": "Vicinia Digital agency clients revenue"}
mca_search: {"query": "car detailing mobile business side hustle"}
mca_search: {"query": "ottoform nctitletransfer Spencer business"}
```

## Response

The model synthesized results from across 3 years of conversations spanning
ChatGPT, Claude, and Claude Code into a structured narrative:

> **Your Entrepreneurial Journey**
>
> **The Core Drive**
> You've had a deep-seated desire to be your own boss from a young age.
> You started with small hustles -- candy sales, landscaping, Instagram theme
> pages (millions of views), and a mobile car detailing business.
>
> **Venture 1: Vicinia Digital (2023-2025)**
> Your first serious swing -- a digital marketing agency you founded in May
> 2023. Fronted $300-400/month in costs yourself. Had a business partner
> (Spencer) who contributed little. Worked at two enterprise jobs
> simultaneously. Eventually shut it down in early 2025.
>
> **Venture 2: nctitletransfer / ottoform (2025)**
> A DMV/title transfer automation business, pivoted toward ottoform.ai.
> Again partnered with Spencer, again fronted all costs. Proved the model
> could work in 5 minutes of testing.
>
> **The Pattern & Where You Were in Early 2026**
> Job searching for income stability while still exploring ottoform.
> ~$40-45K in assets but limited liquid cash. Core tension: wanting to
> build something vs. needing reliable income.
>
> **The Through-Line**
> You've consistently shown the behaviors of an entrepreneur -- you
> self-fund, you execute, you test, you don't quit. The stumbling block
> has been partner dynamics and timing/financial runway.

---

## What happened under the hood

1. **prefetch** hook fired on the user message, injecting top-5 semantically
   similar chunks from the archive into the context
2. Model called **mca_recall** with topic "entrepreneur" to get a structured
   bundle of messages, thread summaries, and thoughts
3. Model called **mca_search** 5 more times with targeted queries to fill gaps
4. Model synthesized all results into a coherent narrative spanning 3 years
   of conversation history across multiple AI platforms

No terminal commands, no raw SQL, no MCP server -- just the memory provider
plugin calling into MyChatArchive's Python API against a 1.4 GB SQLite
database on a NAS share.
