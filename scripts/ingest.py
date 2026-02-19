#!/usr/bin/env python3
"""
Standalone ingestion pipeline for neuroimaging BIDS metadata into ElasticSearch.

This script does the same work as Notebook 1 but as a CLI tool, suitable for
automation and integration into larger pipelines.

Usage:
    python scripts/ingest.py [--dataset-dir DATA_DIR] [--es-url ES_URL] [--index INDEX]
"""

import argparse
from pathlib import Path

import pandas as pd
from bids import BIDSLayout
from elasticsearch import Elasticsearch, helpers
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

EMBEDDING_DIMS = 384
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

INDEX_MAPPING = {
    "mappings": {
        "properties": {
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
            "Manufacturer":           {"type": "keyword"},
            "ManufacturersModelName": {"type": "keyword"},
            "InstitutionName":        {"type": "keyword"},
            "PhaseEncodingDirection": {"type": "keyword"},
            "TaskName":          {"type": "text"},
            "SeriesDescription": {"type": "text"},
            "description_text":   {"type": "text"},
            "metadata_embedding": {
                "type": "dense_vector",
                "dims": EMBEDDING_DIMS,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw"},
            },
            "bids_path": {"type": "keyword"},
        }
    }
}

SUFFIX_DESCRIPTIONS = {
    "T1w": "T1-weighted anatomical structural MRI",
    "T2w": "T2-weighted anatomical structural MRI",
    "bold": "BOLD functional MRI",
    "dwi": "diffusion weighted imaging",
    "FLAIR": "FLAIR MRI",
}


def build_description_text(entities: dict, metadata: dict, participant_info: dict) -> str:
    parts = []
    suffix = entities.get("suffix", "")
    parts.append(SUFFIX_DESCRIPTIONS.get(suffix, suffix))

    task_name = metadata.get("TaskName", entities.get("task", ""))
    if task_name:
        parts.append(f"task: {task_name}")

    field_strength = metadata.get("MagneticFieldStrength")
    if field_strength:
        parts.append(f"{field_strength}T")

    manufacturer = metadata.get("Manufacturer", "")
    model_name = metadata.get("ManufacturersModelName", "")
    if manufacturer:
        scanner = manufacturer
        if model_name:
            scanner += f" {model_name}"
        parts.append(scanner)

    tr = metadata.get("RepetitionTime")
    if tr is not None:
        parts.append(f"RepetitionTime={tr}s")

    te = metadata.get("EchoTime")
    if te is not None:
        parts.append(f"EchoTime={te}s")

    fa = metadata.get("FlipAngle")
    if fa is not None:
        parts.append(f"FlipAngle={fa}deg")

    inst = metadata.get("InstitutionName", "")
    if inst:
        parts.append(f"Institution: {inst}")

    series_desc = metadata.get("SeriesDescription", "")
    if series_desc:
        parts.append(series_desc)

    age = participant_info.get("age")
    sex = participant_info.get("sex")
    if age:
        parts.append(f"age {age}")
    if sex:
        parts.append(f"sex {sex}")

    return " | ".join(parts)


def generate_documents(layout, participant_lookup, model, dataset_dir):
    nifti_files = layout.get(extension=".nii.gz")

    file_data = []
    for bf in nifti_files:
        entities = bf.entities
        metadata = layout.get_metadata(bf.path)
        subj_id = f"sub-{entities.get('subject', '')}"
        participant_info = participant_lookup.get(subj_id, {})
        desc_text = build_description_text(entities, metadata, participant_info)
        file_data.append({
            "entities": entities,
            "metadata": metadata,
            "participant_info": participant_info,
            "desc_text": desc_text,
            "bids_path": str(Path(bf.path).relative_to(Path(dataset_dir).resolve())),
        })

    desc_texts = [fd["desc_text"] for fd in file_data]
    print(f"Encoding {len(desc_texts)} descriptions...")
    embeddings = model.encode(desc_texts, show_progress_bar=True, batch_size=32)

    for fd, embedding in zip(file_data, embeddings):
        entities = fd["entities"]
        metadata = fd["metadata"]
        participant_info = fd["participant_info"]

        doc = {
            "subject":   str(entities.get("subject", "")),
            "session":   str(entities.get("session", "")),
            "task":      str(entities.get("task", "")),
            "run":       str(entities.get("run", "")),
            "suffix":    str(entities.get("suffix", "")),
            "datatype":  str(entities.get("datatype", "")),
            "age":       participant_info.get("age"),
            "sex":       str(participant_info.get("sex", "")),
            "RepetitionTime":        metadata.get("RepetitionTime"),
            "EchoTime":              metadata.get("EchoTime"),
            "FlipAngle":             metadata.get("FlipAngle"),
            "MagneticFieldStrength": metadata.get("MagneticFieldStrength"),
            "SliceThickness":        metadata.get("SliceThickness"),
            "Manufacturer":           metadata.get("Manufacturer"),
            "ManufacturersModelName": metadata.get("ManufacturersModelName"),
            "InstitutionName":        metadata.get("InstitutionName"),
            "PhaseEncodingDirection": metadata.get("PhaseEncodingDirection"),
            "TaskName":          metadata.get("TaskName"),
            "SeriesDescription": metadata.get("SeriesDescription"),
            "description_text":   fd["desc_text"],
            "metadata_embedding": embedding.tolist(),
            "bids_path": fd["bids_path"],
        }

        yield doc


def main():
    parser = argparse.ArgumentParser(description="Ingest BIDS metadata into ElasticSearch")
    parser.add_argument("--dataset-dir", default="data/ds001", help="Path to BIDS dataset")
    parser.add_argument("--es-url", default="http://localhost:9200", help="ElasticSearch URL")
    parser.add_argument("--index", default="neuroimaging", help="Index name")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_dir.resolve()}")

    # Connect to ES
    client = Elasticsearch(args.es_url)
    info = client.info()
    print(f"Connected to ES {info['version']['number']}")

    # Load BIDS layout
    print(f"Loading BIDS layout from {dataset_dir}...")
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

    # Load embedding model
    print(f"Loading embedding model '{EMBEDDING_MODEL}'...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    # Create index
    index_name = args.index
    if client.indices.exists(index=index_name):
        client.indices.delete(index=index_name)
        print(f"Deleted existing index '{index_name}'")
    client.indices.create(index=index_name, body=INDEX_MAPPING)
    print(f"Created index '{index_name}'")

    # Bulk index
    actions = (
        {"_index": index_name, "_source": doc}
        for doc in generate_documents(layout, participant_lookup, model, dataset_dir)
    )
    success, errors = helpers.bulk(client, actions, raise_on_error=True, refresh="wait_for")

    print(f"\nIngestion complete!")
    print(f"  Documents indexed: {success}")
    print(f"  Errors: {len(errors) if isinstance(errors, list) else errors}")

    count = client.count(index=index_name)
    print(f"  Total in index: {count['count']}")


if __name__ == "__main__":
    main()
