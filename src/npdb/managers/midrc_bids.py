from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from npdb.annotation.standardize import (
    generate_participants_json,
    normalize_participants_tsv,
)
from npdb.automation.mappings.resolvers import MappingResolver
from npdb.managers.preparation import extract_archive


def write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")


def unpack_midrc(source: Path, destination: Path) -> Path:
    staging = destination / "sourcedata" / "midrc"
    if staging.is_symlink():
        raise ValueError(f"Refusing MIDRC symlink staging directory: {staging}")
    archives = (
        [
            path
            for path in sorted(source.rglob("*.zip"))
            if not path.resolve().is_relative_to(staging.resolve())
        ]
        if source.is_dir()
        else [source]
    )
    structured = [path for path in archives if path.name.endswith("_structured.zip")]
    if len(structured) != 1:
        raise ValueError(
            "MIDRC preparation requires exactly one *_structured.zip archive."
        )
    name = structured[0].name.removesuffix("_structured.zip")
    roles: dict[str, Path] = {}
    for archive in archives:
        if archive.is_symlink():
            raise ValueError(f"Refusing to unpack symlink archive: {archive}")
        if not archive.name.startswith(name + "_"):
            raise ValueError(
                f"Archive does not belong to MIDRC dataset {name}: {archive.name}"
            )
        role = archive.name[len(name) + 1 :].removesuffix(".zip")
        if role in roles or not re.fullmatch(r"[A-Za-z0-9_-]+", role):
            raise ValueError(f"Conflicting or unsafe MIDRC archive role: {role}")
        roles[role] = archive
    if staging.exists():
        manifest_path = staging / "archives.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise FileExistsError(
                f"MIDRC extraction is incomplete; use a fresh staging destination: {staging}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("Name") != name or sorted(manifest.get("Roles", [])) != sorted(
            roles
        ):
            raise ValueError(
                f"Existing MIDRC extraction does not match the downloaded archives: {staging}"
            )
        if any(
            (staging / role).is_symlink() or not (staging / role).is_dir()
            for role in roles
        ):
            raise FileExistsError(
                f"MIDRC extraction is incomplete; use a fresh staging destination: {staging}"
            )
        return destination
    for role in [
        "structured",
        "imaging_files",
        *sorted(set(roles) - {"structured", "imaging_files"}),
    ]:
        if role in roles:
            extract_archive(roles[role], staging / role)
    write_json(staging / "archives.json", {"Name": name, "Roles": list(roles)})
    return destination


def read_tables(directory: Path) -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {}
    for path in sorted(directory.rglob("*.tsv")):
        if path.is_symlink():
            raise ValueError(f"Refusing MIDRC symlink table: {path}")
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"Malformed MIDRC TSV row in {path.name}")
                node = row.get("type", "")
                if not node:
                    if "imaging_studies.submitter_id" in row and "series_uid" in row:
                        node = (
                            "mr_series_file"
                            if path.name.startswith("mr_series")
                            else "series_file"
                        )
                    else:
                        continue
                tables.setdefault(node, []).append(row)
    return tables


