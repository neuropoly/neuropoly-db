---
agent: agent
description: "Implementation plan for extracting BIDS validation and repair into a reusable bidsrepair package under src/bidsrepair."
---
# bidsrepair package extraction plan

## Goal

Create a provider-independent `bidsrepair` Python package under `src/bidsrepair/`,
alongside `src/npdb/`. Move reusable BIDS validation, structural repair, and
dataset-management functionality into it without changing conversion behavior.

Initially ship both packages in the existing distribution. Separate publication
and a second CLI executable are not part of this migration.

This plan follows an inventory of all 58 Python source files in `src/npdb/` and
reference tracing of the repair APIs.

## Package boundary

Dependency direction:

```text
npdb -> bidsrepair -> standard library / PyBIDS / Rust validator
```

`bidsrepair` must not import `npdb`, Bagel, providers, browser automation,
or NeuroPoly vocabulary configuration.

Separate BIDS structure and repair from provider-specific interpretation,
Neurobagel annotation policy, and conversion orchestration.

## Source inventory and disposition

| Current source | Responsibility | Disposition |
|---|---|---|
| `src/npdb/bids_repair.py` | Repair planning, structural rules, filename associations, path references, safe application, journals and recovery | Move completely |
| `src/npdb/external/bids.py` | Rust validator invocation, structured reports, strict validation, automatic repair/revalidation | Move completely; separate validator execution from workflow orchestration |
| `prepare_provider_dataset` in `src/npdb/providers/preparation.py` | Layout normalization, subject discovery and missing participants-table generation | Extract into `bidsrepair`; retain provider orchestration in `npdb` |
| Wrapped-root selection in `src/npdb/providers/mendeley.py` | Discovering a BIDS dataset inside downloaded content | Extract generic discovery; retain provider-specific exclusions and error context |
| `src/npdb/annotation/standardize.py` | Mixed structural sidecar/table operations and mapping-based standardization | Split selectively; do not move wholesale |
| `src/npdb/annotation/autofix.py` | Mixed generic table cleanup and Neurobagel annotation repair | Extract generic table operations only |
| `src/npdb/annotation/preflight.py` | Bagel-supported suffix checks and phenotype/imaging ID comparison | Keep in `npdb` |
| `src/npdb/providers/midrc_bids.py` | MIDRC clinical joins, acquisition interpretation and DICOM conversion | Keep in `npdb`, consuming shared BIDS helpers |
| `extend_bids_description` in `src/npdb/cli/helpers.py` | Neurobagel repository/access metadata enrichment | Keep in `npdb` |
| `BIDSAnnotator` and `NeurobagelAnnotator` in `src/npdb/annotators.py` | Annotation configuration, mapping resolution and provenance orchestration | Keep in `npdb`, separate from providers, delegating structural operations |
| Archive helpers in `src/npdb/providers/preparation.py` | Safe ZIP/TAR extraction and legacy ZIP offset repair | Keep in `npdb`; these are download infrastructure |

## Proposed layout

```text
src/bidsrepair/
    __init__.py       # Supported public API
    validation.py    # Validator discovery, invocation and report handling
    repair.py        # Planning and structural repair rules
    application.py   # Preflight, journaled application and recovery
    workflow.py      # Validate -> repair -> revalidate
    dataset.py       # Root discovery and layout preparation
    tables.py        # Generic tabular operations
    sidecars.py      # Structural tabular-sidecar handling
```

Introduce modules only as their functionality is extracted; avoid empty
placeholders and unnecessary abstractions.

## Milestone 1: Extract the existing repair core

### Deliverables

- Move the engine from `npdb.bids_repair` into the new package.
- Move validation from `npdb.external.bids` into the new package.
- Separate validator execution from repair orchestration.
- Remove the existing two-way lazy dependency between planning and validation:
  validation does not depend on planning, and the workflow coordinates both.
- Expose supported APIs through `bidsrepair`:
  - `get_bids_validation_report`
  - `validate_bids_dataset`
  - `plan_bids_repair`
  - `format_bids_repair_plan`
  - `apply_bids_repair`
  - `recover_bids_repair`
  - `validate_and_repair_bids_dataset`
  - `BidsRepairError`

### Acceptance

- Preserve public signatures and report shapes.
- Preserve automatic conversion behavior, bounded repair attempts, diagnostics,
  and remaining-error failure rules.
- Preserve journal locations and format so existing incomplete operations remain
  recoverable.
