---
agent: agent
description: "Implementation plan for the provider-independent, validator-driven BIDS repair workflow (npdb repair bids/recover)."
---
# BIDS repair implementation plan

## Goal

Add a provider-independent, validator-driven BIDS repair workflow. It proposes
evidence-backed structural corrections and leaves ambiguous scientific
transformations unresolved.

The standalone repair command defaults to planning and applies changes only on
request. When a provider conversion runs BIDS validation, it automatically
applies supported, evidence-backed repairs, revalidates the dataset, and outputs
the findings it could not fix. Conversion fails if validation errors remain.

Keep the repair engine separate from the existing participant-standardization
command. Integrate it at the shared conversion-validation boundary, not in
provider-specific download or extraction logic.

## Milestone 1: Structured validation and actionable diagnostics

### Deliverables

- Preserve the validator's structured report even when validation fails.
- Expose issue code, severity, path, and message without requiring users to
  inspect a large traceback.
- For `INVALID_DATASET_DESCRIPTION`, distinguish malformed JSON from schema/type
  problems through targeted inspection.
- State the validator's structural coverage explicitly.

### Acceptance

- Reports remain available on nonzero validator exit.
- Fixtures distinguish invalid JSON, missing required fields, and incorrect
  field types.
- Preserve strict failure behavior for validation-only callers. Conversion
  callers will use the validate-repair-revalidate workflow in milestone 6 and
  stop if errors remain afterward.

## Milestone 2: Read-only repair planner

### Deliverables

- Introduce a repair plan containing:
  - Original validator finding.
  - Proposed operation and affected paths.
  - Evidence supporting the operation.
  - Required user input, if any.
  - Unsupported or ambiguous findings and explanations.
- Separate blocking-error repairs from optional warning cleanup.
- Add a CLI planning entry point, for example:

  ```bash
  npdb repair bids <dataset> --dry-run
  ```

  This is a proposed command, not an existing interface. Planning is the default;
  writing through this standalone command requires explicit `--apply`.
- Reuse the same planner for automatic conversion-time repair; do not maintain
  separate provider-specific rules.

### Acceptance

- Planning never changes dataset files.
- Output is deterministic and available in human-readable and JSON forms.
- Unknown issue codes remain visible rather than being silently ignored.

## Milestone 3: Dataset-description repairs

### Deliverables

- Lossless normalization of supported field types.
- Require an explicit `--bids-version` when `BIDSVersion` is missing.
- Make this explicit input available to provider conversion commands as well.
  Without it, leave the missing version unresolved and explain the required
  input; never guess a version or prompt during unattended conversion.
- For string-valued `Authors`, propose wrapping it as one array entry without
  automatically splitting names.
- Preserve unknown fields and existing valid values.

### Acceptance

- No invented version or authors.
- Invalid JSON is reported, not repaired through speculative text rewriting.
- Changes are idempotent and remove the corresponding validator finding in
  fixtures.

## Milestone 4: General filename normalization

### Deliverables

- Add missing subject/session entities from enclosing directories.
- Normalize unambiguous recognized entity syntax, such as `run2` to `run-2`.
- Order recognized entities using the installed BIDS schema.
- Support explicit suffix aliases through a user-supplied mapping.
- Accept the same mapping in provider conversions; do not infer aliases from
  provider identity.
- Treat suffix renaming separately from Neurobagel ontology mapping.

### Safety boundaries

- Reject conflicts between filename and directory entities.
- Leave unknown tokens and ambiguous parses unresolved.
- Do not change file contents or convert modalities.
- Do not assign tasks, runs, or conditions from scientific assumptions.

### Acceptance

- Tests cover missing entities, conflicting entities, ambiguous tokens, suffix
  aliases, extensions, and collisions.
- Already-normalized filenames produce no proposed changes.

## Milestone 5: Coordinated, recoverable application

### Deliverables

- Coordinate image renames with unambiguously associated sidecars and tables.
- Update supported explicit path references, initially fields such as
  `IntendedFor`.
- Preflight the complete plan for collisions, unsafe paths, symlinks, and files
  changed since planning.
- Apply through a journaled process with backups of edited metadata and recovery
  information.
- Never overwrite unrelated existing files.

### Acceptance

- Naming changes preserve entity values and associations.
- Ambiguous associations block the affected repair.
- Injected failures leave changes recoverable.
- Applying a stale plan is rejected.

## Milestone 6: Automatic conversion repair, revalidation, and release

### Deliverables

- Add a shared conversion-validation workflow:
  1. Validate and retain the original structured report.
  2. Plan supported repairs using available evidence and explicit user inputs.
  3. Preflight and automatically apply safe repairs through milestone 5's
     recoverable application mechanism.
  4. Revalidate after application.
  5. Output applied repairs and a list of unresolved findings, including issue
     code, severity, path, message, and why no safe repair was available.
  6. Continue conversion only when no validation errors remain; otherwise fail
     the command with a nonzero exit status before validator-dependent
     conversion steps proceed.
- Wire every provider conversion path that invokes BIDS validation through this
  workflow, including shared downstream conversion calls. Avoid duplicate
  repair passes when providers already share a conversion implementation.
- Repair supported filename warnings as well as blocking errors. Remaining
  warnings are reported but do not by themselves fail conversion.
- Bound repair attempts and stop when no further safe changes are available;
  do not retry indefinitely or hide unresolved errors.
- Summarize resolved, remaining, and newly introduced findings.
- Record a machine-readable audit of applied changes.
- Surface validator execution, planning, application, and revalidation failures
  explicitly and fail conversion rather than treating them as successful
  validation.
- Document supported rules, automatic conversion-time dataset changes,
  explicit-input requirements, unresolved-error behavior, and recovery.
- Show automatic repairs in conversion output; do not silently mutate datasets
  during downloads or extraction.

### Acceptance

- Every rule has offline unit and integration tests.
- Validator-backed fixtures demonstrate actual finding resolution.
- Provider conversion tests prove that repairable errors are fixed automatically
  and conversion proceeds only after successful revalidation.
- Mixed repairable/unrepairable fixtures prove that safe repairs are applied,
  all remaining errors are listed, and conversion fails with a nonzero exit
  status.
- Missing explicit inputs and ambiguous findings remain visible and are not
  replaced with guessed values.
- Warning-only fixtures exercise supported repairs and reporting without
  introducing a new warning-based failure policy.
- Application and validator failures stop conversion with actionable diagnostics
  and recovery information where applicable.
- Repeated conversions are idempotent, and shared validation paths do not apply
  duplicate repairs.
- Full suite passes with network access blocked.
- No provider-specific rules or NeuroData interaction.

## Dependencies and rollout

```text
1 -> 2 -> 3
       -> 4
3 + 4 -> 5 -> 6
```

- Planning preview: milestones 1-3 provide structured diagnostics, dry-run plans,
  and description-repair proposals. Dataset application is not enabled before
  milestone 5's safeguards are complete.
- Complete release: milestones 4-6 add safe coordinated filename repairs and
  application for the standalone command, plus automatic repair for all provider
  conversion paths that validate BIDS. Automatic conversion repair is not
  enabled before milestone 6's integration tests are complete.

## Explicitly out of scope

- EMG/MATLAB conversion.
- Events-table merging or scientific reassignment.
- Physiological timing inference.
- Acquisition metadata inference.
- Automatic exclusion of troublesome files.
- Claiming full BIDS compliance from the current structural validator alone.
