"""Build the judge benchmark from annotated seeds (see docs/eval/judge-bench.md).

A seed is a coding task plus a *correct* reference response whose substantive
parts are annotated. Cases are derived from it in two ways:

- DEGRADED (label by construction): a deterministic operator changes exactly
  one annotated part so the response contradicts the task or is objectively
  wrong (drop a step, change a constant, off-by-one, swap a path, flip a
  condition, drop a constraint, invent an API, wrong tool argument, drop a
  tool call). No model decides these labels.
- EQUIVALENT: a meaning-preserving transform (reformat, add a preamble, drop
  the rationale line, reorder steps marked independent) or an LLM paraphrase.
  Paraphrases are frozen in the seed file and tagged source=llm_paraphrase so
  they can be reported separately (Claude-judging-Claude-text caveat).

The build is deterministic: same seeds -> byte-identical dataset.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SEEDS_PATH = DATA_DIR / "bench" / "seeds.jsonl"
DATASET_PATH = DATA_DIR / "judge_bench_v2.jsonl"
REVIEW_PATH = Path(__file__).resolve().parents[3] / "docs" / "eval" / "seed-review.md"

DEGRADE_OPS = ("drop_step", "change_constant", "off_by_one", "swap_path", "flip_condition",
               "drop_constraint", "invent_api", "wrong_tool_arg", "drop_tool_call")
# replace-style operators: substitute `find` with `replace` (exactly one occurrence)
_REPLACE_OPS = {"change_constant", "off_by_one", "swap_path", "flip_condition", "invent_api",
                "wrong_tool_arg"}
EQUIV_OPS = ("reformat", "add_preamble", "drop_rationale", "reorder_independent", "llm_paraphrase")

PREAMBLES = ["Here's the change.", "Sure, here is how I'd do it.", "Done. Summary below.",
             "Below is the fix."]
DEGRADED_PER_SEED = 2
EQUIVALENT_PER_SEED = 2


class SeedError(ValueError):
    pass


# ── rendering ────────────────────────────────────────────────────────────────

def _sentence(step: str) -> str:
    """Prose sentence from a step: capitalized first letter, trailing period."""
    if step[:1].islower():
        step = step[0].upper() + step[1:]
    return step if step.endswith((".", "]")) else step + "."


def render(seed: dict, *, steps: list[str] | None = None, style: str | None = None,
           intro: str | None = None, rationale: bool = True) -> str:
    steps = list(seed["steps"] if steps is None else steps)
    style = style or seed.get("style", "numbered")
    parts: list[str] = []
    intro = seed.get("intro", "") if intro is None else intro
    if intro:
        parts.append(intro)
    if style == "numbered":
        parts.append("\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)))
    elif style == "bullets":
        parts.append("\n".join(f"- {s}" for s in steps))
    elif style == "prose":
        parts.append(" ".join(_sentence(s) for s in steps))
    elif style == "plain":  # tool-call sequences: one call per line
        parts.append("\n".join(steps))
    else:
        raise SeedError(f"unknown style {style!r}")
    if seed.get("outro"):
        parts.append(seed["outro"])
    if rationale and seed.get("rationale"):
        parts.append(f"Why: {seed['rationale']}")
    return "\n\n".join(parts)


# ── degradations (label by construction) ─────────────────────────────────────

def apply_mutation(seed: dict, m: dict) -> str:
    op = m["op"]
    if op == "drop_step":
        steps = list(seed["steps"])
        steps.pop(m["index"])
        return render(seed, steps=steps)
    if op == "drop_tool_call":
        steps = [s for s in seed["steps"] if s != m["find"]]
        return render(seed, steps=steps)
    original = render(seed)
    if op == "drop_constraint":
        return original.replace(m["find"], "").replace("  ", " ").replace(" .", ".").strip()
    if op in _REPLACE_OPS:
        return original.replace(m["find"], m["replace"])
    raise SeedError(f"unknown degradation op {op!r}")


# ── equivalent transforms ────────────────────────────────────────────────────

def apply_equivalent(seed: dict, op: str, k: int) -> str | None:
    """Return a meaning-preserving variant, or None if op doesn't apply to this seed."""
    style = seed.get("style", "numbered")
    if op == "reformat":
        if style == "plain":
            return None
        alt = {"numbered": "bullets", "bullets": "numbered", "prose": "numbered"}[style]
        if style == "numbered" and seed.get("independent_steps"):
            alt = "prose"
        return render(seed, style=alt)
    if op == "add_preamble":
        pre = PREAMBLES[k % len(PREAMBLES)]
        base = render(seed)
        return f"{pre}\n\n{base}"
    if op == "drop_rationale":
        return render(seed, rationale=False) if seed.get("rationale") else None
    if op == "reorder_independent":
        if not seed.get("independent_steps") or len(seed["steps"]) < 2:
            return None
        return render(seed, steps=list(reversed(seed["steps"])))
    if op == "llm_paraphrase":
        return seed.get("paraphrase") or None
    raise SeedError(f"unknown equivalence op {op!r}")


