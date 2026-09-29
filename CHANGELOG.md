# Changelog

## 0.1.0 — pre-release preparation (2026-09-29)

This entry describes the prepared source tree, not a completed GitHub or PyPI publication.

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

- Run and inspect real Anthropic API examples using an authorized key/model.
- Register the actual GitHub repository and execute its CI.
- Publish `v0.1.0` with the GitHub pre-release flag after release gates pass.
- Register the PyPI project/Trusted Publisher and separately publish an intended package version.
- Obtain independent human ratings for meaningful real evaluation; public fixtures are demonstrations only.
