"""
SafeOpsAI — Scoring Agent: Cost / Reliability / Security scorers
==================================================================
Deterministic, rule-based scoring. NO LLM calls.

Design goals (defensible in a paper):
  1. The candidate-action vocabulary is explicit (CANONICAL_ACTIONS).
  2. Every criterion score starts from a documented base table (BASE_SCORES)
     and is modified by transparent, invertible formulas. Nothing is hidden.
  3. Every score records its `components` breakdown so the arithmetic can be
     reproduced exactly from the DB row.

Scoring convention
------------------
Higher = better on ALL three criteria (0–10).
  cost       → 10 means "cheapest / least expensive to apply"
  reliability→ 10 means "most likely to fix the incident without side effects"
  security   → 10 means "introduces the least security risk / attack surface"

The coordinator (Phase 3) combines them as:
  final_score = w_cost*cost + w_reliability*reliability + w_security*security
so the convention must be consistent — that is why cost is inverted.
"""

from __future__ import annotations

from typing import Any

from .models import Candidate, CandidateContext, ScoredCandidate, ScoredDimension


# ── Canonical candidate-action list ─────────────────────────────────────────
# This is the ONLY vocabulary the scoring agent understands. It matches the
# RemediationType enum emitted by the Root Cause Agent so the two agents agree
# on action names. Unknown actions are downgraded to "investigate".
CANONICAL_ACTIONS: dict[str, str] = {
    "restart_service":   "Restart the affected service container (no code change).",
    "restart_database":  "Restart the database container.",
    "scale_up":          "Increase capacity (replicas / resources) of a service.",
    "rollback_config":   "Revert configuration to the last known-good version.",
    "redeploy":          "Rebuild and redeploy the service image.",
    "clear_fault":       "Clear the active injected fault (POST /admin/fault/reset).",
    "investigate":       "No automated fix — manual investigation required.",
}

# ── Base scores (action → (cost, reliability, security)) ───────────────────
# Rationale for each row, left-to-right:
#   clear_fault:      zero infra change, ~0 downtime → cheapest; directly removes
#                     the known fault → most reliable; no new attack surface.
#   rollback_config:  quick config restore, no rebuild → cheap; restores a known
#                     good state → reliable; known-good config → secure.
#   restart_service:  brief downtime, no infra → cheap; proven recovery for
#                     crashes → reliable; no code/config change → secure.
#   restart_database: brief downtime but DB reconnect across all dependents →
#                     moderate cost; high blast radius → lower reliability;
#                     no code change → secure.
#   scale_up:         provisioning is real spend → more expensive; adds headroom
#                     → reliable; new resources but same config → fairly secure.
#   redeploy:         build + rollout pipeline → expensive; new code could
#                     regress → lower reliability; new build could change
#                     security posture → lowest security.
#   investigate:      no direct spend but extends incident duration (opportunity
#                     cost) → mid cost; does NOT resolve anything → low
#                     reliability; no change → secure.
BASE_SCORES: dict[str, tuple[float, float, float]] = {
    "clear_fault":      (9.5, 9.0, 9.5),
    "rollback_config":  (8.0, 7.5, 8.5),
    "restart_service":  (7.5, 8.0, 8.5),
    "restart_database": (6.5, 5.0, 8.0),
    "scale_up":         (4.0, 7.0, 7.5),
    "redeploy":         (3.5, 5.0, 5.5),
    "investigate":      (5.5, 3.0, 8.0),
}

# Actions that deliberately do not touch code or config — security bonus.
_NO_CHANGE_ACTIONS = {"clear_fault", "restart_service", "restart_database",
                      "rollback_config", "investigate"}