- Preserve filename rules, explicit-input requirements, associations, references,
  collision checks, stale-plan checks and rollback behavior.
- New package code has no dependency on `npdb`.

## Milestone 2: Rewire npdb consumers

### Deliverables

- Update imports in:
  - `src/npdb/cli/cli.py`
  - `src/npdb/cli/facade.py`
  - `src/npdb/providers/neurobagel.py`
  - `src/npdb/providers/neuropoly.py`
- Retain thin compatibility wrappers at the old module paths initially.
- Update tests and mocks to target the actual implementation lookup sites, not
  merely obsolete import paths.
- Keep `npdb repair` and provider/local conversion options unchanged.

### Acceptance

- Conversion still runs repair before description parsing and suffix preflight.
- Shared conversion paths do not perform duplicate repair passes.
- Strict validation-only callers remain strict.
- Existing CLI dry-run, explicit apply, human/JSON output and recovery behavior
  remain unchanged.
- Public compatibility imports work without duplicating implementation.

## Milestone 3: Extract generic dataset management

### Deliverables

- Extract layout preparation from provider code:
  - Raw-data flattening.
  - Existing top-level directory classification behavior.
  - Subject discovery.
  - Missing participants-table generation.
- Extract wrapped BIDS-root discovery as a read-only helper.
- Keep provider selection policies and provider-specific diagnostic context in
  `npdb`.
- Keep archive extraction and download/cache management in `npdb`.

### Acceptance

- Existing preparation behavior and safety checks are preserved.
- Root discovery remains deterministic and reports ambiguous roots.
- Preparation remains separate from validator-driven repair: moving these
  functions must not enable layout changes during every repair pass.
- Providers call shared helpers rather than duplicating their logic.

## Milestone 4: Separate structural tables and sidecars from annotation policy

### Deliverables

- Extract generic table editing and structural sidecar-field handling from
  `annotation/standardize.py` and `annotation/autofix.py`.
- Keep the following in `npdb`:
  - `ResolvedMapping` and phenotype registry interpretation.
  - Neurobagel IRI expansion and `Annotations` generation.
  - Age-format vocabulary and categorical-term configuration.
  - Mapping-based column selection and metadata generation.
  - Annotation provenance and interactive/AI resolution.
- Make `npdb` translate resolved mappings into plain structural inputs before
  calling `bidsrepair`, where a split is necessary.

### Safety boundaries

- Existing cleanup can delete duplicate or empty-ID rows. Extraction must not
  promote these operations into automatic safe repair.
- Preserve their existing explicit standardization usage and policy.
- Do not broaden scientific inference, metadata rewriting, or repair scope as
  part of this refactor.
- Do not transfer NeuroPoly configuration ownership into `bidsrepair`.

### Acceptance

- The structural layer does not require annotation configuration or resolvers.
- Existing participant standardization and Neurobagel annotation outputs are
  preserved.
- Generic operations have focused tests independent of Bagel/provider imports.

## Milestone 5: Packaging, documentation and verification

### Deliverables

- Update `pyproject.toml` build configuration to include both top-level packages.
- Verify wheel and source-distribution contents, not only checkout imports.
- Document the package boundary, public APIs, compatibility paths and migration.
- Keep the existing command entry point; no separate executable is required.

### Acceptance

- `bidsrepair` imports without importing `npdb` or Bagel.
- Installed-wheel imports work outside the repository.
- Wheel and source distribution include required package files.
- Public APIs, CLI output, failure behavior and existing journal recovery remain
  compatible.
- Actual Rust-validator fixtures still demonstrate finding resolution.
- Provider option-forwarding tests pass.
- The full suite passes with network access blocked.
- No NeuroData interaction or modification of user datasets is required.


## Dependencies and rollout

```text
1 -> 2
2 -> 3
2 -> 4
2 + 3 + 4 -> 5
```

Packaging verification is also a prerequisite for shipping each extraction
stage, not merely an end-of-project check.

1. First extract the core and rewire consumers in a behavior-preserving change
   (milestones 1-2, with packaging verification).
2. Extract adjacent dataset-management helpers (milestone 3).
3. Split structural table/sidecar helpers from annotation policy (milestone 4).
4. Complete documentation and full verification (milestone 5).

Keep compatibility wrappers until their removal is explicitly scheduled. Do not
combine this extraction with new repair rules, provider behavior changes,
dependency upgrades, or a separately published distribution.
