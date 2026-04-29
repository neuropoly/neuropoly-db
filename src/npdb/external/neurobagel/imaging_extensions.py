"""
Imaging modality extension for Neurobagel/Bagel-CLI.

Bagel's built-in suffix→IRI mapping covers only 8 standard modalities.
This module lets neuropoly-db extend that mapping locally so that datasets
with non-standard suffixes (UNIT1, MP2RAGE, BF, TEM, …) can still be
processed without waiting for upstream changes.

Public API
----------
- ``load_extensions(path)``  — load ``config/imaging_extensions.json``
- ``save_extensions(data, path)``  — atomic save
- ``load_neuropoly_vocab(path)``  — load ``config/neuropoly_imaging_modalities.json``
- ``resolve_suffix(suffix, extensions, ai_client, neuropoly_vocab_path)``
  → ``(iri, is_new, description)``  — look up or create an IRI for a suffix
- ``patch_bagel_suffix_map(extra)``  — monkey-patch bagel at runtime
- ``STATIC_FALLBACKS``  — built-in heuristics for well-known custom suffixes (deprecated, kept for backward compatibility)
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# nidm:-aliased fallbacks for MRI variants that map to existing standard terms.
# These are not in neuropoly_imaging_modalities.json (which covers nb: terms only).
# Keys: BIDS suffix string
# Values: (nidm IRI, short description)
# ---------------------------------------------------------------------------

_NIDM_ALIASES: Dict[str, Tuple[str, str]] = {
    # MP2RAGE family
    "UNIT1": (
        "nidm:T1Weighted",
        "UNiform T1-weighted image produced by the MP2RAGE sequence (BIDS suffix UNIT1).",
    ),
    "MP2RAGE": (
        "nidm:T1Weighted",
        "Magnetisation-Prepared 2 Rapid Acquisition Gradient Echoes T1-weighted image.",
    ),
    "T1map": (
        "nidm:T1Weighted",
        "Quantitative T1 map — voxel-wise T1 relaxation time.",
    ),
    "T2map": (
        "nidm:T2Weighted",
        "Quantitative T2 map — voxel-wise T2 relaxation time.",
    ),
    "T2starmap": (
        "nidm:T2StarWeighted",
        "Quantitative T2* map — voxel-wise T2* relaxation time.",
    ),
    "SWI": (
        "nidm:T2StarWeighted",
        "Susceptibility-Weighted Image derived from T2* contrast.",
    ),
    "SWIp": (
        "nidm:T2StarWeighted",
        "Susceptibility-Weighted Image phase component.",
    ),
    "angio": (
        "nidm:T1Weighted",
        "Time-of-flight or contrast-enhanced MR angiography.",
    ),
}

# Backward-compatibility alias: callers that imported STATIC_FALLBACKS directly
# still work.  The nb: entries are now sourced from neuropoly_imaging_modalities.json
# but we keep the dict populated for tests that import it by name.
_NB_FALLBACKS: Dict[str, Tuple[str, str]] = {
    "BF": ("nb:BrightFieldMicroscopy", "Bright-field optical microscopy image."),
    "DF": ("nb:DarkFieldMicroscopy", "Dark-field optical microscopy image."),
    "PC": ("nb:PhaseContrastMicroscopy", "Phase-contrast optical microscopy image."),
    "DIC": ("nb:DifferentialInterferenceContrastMicroscopy", "Differential interference contrast (DIC) optical microscopy image."),
    "FLUO": ("nb:FluorescenceMicroscopy", "Fluorescence microscopy image."),
    "CONF": ("nb:ConfocalMicroscopy", "Confocal fluorescence microscopy image."),
    "PLI": ("nb:PolarisedLightImaging", "Polarised Light Imaging (PLI) of white matter fiber orientation."),
    "TEM": ("nb:TransmissionElectronMicroscopy", "Transmission electron microscopy image."),
    "SEM": ("nb:ScanningElectronMicroscopy", "Scanning electron microscopy image."),
    "uCT": ("nb:MicroComputedTomography", "Micro-computed tomography (micro-CT) image."),
    "OCT": ("nb:OpticalCoherenceTomography", "Optical coherence tomography image."),
    "CARS": ("nb:CoherentAntiStokesRamanSpectroscopyMicroscopy", "Coherent anti-Stokes Raman scattering (CARS) microscopy image."),
    "T2star": ("nb:T2StarWeighted", "T2*-weighted image."),
}

# Public alias kept for backward compatibility (tests import this by name)
STATIC_FALLBACKS: Dict[str, Tuple[str, str]] = {
    **_NB_FALLBACKS, **_NIDM_ALIASES}


# ---------------------------------------------------------------------------
# Extensions file helpers
# ---------------------------------------------------------------------------

def load_neuropoly_vocab(path: Path) -> Dict[str, Tuple[str, str]]:
    """
    Load ``config/neuropoly_imaging_modalities.json`` and return a lookup
    dict keyed by abbreviation: ``{abbreviation: ("nb:Id", name)}``.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blocks = json.load(fh)
        result: Dict[str, Tuple[str, str]] = {}
        for block in blocks:
            prefix = block.get("namespace_prefix", "")
            for term in block.get("terms", []):
                abbr = term.get("abbreviation", "")
                term_id = term.get("id", "")
                name = term.get("name", "")
                if abbr and term_id and prefix:
                    iri = f"{prefix}:{term_id}"
                    result[abbr] = (iri, name)
        return result
    except Exception:
        return {}