# ── validation ───────────────────────────────────────────────────────────────

def substance_markers(seed: dict) -> list[str]:
    """Strings that carry the answer's substance; equivalence transforms must keep them."""
    return [m["find"] for m in seed["mutations"] if "find" in m and m["op"] != "drop_tool_call"]


def validate_seed(seed: dict) -> None:
    sid = seed.get("id", "?")
    for key in ("id", "task", "steps", "mutations", "split"):
        if key not in seed:
            raise SeedError(f"{sid}: missing {key}")
    if seed["split"] not in ("dev", "test"):
        raise SeedError(f"{sid}: split must be dev or test")
    original = render(seed)
    if len(seed["mutations"]) < DEGRADED_PER_SEED:
        raise SeedError(f"{sid}: needs >= {DEGRADED_PER_SEED} mutations")
    for m in seed["mutations"]:
        if m["op"] not in DEGRADE_OPS:
            raise SeedError(f"{sid}: unknown op {m['op']}")
        if m["op"] == "drop_step":
            if not 0 <= m["index"] < len(seed["steps"]) or len(seed["steps"]) < 2:
                raise SeedError(f"{sid}: bad drop_step index")
        else:
            n = original.count(m["find"])
            if n != 1:
                raise SeedError(f"{sid}: {m['op']} find {m['find']!r} occurs {n}x (need exactly 1)")
            if m["op"] == "drop_tool_call" and m["find"] not in seed["steps"]:
                raise SeedError(f"{sid}: drop_tool_call must name a whole step")
            if m["op"] in _REPLACE_OPS and (not m.get("replace") or m["replace"] == m["find"]):
                raise SeedError(f"{sid}: {m['op']} needs a different replace")
        if apply_mutation(seed, m) == original:
            raise SeedError(f"{sid}: {m['op']} did not change the output")
        if not m.get("why"):
            raise SeedError(f"{sid}: {m['op']} needs a 'why' (what it contradicts)")
    markers = substance_markers(seed)
    # drop_rationale must be meaning-preserving: the rationale may not carry substance
    for mk in markers:
        if mk in seed.get("rationale", ""):
            raise SeedError(f"{sid}: rationale contains substance marker {mk!r}")
    # a paraphrase must keep every substance marker verbatim (checkable equivalence)
    if seed.get("paraphrase"):
        missing = [mk for mk in markers if mk not in seed["paraphrase"]]
        if missing:
            raise SeedError(f"{sid}: paraphrase drops substance markers {missing}")


# ── build ────────────────────────────────────────────────────────────────────

def load_seeds(path: Path = SEEDS_PATH) -> list[dict]:
    seeds = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = [s["id"] for s in seeds]
    if len(ids) != len(set(ids)):
        raise SeedError("duplicate seed ids")
    for s in seeds:
        validate_seed(s)
    return seeds


