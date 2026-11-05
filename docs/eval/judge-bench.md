# Judge benchmark: method, results, limits

DevKit's ablation suggests removing context only when a Claude judge says the
answer without that context is equivalent ("SAFE"). This benchmark measures
how far that judge can be trusted. The number that matters most is the
**false-SAFE rate**: the share of degraded answers the judge calls SAFE. Each
false SAFE is a suggestion that would silently delete context the task needed.

## Dataset (`devkit/data/judge_bench_v2.jsonl`, 120 cases)

Built deterministically by `devkit eval bench-build` from 30 annotated seeds in
`devkit/data/bench/seeds.jsonl`. `devkit eval bench-build --check` (run in the
tests) fails if the committed dataset no longer matches the seeds.

**Seeds.** Each seed is a coding task and a correct reference answer: a short
change plan, an explanation, or a sequence of tool calls. The parts of the
answer that carry its meaning are annotated: steps, constants, paths,
conditions, stated constraints, API calls, tool arguments. Topics: security
fixes, bug fixes, database work, infrastructure config, refactors, performance,
explanations, and tool-call sequences (7 of 30 seeds).

**Degraded cases (60): labelled by construction.** A deterministic operator
changes exactly one annotated part, and every mutation records a `why`: what it
contradicts in the task, or why it is objectively wrong. No model decides
these labels.

| Operator | What changes | Cases |
|---|---|---:|
| `drop_step` | an essential step is removed | 8 |
| `change_constant` | a value the task specifies changes (timeout, status code, version) | 8 |
| `flip_condition` | a comparison or boolean is inverted | 7 |
| `wrong_tool_arg` | the right tool with a wrong argument | 7 |
| `drop_tool_call` | a required tool call is removed | 7 |
| `off_by_one` | a range, slice, or count moves by one | 6 |
| `swap_path` | a file, route, key prefix, or field name is wrong | 6 |
| `invent_api` | a real call is replaced with one that doesn't exist | 6 |
| `drop_constraint` | a stated requirement disappears from a step | 5 |

**Equivalent cases (60).** Meaning-preserving transforms:

| Operator | What changes | Cases |
|---|---|---:|
| `reformat` | numbered list, bullets, or prose | 17 |
| `add_preamble` | an opening line such as "Here's the change." | 16 |
| `drop_rationale` | the "Why:" line is removed (it never carries substance) | 12 |
| `llm_paraphrase` | the answer is reworded | 11 |
| `reorder_independent` | steps marked order-independent are reversed | 4 |

Guards, enforced by the validator and the tests:

- Every equivalent variant must still contain each substance marker verbatim.
- A rationale may not contain a marker.
- Every replace target must occur exactly once in the answer.

**Split.** Seeds are split 15/15 into `dev` and `test`. All cases from one
seed land in the same half. If the judge prompt is ever tuned, tune on `dev`
and report `test`.

## Measurement

`devkit eval bench --split test --runs 3` runs every test case 3 times. The
judge compares the pair in both orderings, and each comparison is one judge
call. It reports:

- the range across runs for accuracy, false-SAFE, false-reject, the inconclusive
  rate, and order agreement;
- a **95% Wilson interval** on the false-SAFE rate, taken from the worst run.
  Repeated runs over the same cases are not independent samples, so they are
  not pooled.
- errors per operator, per run, each with a Wilson interval;
- paraphrase false rejects, reported separately (see the limits below).

INCONCLUSIVE counts as "not SAFE". For a degraded case that's the correct
outcome; for an equivalent case it's a missed saving.

## Results

See the "Results" section filled in from the latest run below.

## Limits, and the questions to expect

**"Who wrote the seeds?"** Claude (the coding assistant) drafted them. Review
status: **pending author review** of every task, reference answer, and
mutation `why` (`docs/eval/seed-review.md` lists them in readable form).
Degraded labels come from construction, not from a model's opinion. A wrong
seed would still mislabel its cases, which is why the human review matters.

**"Isn't it circular for Claude to judge Claude-written text?"** Partly, and
only for the 11 `llm_paraphrase` cases, which a model wrote. They are tagged
and reported on their own line. The other 109 cases come from deterministic
edits to the reference answer.

**"Is 60 test cases enough?"** For the headline false-SAFE rate, it supports
an upper bound of about 11% at 0 errors (Wilson, n=30 degraded). Per-operator
rates rest on 2 to 4 cases each, so their intervals are wide. Read them as "is
there an obvious blind spot", not as precise rates. Adding seeds narrows every
interval; the build balances operators automatically.

**"Did you tune on the test set?"** No tuning has been done. The seed-level
split exists so future tuning cannot leak into the reported numbers.

**"Synthetic answers aren't production traffic."** True. These answers are
short and clean, while real Claude Code turns are long and messy. The benchmark
tests whether the judge notices one-token, meaning-changing edits. It does not
claim the judge is reliable on every real request. Next step: label pairs from
real `devkit eval ablate` runs and add them as a third split.

**"Which model?"** The judge defaults to the configured `fast_model`
(`claude-haiku-5-5`). `--model` benchmarks a different one, and the model is
recorded in every saved report.