def index_rows(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        identifier = row.get(key, "").strip()
        if not identifier:
            raise ValueError(f"MIDRC metadata row is missing {key}.")
        if identifier in indexed and indexed[identifier] != row:
            raise ValueError(f"Conflicting MIDRC metadata for {key}: {identifier}")
        indexed[identifier] = row
    return indexed


def subject_labels(cases: dict[str, dict[str, str]]) -> dict[str, str]:
    labels = {case: "sub-" + re.sub(r"[^A-Za-z0-9]", "", case) for case in cases}
    if "sub-" in labels.values() or len(set(labels.values())) != len(labels):
        raise ValueError("MIDRC case identifiers collide after BIDS normalization.")
    return labels


def prepare_metadata(
    source: Path, output: Path, name: str
) -> dict[str, list[dict[str, str]]]:
    tables = read_tables(source)
    cases = index_rows(tables.get("case", []), "submitter_id")
    if not cases:
        raise ValueError("MIDRC structured data has no case records.")
    labels = subject_labels(cases)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"MIDRC BIDS destination already exists: {output}")
    if any(path.is_symlink() for path in output.parents):
        raise ValueError(f"Refusing MIDRC symlink destination: {output}")
    output.mkdir(parents=True)
    dataset = tables.get("dataset", [{}])[0]
    write_json(
        output / "dataset_description.json",
        {
            "Name": dataset.get("dataset_name") or dataset.get("name") or name,
            "BIDSVersion": "1.9.0",
            "DatasetType": "raw",
        },
    )
    excluded = {"type", "submitter_id", "case_ids", "datasets", "datasets.submitter_id"}
    extra = sorted(
        set().union(*(row.keys() for row in cases.values()))
        - excluded
        - {"age_at_index", "sex"}
    )
    columns = ["participant_id", "age", "sex", *extra]
    participants = output / "participants.tsv"
    with participants.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=columns, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for identifier, row in sorted(cases.items()):
            values = {column: row.get(column, "").strip() or "n/a" for column in extra}
            values.update(
                participant_id=labels[identifier],
                age=row.get("age_at_index", "").strip() or "n/a",
                sex={"Male": "M", "Female": "F", "Other": "O"}.get(
                    row.get("sex", ""), "n/a"
                ),
            )
            writer.writerow(values)
    normalize_participants_tsv(participants)
    resolver = MappingResolver()
    sidecar = generate_participants_json(
        participants,
        resolver.resolve_columns(["participant_id", "age", "sex"]),
        resolver.mappings,
    )
    for column in extra:
        sidecar[column] = {"Description": f"MIDRC case metadata: {column}."}
    sidecar["age"][
        "Description"
    ] = "Age at index in years; see age_at_index_gt89 for censored ages."
    write_json(output / "participants.json", sidecar)
    return tables


