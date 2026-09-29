# Changelog

## 0.1.0 — pre-release candidate (2026-09-29)

### Added

- Prompt set v2 (`common-v2`, `rubric-*-v2`, `free-*-v2`), selected with `--prompt-set` (default `v2` for `run`, `batch`, and `scripts/live_smoke.py`). The v1 prompts remain and reproduce earlier runs.
- `review_flags` returned by each procedure's final stage without extra API calls. Flags mark items `needs_review`, never change verdicts or reasons, are shown read-only in review YAML, preserved by `finalize`, and checked against the raw final response by the live release gate.
- `docs/review-procedure-v2.md` describing the changes, flag rules, exploratory evaluation fields, and naming of reruns.

### Verified

- Public v2 live evidence for `ex-001` a01/a02 in both modes: four results, 12 successful API requests, zero retries, and 12/12 verdicts matching the example criteria. The nine evidence files are preserved with their original content.
- Original-input dev comparison remains 94/96 for both v1 and v2; revised-input confirmation is separately 32/32. See the release notes for limitations.
- The v2 implementation commit passed GitHub CI. Final evidence and release checks remain required for publication.

### Changed

- The live release gate requires v2 evidence. Paired-mode evaluation requires both modes to use the same prompt set and common prompt.
- The Python `RunConfig` default stays `v1` so that existing callers and v1 records are unaffected.


## Initial implementation included in 0.1.0

The source is public on GitHub. GitHub Release publication and PyPI distribution are separate steps.

### Added

- A `nonsul-review` CLI with `run`, `batch`, `review`, `finalize`, `evaluate`, and `validate`.
- Strict YAML/Markdown/JSON contracts, exact rubric coverage, answer linkage, and verbatim evidence validation.
- Separate rubric-first and feedback-first procedures using the same original information and model settings.
- Anthropic Messages API integration, bounded retries, per-attempt raw response files, and failure records.
- Prompt, input, answer-body, and configuration hashes in reproducibility metadata.
- Human-editable review YAML with protected originals, finalized judgments, and complete change records.
- Human-human and model-human agreement, linear weighted Cohen kappa, error detection, coverage, alternative-solution false accusations, and error-type slices.
- Explicit dev/test separation, duplicate ID checks, and non-overwriting output behavior.
- A public original mathematics problem with correct, incorrect, and valid alternative answers.
- Two essay-level original examples (`ex-002` recurrence/induction/limit, `ex-003` Newton iteration convergence), adapted from the author's own mock exam problems, each with four rubric steps and correct, flawed, and alternative answers. They have no demo fixtures and are intended for live API runs.
- Clearly marked hand-authored offline fixtures. Demo predictions are excluded from ordinary evaluation and real-API release gates.
- Package metadata, wheel/sdist resource inclusion, MIT license, Korean user documentation, CI, and manual publishing workflows.
- A real-API smoke command and a release gate checking public source/archive hygiene and hashed live evidence.

### Specification clarifications

- An undecidable result uses `verdict: null` and `needs_review: true`. It does not count as `not_met`; finalization requires instructor resolution.
- Answer metadata adds `split` and `is_alternative` for held-out evaluation and alternative-solution analysis. Neither enters the model prompt.
- Temperature defaults to 1 for current Anthropic compatibility; explicit supported alternatives are preserved and recorded.
- Features originally planned for v0.2/v0.3 are included for review in this source version. Their presence does not establish empirical superiority of a review method.
- GitHub's pre-release flag is separate from Python package version semantics. Version `0.1.0` is retained from the specification.

### Still required before publication

- Confirm CI for the final evidence/documentation commit before tagging.
- Publish `v0.1.0` with the GitHub pre-release flag after release gates pass.
- Register the PyPI project/Trusted Publisher and separately publish an intended package version.
- Obtain independent human ratings for meaningful real evaluation; public fixtures are demonstrations only.