# ── Canonical candidate set ─────────────────────────────────────────────────
# The coordinator (Phase 3) needs a comparable, non-empty candidate set to
# rank. The LLM RCA often proposes only 1–2 candidates, so the scoring agent
# ALWAYS adds a canonical action universe for the diagnosed root-cause service.
# Every candidate is still scored by the SAME transparent rules. Candidates
# proposed by the RCA are flagged proposed_by_rca=True so the paper can show
# which candidates came from the LLM vs. the deterministic safety net.
CANONICAL_CANDIDATES_BY_SERVICE: dict[str, list[Candidate]] = {
    "database": [
        Candidate("clear_fault",       "backend",  "Clear active injected fault via /admin/fault/reset.", 1, 0, True, False),
        Candidate("restart_service",   "database", "Restart the database service container.",             2, 20, True, False),
        Candidate("restart_database",  "database", "Restart the database container.",                     3, 60, False, False),
        Candidate("scale_up",          "database", "Increase database capacity (replicas / resources).",  4, 0, False, False),
    ],
    "backend": [
        Candidate("clear_fault",       "backend",  "Clear active injected fault via /admin/fault/reset.", 1, 0, True, False),
        Candidate("restart_service",   "backend",  "Restart the backend service container.",              2, 30, True, False),
        Candidate("scale_up",          "backend",  "Increase backend capacity (replicas / resources).",   3, 0, False, False),
        Candidate("redeploy",          "backend",  "Rebuild and redeploy the backend image.",             4, 120, False, False),
    ],
}

# Fallback when the root-cause service is unknown.
CANONICAL_CANDIDATES_FALLBACK: list[Candidate] = [
    Candidate("clear_fault",       "backend", "Clear active injected fault via /admin/fault/reset.", 1, 0, True, False),
    Candidate("restart_service",   "backend", "Restart the affected service container.",             2, 30, True, False),
    Candidate("investigate",       "backend", "Manual investigation required.",                      3, 0, True, False),
]


def canonical_candidates_for(service: str) -> list[Candidate]:
    return CANONICAL_CANDIDATES_BY_SERVICE.get(
        service, CANONICAL_CANDIDATES_FALLBACK
    )


def build_candidate_set(
    rca_candidates: list[Candidate],
    ctx: CandidateContext,
    min_candidates: int = 3,
    max_candidates: int = 6,
) -> list[Candidate]:
    """
    Merge the RCA-proposed candidates with the canonical universe.
      - RCA candidates keep their priority and proposed_by_rca=True.
      - Canonical candidates fill in the gaps (dedup by action+target).
      - The result is capped at max_candidates and padded to at least
        min_candidates (investigate if necessary).
    """
    seen: set[tuple[str, str]] = set()
    merged: list[Candidate] = []

    for c in rca_candidates:
        key = (c.action, c.target)
        if key not in seen:
            seen.add(key)
            merged.append(c)

    for c in canonical_candidates_for(ctx.root_cause_service or ctx.service):
        key = (c.action, c.target)
        if key not in seen:
            seen.add(key)
            merged.append(c)

    if len(merged) > max_candidates:
        merged = merged[:max_candidates]

    while len(merged) < min_candidates:
        c = Candidate(
            action="investigate",
            target=ctx.root_cause_service or ctx.service or "backend",
            description="No automated fix available — manual investigation required.",
            priority=len(merged) + 1,
            estimated_downtime_seconds=0,
            reversible=True,
            proposed_by_rca=False,
        )
        key = (c.action, c.target)
        if key not in seen:
            seen.add(key)
            merged.append(c)
        else:
            break

    return merged


# Maximum downward adjustment applied by the downtime penalty (cost scorer).
_DOWNTIME_PENALTY_CAP = 3.0
# Downtime in seconds that maps to the full penalty.
_DOWNTIME_PENALTY_DENOM = 120.0


def _normalise_action(action: str) -> str:
    a = str(action).strip().lower()
    if a not in BASE_SCORES:
        return "investigate"
    return a


def _clamp(score: float) -> float:
    return round(max(0.0, min(10.0, score)), 2)


# ── Per-criterion scorers ───────────────────────────────────────────────────

