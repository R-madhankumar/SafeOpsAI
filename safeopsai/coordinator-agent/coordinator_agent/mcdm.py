"""
SafeOpsAI — Coordinator: Deterministic MCDM core
==================================================
Pure Python, NO LLM. This is the auditable decision math for the paper.

Two scoring methods, both operating on the SAME 0–10 criterion scores
(cost / reliability / security) produced by the scoring agent:

  1. Weighted Sum (default)
       final_score = w_cost*cost + w_reliability*reliability + w_security*security
     Higher is better. Winner = max(final_score).

  2. TOPSIS (ablation)
       Technique for Order Preference by Similarity to Ideal Solution.
       - vector-normalise the decision matrix
       - weight the normalised matrix
       - compute distance to the ideal-best (A+) and ideal-worst (A-) points
       - closeness coefficient C = D- / (D+ + D-)  in [0, 1]
     Winner = max(C).

Every intermediate value is exposed on the returned RankedCandidate so the
ranking is reproducible from the DB row alone.

Weights
-------
Weights are normalised to sum to 1.0 internally for TOPSIS correctness and
for clean reporting. Ranking for weighted sum is invariant to that scale, so
both methods are comparable. The RAW weights supplied are also logged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

CRITERIA = ("cost", "reliability", "security")


@dataclass
class CandidateRow:
    """A candidate with its three criterion scores (0–10, higher better)."""
    action:       str
    target:       str
    cost:         float
    reliability:  float
    security:     float
    priority:     int = 1

    def values(self) -> list[float]:
        return [self.cost, self.reliability, self.security]


@dataclass
class RankedCandidate:
    """A candidate after ranking — carries the full audit trail."""
    row:          CandidateRow
    method:       str
    metric:       float              # weighted sum OR TOPSIS closeness coefficient
    rank:         int                # 1 = best
    weights:      dict[str, float]   # normalised weights used
    detail:       dict[str, Any] = field(default_factory=dict)  # method-specific internals

    def to_dict(self) -> dict[str, Any]:
        return {
            "action":      self.row.action,
            "target":      self.row.target,
            "priority":    self.row.priority,
            "scores":      {
                "cost":        round(self.row.cost, 3),
                "reliability": round(self.row.reliability, 3),
                "security":    round(self.row.security, 3),
            },
            "method":      self.method,
            "metric":      round(self.metric, 4),
            "rank":        self.rank,
            "weights":     {k: round(v, 4) for k, v in self.weights.items()},
            "detail":      self.detail,
        }


# ── Helpers ─────────────────────────────────────────────────────────────────

def normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalise weights to sum to 1.0. Missing criteria default to 0."""
    w = {c: float(weights.get(c, 0.0)) for c in CRITERIA}
    total = sum(w.values())
    if total <= 0.0:
        # Fall back to equal weights rather than divide by zero.
        return {c: 1.0 / len(CRITERIA) for c in CRITERIA}
    return {c: v / total for c, v in w.items()}


def _tiebreak(candidates: list[RankedCandidate]) -> list[RankedCandidate]:
    """Deterministic tie-break: higher metric, then lower priority number, then action."""
    candidates.sort(
        key=lambda rc: (-rc.metric, rc.row.priority, rc.row.action),
    )
    for i, rc in enumerate(candidates, start=1):
        rc.rank = i
    return candidates


# ── Method 1: Weighted sum ───────────────────────────────────────────────────

def weighted_sum(rows: list[CandidateRow], weights: dict[str, float]) -> list[RankedCandidate]:
    """final_score = sum(w_j * s_j) per candidate; rank by final_score desc."""
    w = normalise_weights(weights)
    ranked = []
    for row in rows:
        metric = w["cost"] * row.cost + w["reliability"] * row.reliability + w["security"] * row.security
        ranked.append(RankedCandidate(
            row=row,
            method="weighted_sum",
            metric=metric,
            rank=0,
            weights=w,
            detail={
                "components": {
                    c: round(w[c] * row.values()[i], 4)
                    for i, c in enumerate(CRITERIA)
                },
            },
        ))
    return _tiebreak(ranked)


# ── Method 2: TOPSIS ─────────────────────────────────────────────────────────

