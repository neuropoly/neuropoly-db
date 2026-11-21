#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path
import csv
import glob
import os


def run(command: list[str]) -> int:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, check=False)
    return result.returncode

def validate_download(query_path: Path, download_dir: Path) -> int:
    """
    Validate that the downloaded data corresponds to the expected ones 
    in the TSV of the Neurobagel query. It proceeds in 7 steps:

        1. Basic controls on the query TSV (check headers, etc...)
        2. Expected values extracted from the TSV
        3. Extraction of the downloaded data
        4. Global subjects comparison (check that every expected subjects 
           have been downloaded and that every downloaded subject was expected)
        5. Check per dataset (check that each dataset have been correctly downloaded 
           and that each dataset contains every of his subjects)
        6. Metadata verification (sex and age for the moment)
        7. Final verdict: if at least one of the previous steps returns an error, 
           the validation fail, else it succeeds
    """

    # step 1
    with open(query_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            print("FAIL: query TSV has no header")
            return 1
        fieldnames = reader.fieldnames
        expected_rows = list(reader)

    if not expected_rows:
        print("FAIL: query TSV contains no subjects")
        return 1

    missing_columns = {"DatasetName", "SubjectID"} - set(fieldnames)
    if missing_columns:
        print("FAIL: query TSV missing column(s): " + ", ".join(sorted(missing_columns)))
        raise SystemExit(1)

    # step 2
    expected_by_dataset = {}
    expected_subject_sex = {}
    expected_subject_age = {}
    for row in expected_rows:
        dataset = (row.get("DatasetName") or "unknown").strip()
        subject = (row.get("SubjectID") or "").strip()
        if not dataset or not subject:
            continue
        expected_by_dataset.setdefault(dataset, set()).add(subject)
        sex = normalize_sex(row.get("Sex"))
        age = row.get("Age")
        if sex:
            expected_subject_sex.setdefault(subject, set()).add(sex)
        if age not in (None, ""):
            try:
                expected_subject_age.setdefault(subject, set()).add(float(age))
            except ValueError:
                pass

    if not expected_by_dataset:
        print("FAIL: query TSV contains no valid DatasetName/SubjectID rows")
        return 1

    # step 3
    participants = {}
    for pheno_path in glob.glob(os.path.join(download_dir, "**", "participants.tsv"), recursive=True):
        with open(pheno_path, newline="", encoding="utf-8") as f:
            participant_reader = csv.DictReader(f, delimiter="\t")
            fieldnames = participant_reader.fieldnames or []
            subject_column = find_subject_column(fieldnames)
            age_column = find_value_column(fieldnames, ("age", "participant_age", "age_years"))
            sex_column = find_value_column(fieldnames, ("sex", "participant_sex", "gender", "biological_sex"))
            if not subject_column:
                continue
            for row in participant_reader:
                subject = (row.get(subject_column) or "").strip()
                if not subject:
                    continue
                participants.setdefault(subject, {})
                if age_column:
                    participants[subject]["age"] = row.get(age_column)
                if sex_column:
                    participants[subject]["sex"] = normalize_sex(row.get(sex_column))

    downloaded_subjects = set(participants)
    for path in glob.glob(os.path.join(download_dir, "**", "sub-*"), recursive=True):
        if os.path.isdir(path):
            downloaded_subjects.add(os.path.basename(path))

    # step 4
    expected_subjects = set().union(*expected_by_dataset.values())
    missing = sorted(expected_subjects - downloaded_subjects)
    unexpected = sorted(downloaded_subjects - expected_subjects)
    errors = []

    if missing:
        errors.append("Missing subjects: " + ", ".join(missing))
    if unexpected:
        errors.append("Unexpected subjects: " + ", ".join(unexpected))

    # step 5
    for dataset, subjects in expected_by_dataset.items():
        dataset_dir = os.path.join(download_dir, dataset)
        if not os.path.isdir(dataset_dir):
            errors.append(f"Missing dataset directory: {dataset}")
            continue
        local_subjects = set()
        for path in glob.glob(os.path.join(dataset_dir, "**", "sub-*"), recursive=True):
            if os.path.isdir(path):
                local_subjects.add(os.path.basename(path))
        missing_in_dataset = subjects - local_subjects
        if missing_in_dataset:
            # HTTP downloads and some repositories may not use sub-* directory names;
            # participants.tsv already provides an exact global subject check.
            participants_in_dataset = set()
            for pheno_path in glob.glob(os.path.join(dataset_dir, "**", "participants.tsv"), recursive=True):
                with open(pheno_path, newline="", encoding="utf-8") as f:
                    pheno_reader = csv.DictReader(f, delimiter="\t")
                    subject_column = find_subject_column(pheno_reader.fieldnames)
                    if subject_column:
                        participants_in_dataset.update(
                            (row.get(subject_column) or "").strip()
                            for row in pheno_reader
                            if (row.get(subject_column) or "").strip()
                        )
            missing_in_dataset -= participants_in_dataset
        if missing_in_dataset:
            errors.append(
                f"Missing subjects in {dataset}: " + ", ".join(sorted(missing_in_dataset))
            )

    # step 6
    for subject, possible_sexes in expected_subject_sex.items():
        actual_sex = participants.get(subject, {}).get("sex")
        if actual_sex and actual_sex not in possible_sexes:
            errors.append(
                f"Sex mismatch for {subject}: downloaded={actual_sex}, expected={','.join(sorted(possible_sexes))}"
            )

    for subject, possible_ages in expected_subject_age.items():
        raw_age = participants.get(subject, {}).get("age")
        if raw_age not in (None, ""):
            try:
                actual_age = float(raw_age)
            except ValueError:
                errors.append(f"Age invalid for {subject}: {raw_age}")
                continue
            if possible_ages and all(abs(actual_age - age) > 0.001 for age in possible_ages):
                errors.append(
                    f"Age mismatch for {subject}: downloaded={actual_age}, expected={','.join(str(a) for a in sorted(possible_ages))}"
                )
    # step 7
    if errors:
        print("DOWNLOAD VALIDATION FAILED")
        for error in errors:
            print("- " + error)
        return 1

    print(f"DOWNLOAD VALIDATION OK: {len(expected_subjects)} subject(s), {len(expected_by_dataset)} dataset(s)")
    return 0

def normalize_sex(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text in {"m", "male", "snomed:248153007", "248153007"} or text.endswith("/248153007"):
        return "male"
    if text in {"f", "female", "snomed:248152002", "248152002"} or text.endswith("/248152002"):
        return "female"
    if text in {"o", "other", "snomed:32570681000036106", "32570681000036106"} or text.endswith("/32570681000036106"):
        return "other"
    return text

def find_subject_column(fieldnames):
    candidates = ("participant_id", "subject_id", "subjectid", "sub_id")
    names = {name.lower().strip(): name for name in fieldnames or []}
    for candidate in candidates:
        if candidate in names:
            return names[candidate]
    return None

def find_value_column(fieldnames, candidates):
    names = {name.lower().strip(): name for name in fieldnames or []}
    for candidate in candidates:
        if candidate in names:
            return names[candidate]
    return None


def main() -> int:
    """
    Run the whole validation pipeline. It proceeds in 4 steps:

        1. Collects all arguments via the parser 
        2. Call the bash script 'query_neurobagel.sh' that executes the request to the neurobagel API
        3. If download is not skipped, runs npdb download
        4. Runs validation

    Arguments of the CLI:
        --api-url : optional, if the API to query differs from https://api.neurobagel.org, can be specified here
        --sex, --min-age, --max-age : optional, filtering criteria for the query
        --query-tsv : optional, if the query results have already been downloaded manually or in another way, the path to the 
                      file can be provided here and no query will be run
        --output-dir : provide the output directory where the TSV and the datasets will be downloaded
        --skip-download : optional, if the npdb download command have already been ran, could be specified here
        --no-verify-ssl : optional, flag to disable SSL certificate verification during `npdb download`

    Returns format: if the validation succeeds, returns code 0 and prints a message to confirm the outcome, 
                    else returns 1 (validation failed or no headers in the TSV)
    """

    parser = argparse.ArgumentParser(description="Query Neurobagel, download the result, and validate it locally")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--sex", default="")
    parser.add_argument("--min-age", default="")
    parser.add_argument("--max-age", default="")
    parser.add_argument("--query-tsv", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--no-verify-ssl", action="store_true")

    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    query_script = scripts_dir / "query_neurobagel.sh"

    query_tsv = args.query_tsv

    if query_tsv is None:
        query_tsv = args.output_dir/"neurobagel-query.tsv"
        args.output_dir.mkdir(parents=True, exist_ok=True)

        command = [str(query_script), "--output", str(query_tsv)]
        if args.api_url:
            command += ["--api-url", args.api_url]
        if args.sex:
            command += ["--sex", args.sex]
        if args.min_age:
            command += ["--min-age", args.min_age]
        if args.max_age:
            command += ["--max-age", args.max_age]

        if run(command) != 0:
            print("Neurobagel query failed", file=sys.stderr)
            return 1

    if not args.skip_download:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        command = ["uv", "run", "npdb", "download", "--output-dir", str(args.output_dir)]
        if args.no_verify_ssl:
            command.append("--no-verify-ssl")
        command.append(str(query_tsv))

        download_code = run(command)
        if download_code != 0:
            print("npdb download failed", file=sys.stderr)
            return 1

    return validate_download(query_tsv, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())