def score_cost(candidate: Candidate, ctx: CandidateContext) -> ScoredDimension:
    """Cost: cheaper = higher. Base minus a downtime penalty."""
    action = _normalise_action(candidate.action)
    base = BASE_SCORES[action][0]

    penalty = min(
        _DOWNTIME_PENALTY_CAP,
        candidate.estimated_downtime_seconds / _DOWNTIME_PENALTY_DENOM,
    )
    final = _clamp(base - penalty)

    justification = (
        f"{action}: base cost score {base:.1f} "
        f"minus downtime penalty {penalty:.2f} "
        f"({candidate.estimated_downtime_seconds}s downtime)"
        f" = {final:.1f}. Higher is cheaper."
    )
    components = {
        "base":            base,
        "downtime_penalty": penalty,
        "downtime_s":       candidate.estimated_downtime_seconds,
        "final":            final,
    }
    return ScoredDimension("cost", final, justification, components)


def score_reliability(candidate: Candidate, ctx: CandidateContext) -> ScoredDimension:
    """
    Reliability: base, plus a target-match bonus and a reversibility bonus.
      target_match: +1.0 if the action targets the diagnosed root-cause service
                    (fixing the actual cause), else -0.5.
      reversible:   +0.5 if the action can be undone, else -0.5.
    """
    action = _normalise_action(candidate.action)
    base = BASE_SCORES[action][1]

    target_match = (
        +1.0 if candidate.target == ctx.root_cause_service else -0.5
    )
    reversible = +0.5 if candidate.reversible else -0.5

    final = _clamp(base + target_match + reversible)

    justification = (
        f"{action} -> target={candidate.target}, root_cause={ctx.root_cause_service}: "
        f"base {base:.1f} + target_match {target_match:+.1f} "
        f"+ reversible {reversible:+.1f} = {final:.1f}."
    )
    components = {
        "base":            base,
        "target_match":    target_match,
        "reversible":      reversible,
        "root_cause_svc":  ctx.root_cause_service,
        "candidate_target": candidate.target,
        "final":           final,
    }
    return ScoredDimension("reliability", final, justification, components)


def score_security(candidate: Candidate, ctx: CandidateContext) -> ScoredDimension:
    """
    Security: base table value. Actions that change no code/config are flagged
    in the justification. A redeploy gets the lowest base because a new build
    may change the running software's security posture.
    """
    action = _normalise_action(candidate.action)
    base = BASE_SCORES[action][2]
    final = _clamp(base)

    if action in _NO_CHANGE_ACTIONS:
        justification = (
            f"{action}: no code or configuration change, so no new attack "
            f"surface. Base security score {base:.1f}."
        )
    else:
        justification = (
            f"{action}: changes running infrastructure/code, so a slightly "
            f"reduced security score {base:.1f} (no new risk is added by the "
            f"scorer itself — this is the conservative default)."
        )
    components = {
        "base":      base,
        "no_change": action in _NO_CHANGE_ACTIONS,
        "final":     final,
    }
    return ScoredDimension("security", final, justification, components)


# ── Orchestration ───────────────────────────────────────────────────────────

def score_candidate(candidate: Candidate, ctx: CandidateContext) -> ScoredCandidate:
    """Score one candidate on all three criteria."""
    return ScoredCandidate(
        candidate=candidate,
        scores={
            "cost":        score_cost(candidate, ctx),
            "reliability": score_reliability(candidate, ctx),
            "security":    score_security(candidate, ctx),
        },
    )


def score_candidates(
    candidates: list[Candidate],
    ctx: CandidateContext,
    min_candidates: int = 3,
    max_candidates: int = 6,
) -> list[ScoredCandidate]:
    """
    Score every candidate for an incident.

    The candidate set is the RCA's candidates merged with the canonical
    universe (build_candidate_set), so the coordinator always has a
    comparable, non-empty set to rank.
    """
    merged = build_candidate_set(candidates, ctx, min_candidates, max_candidates)
    return [score_candidate(c, ctx) for c in merged]


def scored_to_jsonb(scored: list[ScoredCandidate]) -> dict[str, Any]:
    """Serialize the scored candidate set for the agent_decisions.raw_output."""
    return {
        "candidates": [c.to_dict() for c in scored],
        "convention": "higher-is-better on all criteria (cost=cheapest)",
    }