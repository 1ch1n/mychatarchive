"""Provenance/governance-aware retrieval explanation helpers."""

from __future__ import annotations

import json
from typing import Any

from mychatarchive.itir_residuals import summarize_projection_residual


SEMANTIC_RESULT_SCHEMA = "mychatarchive.semantic_result.v1"


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_itir_signals(message_meta: dict[str, Any], chunk_meta: dict[str, Any]) -> dict[str, Any]:
    itir_payload = {}
    message_itir = message_meta.get("itir")
    chunk_itir = chunk_meta.get("itir")
    if isinstance(message_itir, dict):
        itir_payload = message_itir
    elif isinstance(chunk_itir, dict):
        itir_payload = chunk_itir

    tokenizer_receipt = itir_payload.get("tokenizer_profile_receipt")
    relational_bundle = itir_payload.get("relational_bundle")

    profile_id = None
    if isinstance(tokenizer_receipt, dict):
        profile_id = tokenizer_receipt.get("profile_id")

    return {
        "has_itir_payload": bool(itir_payload),
        "tokenizer_profile_id": profile_id,
        "has_relational_bundle": bool(relational_bundle),
    }


def _compute_governance_score(*, source_path: str | None, source_bucket: str | None,
                              source_thread_id: str | None, source_message_id: str | None,
                              has_provenance_json: bool, block_count: int, ref_count: int,
                              has_itir_payload: bool) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if source_path:
        score += 0.14
        reasons.append("source_path_present")
    if source_bucket:
        score += 0.08
        reasons.append("source_bucket_present")
    if source_thread_id:
        score += 0.08
        reasons.append("source_thread_id_present")
    if source_message_id:
        score += 0.08
        reasons.append("source_message_id_present")
    if has_provenance_json:
        score += 0.16
        reasons.append("message_provenance_json_present")
    if block_count > 0:
        score += min(block_count, 3) / 3 * 0.16
        reasons.append(f"message_blocks={block_count}")
    if ref_count > 0:
        score += min(ref_count, 3) / 3 * 0.20
        reasons.append(f"provenance_refs={ref_count}")
    if has_itir_payload:
        score += 0.10
        reasons.append("itir_payload_present")

    return min(score, 1.0), reasons