def load_extensions(path: Path) -> Dict[str, Any]:
    """
    Load the local imaging extensions file.

    Returns an empty extension dict if the file does not exist.
    """
    if not path.exists():
        return {"version": "1", "extensions": {}}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_extensions(data: Dict[str, Any], path: Path) -> None:
    """Atomically save *data* to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# IRI resolution
# ---------------------------------------------------------------------------

_IRI_VALID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+$")


def _sanitize_iri(raw: str) -> Optional[str]:
    """Return *raw* if it looks like a valid prefixed IRI, else None."""
    raw = raw.strip()
    if _IRI_VALID.match(raw):
        return raw
    return None


def _llm_resolve(suffix: str, ai_client: Any) -> Optional[Tuple[str, str]]:
    """
    Ask the AI client to map *suffix* to a Neurobagel Image IRI.

    Returns ``(iri, description)`` or ``None`` when the LLM cannot help.

    The LLM is instructed to:
    1. Prefer an *existing* nidm or nb term if one clearly fits.
    2. Otherwise propose a new ``nb:CamelCase`` term together with a one-sentence
       description of the modality.
    3. Respond in strict JSON so we can parse it reliably.
    """
    if ai_client is None:
        return None

    prompt = (
        f"You are a neuroimaging metadata expert.\n"
        f"The BIDS suffix '{suffix}' is not yet supported by Neurobagel.\n"
        f"Existing supported suffixes map to nidm terms such as:\n"
        f"  T1w → nidm:T1Weighted, T2w → nidm:T2Weighted, dwi → nidm:DiffusionWeighted,\n"
        f"  bold → nidm:FlowWeighted, asl → nidm:ArterialSpinLabeling,\n"
        f"  eeg → nidm:Electroencephalography, meg → nidm:Magnetoencephalography,\n"
        f"  pet → nidm:PositronEmissionTomography\n"
        f"Task: provide the best IRI for '{suffix}'.\n"
        f"Rules:\n"
        f"  - If an existing nidm: term clearly fits, use it.\n"
        f"  - If no existing term fits, propose a new nb:CamelCase term.\n"
        f"  - Respond ONLY with valid JSON: "
        f'  {{"iri": "<prefix:Term>", "description": "<one sentence>"}}\n'
        f"  - The IRI must match the regex: [a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+"
    )

    try:
        response = ai_client.chat(prompt)
        # Try to extract JSON from the response
        json_match = re.search(r"\{[^{}]+\}", response, re.DOTALL)
        if not json_match:
            return None
        parsed = json.loads(json_match.group())
        iri = _sanitize_iri(parsed.get("iri", ""))
        description = str(parsed.get("description", "")).strip()
        if iri and description:
            return iri, description
    except Exception:
        pass
    return None


def resolve_suffix(
    suffix: str,
    extensions: Dict[str, Any],
    ai_client: Any = None,
    neuropoly_vocab_path: Optional[Path] = None,
) -> Tuple[str, bool, str]:
    """
    Find or create a Neurobagel Image IRI for *suffix*.

    Resolution order:
    1. Already in *extensions["extensions"]* (cache).
    2. In ``neuropoly_imaging_modalities.json`` (file-based nb: vocab).
    3. In ``_NIDM_ALIASES`` (hardcoded nidm: aliases for MRI variants).
    4. LLM query (if *ai_client* is provided).
    5. Generate a generic ``nb:Custom{Suffix}Image`` term as last resort.

    Returns:
        ``(iri, is_new, description)`` where *is_new* is True when the mapping
        was not present in *extensions* before this call.  The caller is
        responsible for persisting the updated *extensions* dict.
    """
    existing = extensions.get("extensions", {})

    # 1. Already known
    if suffix in existing:
        entry = existing[suffix]
        return entry["iri"], False, entry.get("description", "")

    # 2. neuropoly_imaging_modalities.json (nb: terms)
    neuropoly_vocab = load_neuropoly_vocab(
        neuropoly_vocab_path) if neuropoly_vocab_path else {}
    if suffix in neuropoly_vocab:
        iri, description = neuropoly_vocab[suffix]
        existing[suffix] = {
            "iri": iri,
            "description": description,
            "source": "neuropoly_vocab",
            "added": str(date.today()),
        }
        extensions["extensions"] = existing
        return iri, True, description

    # 3. nidm: aliases (hardcoded)
    if suffix in _NIDM_ALIASES:
        iri, description = _NIDM_ALIASES[suffix]
        existing[suffix] = {
            "iri": iri,
            "description": description,
            "source": "static_fallback",
            "added": str(date.today()),
        }
        extensions["extensions"] = existing
        return iri, True, description

    # Backward compat: nb: fallbacks not in vocab file
    if suffix in _NB_FALLBACKS:
        iri, description = _NB_FALLBACKS[suffix]
        existing[suffix] = {
            "iri": iri,
            "description": description,
            "source": "static_fallback",
            "added": str(date.today()),
        }
        extensions["extensions"] = existing
        return iri, True, description

    # 4. LLM
    llm_result = _llm_resolve(suffix, ai_client)
    if llm_result:
        iri, description = llm_result
        existing[suffix] = {
            "iri": iri,
            "description": description,
            "source": "llm",
            "added": str(date.today()),
        }
        extensions["extensions"] = existing
        return iri, True, description

    # 5. Generic fallback — safe camel-case IRI
    safe = re.sub(r"[^a-zA-Z0-9]", "", suffix)
    if not safe or not safe[0].isalpha():
        safe = "Custom" + safe
    else:
        safe = safe[0].upper() + safe[1:]
    iri = f"nb:{safe}Image"
    description = f"Custom imaging modality with BIDS suffix '{suffix}'."
    existing[suffix] = {
        "iri": iri,
        "description": description,
        "source": "generic_fallback",
        "added": str(date.today()),
    }
    extensions["extensions"] = existing
    return iri, True, description


# ---------------------------------------------------------------------------
# Runtime bagel patch
# ---------------------------------------------------------------------------

def patch_bagel_suffix_map(extra: Dict[str, str]) -> None:
    """
    Monkey-patch bagel to recognise *extra* suffixes at all three filtering
    stages inside ``bids2tsv``:

    - Stage 1 ``get_all_bids_suffixes()``       — BIDS-schema recognition
    - Stage 2 ``get_bids_raw_data_suffixes()``  — raw-data type filter
    - Stage 3 ``get_bids_suffix_to_std_term_mapping()`` — Neurobagel IRI map

    Without patching Stages 1 and 2, any suffix absent from the BIDS spec
    (e.g. RAMIRA, BF, TEM) is silently dropped before Stage 3 is ever reached,
    even though Stage 3 already knows about it.

    This is idempotent: calling it multiple times with the same *extra* dict
    produces the same result.

    Args:
        extra: Mapping of ``{bids_suffix: "namespace:Term"}`` to inject.
    """
    try:
        from bagel.utilities import bids_utils as _bu
    except ImportError:
        return

    extra_suffixes = frozenset(extra.keys())

    # ── Stage 3: Neurobagel IRI mapping ──────────────────────────────────
    _orig_mapping = _bu.get_bids_suffix_to_std_term_mapping

    def _patched_get_mapping() -> Dict[str, str]:
        mapping = _orig_mapping()
        mapping.update(extra)
        return mapping

    _bu.get_bids_suffix_to_std_term_mapping = _patched_get_mapping

    # ── Stage 1: BIDS-schema recognition ─────────────────────────────────
    _orig_all_bids = _bu.get_all_bids_suffixes

    def _patched_all_bids() -> set:
        return _orig_all_bids() | extra_suffixes

    _bu.get_all_bids_suffixes = _patched_all_bids

    # ── Stage 2: raw-data suffix filter ──────────────────────────────────
    _orig_raw_data = _bu.get_bids_raw_data_suffixes

    def _patched_raw_data() -> set:
        return _orig_raw_data() | extra_suffixes

    _bu.get_bids_raw_data_suffixes = _patched_raw_data


def build_extra_mapping(
    suffixes: List[str],
    extensions_path: Path,
    ai_client: Any = None,
    neuropoly_vocab_path: Optional[Path] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Resolve each suffix in *suffixes* against the extensions file and return
    ``(extra_mapping, warnings)``.

    Saves any newly added entries back to *extensions_path*.
    When a new ``nb:`` term is resolved via LLM or generic fallback and
    *neuropoly_vocab_path* is provided, the term is promoted into
    ``neuropoly_imaging_modalities.json`` if not already present.
    """
    data = load_extensions(extensions_path)
    extra: Dict[str, str] = {}
    warnings: List[str] = []
    changed = False

    for suffix in suffixes:
        iri, is_new, description = resolve_suffix(
            suffix, data, ai_client, neuropoly_vocab_path
        )
        extra[suffix] = iri
        if is_new:
            source = data["extensions"][suffix].get("source", "unknown")
            warnings.append(
                f"Imaging extension: mapped '{suffix}' → '{iri}' "
                f"(source: {source}; {description})"
            )
            changed = True
            # Promote new nb: terms into neuropoly_imaging_modalities.json
            if iri.startswith("nb:") and neuropoly_vocab_path is not None:
                _promote_to_neuropoly_vocab(
                    suffix=suffix,
                    iri=iri,
                    name=description,
                    vocab_path=neuropoly_vocab_path,
                    warnings=warnings,
                )
        else:
            warnings.append(
                f"Imaging extension: reused cached mapping '{suffix}' → '{iri}'"
            )

    if changed:
        save_extensions(data, extensions_path)

    return extra, warnings


