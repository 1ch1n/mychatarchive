# MyChatArchive ITIR-Native Transition Audit

## Purpose

This document audits the current `mychatarchive` ingest architecture against the
newer archive/provenance spine already proven in `chat-export-structurer`, then
lays out the migration path to make `mychatarchive` properly ITIR-native rather
than merely ITIR-enriched.

The intended end state is:

- `chat-export-structurer` remains the source artifact + provenance ingest spine.
- `mychatarchive` becomes the product/query layer above that spine.
- SensibLaw/ITIR predicate and residual machinery becomes first-class retrieval
  infrastructure rather than JSON stuffed into `messages.meta`.

## Current Audit

### What `mychatarchive` already does well

- Stable archive identity:
  - `source_thread_id` and `source_message_id` are preserved and preferred when
    present.
  - Fallback dedupe is deterministic and thread-aware.
- Operational ingest surfaces already exist:
  - direct file import
  - directory import
  - named path sources
  - auto-discovery sources
  - live ChatGPT-backed sources with DB-first selector resolution
- Product surfaces already exist:
  - FTS search
  - vector embeddings
  - MCP server
  - thread summaries
  - NotebookLM helpers
- Shared-reducer integration is already wired and enforced:
  - token spans
  - lexeme refs
  - structure occurrences
  - relational bundle

### What `mychatarchive` does not yet implement

The current storage layer does not yet expose archive-truth or governance
surfaces that are already present in the newer ingest spine:

- no `source_path`
- no `source_bucket`
- no `provenance_json` on `messages`
- no `message_blocks`
- no `provenance_refs`
- no `itir.normalized.artifact.v1` batch sidecars
- no replay-oriented provenance pointers
- no first-class authority/lineage/follow/unresolved-pressure surfaces

The current ITIR integration is limited to per-message shared-reducer payloads
written into `messages.meta["itir"]`. That is useful, but it is not the same as
full ITIR archive or residual-lattice integration.

### Deeper SensibLaw/ITIR machinery currently unused

The local SensibLaw surface already exposes materially richer structure than
`mychatarchive` uses today, including:

- `PredicatePNF`
- `PredicateAtom`
- `PredicateIndex`
- `build_predicate_index`
- `build_predicate_ref_map`
- `collect_candidate_predicate_refs`
- `collect_candidate_residuals`
- `compute_indexed_residual`
- `compute_residual`
- `join_residual`

This means the gap is not lack of upstream machinery. The gap is that
`mychatarchive` has not yet promoted these outputs into durable retrieval and
governance surfaces.

## Current Ingest Machinery That Must Be Preserved

The migration must preserve the existing operator workflows:

- `mychatarchive import <file-or-dir>`
- `mychatarchive import --from <source>`
- `mychatarchive sync`
- named file/directory sources
- live sources
- auto-discovery sources
- incremental re-runs without duplicate explosion

The right migration is therefore not a rewrite of the CLI contract. It is a
replacement of the ingest/storage substrate under those entrypoints.

## Architectural Read

### Correct layer split

- `chat-export-structurer`
  - source parsing
  - replayable provenance
  - batch sidecars
  - archive truth
  - parser-specific normalization
- `mychatarchive`
  - retrieval
  - ranking
  - embeddings
  - MCP/query APIs
  - summaries
  - ITIR predicate/residual-aware context assembly

`mychatarchive` should not re-implement every parser or provenance decision
itself. It should consume the normalized archive truth surface.

## Transition Roadmap

## Stage 0: Honest Contract and Compatibility Envelope

### Goals

- Stop overstating current ITIR depth.
- Define the compatibility boundary for existing users and DBs.

### Deliverables

- README language that describes current integration as shared-reducer
  enrichment, not full ITIR-native architecture.
- explicit migration note that existing CLI commands remain stable
- transition doc and milestone plan

### Success criteria

- user-facing docs no longer imply that `messages.meta["itir"]` is the end state
- migration work can proceed without changing day-to-day commands first

## Stage 1: Archive-Truth Parity in `mychatarchive`

### Goals

Bring `mychatarchive`’s SQLite schema and ingest write path up to parity with
the archive-truth surfaces already in the ingest spine.

### Required schema additions

- extend `messages` with:
  - `source_path`
  - `source_bucket`
  - `provenance_json`
- add `message_blocks`
- add `provenance_refs`

### Required ingest changes