def _tier_for_score(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.40:
        return "medium"
    return "low"


def _source_db_for_connection(con) -> str | None:
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
    except Exception:
        return None
    for row in rows:
        if row[1] == "main":
            return row[2] or None
    return None


def _missing_contract_fields(result: dict[str, Any]) -> list[str]:
    required_paths = {
        "chunk_id": result.get("chunk_id"),
        "message_id": result.get("message_id"),
        "canonical_thread_id": result.get("canonical_thread_id"),
        "provenance.canonical_thread_id": result.get("provenance", {}).get("canonical_thread_id"),
        "source_db": result.get("source_db"),
    }
    return [field for field, value in required_paths.items() if value in (None, "")]


def build_provenance_ranked_chunk_results(
    con,
    semantic_matches: list[tuple[str, float]],
    *,
    limit: int,
    governance_weight: float = 0.30,
    rerank_by_governance: bool = True,
    query_projection: dict[str, Any] | None = None,
    predicate_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build provenance-aware retrieval results from semantic chunk matches."""
    if not semantic_matches and not predicate_candidates:
        return []

    weight = max(0.0, min(1.0, governance_weight))
    rows: list[dict[str, Any]] = []
    source_db = _source_db_for_connection(con)
    semantic_by_chunk = {chunk_id: float(distance) for chunk_id, distance in semantic_matches}
    predicate_by_chunk = {
        candidate["chunk_id"]: candidate
        for candidate in (predicate_candidates or [])
        if candidate.get("chunk_id")
    }
    all_chunk_ids = list(dict.fromkeys([*semantic_by_chunk.keys(), *predicate_by_chunk.keys()]))

    for chunk_id in all_chunk_ids:
        fetched = con.execute(
            """
            SELECT c.text, c.canonical_thread_id, c.ts_start, c.ts_end, c.meta, c.message_id,
                   m.role, m.title, m.source_thread_id, m.source_message_id,
                   m.source_path, m.source_bucket, m.provenance_json, m.meta,
                   m.platform, m.account_id, m.source_id, m.ts
            FROM chunks c
            LEFT JOIN messages m ON c.message_id = m.message_id
            WHERE c.chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if not fetched:
            continue
        columns = [
            "text",
            "canonical_thread_id",
            "ts_start",
            "ts_end",
            "chunk_meta",
            "message_id",
            "role",
            "title",
            "source_thread_id",
            "source_message_id",
            "source_path",
            "source_bucket",
            "provenance_json",
            "message_meta",
            "platform",
            "account_id",
            "source_id",
            "message_ts",
        ]
        row = dict(zip(columns, fetched))

        semantic_distance = semantic_by_chunk.get(chunk_id)
        chunk_meta = _load_json(row["chunk_meta"])
        message_meta = _load_json(row["message_meta"])
        message_provenance_json = _load_json(row["provenance_json"])
        has_provenance_json = bool(message_provenance_json)

        message_id = row["message_id"]
        block_count = 0
        ref_count = 0
        predicate_count = 0
        if message_id:
            block_count = con.execute(
                "SELECT COUNT(*) FROM message_blocks WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]
            ref_count = con.execute(
                "SELECT COUNT(*) FROM provenance_refs WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]
            predicate_count = con.execute(
                "SELECT COUNT(*) FROM predicate_refs WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]

        itir_signals = _extract_itir_signals(message_meta, chunk_meta)
        candidate_projection = {}
        if isinstance(message_meta.get("itir"), dict):
            candidate_projection = message_meta["itir"].get("predicate_projection") or {}
        residual_summary = summarize_projection_residual(query_projection, candidate_projection)
        predicate_candidate = predicate_by_chunk.get(chunk_id) or {}
        predicate_similarity = float(predicate_candidate.get("predicate_score") or 0.0)
        governance_score, reasons = _compute_governance_score(
            source_path=row["source_path"],
            source_bucket=row["source_bucket"],
            source_thread_id=row["source_thread_id"],
            source_message_id=row["source_message_id"],
            has_provenance_json=has_provenance_json,
            block_count=block_count,
            ref_count=ref_count,
            has_itir_payload=bool(itir_signals["has_itir_payload"]),
        )
        governance_score = min(
            1.0,
            governance_score
            + min(predicate_count, 4) * 0.03
            + (residual_summary["score"] * 0.20 if residual_summary["available"] else 0.0),
        )
        if predicate_count > 0:
            reasons.append(f"predicate_refs={predicate_count}")
        if residual_summary["available"]:
            if residual_summary["shared_predicates"]:
                reasons.append(
                    "shared_predicates=" + ",".join(residual_summary["shared_predicates"][:4])
                )
            if residual_summary["contradictions"]:
                reasons.append(
                    "predicate_contradictions=" + ",".join(residual_summary["contradictions"][:4])
                )
        if predicate_candidate.get("matched_signatures"):
            reasons.append(
                "predicate_candidate_matches="
                + ",".join(predicate_candidate["matched_signatures"][:4])
        )
        semantic_similarity = (
            max(0.0, 1.0 - float(semantic_distance))
            if semantic_distance is not None
            else 0.0
        )
        retrieval_similarity = max(semantic_similarity, predicate_similarity)
        rank_score = (retrieval_similarity * (1.0 - weight)) + (governance_score * weight)
        excerpt = (row["text"] or "")[:1000]

        result = {
            "schema": SEMANTIC_RESULT_SCHEMA,
            "result_type": "message_chunk",
            "source_db": source_db,
            "chunk_id": chunk_id,
            "message_id": message_id,
            "canonical_thread_id": row["canonical_thread_id"],
            "thread_id": row["canonical_thread_id"],
            "source_thread_id": row["source_thread_id"],
            "source_message_id": row["source_message_id"],
            "platform": row["platform"],
            "account_id": row["account_id"],
            "source_id": row["source_id"],
            "title": row["title"] or "",
            "role": row["role"] or "",
            "timestamp": row["ts_start"],
            "timestamp_start": row["ts_start"],
            "timestamp_end": row["ts_end"],
            "message_timestamp": row["message_ts"],
            "excerpt": excerpt,
            "text": excerpt,
            "scores": {
                "distance": round(float(semantic_distance), 6) if semantic_distance is not None else None,
                "semantic_distance": (
                    round(float(semantic_distance), 6) if semantic_distance is not None else None
                ),
                "similarity": round(retrieval_similarity, 4),
                "semantic_similarity": round(semantic_similarity, 4),
                "predicate_similarity": round(predicate_similarity, 4),
                "rank_score": round(rank_score, 4),
                "governance_score": round(governance_score, 4),
            },
            "similarity": round(retrieval_similarity, 4),
            "semantic_similarity": round(semantic_similarity, 4),
            "predicate_similarity": round(predicate_similarity, 4),
            "rank_score": round(rank_score, 4),
            "provenance": {
                "canonical_thread_id": row["canonical_thread_id"],
                "message_id": message_id,
                "chunk_id": chunk_id,
                "source_thread_id": row["source_thread_id"],
                "source_message_id": row["source_message_id"],
                "source_path": row["source_path"],
                "source_bucket": row["source_bucket"],
                "source_id": row["source_id"],
                "provenance_json": message_provenance_json or None,
            },
            "governance": {
                "score": round(governance_score, 4),
                "tier": _tier_for_score(governance_score),
                "signals": reasons,
                "source": {
                    "source_thread_id": row["source_thread_id"],
                    "source_message_id": row["source_message_id"],
                    "source_path": row["source_path"],
                    "source_bucket": row["source_bucket"],
                    "source_id": row["source_id"],
                },
                "evidence_counts": {
                    "message_blocks": block_count,
                    "provenance_refs": ref_count,
                    "predicate_refs": predicate_count,
                },
                "itir": itir_signals,
                "predicate_residual": residual_summary,
            },
        }
        missing_fields = _missing_contract_fields(result)
        if missing_fields:
            result["warnings"] = [
                {
                    "code": "semantic_result_missing_provenance",
                    "missing_fields": missing_fields,
                    "message": "Semantic result has incomplete canonical provenance.",
                }
            ]

        rows.append(result)

    if rerank_by_governance:
        rows.sort(key=lambda item: (item["rank_score"], item["similarity"]), reverse=True)

    return rows[:limit]