def _promote_to_neuropoly_vocab(
    suffix: str,
    iri: str,
    name: str,
    vocab_path: Path,
    warnings: List[str],
) -> None:
    """
    Add a new nb: term to *vocab_path* if it is not already present.

    On any file I/O or validation error the failure is appended to *warnings*
    and the caller continues — the data is still in the graph with the correct
    IRI, but the label will be null in the query UI until the vocab file is
    updated manually.
    """
    try:
        prefix, term_id = iri.split(":", 1)
        # Basic IRI validation
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]+$", term_id):
            warnings.append(
                f"vocab_extension_pending: IRI '{iri}' for suffix '{suffix}' "
                f"has an invalid local name — add manually to {vocab_path}"
            )
            return

        if vocab_path.exists():
            with open(vocab_path, "r", encoding="utf-8") as fh:
                blocks = json.load(fh)
        else:
            blocks = []

        # Find the matching namespace block (or create one)
        nb_block: Optional[Dict[str, Any]] = None
        for block in blocks:
            if block.get("namespace_prefix") == prefix:
                nb_block = block
                break
        if nb_block is None:
            nb_block = {
                "namespace_prefix": prefix,
                "namespace_url": "http://neurobagel.org/vocab/",
                "vocabulary_name": "NeuroPoly extended imaging modality terms",
                "version": "1.0.0",
                "terms": [],
            }
            blocks.append(nb_block)

        # Check not already present
        existing_ids = {t.get("id") for t in nb_block.get("terms", [])}
        if term_id in existing_ids:
            return

        nb_block.setdefault("terms", []).append(
            {"name": name, "id": term_id, "abbreviation": suffix}
        )

        tmp = vocab_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(blocks, fh, indent=2)
            tmp.replace(vocab_path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    except Exception as exc:
        warnings.append(
            f"vocab_extension_pending: could not promote '{suffix}' → '{iri}' "
            f"into {vocab_path}: {exc}. Add manually."
        )