def series_sidecar(row: dict[str, str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for source, target in {
        "manufacturer": "Manufacturer",
        "manufacturer_model_name": "ManufacturersModelName",
        "mr_acquisition_type": "MRAcquisitionType",
        "series_description": "SeriesDescription",
        "body_part_examined": "BodyPartExamined",
    }.items():
        if row.get(source):
            metadata[target] = row[source]
    for source, target, scale in (
        ("echo_time", "EchoTime", 0.001),
        ("repetition_time", "RepetitionTime", 0.001),
        ("magnetic_field_strength", "MagneticFieldStrength", 1),
        ("slice_thickness", "SliceThickness", 1),
        ("spacing_between_slices", "SpacingBetweenSlices", 1),
        ("flip_angle", "FlipAngle", 1),
    ):
        if row.get(source):
            value = float(row[source]) * scale
            if not math.isfinite(value):
                raise ValueError(f"Non-finite MIDRC acquisition value: {source}")
            metadata[target] = value
    if row.get("image_type"):
        metadata["ImageType"] = row["image_type"].split("_")
    return metadata


def acquisition_suffix(row: dict[str, str]) -> str:
    if row.get("modality", "MR") != "MR":
        raise ValueError("Only structural MR conversion is currently supported.")
    description = row.get("series_description", "").lower()
    if re.search(r"\bflair\b", description):
        return "FLAIR"
    for pattern, suffix in (
        (r"\bt1(?:w)?\b", "T1w"),
        (r"\bt2(?:w)?\b", "T2w"),
        (r"\bpd(?:w)?\b", "PDw"),
    ):
        if re.search(pattern, description):
            return suffix
    raise ValueError(f"Unknown structural MR acquisition: {description!r}")


def image_identities(
    tables: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    cases = index_rows(tables.get("case", []), "submitter_id")
    subjects = subject_labels(cases)
    studies = index_rows(tables.get("imaging_study", []), "submitter_id")
    series_rows = [
        row
        for node, rows in tables.items()
        if node.endswith("series_file")
        for row in rows
    ]
    series = index_rows(series_rows, "series_uid")
    annotations: dict[str, set[tuple[str, str, str]]] = {}
    invalid_annotations: set[str] = set()
    pattern = re.compile(
        r"(.+)_Study-([A-Za-z]+)-(\d+)_Series-(\d+)(?:_SEG)?\.nii(?:\.gz)?$"
    )
    for row in tables.get("annotation_file", []):
        parent_uid = row.get("mr_series_files.submitter_id", "")
        parent = next(
            (
                item
                for item in series.values()
                if item.get("submitter_id") == parent_uid
            ),
            None,
        )
        if (
            parent
            and row.get("imaging_studies.submitter_id")
            and row["imaging_studies.submitter_id"]
            != parent.get("imaging_studies.submitter_id")
        ):
            invalid_annotations.add(parent["series_uid"])
        match = pattern.fullmatch(Path(row.get("file_name", "")).name)
        if match:
            case, modality, session, run = match.groups()
            annotations.setdefault(
                row.get("mr_series_files.submitter_id", ""), set()
            ).add((case, modality + session, run))
    identities: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, str]] = []
    study_order = {uid: str(number) for number, uid in enumerate(sorted(studies), 1)}
    run_counts: dict[str, int] = {}
    used: set[str] = set()
    for uid, row in sorted(series.items()):
        study_uid = row.get("imaging_studies.submitter_id", "")
        run_counts[study_uid] = run_counts.get(study_uid, 0) + 1
        try:
            if uid in invalid_annotations:
                raise ValueError(
                    "Annotation study link disagrees with its parent series."
                )
            study = studies.get(study_uid)
            if study is None:
                raise ValueError("Series has no matching imaging study.")
            case = study.get("cases.submitter_id") or study.get("case_ids", "")
            if case not in subjects or row.get("case_ids", case) != case:
                raise ValueError(
                    "Series/study case relationship is missing or inconsistent."
                )
            labels = annotations.get(row.get("submitter_id", uid), set())
            if len(labels) > 1:
                raise ValueError(
                    "Conflicting source Study/Series labels for the same series UID."
                )
            if labels:
                named_case, session, run = next(iter(labels))
                if named_case != case:
                    raise ValueError(
                        "Annotation filename disagrees with its linked case."
                    )
            else:
                session, run = study_order[study_uid], str(run_counts[study_uid])
            suffix = acquisition_suffix(row)
            stem = f"{subjects[case]}_ses-{session}_run-{run}"
            if stem + suffix in used:
                raise ValueError("Source labels produce colliding BIDS image names.")
            used.add(stem + suffix)
            identities[uid] = {
                "subject": subjects[case],
                "session": session,
                "stem": stem,
                "suffix": suffix,
                "metadata": series_sidecar({**study, **row}),
                "study_uid": study_uid,
            }
        except ValueError as exc:
            rejected.append({"source": uid, "reason": str(exc)})
    return identities, rejected


def convert_dicom(source: Path, output: Path) -> list[Path]:
    executable = shutil.which("dcm2niix")
    if executable is None:
        raise RuntimeError("MIDRC DICOM preparation requires dcm2niix on PATH.")
    output.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            executable,
            "-b",
            "y",
            "-ba",
            "y",
            "-z",
            "y",
            "-f",
            "%s",
            "-o",
            str(output),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"dcm2niix failed for {source}: {result.stderr or result.stdout}"
        )
    images = sorted(output.glob("*.nii.gz"))
    if not images:
        raise RuntimeError(f"dcm2niix produced no NIfTI images for {source}.")
    return images


