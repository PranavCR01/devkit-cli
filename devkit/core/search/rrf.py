from collections import defaultdict


def rrf_merge(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over multiple ranked result lists.

    k=60 is the standard RRF constant (Cormack et al. 2009).
    Formula: score(id) = sum(1 / (k + rank)) across all lists.
    Lower k amplifies rank differences; higher k levels them out.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranked_list in ranked_lists:
        for rank, (item_id, _) in enumerate(ranked_list, start=1):
            scores[item_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
