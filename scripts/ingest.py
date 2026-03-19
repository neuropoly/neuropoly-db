#!/usr/bin/env python3
"""
Standalone ingestion pipeline for neuroimaging BIDS metadata into ElasticSearch.

This script does the same work as Notebook 1 but as a CLI tool, suitable for
automation and integration into larger pipelines.

Usage:
    python scripts/ingest.py                              # ingest all datasets in data/
    python scripts/ingest.py --data-dir data/              # explicit data directory
    python scripts/ingest.py --dataset-dir data/ds001      # ingest one dataset
    python scripts/ingest.py --recreate                    # wipe index first
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from bids import BIDSLayout
from elasticsearch import Elasticsearch, helpers
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

EMBEDDING_DIMS = 768
EMBEDDING_MODEL = "all-mpnet-base-v2"

INDEX_SETTINGS = {
    "number_of_replicas": 0
}

INDEX_MAPPINGS = {
    "properties": {
        # === Dataset & BIDS entities ===
        "dataset":   {"type": "keyword"},
        "subject":   {"type": "keyword"},
        "session":   {"type": "keyword"},
        "run":       {"type": "keyword"},
        "suffix":    {"type": "keyword"},
        "datatype":  {"type": "keyword"},
        "ImageType": {"type": "keyword"},

        # === Participant demographics ===
        "age":       {"type": "float"},
        "sex":       {"type": "keyword"},
        "BodyPart":               {"type": "keyword"},

        # === MRI physics — sequence universal ===
        "RepetitionTime":                    {"type": "float"},
        "EchoTime":                          {"type": "float"},
        "FlipAngle":                         {"type": "float"},
        "SliceThickness":                    {"type": "float"},
        "InversionTime":                     {"type": "float"},
        "MultibandAccelerationFactor":       {"type": "float"},
        "TotalReadoutTime":                  {"type": "float"},
        "EffectiveEchoSpacing":              {"type": "float"},
        "PhaseEncodingDirection":            {"type": "keyword"},
        "PulseSequenceType":                 {"type": "keyword"},
        "MRAcquisitionType":                 {"type": "keyword"},
        "ScanningSequence":                  {"type": "keyword"},
        "SequenceName":                      {"type": "keyword"},
        "PulseSequenceName":                 {"type": "keyword"},
        "BackgroundSuppression":             {"type": "keyword"},

        # === MRI physics — functional fMRI ===
        "task":                              {"type": "keyword"},

        # === MRI physics — fieldmap (dual-echo) ===
        "EchoTime1":  {"type": "float"},
        "EchoTime2":  {"type": "float"},

        # === MRI physics — quantitative / relaxometry ===
        "RepetitionTimePreparation": {"type": "float"},
        "RepetitionTimeExcitation":  {"type": "float"},

        # === MRI physics — perfusion (ASL) ===
        "PostLabelingDelay":  {"type": "float"},
        "LabelingDuration":   {"type": "float"},

        # === MRI physics — spectroscopy ===
        "NumberOfSpectralPoints": {"type": "float"},
        "SpectralWidth":          {"type": "float"},

        # === Scanner / acquisition — categorical ===
        "Manufacturer":           {"type": "keyword"},
        "ManufacturersModelName": {"type": "keyword"},
        "InstitutionName":        {"type": "keyword"},
        "MagneticFieldStrength":  {"type": "float"},
        "ReceiveCoilName":        {"type": "keyword"},
        "SoftwareVersions":       {"type": "keyword"},

        # === Modality-specific — quantitative MRI ===
        "MTState":         {"type": "keyword"},
        "ResonantNucleus": {"type": "keyword"},

        # === Modality-specific — diffusion ===
        "DiffusionScheme": {"type": "keyword"},

        # === Modality-specific — perfusion (ASL) ===
        "ArterialSpinLabelingType": {"type": "keyword"},
        "M0Type":                   {"type": "keyword"},

        # === Modality-specific — PET ===
        "TracerName":         {"type": "keyword"},
        "TracerRadionuclide": {"type": "keyword"},
        "PharmaceuticalName": {"type": "keyword"},

        # === Modality-specific — spectroscopy ===
        "WaterSuppression": {"type": "keyword"},

        # === MISC metadata fields ===
        "NumberOfVolumesDiscardedByScanner": {"type": "float"},
        "NumberOfVolumesDiscardedByUser":    {"type": "float"},
        "NumberOfAverages":                  {"type": "float"},

        # === Text — full-text searchable ===
        "SeriesDescription": {"type": "text"},
        "ProtocolName":      {"type": "text"},
        "CogAtlasID":        {"type": "text"},
        "Instructions":      {"type": "text"},

        # === Computed fields ===
        "description_text":   {"type": "text"},
        "metadata_embedding": {
            "type": "dense_vector",
            "dims": 768,
            "similarity": "cosine",
            "index_options": {"type": "int8_hnsw"},
        },
        "bids_path":         {"type": "keyword"},
        "study_description": {"type": "text"},
        "modality_group":    {"type": "keyword"},
    }
}

SUFFIX_DESCRIPTIONS = {
    # --- Structural anatomical MRI ---
    "T1w":        "T1-weighted anatomical structural MRI",
    "T2w":        "T2-weighted anatomical structural MRI",
    "FLAIR":      "fluid-attenuated inversion recovery MRI",
    "FLASH":      "fast low-angle shot gradient echo MRI",
    "inplaneT2":  "in-plane T2-weighted anatomical MRI",
    "UNIT1":      "uniform T1 image from MP2RAGE",
    "defacemask": "binary de-face mask for anatomical anonymization",
    # --- Functional MRI ---
    "bold":       "BOLD functional MRI",
    "boldref":    "BOLD reference volume for functional alignment",
    "sbref":      "single-band reference image for multiband fMRI",
    # --- Diffusion ---
    "dwi":        "diffusion-weighted imaging for white-matter microstructure",
    # --- Fieldmaps ---
    "phasediff":  "phase-difference fieldmap for distortion correction",
    "magnitude1": "magnitude image 1 for fieldmap estimation",
    "magnitude2": "magnitude image 2 for fieldmap estimation",
    "magnitude":  "magnitude image for fieldmap estimation",
    "fieldmap":   "directly measured B0 fieldmap",
    "epi":        "EPI fieldmap (blip-up/blip-down) for distortion correction",
    # --- Quantitative MRI / relaxometry ---
    "T1map":      "quantitative T1 relaxation time map",
    "T2map":      "quantitative T2 relaxation time map",
    "T2starmap":  "quantitative T2* relaxation time map",
    "R1map":      "quantitative R1 relaxation rate map",
    "R2starmap":  "quantitative R2* relaxation rate map",
    "PDmap":      "proton density map",
    "M0map":      "equilibrium magnetization M0 map",
    "MTsat":      "magnetization transfer saturation map",
    "MTRmap":     "magnetization transfer ratio map",
    "MWFmap":     "myelin water fraction map",
    "Chimap":     "quantitative susceptibility (chi) map",
    "TB1map":     "transmit B1 field map",
    "VFA":        "variable flip-angle images for T1 mapping",
    "IRT1":       "inversion-recovery T1 mapping sequence",
    "MTS":        "magnetization transfer saturation weighted image",
    "TB1DAM":     "double-angle method B1 transmit field mapping",
    "TB1TFL":     "turbo-flash B1 transmit field mapping",
    "TB1AFI":     "actual flip-angle imaging B1 transmit field mapping",
    "TB1SRGE":    "saturation-recovery gradient echo B1 mapping",
    "MESE":       "multi-echo spin-echo for T2 mapping",
    "MEGRE":      "multi-echo gradient-echo for T2* mapping",
    # --- Segmentation / derived ---
    "dseg":       "discrete segmentation label map",
    "probseg":    "probabilistic segmentation map",
    "mask":       "binary brain or ROI mask",
    "register":   "registration transform or registered image",
    # --- Spectroscopy ---
    "svs":        "single-voxel MR spectroscopy",
    "mrsref":     "MR spectroscopy reference scan",
    "mrsi":       "MR spectroscopic imaging",
    # --- PET ---
    "pet":        "positron emission tomography",
    # --- ASL / perfusion ---
    "asl":        "arterial spin labeling perfusion imaging",
    "m0scan":     "M0 calibration scan for arterial spin labeling",
}


def safe_float(value):
    """Convert a value to float, returning None for non-numeric values (including NaN)."""
    if value is None:
        return None
    try:
        result = float(value)
        if result != result:  # NaN check
            return None
        return result
    except (ValueError, TypeError):
        return None


def safe_str(value, default=""):
    """Convert a value to str, returning default for None/NaN."""
    if value is None:
        return default
    try:
        if isinstance(value, float) and value != value:  # NaN
            return default
    except TypeError:
        pass
    s = str(value)
    if s.lower() == "nan":
        return default
    return s


def _is_valid(value) -> bool:
    """Return True if value is non-None, non-NaN, and non-empty."""
    if value is None:
        return False
    try:
        if isinstance(value, float) and value != value:  # NaN
            return False
    except TypeError:
        pass
    if isinstance(value, str) and value.strip().lower() in ("", "nan", "n/a"):
        return False
    return True


# Modality groups: map suffix → broad research category used in description_text
MODALITY_GROUPS = {
    "bold":       "functional",
    "sbref":      "functional",
    "boldref":    "functional",
    "T1w":        "structural",
    "T2w":        "structural",
    "FLAIR":      "structural",
    "FLASH":      "structural",
    "inplaneT2":  "structural",
    "UNIT1":      "structural",
    "dwi":        "diffusion",
    "phasediff":  "fieldmap",
    "magnitude1": "fieldmap",
    "magnitude2": "fieldmap",
    "magnitude":  "fieldmap",
    "fieldmap":   "fieldmap",
    "epi":        "fieldmap",
    "T1map":      "quantitative",
    "T2map":      "quantitative",
    "T2starmap":  "quantitative",
    "MTsat":      "quantitative",
    "MTRmap":     "quantitative",
    "Chimap":     "quantitative",
    "TB1map":     "quantitative",
    "asl":        "perfusion",
    "m0scan":     "perfusion",
    "pet":        "pet",
    "svs":        "spectroscopy",
    "mrsi":       "spectroscopy",
}


def build_description_text(
    entities: dict,
    metadata: dict,
    participant_info: dict,
    study_description: str = "",
) -> str:
    """
    Build a natural-prose description for BM25 and embedding encoding.

    Uses modality-specific sentence templates so sentence-transformer models
    receive text that closely resembles the natural-language queries they were
    trained on.  Each modality includes the most discriminating fields for that
    scan type (e.g. ASL labeling parameters for perfusion, tracer info for PET).
    """
    suffix = entities.get("suffix", "")
    modality = MODALITY_GROUPS.get(suffix, "other")
    suffix_desc = SUFFIX_DESCRIPTIONS.get(suffix, suffix)

    # ── Shared scanner context ─────────────────────────────────────────────────
    field = metadata.get("MagneticFieldStrength")
    manufacturer = safe_str(metadata.get("Manufacturer"))
    model_name = safe_str(metadata.get("ManufacturersModelName"))
    institution = safe_str(metadata.get("InstitutionName"))
    acq_type = safe_str(metadata.get("MRAcquisitionType"))
    pulse_seq = safe_str(metadata.get("PulseSequenceType")
                         or metadata.get("ScanningSequence"))
    series_desc = safe_str(metadata.get("SeriesDescription"))
    protocol = safe_str(metadata.get("ProtocolName"))
    coil = safe_str(metadata.get("ReceiveCoilName"))
    body_part = safe_str(metadata.get("BodyPart")
                         or metadata.get("BodyPartExamined"))

    # Build "on a X Tesla Y Z scanner" phrase
    scanner_parts = []
    if field:
        tesla_map = {1.5: "1.5 Tesla", 3.0: "3 Tesla", 3: "3 Tesla",
                     7.0: "7 Tesla", 7: "7 Tesla"}
        scanner_parts.append(tesla_map.get(field, f"{field} Tesla"))
    if manufacturer:
        scanner_parts.append(manufacturer)
    if model_name:
        scanner_parts.append(model_name)
    scanner_phrase = ("on a " + " ".join(scanner_parts) +
                      " scanner") if scanner_parts else ""
    location_phrase = f" at {institution}" if institution else ""

    # Standard acquisition parameters
    tr = metadata.get("RepetitionTime")
    te = metadata.get("EchoTime")
    ti = metadata.get("InversionTime")
    fa = metadata.get("FlipAngle")
    param_parts = []
    if tr is not None:
        param_parts.append(f"TR {tr} s")
    if te is not None:
        param_parts.append(f"TE {te} s")
    if ti is not None:
        param_parts.append(f"TI {ti} s")
    if fa is not None:
        param_parts.append(f"flip angle {fa}°")
    param_str = "; ".join(param_parts)

    # Demographics
    age = participant_info.get("age")
    sex = participant_info.get("sex")
    demo_parts = []
    if _is_valid(age):
        demo_parts.append(f"age {age}")
    if _is_valid(sex):
        demo_parts.append(f"sex {sex}")
    demo_str = ("Participant: " + ", ".join(demo_parts) +
                ".") if demo_parts else ""

    # ── Modality-specific prose ────────────────────────────────────────────────
    if modality == "functional":
        task_name = safe_str(metadata.get("TaskName") or entities.get("task"))
        task_desc = safe_str(metadata.get("TaskDescription"))
        mbaf = metadata.get("MultibandAccelerationFactor")
        nvd_s = metadata.get("NumberOfVolumesDiscardedByScanner")
        nvd_u = metadata.get("NumberOfVolumesDiscardedByUser")
        trt = metadata.get("TotalReadoutTime")
        cog_id = safe_str(metadata.get("CogAtlasID"))
        instructions = safe_str(metadata.get("Instructions"))

        task_clause = f" acquired during the {task_name} task" if task_name else ""
        sentence = f"A {suffix_desc} scan{task_clause}"
        if scanner_phrase:
            sentence += f" {scanner_phrase}{location_phrase}"
        sentence += "."
        if task_desc:
            sentence += f" Task: {task_desc[:250]}."
        if param_str:
            sentence += f" Parameters: {param_str}."
        if mbaf:
            sentence += f" Multiband acceleration factor: {int(mbaf)}."
        if nvd_s or nvd_u:
            sentence += (f" Dummy scans discarded: {int(nvd_s or 0)} (scanner)"
                         f" + {int(nvd_u or 0)} (user).")
        if trt:
            sentence += f" Total readout time: {trt} s."
        if cog_id:
            sentence += f" Cognitive Atlas task: {cog_id}."
        if instructions:
            sentence += f" Instructions: {instructions[:200]}."

    elif modality == "structural":
        acq_prefix = f"{acq_type} " if acq_type else ""
        sentence = f"A {acq_prefix}{suffix_desc} scan"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if pulse_seq:
            sentence += f" Pulse sequence: {pulse_seq}."
        if param_str:
            sentence += f" Parameters: {param_str}."
        if coil:
            sentence += f" Receive coil: {coil}."
        if body_part:
            sentence += f" Body part: {body_part}."

    elif modality == "diffusion":
        diff_scheme = safe_str(metadata.get("DiffusionScheme"))
        trt = metadata.get("TotalReadoutTime")
        ees = metadata.get("EffectiveEchoSpacing")
        ped = safe_str(metadata.get("PhaseEncodingDirection"))

        sentence = f"A {suffix_desc} scan"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if diff_scheme:
            sentence += f" Diffusion scheme: {diff_scheme}."
        if param_str:
            sentence += f" Parameters: {param_str}."
        if trt:
            sentence += f" Total readout time: {trt} s."
        if ees:
            sentence += f" Effective echo spacing: {ees} s."
        if ped:
            sentence += f" Phase encoding direction: {ped}."

    elif modality == "fieldmap":
        te1 = metadata.get("EchoTime1")
        te2 = metadata.get("EchoTime2")
        trt = metadata.get("TotalReadoutTime")
        ees = metadata.get("EffectiveEchoSpacing")
        ped = safe_str(metadata.get("PhaseEncodingDirection"))

        sentence = f"A {suffix_desc} for B0 field mapping and distortion correction"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if te1 or te2:
            echo_parts = []
            if te1:
                echo_parts.append(f"EchoTime1 {te1} s")
            if te2:
                echo_parts.append(f"EchoTime2 {te2} s")
            sentence += f" {', '.join(echo_parts)}."
        elif te:
            sentence += f" Echo time: {te} s."
        if trt:
            sentence += f" Total readout time: {trt} s."
        if ees:
            sentence += f" Effective echo spacing: {ees} s."
        if ped:
            sentence += f" Phase encoding direction: {ped}."

    elif modality == "quantitative":
        rtp = metadata.get("RepetitionTimePreparation")
        rte = metadata.get("RepetitionTimeExcitation")
        mt_state = safe_str(metadata.get("MTState"))
        nucleus = safe_str(metadata.get("ResonantNucleus"))

        sentence = f"A {suffix_desc} quantitative MRI map"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if param_str:
            sentence += f" Parameters: {param_str}."
        if rtp:
            sentence += f" Repetition time preparation: {rtp} s."
        if rte:
            sentence += f" Repetition time excitation: {rte} s."
        if mt_state:
            sentence += f" Magnetization transfer state: {mt_state}."
        if nucleus:
            sentence += f" Resonant nucleus: {nucleus}."

    elif modality == "perfusion":
        asl_type = safe_str(metadata.get("ArterialSpinLabelingType"))
        pld = metadata.get("PostLabelingDelay")
        ld = metadata.get("LabelingDuration")
        bg_sup = safe_str(metadata.get("BackgroundSuppression"))
        m0_type = safe_str(metadata.get("M0Type"))

        asl_qualifier = f"{asl_type} " if asl_type else ""
        sentence = (f"An {asl_qualifier}arterial spin labeling (ASL) perfusion MRI scan "
                    f"measuring cerebral blood flow (CBF)")
        if scanner_phrase:
            sentence += f", acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if pld:
            sentence += f" Post-labeling delay: {pld} s."
        if ld:
            sentence += f" Labeling duration: {ld} s."
        if bg_sup:
            sentence += f" Background suppression: {bg_sup}."
        if m0_type:
            sentence += f" M0 calibration type: {m0_type}."
        if param_str:
            sentence += f" Parameters: {param_str}."

    elif modality == "pet":
        tracer = safe_str(metadata.get("TracerName"))
        radionuclide = safe_str(metadata.get("TracerRadionuclide"))
        pharma = safe_str(metadata.get("PharmaceuticalName"))

        tracer_clause = f" using the tracer {tracer}" if tracer else ""
        radionuclide_clause = f" ({radionuclide})" if radionuclide else ""
        sentence = f"A positron emission tomography (PET) scan{tracer_clause}{radionuclide_clause}"
        if institution:
            sentence += f" acquired at {institution}"
        sentence += "."
        if pharma:
            sentence += f" Pharmaceutical: {pharma}."

    elif modality == "spectroscopy":
        nucleus = safe_str(metadata.get("ResonantNucleus"))
        ws = safe_str(metadata.get("WaterSuppression"))
        sw = metadata.get("SpectralWidth")
        nsp = metadata.get("NumberOfSpectralPoints")
        n_avg = metadata.get("NumberOfAverages")

        nucleus_clause = f" of the {nucleus} nucleus" if nucleus else ""
        sentence = f"A {suffix_desc} scan{nucleus_clause}"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if ws:
            sentence += f" Water suppression: {ws}."
        if sw:
            sentence += f" Spectral width: {sw} Hz."
        if nsp:
            sentence += f" Number of spectral points: {int(nsp)}."
        if n_avg:
            sentence += f" Number of averages: {n_avg}."

    else:
        # Fallback for unlisted modalities
        sentence = f"A {suffix_desc} scan"
        if scanner_phrase:
            sentence += f" acquired {scanner_phrase}{location_phrase}"
        sentence += "."
        if param_str:
            sentence += f" Parameters: {param_str}."

    # ── Append cross-cutting context ──────────────────────────────────────────
    if series_desc:
        sentence += f" Series: {series_desc}."
    if protocol and protocol != series_desc:
        sentence += f" Protocol: {protocol}."
    if study_description:
        sentence += f" Study: {study_description[:200]}."
    if demo_str:
        sentence += f" {demo_str}"

    # For non-functional modalities that still carry a task entity
    task_name = safe_str(metadata.get("TaskName") or entities.get("task"))
    if task_name and modality != "functional":
        sentence = f"Task: {task_name}. " + sentence

    return sentence.strip()


def get_modality_group(suffix: str) -> str:
    return MODALITY_GROUPS.get(suffix, "other")


def generate_documents(layout, participant_lookup, model, dataset_dir, dataset_name):
    nifti_files = layout.get(extension=[".nii.gz", ".nii"])
    dataset_root = Path(dataset_dir).resolve()

    # Extract study-level description from dataset_description.json
    study_desc_path = dataset_root / "dataset_description.json"
    study_description = ""
    if study_desc_path.exists():
        try:
            dd = json.loads(study_desc_path.read_text())
            study_description = dd.get(
                "Name", "") + " " + dd.get("HowToAcknowledge", "")
            study_description = study_description.strip()
        except Exception:
            pass

    file_data = []
    for bf in nifti_files:
        entities = bf.entities
        metadata = layout.get_metadata(bf.path)
        subj_id = f"sub-{entities.get('subject', '')}"
        participant_info = participant_lookup.get(subj_id, {})
        desc_text = build_description_text(entities, metadata, participant_info,
                                           study_description=study_description)
        file_data.append({
            "entities": entities,
            "metadata": metadata,
            "participant_info": participant_info,
            "desc_text": desc_text,
            "bids_path": str(Path(bf.path).resolve().relative_to(dataset_root)),
        })

    desc_texts = [fd["desc_text"] for fd in file_data]
    print(f"  Encoding {len(desc_texts)} descriptions...")
    embeddings = model.encode(
        desc_texts, show_progress_bar=True, batch_size=32)

    for fd, embedding in zip(file_data, embeddings):
        entities = fd["entities"]
        metadata = fd["metadata"]
        participant_info = fd["participant_info"]

        doc = {
            "dataset":   dataset_name,
            "subject":   str(entities.get("subject", "")),
            "session":   str(entities.get("session", "")),
            "task":      str(entities.get("task", "")),
            "run":       str(entities.get("run", "")),
            "suffix":    str(entities.get("suffix", "")),
            "datatype":  str(entities.get("datatype", "")),
            "age":       safe_float(participant_info.get("age")),
            "sex":       safe_str(participant_info.get("sex")),
            # Universal MRI physics
            "RepetitionTime":        safe_float(metadata.get("RepetitionTime")),
            "EchoTime":              safe_float(metadata.get("EchoTime")),
            "FlipAngle":             safe_float(metadata.get("FlipAngle")),
            "MagneticFieldStrength": safe_float(metadata.get("MagneticFieldStrength")),
            "SliceThickness":        safe_float(metadata.get("SliceThickness")),
            "InversionTime":         safe_float(metadata.get("InversionTime")),
            # Functional fMRI
            "MultibandAccelerationFactor":       safe_float(metadata.get("MultibandAccelerationFactor")),
            "NumberOfVolumesDiscardedByScanner": safe_float(metadata.get("NumberOfVolumesDiscardedByScanner")),
            "NumberOfVolumesDiscardedByUser":    safe_float(metadata.get("NumberOfVolumesDiscardedByUser")),
            "TotalReadoutTime":                  safe_float(metadata.get("TotalReadoutTime")),
            "EffectiveEchoSpacing":              safe_float(metadata.get("EffectiveEchoSpacing")),
            # Fieldmap (dual-echo)
            "EchoTime1":  safe_float(metadata.get("EchoTime1")),
            "EchoTime2":  safe_float(metadata.get("EchoTime2")),
            # Quantitative / relaxometry
            "RepetitionTimePreparation": safe_float(metadata.get("RepetitionTimePreparation")),
            "RepetitionTimeExcitation":  safe_float(metadata.get("RepetitionTimeExcitation")),
            # Perfusion (ASL)
            "PostLabelingDelay": safe_float(metadata.get("PostLabelingDelay")),
            "LabelingDuration":  safe_float(metadata.get("LabelingDuration")),
            # Spectroscopy
            "NumberOfSpectralPoints": safe_float(metadata.get("NumberOfSpectralPoints")),
            "SpectralWidth":          safe_float(metadata.get("SpectralWidth")),
            "NumberOfAverages":       safe_float(metadata.get("NumberOfAverages")),
            # Scanner / acquisition — categorical
            "Manufacturer":           metadata.get("Manufacturer"),
            "ManufacturersModelName": metadata.get("ManufacturersModelName"),
            "InstitutionName":        metadata.get("InstitutionName"),
            "PhaseEncodingDirection": metadata.get("PhaseEncodingDirection"),
            "PulseSequenceType":      metadata.get("PulseSequenceType"),
            "MRAcquisitionType":      metadata.get("MRAcquisitionType"),
            "ScanningSequence":       metadata.get("ScanningSequence"),
            "BodyPart":               metadata.get("BodyPart", metadata.get("BodyPartExamined")),
            "ReceiveCoilName":        metadata.get("ReceiveCoilName"),
            "SoftwareVersions":       metadata.get("SoftwareVersions"),
            "ImageType":              metadata.get("ImageType"),
            "SequenceName":           metadata.get("SequenceName"),
            "PulseSequenceName":      metadata.get("PulseSequenceName"),
            # Quantitative MRI
            "MTState":         metadata.get("MTState"),
            "ResonantNucleus": metadata.get("ResonantNucleus"),
            # Diffusion
            "DiffusionScheme": metadata.get("DiffusionScheme"),
            # Perfusion (ASL) — categorical
            "ArterialSpinLabelingType": metadata.get("ArterialSpinLabelingType"),
            "M0Type":                   metadata.get("M0Type"),
            "BackgroundSuppression":    metadata.get("BackgroundSuppression"),
            # PET
            "TracerName":        metadata.get("TracerName"),
            "TracerRadionuclide": metadata.get("TracerRadionuclide"),
            "PharmaceuticalName": metadata.get("PharmaceuticalName"),
            # Spectroscopy
            "WaterSuppression": metadata.get("WaterSuppression"),
            # Text fields
            "TaskName":          metadata.get("TaskName"),
            "TaskDescription":   metadata.get("TaskDescription"),
            "SeriesDescription": metadata.get("SeriesDescription"),
            "ProtocolName":      metadata.get("ProtocolName"),
            "CogAtlasID":        metadata.get("CogAtlasID"),
            "Instructions":      metadata.get("Instructions"),
            # Computed
            "description_text":   fd["desc_text"],
            "metadata_embedding": embedding.tolist(),
            "study_description":  study_description,
            "modality_group":     get_modality_group(str(entities.get("suffix", ""))),
            "bids_path": fd["bids_path"],
        }

        # Strip None values so absent fields are truly missing in ES (not null).
        # This keeps _source clean and ensures `exists` queries behave correctly.
        doc = {k: v for k, v in doc.items() if v is not None}

        yield doc


def find_bids_datasets(data_dir):
    """Find all BIDS datasets (directories with dataset_description.json) under data_dir."""
    data_path = Path(data_dir)
    return sorted(
        d for d in data_path.iterdir()
        if d.is_dir() and (d / "dataset_description.json").exists()
    )


def main():
    parser = argparse.ArgumentParser(
        description="Ingest BIDS metadata into ElasticSearch")
    parser.add_argument("--data-dir", default="data",
                        help="Parent directory containing BIDS datasets")
    parser.add_argument("--dataset-dir", default=None,
                        help="Path to a single BIDS dataset (overrides --data-dir)")
    parser.add_argument(
        "--es-url", default="http://localhost:9200", help="ElasticSearch URL")
    parser.add_argument("--index", default="neuroimaging", help="Index name")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete and recreate the index before ingesting")
    args = parser.parse_args()

    # Determine which datasets to ingest
    if args.dataset_dir:
        dataset_dir = Path(args.dataset_dir)
        if not dataset_dir.exists():
            raise FileNotFoundError(
                f"Dataset not found: {dataset_dir.resolve()}")
        dataset_dirs = [dataset_dir]
    else:
        data_dir = Path(args.data_dir)
        if not data_dir.exists():
            raise FileNotFoundError(
                f"Data directory not found: {data_dir.resolve()}")
        dataset_dirs = find_bids_datasets(data_dir)
        if not dataset_dirs:
            raise FileNotFoundError(
                f"No BIDS datasets found in {data_dir.resolve()}")

    print(f"Datasets to ingest: {[d.name for d in dataset_dirs]}")

    # Connect to ES
    client = Elasticsearch(args.es_url, request_timeout=120)
    info = client.info()
    print(f"Connected to ES {info['version']['number']}")

    # Load embedding model
    print(f"Loading embedding model '{EMBEDDING_MODEL}'...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # Create or recreate index
    index_name = args.index
    if args.recreate and client.indices.exists(index=index_name):
        client.indices.delete(index=index_name)
        print(f"Deleted existing index '{index_name}'")
    if not client.indices.exists(index=index_name):
        client.indices.create(
            index=index_name, settings=INDEX_SETTINGS, mappings=INDEX_MAPPINGS)
        print(f"Created index '{index_name}'")
    else:
        print(f"Index '{index_name}' already exists — appending documents")

    # Ingest each dataset
    total_success = 0
    total_errors = 0
    skipped = []
    for dataset_dir in dataset_dirs:
        ds_name = dataset_dir.name
        print(f"\n--- {ds_name} ---")

        try:
            # Load BIDS layout
            print(f"  Loading BIDS layout...")
            layout = BIDSLayout(str(dataset_dir), validate=False)
            print(f"  Subjects: {layout.get_subjects()}")
            print(f"  Suffixes: {layout.get_suffixes()}")

            # Load participants
            participants_file = dataset_dir / "participants.tsv"
            participant_lookup = {}
            if participants_file.exists():
                participants = pd.read_csv(participants_file, sep="\t")
                for _, row in participants.iterrows():
                    participant_lookup[row["participant_id"]] = row.to_dict()
                print(f"  Participants: {len(participant_lookup)}")

            # Bulk index (raise_on_error=False so one bad doc doesn't abort the run)
            actions = (
                {"_index": index_name, "_source": doc}
                for doc in generate_documents(layout, participant_lookup, model, dataset_dir, ds_name)
            )
            success, errors = helpers.bulk(
                client, actions, raise_on_error=False, refresh="wait_for")
            total_success += success
            if errors:
                total_errors += len(errors)
                print(f"  Indexed: {success} documents ({len(errors)} failed)")
                for err in errors[:3]:
                    print(f"    ⚠ {err}")
            else:
                print(f"  Indexed: {success} documents")
        except Exception as exc:
            skipped.append(ds_name)
            print(f"  ⚠ SKIPPED: {exc}")

    count = client.count(index=index_name)
    print(f"\nIngestion complete!")
    print(f"  Total documents indexed this run: {total_success}")
    if total_errors:
        print(f"  Total document errors: {total_errors}")
    if skipped:
        print(f"  Skipped datasets: {skipped}")
    print(f"  Total in index: {count['count']}")


if __name__ == "__main__":
    main()
