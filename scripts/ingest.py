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
            "dataset":   {"type": "keyword"},
            "subject":   {"type": "keyword"},
            "session":   {"type": "keyword"},
            "task":      {"type": "keyword"},
            "run":       {"type": "keyword"},
            "suffix":    {"type": "keyword"},
            "datatype":  {"type": "keyword"},
            "age":       {"type": "float"},
            "sex":       {"type": "keyword"},
            "RepetitionTime":        {"type": "float"},
            "EchoTime":              {"type": "float"},
            "FlipAngle":             {"type": "float"},
            "MagneticFieldStrength": {"type": "float"},
            "SliceThickness":        {"type": "float"},
            "InversionTime":         {"type": "float"},
            "Manufacturer":           {"type": "keyword"},
            "ManufacturersModelName": {"type": "keyword"},
            "InstitutionName":        {"type": "keyword"},
            "PhaseEncodingDirection": {"type": "keyword"},
            "PulseSequenceType":      {"type": "keyword"},
            "MRAcquisitionType":      {"type": "keyword"},
            "ScanningSequence":       {"type": "keyword"},
            "BodyPart":               {"type": "keyword"},
            "ReceiveCoilName":        {"type": "keyword"},
            "TaskName":          {"type": "text"},
            "TaskDescription":   {"type": "text"},
            "SeriesDescription": {"type": "text"},
            "ProtocolName":      {"type": "text"},
            "description_text":   {"type": "text"},
            "metadata_embedding": {
                "type": "dense_vector",
                "dims": 768,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw"},
            },
            "bids_path": {"type": "keyword"},
            "study_description": {"type": "text"},
            "modality_group": {"type": "keyword"},
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
    Build a rich, prose-style description text for BM25 and embedding.

    Strategy: start from the modality, layer in scanner/protocol details,
    then task/study context.  Expand abbreviations inline so both BM25 and
    the embedding encoder see human-readable terms.
    """
    parts = []
    suffix = entities.get("suffix", "")
    modality = MODALITY_GROUPS.get(suffix, "")

    # --- Modality + suffix ---
    suffix_desc = SUFFIX_DESCRIPTIONS.get(suffix, suffix)
    if modality:
        parts.append(f"{suffix_desc} ({modality} MRI)")
    else:
        parts.append(suffix_desc)

    # --- Task / paradigm ---
    task_name = metadata.get("TaskName", "") or entities.get("task", "")
    task_entity = entities.get("task", "")
    if task_name:
        parts.append(f"task: {task_name}")
    elif task_entity:
        parts.append(f"task: {task_entity}")

    # Task description — include up to 300 chars for richer semantic context
    task_desc = metadata.get("TaskDescription", "")
    if task_desc:
        parts.append(task_desc[:300])

    # --- Scanner & acquisition ---
    field_strength = metadata.get("MagneticFieldStrength")
    if field_strength:
        # Include numeric value AND prose for BM25 + embedding
        tesla_map = {1.5: "1.5 Tesla", 3.0: "3 Tesla", 7.0: "7 Tesla",
                     3: "3 Tesla", 7: "7 Tesla"}
        tesla_str = tesla_map.get(field_strength, f"{field_strength} Tesla")
        parts.append(f"{tesla_str} ({field_strength}T) MRI scanner")

    manufacturer = metadata.get("Manufacturer", "")
    model_name = metadata.get("ManufacturersModelName", "")
    if manufacturer:
        scanner = f"{manufacturer} scanner"
        if model_name:
            scanner += f" model {model_name}"
        parts.append(scanner)

    inst = metadata.get("InstitutionName", "")
    if inst:
        parts.append(f"acquired at {inst}")

    # --- Acquisition parameters ---
    acq_type = metadata.get("MRAcquisitionType", "")
    if acq_type:
        parts.append(f"{acq_type} acquisition")

    pulse_seq = metadata.get("PulseSequenceType", "")
    scanning_seq = metadata.get("ScanningSequence", "")
    if pulse_seq:
        parts.append(f"pulse sequence: {pulse_seq}")
    elif scanning_seq:
        parts.append(f"scanning sequence: {scanning_seq}")

    tr = metadata.get("RepetitionTime")
    if tr is not None:
        parts.append(f"RepetitionTime {tr}s")

    te = metadata.get("EchoTime")
    if te is not None:
        parts.append(f"EchoTime {te}s")

    ti = metadata.get("InversionTime")
    if ti is not None:
        parts.append(f"InversionTime {ti}s")

    fa = metadata.get("FlipAngle")
    if fa is not None:
        parts.append(f"FlipAngle {fa} degrees")

    body_part = metadata.get("BodyPart", metadata.get("BodyPartExamined", ""))
    if body_part:
        parts.append(f"body part: {body_part}")

    coil = metadata.get("ReceiveCoilName", "")
    if coil:
        parts.append(f"receive coil: {coil}")

    # --- Protocol / series ---
    series_desc = metadata.get("SeriesDescription", "")
    if series_desc:
        parts.append(f"series: {series_desc}")

    protocol = metadata.get("ProtocolName", "")
    if protocol and protocol != series_desc:
        parts.append(f"protocol: {protocol}")

    # --- Study-level context from dataset_description.json ---
    if study_description:
        parts.append(study_description[:200])

    # --- Demographics ---
    age = participant_info.get("age")
    sex = participant_info.get("sex")
    if _is_valid(age):
        parts.append(f"participant age {age}")
    if _is_valid(sex):
        parts.append(f"participant sex {sex}")

    return " | ".join(parts)


def get_modality_group(suffix: str) -> str:
    return MODALITY_GROUPS.get(suffix, "other")


def generate_documents(layout, participant_lookup, model, dataset_dir, dataset_name):
    nifti_files = layout.get(extension=".nii.gz")
    dataset_root = Path(dataset_dir).resolve()

    # Extract study-level description from dataset_description.json
    study_desc_path = dataset_root / "dataset_description.json"
    study_description = ""
    if study_desc_path.exists():
        try:
            dd = json.loads(study_desc_path.read_text())
            study_description = dd.get("Name", "") + " " + dd.get("HowToAcknowledge", "")
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
    embeddings = model.encode(desc_texts, show_progress_bar=True, batch_size=32)

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
            "RepetitionTime":        safe_float(metadata.get("RepetitionTime")),
            "EchoTime":              safe_float(metadata.get("EchoTime")),
            "FlipAngle":             safe_float(metadata.get("FlipAngle")),
            "MagneticFieldStrength": safe_float(metadata.get("MagneticFieldStrength")),
            "SliceThickness":        safe_float(metadata.get("SliceThickness")),
            "InversionTime":         safe_float(metadata.get("InversionTime")),
            "Manufacturer":           metadata.get("Manufacturer"),
            "ManufacturersModelName": metadata.get("ManufacturersModelName"),
            "InstitutionName":        metadata.get("InstitutionName"),
            "PhaseEncodingDirection": metadata.get("PhaseEncodingDirection"),
            "PulseSequenceType":      metadata.get("PulseSequenceType"),
            "MRAcquisitionType":      metadata.get("MRAcquisitionType"),
            "ScanningSequence":       metadata.get("ScanningSequence"),
            "BodyPart":               metadata.get("BodyPart", metadata.get("BodyPartExamined")),
            "ReceiveCoilName":        metadata.get("ReceiveCoilName"),
            "TaskName":          metadata.get("TaskName"),
            "TaskDescription":   metadata.get("TaskDescription"),
            "SeriesDescription": metadata.get("SeriesDescription"),
            "ProtocolName":      metadata.get("ProtocolName"),
            "description_text":   fd["desc_text"],
            "metadata_embedding": embedding.tolist(),
            "study_description":  study_description,
            "modality_group":     get_modality_group(str(entities.get("suffix", ""))),
            "bids_path": fd["bids_path"],
        }

        yield doc


def find_bids_datasets(data_dir):
    """Find all BIDS datasets (directories with dataset_description.json) under data_dir."""
    data_path = Path(data_dir)
    return sorted(
        d for d in data_path.iterdir()
        if d.is_dir() and (d / "dataset_description.json").exists()
    )


def main():
    parser = argparse.ArgumentParser(description="Ingest BIDS metadata into ElasticSearch")
    parser.add_argument("--data-dir", default="data", help="Parent directory containing BIDS datasets")
    parser.add_argument("--dataset-dir", default=None, help="Path to a single BIDS dataset (overrides --data-dir)")
    parser.add_argument("--es-url", default="http://localhost:9200", help="ElasticSearch URL")
    parser.add_argument("--index", default="neuroimaging", help="Index name")
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate the index before ingesting")
    args = parser.parse_args()

    # Determine which datasets to ingest
    if args.dataset_dir:
        dataset_dir = Path(args.dataset_dir)
        if not dataset_dir.exists():
            raise FileNotFoundError(f"Dataset not found: {dataset_dir.resolve()}")
        dataset_dirs = [dataset_dir]
    else:
        data_dir = Path(args.data_dir)
        if not data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir.resolve()}")
        dataset_dirs = find_bids_datasets(data_dir)
        if not dataset_dirs:
            raise FileNotFoundError(f"No BIDS datasets found in {data_dir.resolve()}")

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
        client.indices.create(index=index_name, settings=INDEX_SETTINGS, mappings=INDEX_MAPPINGS)
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
            success, errors = helpers.bulk(client, actions, raise_on_error=False, refresh="wait_for")
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
