# Demo video

Scripts that produce the 3-minute DevKit demo video: silent, with burned-in
captions, at 1080p. Every terminal frame is real output from DevKit and the
Claude API. Nothing is typed by hand and nothing is mocked.

| File | What it does |
|---|---|
| `setup.py` | Builds the workspace: the sample app as a git repo, a Claude Code-shaped request, the PR export, isolated DevKit and Claude Code state, the registered hook, and the cached embedding model. |
| `capture.py` | Runs each scene's commands for real and saves their output and timing to `captures/<scene>.json`. |
| `scenes.py` | Scene order, captions, and timing. |
| `render.py` | Turns the captures into `out/devkit-demo.mp4`. Re-rendering costs no API calls. |
| `captures/` | The captures behind the published video. Paths are shown as `~/demo` and `~/.devkit`. |
| `sample_app/` | `acme-billing`, a small Flask API with **deliberate vulnerabilities** (f-string SQL, a missing auth check, an unverified webhook, placeholder secrets). Never deploy it. Running `devkit scan` on this repo will report it. |

## Reproduce

```bash
pip install -e ".[memory,graph]" pillow
```
```bash
python demo/setup.py --fresh
```
```bash
ANTHROPIC_API_KEY=... python demo/capture.py
```
```bash
python demo/render.py
```

You need `ffmpeg`, the Inter and DejaVu Sans Mono fonts (paths at the top of
`render.py`), the `claude` CLI for the `claude` scene, and `gh` for the PR
export. `DEVKIT_DEMO_WORK` moves the workspace; it defaults to `demo/.work`.
`DEVKIT_DEMO_CAPTURES` writes captures somewhere other than `captures/`.
`DEVKIT_DEMO_TRANSCRIPTS` points the metrics scene at the right Claude Code
transcript folder.

Capture the scenes in the order they appear in `capture.py` (the default with
no arguments). Later scenes depend on earlier ones: memory before inject and
`claude`, ablate before report, and the handoff scenes in sequence. Re-capture
a single scene with `python demo/capture.py scan`.

Approximate cost of one full capture: 2 scans with Sonnet, about 9 ablation
calls, 360 judge calls (`bench`, about 9 minutes), and one Claude Code turn.
Re-rendering is free.

## What the video stages, and what it doesn't

- **The sample app is synthetic.** Its vulnerabilities are deliberate.
- **The Stripe branch is staging.** `setup.py` makes an empty commit on
  `feature/stripe-webhooks` so the SessionStart hook has a branch and commit
  subject to rank memory against. No code changes come with it.
- **The ablation request is built, not captured.** It is built by `setup.py`
  in the shape of a Claude Code turn, and the caption says so.
- **Hugging Face output is hidden.** Its progress bars and hub warnings are
  silenced with environment variables (`HF_HUB_*`, `TRANSFORMERS_VERBOSITY`).
  A default install prints them on every memory command.
- **Semgrep did not run in the published recording** because `semgrep.dev` was
  unreachable. The scan is shown with `--no-semgrep`, on screen.
- **Long waits are sped up and labelled** with the real duration: the scan
  (about 21 s), ablation (about 27 s), the benchmark (8.8 min), and each memory
  command (about 11 s, mostly loading the model).
- **The benchmark scene shows the saved output of the v2.1 run** recorded in
  `docs/eval/judge-bench.md`. Re-capturing runs the benchmark again, and its
  numbers will vary from run to run.
- **The metrics scene shows real AI cost** from the author's Claude Code
  transcripts, at list prices.