- accept parser outputs with:
  - `content_blocks`
  - `provenance_refs`
  - `source_path`
  - `source_bucket`
- persist those surfaces without collapsing them into `meta`
- keep old rows readable

### Success criteria

- all new archive writes can preserve replayable provenance
- new query surfaces can distinguish semantic text from structured block content
- no existing import command breaks

## Stage 2: Transition Current Ingest Entry Points to the Archive Spine

### Goals

Move the source-specific parsing burden out of `mychatarchive` and into the
archive ingest spine while keeping `mychatarchive`’s CLI ergonomics.

### Recommended approach

- wrap `chat-export-structurer` as a library or subprocess-backed adapter
- keep `mychatarchive import` / `sync` as orchestrators
- make `mychatarchive` responsible for:
  - source discovery
  - source configuration
  - job orchestration
  - post-ingest embedding/summarization/index work
- make the ingest spine responsible for:
  - parser selection
  - source normalization
  - provenance
  - sidecars

### Why this is the right cut

- avoids dual-maintaining parser logic
- reuses the already-tested provenance model
- preserves current UX while changing internals

### Success criteria

- `mychatarchive` can ingest at least the currently supported formats through
  the spine without regression
- parser coverage becomes additive instead of forked

## Stage 3: Batch Artifact and Governance Surfaces

### Goals

Make archive batches and follow/review surfaces explicit in `mychatarchive`,
rather than leaving provenance entirely implicit.

### Deliverables

- awareness of `itir.normalized.artifact.v1`
- storage or registry for batch sidecars
- product-level handling of:
  - `authority_class`
  - `lineage`
  - `follow_obligation`
  - `unresolved_pressure_status`

### Success criteria

- MCP/search surfaces can explain whether data is source-derived, derived, or
  unresolved
- archive searches and follow workflows can emit or consume governance-aware
  artifacts

## Stage 4: Predicate Projection Tables

### Goals

Move beyond storing raw shared-reducer payloads in `messages.meta`, and persist
bounded, queryable ITIR projections.

### Recommended tables

- `predicate_refs`
  - predicate ref id
  - message id / chunk id
  - predicate signature
  - polarity
  - modality
  - provenance span refs
- `predicate_roles`
  - predicate ref id
  - role name
  - argument value
  - argument span refs
- optional cached `predicate_messages` / `predicate_chunks` bridge tables for
  faster retrieval

### Success criteria

- `mychatarchive` can answer predicate-level queries without reparsing
  `messages.meta`
- projections remain bounded and source-linked

## Stage 5: Residual and Index Layer

### Goals

Make SensibLaw residual-lattice machinery a live retrieval primitive.

### Deliverables

- persistent or rebuildable `PredicateIndex`-style surfaces
- residual lookup APIs over messages/chunks/threads
- retrieval questions such as:
  - what overlaps?
  - what contradicts?
  - what remains unresolved?
  - what needs follow-up?

### Success criteria

- retrieval ranking can use predicate overlap and residuals, not only embeddings
- context assembly can explain why a result was selected

## Stage 6: Product Use of ITIR Governance and Residuals

### Goals

Use the new surfaces in actual user-visible product behavior.

### Product surfaces

- retrieval ranking and filtering
- follow-up generation
- context assembly
- duplicate/coverage reporting
- explanation surfaces in MCP responses

### Success criteria

- search/context answers can distinguish:
  - authoritative source artifacts
  - derived summaries
  - unresolved pressure
  - follow obligations

## Migration Risks

### Risk 1: Forked parser logic

If `mychatarchive` keeps growing its own parser registry while the spine also
grows, the projects will diverge and provenance behavior will drift.

### Risk 2: Meta-blob stagnation

If `messages.meta["itir"]` remains the only ITIR storage surface, deeper PNF and
residual work will stay expensive and ad hoc.

### Risk 3: Product features outrun archive truth

If embeddings, summaries, and MCP retrieval continue to evolve without
archive-truth parity, user-facing answers will look richer than the archive is
trustworthy.

## Recommended Implementation Order

1. Stage 1: archive-truth parity
2. Stage 2: route ingest entrypoints through the archive spine
3. Stage 3: sidecar + governance awareness
4. Stage 4: predicate projection tables
5. Stage 5: residual/index layer
6. Stage 6: product integration

This order matters. Predicate and residual work should not be built on top of
an archive that still lacks replayable provenance and structured blocks.