def prepare_imaging(
    staging: Path, output: Path, tables: dict[str, list[dict[str, str]]]
) -> dict[str, Any]:
    identities, rejected = image_identities(tables)
    report: dict[str, Any] = {"images": {}, "quarantine": rejected}
    imaging = staging / "imaging_files"
    if not imaging.exists():
        return report
    if shutil.which("dcm2niix") is None:
        raise RuntimeError("MIDRC DICOM preparation requires dcm2niix on PATH.")
    archives = list(imaging.rglob("*.zip"))
    dicoms = list(imaging.rglob("*.dcm"))
    handled: set[Path] = set()
    for uid, identity in identities.items():
        packages = [path for path in archives if path.stem == uid]
        directories = {path.parent for path in dicoms if uid in path.parts}
        if len(packages) + len(directories) != 1:
            report["quarantine"].append(
                {"source": uid, "reason": "Missing or ambiguous DICOM series payload."}
            )
            continue
        if packages:
            source = extract_archive(packages[0], staging / "work" / "dicom" / uid)
            handled.add(packages[0])
        else:
            source = next(iter(directories))
            handled.update(path for path in dicoms if path.parent == source)
        from pydicom import dcmread

        headers = [path for path in source.rglob("*") if path.is_file()]
        if not headers:
            raise ValueError(f"Empty DICOM series payload: {uid}")
        for path in headers:
            if path.is_symlink():
                raise ValueError(f"Refusing MIDRC symlink image: {path}")
            header = dcmread(path, stop_before_pixels=True)
            if (
                str(header.get("SeriesInstanceUID", "")) != uid
                or str(header.get("StudyInstanceUID", "")) != identity["study_uid"]
            ):
                raise ValueError(
                    f"DICOM header disagrees with MIDRC series/study metadata: {uid}"
                )
        images = convert_dicom(source, staging / "work" / "converted" / uid)
        pending: list[tuple[Path, Path, dict[str, Any]]] = []
        for image in images:
            sidecar = image.with_name(image.name.removesuffix(".nii.gz") + ".json")
            if not sidecar.is_file():
                raise ValueError(f"dcm2niix image has no JSON sidecar: {image}")
            metadata = {
                **identity["metadata"],
                **json.loads(sidecar.read_text(encoding="utf-8")),
            }
            entities = identity["stem"]
            if len(images) > 1:
                echo = metadata.get("EchoNumber")
                if echo is not None:
                    entities += f"_echo-{int(echo)}"
                image_type = metadata.get("ImageType", [])
                if "PHASE" in image_type or "P" in image_type:
                    entities += "_part-phase"
                elif "MAGNITUDE" in image_type or "M" in image_type:
                    entities += "_part-mag"
            destination = (
                output
                / identity["subject"]
                / f"ses-{identity['session']}"
                / "anat"
                / f"{entities}_{identity['suffix']}.nii.gz"
            )
            pending.append((image, destination, metadata))
        if len({str(item[1]) for item in pending}) != len(pending):
            report["quarantine"].append(
                {
                    "source": uid,
                    "reason": "Converter outputs cannot be distinguished by supported BIDS entities.",
                }
            )
            continue
        paths = []
        for image, destination, metadata in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    f"MIDRC image destination already exists: {destination}"
                )
            with (
                image.open("rb") as source_stream,
                destination.open("xb") as target_stream,
            ):
                shutil.copyfileobj(source_stream, target_stream)
            write_json(
                destination.with_name(
                    destination.name.removesuffix(".nii.gz") + ".json"
                ),
                metadata,
            )
            paths.append(destination.relative_to(output).as_posix())
        report["images"][uid] = paths
    for path in set(archives + dicoms) - handled:
        report["quarantine"].append(
            {
                "source": path.relative_to(staging).as_posix(),
                "reason": "Unmapped imaging payload; retained in sourcedata.",
            }
        )
    write_json(staging / "conversion_report.json", report)
    if not report["images"]:
        raise ValueError(
            "MIDRC preparation produced no mapped raw images; see conversion_report.json."
        )
    return report