def _rank(seed_id: str, op: str) -> str:
    return hashlib.sha256(f"{seed_id}:{op}".encode()).hexdigest()


def build(seeds: list[dict]) -> list[dict]:
    """Pick DEGRADED_PER_SEED mutations and EQUIVALENT_PER_SEED transforms per seed,
    greedily balancing operator counts across the dataset (deterministic)."""
    deg_counts: Counter = Counter()
    eq_counts: Counter = Counter()
    cases: list[dict] = []
    for s in seeds:
        original = render(s)
        base = {"seed_id": s["id"], "task": s["task"], "original": original, "split": s["split"],
                "domain": s.get("domain", "")}
        muts = sorted(s["mutations"], key=lambda m: (deg_counts[m["op"]], _rank(s["id"], m["op"] + m.get("find", str(m.get("index"))))))
        chosen, used_ops = [], set()
        for m in muts:
            if len(chosen) == DEGRADED_PER_SEED:
                break
            if m["op"] in used_ops:
                continue
            chosen.append(m); used_ops.add(m["op"])
        for m in muts:  # fall back to a repeated op only if the seed lacks variety
            if len(chosen) < DEGRADED_PER_SEED and m not in chosen:
                chosen.append(m)
        for m in chosen:
            deg_counts[m["op"]] += 1
            cases.append({**base, "id": f"{s['id']}-d-{m['op']}", "reduced": apply_mutation(s, m),
                          "label": "degraded", "operator": m["op"], "source": "constructed",
                          "why": m["why"]})
        candidates = []
        for k, op in enumerate(EQUIV_OPS):
            variant = apply_equivalent(s, op, k + len(cases))
            if variant and variant != original:
                candidates.append((eq_counts[op], _rank(s["id"], op), op, variant))
        candidates.sort()
        for _, _, op, variant in candidates[:EQUIVALENT_PER_SEED]:
            eq_counts[op] += 1
            cases.append({**base, "id": f"{s['id']}-e-{op}", "reduced": variant, "label": "equivalent",
                          "operator": op, "source": "llm_paraphrase" if op == "llm_paraphrase" else "constructed",
                          "why": ""})
    return cases


def write_dataset(cases: list[dict], path: Path = DATASET_PATH) -> None:
    path.write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in cases),
                    encoding="utf-8")


def summary(cases: list[dict]) -> dict:
    return {
        "cases": len(cases),
        "by_label": dict(Counter(c["label"] for c in cases)),
        "by_operator": dict(sorted(Counter(c["operator"] for c in cases).items())),
        "by_split": dict(Counter(c["split"] for c in cases)),
        "llm_paraphrase": sum(c["source"] == "llm_paraphrase" for c in cases),
    }


def review_markdown(seeds: list[dict], cases: list[dict]) -> str:
    """Human review sheet: every seed's task, reference answer, and each case derived from it."""
    out = ["# Judge benchmark: seed review sheet", "",
           "Generated by `devkit eval bench-build`; do not edit by hand. For each seed, check:",
           "1. the reference answer is correct for the task;",
           "2. every degraded case really is wrong for the stated reason;",
           "3. every equivalent case really means the same thing.", ""]
    by_seed: dict[str, list[dict]] = {}
    for c in cases:
        by_seed.setdefault(c["seed_id"], []).append(c)
    for sd in seeds:
        out += [f"## {sd['id']} ({sd['split']}, {sd.get('domain', '')})", "",
                f"**Task:** {sd['task']}", "", "**Reference answer:**", "", "```", render(sd), "```", ""]
        for c in by_seed.get(sd["id"], []):
            out += [f"- **{c['label']} / {c['operator']}**" + (f": {c['why']}" if c["why"] else ""), "",
                    "  ```", *("  " + line for line in c["reduced"].splitlines()), "  ```", ""]
        out += ["- [ ] reviewed", ""]
    return "\n".join(out)