def topsis(rows: list[CandidateRow], weights: dict[str, float]) -> list[RankedCandidate]:
    """
    TOPSIS with all-benefit criteria (higher is better for all three).

    Steps (documented for the paper):
      1. r_ij = x_ij / sqrt(sum_k x_kj^2)             vector normalisation
      2. v_ij = w_j * r_ij                            weighted normalised matrix
      3. A+_j = max_i v_ij,  A-_j = min_i v_ij        ideal best / worst
      4. D+_i = sqrt(sum_j (v_ij - A+_j)^2)           distance to ideal best
         D-_i = sqrt(sum_j (v_ij - A-_j)^2)           distance to ideal worst
      5. C_i  = D-_i / (D+_i + D-_i)                  closeness coefficient
    Rank by C_i desc.
    """
    w = normalise_weights(weights)
    n = len(rows)
    if n == 0:
        return []

    # Step 1: vector-normalise each column
    norms: list[float] = []
    for j in range(len(CRITERIA)):
        denom = math.sqrt(sum(rows[i].values()[j] ** 2 for i in range(n)))
        norms.append(denom if denom > 0 else 0.0)

    v: list[list[float]] = []
    for i in range(n):
        row_v = []
        for j in range(len(CRITERIA)):
            r_ij = rows[i].values()[j] / norms[j] if norms[j] > 0 else 0.0
            row_v.append(w[CRITERIA[j]] * r_ij)      # Step 2
        v.append(row_v)

    # Step 3: ideal best / worst
    a_plus  = [max(v[i][j] for i in range(n)) for j in range(len(CRITERIA))]
    a_minus = [min(v[i][j] for i in range(n)) for j in range(len(CRITERIA))]

    ranked = []
    for i in range(n):
        # Steps 4 & 5
        d_plus  = math.sqrt(sum((v[i][j] - a_plus[j]) ** 2 for j in range(len(CRITERIA))))
        d_minus = math.sqrt(sum((v[i][j] - a_minus[j]) ** 2 for j in range(len(CRITERIA))))
        denom = d_plus + d_minus
        cc = (d_minus / denom) if denom > 1e-12 else 0.5   # equal case → tie
        ranked.append(RankedCandidate(
            row=rows[i],
            method="topsis",
            metric=cc,
            rank=0,
            weights=w,
            detail={
                "d_plus":       round(d_plus, 4),
                "d_minus":      round(d_minus, 4),
                "cc":           round(cc, 4),
                "normalised":   [round(vv, 4) for vv in v[i]],
                "ideal_plus":   [round(x, 4) for x in a_plus],
                "ideal_minus":  [round(x, 4) for x in a_minus],
            },
        ))
    return _tiebreak(ranked)


# ── Dispatch ─────────────────────────────────────────────────────────────────

def rank_candidates(
    rows: list[CandidateRow],
    weights: dict[str, float],
    method: str = "weighted_sum",
) -> list[RankedCandidate]:
    """Rank candidates using the requested method. Falls back to weighted_sum."""
    if method == "topsis":
        return topsis(rows, weights)
    return weighted_sum(rows, weights)


def select_winner(ranked: list[RankedCandidate]) -> RankedCandidate | None:
    """Return the top-ranked candidate, or None if there are no candidates."""
    return ranked[0] if ranked else None


def decision_to_jsonb(
    incident_id: int,
    method: str,
    raw_weights: dict[str, float],
    ranked: list[RankedCandidate],
    winner: RankedCandidate | None,
) -> dict[str, Any]:
    """Serialize the full decision for agent_decisions.raw_output (auditable)."""
    return {
        "incident_id":  incident_id,
        "method":       method,
        "weights_raw":  {k: round(float(v), 4) for k, v in raw_weights.items()},
        "weights_used": ranked[0].weights if ranked else {},
        "ranking":      [rc.to_dict() for rc in ranked],
        "winner":       winner.to_dict() if winner else None,
        "formula": (
            "final = w_cost*cost + w_reliability*reliability + w_security*security"
            if method == "weighted_sum"
            else "TOPSIS closeness coefficient C = D-/(D+ + D-) on normalised weighted matrix"
        ),
    }