def prepare_derivatives(
    staging: Path,
    output: Path,
    tables: dict[str, list[dict[str, str]]],
    report: dict[str, Any],
) -> None:
    import nibabel as nib
    import numpy as np

    annotations = index_rows(tables.get("annotation_file", []), "file_name")
    manifest = json.loads((staging / "archives.json").read_text(encoding="utf-8"))
    report["derivatives"] = {}
    for role in sorted(set(manifest["Roles"]) - {"structured", "imaging_files"}):
        source_root = staging / role
        derivative = output / "derivatives" / role
        for image in sorted(source_root.rglob("*")):
            if not image.is_file() or not image.name.endswith((".nii", ".nii.gz")):
                continue
            if image.is_symlink():
                raise ValueError(f"Refusing MIDRC symlink derivative: {image}")
            record = annotations.get(image.name)
            raw_paths = (
                report["images"].get(record.get("mr_series_files.submitter_id", ""), [])
                if record
                else []
            )
            if record is None or len(raw_paths) != 1:
                report["quarantine"].append(
                    {
                        "source": image.relative_to(staging).as_posix(),
                        "reason": "Derivative has no unambiguous annotation-to-raw-image link; retained in sourcedata.",
                    }
                )
                continue
            raw = output / raw_paths[0]
            case = record.get("cases.submitter_id") or record.get("case_ids", "")
            if (
                case
                and "sub-" + re.sub(r"[^A-Za-z0-9]", "", case)
                != raw.relative_to(output).parts[0]
            ):
                report["quarantine"].append(
                    {
                        "source": image.relative_to(staging).as_posix(),
                        "reason": "Derivative case link disagrees with its raw image.",
                    }
                )
                continue
            stem = raw.name.removesuffix(".nii.gz")
            entities, suffix = stem.rsplit("_", 1)
            metadata = json.loads(
                raw.with_name(stem + ".json").read_text(encoding="utf-8")
            )
            if image.name.endswith(("_SEG.nii", "_SEG.nii.gz")):
                data = np.asanyarray(nib.load(image).dataobj)
                if (
                    not np.all(np.isfinite(data))
                    or np.any(data < 0)
                    or not np.all(data == np.floor(data))
                ):
                    report["quarantine"].append(
                        {
                            "source": image.relative_to(staging).as_posix(),
                            "reason": "SEG payload is not a finite non-negative discrete label image.",
                        }
                    )
                    continue
                stem = entities + "_desc-SEG_dseg"
                metadata.pop("EchoTime", None)
                metadata.pop("RepetitionTime", None)
                metadata.pop("SliceTiming", None)
                metadata.pop("VolumeTiming", None)
            else:
                stripped = record.get("skull_stripped", "").strip().lower()
                if stripped in {"true", "false"}:
                    metadata["SkullStripped"] = stripped == "true"
                else:
                    raw_image = nib.as_closest_canonical(nib.load(raw))
                    derived_image = nib.as_closest_canonical(nib.load(image))
                    if (
                        raw_image.shape != derived_image.shape
                        or not np.allclose(
                            raw_image.affine, derived_image.affine, atol=0.001
                        )
                        or not np.array_equal(
                            np.asanyarray(raw_image.dataobj),
                            np.asanyarray(derived_image.dataobj),
                        )
                    ):
                        report["quarantine"].append(
                            {
                                "source": image.relative_to(staging).as_posix(),
                                "reason": "Derived anatomical image has unknown skull-stripping status and is not equivalent to raw.",
                            }
                        )
                        continue
                    metadata["SkullStripped"] = False
            extension = ".nii.gz" if image.name.endswith(".nii.gz") else ".nii"
            destination = (
                derivative / raw.relative_to(output).parent / (stem + extension)
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not (derivative / "dataset_description.json").exists():
                write_json(
                    derivative / "dataset_description.json",
                    {
                        "Name": f"{manifest['Name']} {role}",
                        "BIDSVersion": "1.9.0",
                        "DatasetType": "derivative",
                        "GeneratedBy": [
                            {
                                "Name": "npdb",
                                "Description": "MIDRC derivative organization; original annotation metadata retained in sidecars.",
                            }
                        ],
                        "DatasetLinks": {"raw": "../.."},
                    },
                )
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    f"MIDRC derivative destination already exists: {destination}"
                )
            with (
                image.open("rb") as source_stream,
                destination.open("xb") as target_stream,
            ):
                shutil.copyfileobj(source_stream, target_stream)
            metadata.update(
                {
                    "Sources": ["bids:raw:" + raw_paths[0]],
                    "OriginalFileName": image.name,
                    "AnnotationName": record.get("annotation_name", ""),
                    "AnnotationMethod": record.get("annotation_method", ""),
                }
            )
            write_json(destination.with_name(stem + ".json"), metadata)
            report["derivatives"][image.relative_to(staging).as_posix()] = (
                destination.relative_to(output).as_posix()
            )
        for path in sorted(source_root.rglob("*")):
            if path.is_file() and not path.name.endswith((".nii", ".nii.gz", ".json")):
                report["quarantine"].append(
                    {
                        "source": path.relative_to(staging).as_posix(),
                        "reason": "Unsupported derivative file format; retained in sourcedata.",
                    }
                )
    report_path = staging / "derivative_report.json"
    write_json(report_path, report)
