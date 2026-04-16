"""
Transform annotation output to Bagel-compliant data dictionary schema.

Bagel expects the following structure:
{
  "column_name": {
    "Description": "...",
    "Annotations": {
      "IsAbout": {"TermURL": "...", "Label": "..."},
      "VariableType": "Identifier|Categorical|Continuous|Collection",
      "MissingValues": [],
      # For Categorical:
      "Levels": {"value1": {"TermURL": "...", "Label": "..."}, ...},
      # For Continuous:
      "Format": {"TermURL": "...", "Label": "..."},
      # For Collection:
      "IsPartOf": {"TermURL": "...", "Label": "..."}
    }
  }
}

This module converts our intermediate format to this schema.
"""
import json
from pathlib import Path
from typing import Any, Dict


VARIABLE_TERMS = {
    "nb:ParticipantID": {
        "TermURL": "nb:ParticipantID",
        "Label": "Participant ID"
    },
    "nb:SessionID": {
        "TermURL": "nb:SessionID",
        "Label": "Session ID"
    },
    "nb:Age": {
        "TermURL": "nb:Age",
        "Label": "Age"
    },
    "nb:Sex": {
        "TermURL": "nb:Sex",
        "Label": "Sex"
    },
    "nb:Diagnosis": {
        "TermURL": "nb:Diagnosis",
        "Label": "Diagnosis"
    },
}
# Mapping from format IRIs to Term representations (abbreviated)
FORMAT_TERMS = {
    "nb:FromFloat": {
        "TermURL": "nb:FromFloat",
        "Label": "Floating point number"
    },
}
# URL prefixes for expanding abbreviated IRIs
URL_PREFIXES = {
    "snomed": "http://purl.bioontology.org/ontology/SNOMEDCT/",
    "ncit": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#",
    "nb": "http://neurobagel.org/vocab/",
}


# Mapping from Neurobagel variable IRIs to Term representations
# NOTE: These should be abbreviated IRIs (e.g., "nb:ParticipantID"), not full URLs
# Bagel CLI expects abbreviated IRIs and handles URL expansion internally
def expand_iri(iri: str) -> str:
    """
    Expand abbreviated IRI (e.g. 'snomed:123') to full URL.

    Args:
        iri: IRI string, possibly abbreviated with prefix (e.g. 'snomed:248153007')

    Returns:
        Full URL or original string if not abbreviated
    """
    if not iri or ":" not in iri or iri.startswith("http"):
        return iri

    prefix, code = iri.split(":", 1)
    if prefix in URL_PREFIXES:
        return URL_PREFIXES[prefix] + code
    return iri


def convert_to_bagel_schema(
    parsed_annotations: Dict[str, Any],
    phenotype_mappings: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Convert parsed annotations to Bagel-compliant data dictionary schema.

    Args:
        parsed_annotations: Output from annotation tool with flat structure:
            {"column_name": {"variable": "nb:...", "source": "...", "confidence": ..., ...}}
        phenotype_mappings: Static phenotype mappings with variable types and levels

    Returns:
        Bagel-compliant dictionary with proper schema
    """
    bagel_dict = {}

    for column_name, annotation_info in parsed_annotations.items():
        variable = annotation_info.get("variable", "unknown")
        rationale = annotation_info.get("rationale", "")

        # Get the variable term
        term = VARIABLE_TERMS.get(variable)
        if not term:
            print(
                f"⚠ Warning: No term mapping for {variable}, skipping {column_name}")
            continue

        # Initialize the column entry
        bagel_dict[column_name] = {
            "Description": rationale,  # Use rationale as description
            "Annotations": {
                "IsAbout": term
                # Note: MissingValues will be added later if appropriate for the type
            }
        }

        # Determine variable type from mappings
        # Try to find the mapping by searching through phenotype mappings
        variable_type = None
        levels = None
        format_term = None

        for col_map_name, col_mapping in phenotype_mappings.get("mappings", {}).items():
            if col_mapping.get("variable") == variable:
                variable_type = col_mapping.get("variableType")
                if variable_type == "Categorical" and "levels" in col_mapping:
                    levels = col_mapping["levels"]
                if variable_type == "Continuous" and "format" in col_mapping:
                    format_iri = col_mapping["format"]
                    format_term = FORMAT_TERMS.get(format_iri)
                break

        # If we couldn't find it in phenotype_mappings, try to infer from the column_name
        if not variable_type:
            # Fallback: try to guess based on variable IRI
            if "Identifier" in variable or "ID" in variable:
                variable_type = "Identifier"
            elif "Age" in variable:
                variable_type = "Continuous"
            elif "Sex" in variable or "Gender" in variable:
                variable_type = "Categorical"
            else:
                variable_type = "Categorical"  # Default guess

        # Add variable type
        bagel_dict[column_name]["Annotations"]["VariableType"] = variable_type

        # Add MissingValues for non-Identifier types (Identifier doesn't allow additionalProperties)
        # Include common missing value markers found in the data
        if variable_type != "Identifier":
            bagel_dict[column_name]["Annotations"]["MissingValues"] = [
                "n/a", "N/A", "NA", ""]

        # Add type-specific fields
        if variable_type == "Categorical" and levels:
            # Normalize level field names to Bagel format (TermURL/Label instead of termURL/label)
            # Keep abbreviated IRIs (e.g., snomed:123), don't expand to full URLs
            normalized_levels = {}
            for level_key, level_value in levels.items():
                term_url = level_value.get(
                    "TermURL") or level_value.get("termURL", "")
                #  Don't expand URLs - keep abbreviated IRIs for Bagel validation
                normalized_levels[level_key] = {
                    "TermURL": term_url,
                    "Label": level_value.get("Label") or level_value.get("label", "")
                }
            bagel_dict[column_name]["Annotations"]["Levels"] = normalized_levels
        elif variable_type == "Continuous" and format_term:
            bagel_dict[column_name]["Annotations"]["Format"] = format_term
        elif variable_type == "Continuous" and not format_term:
            # Default continuous format
            bagel_dict[column_name]["Annotations"]["Format"] = {
                "TermURL": "http://neurobagel.org/vocab/FromFloat",
                "Label": "Floating point number"
            }

    return bagel_dict


def save_as_bagel_schema(
    output_path: Path,
    parsed_annotations: Dict[str, Any],
    phenotype_mappings: Dict[str, Any],
    verbose: bool = True
) -> None:
    """
    Convert annotations to Bagel schema and save to file.

    Args:
        output_path: Path to save the Bagel-compliant dictionary
        parsed_annotations: Parsed annotations from tool
        phenotype_mappings: Static phenotype mappings
        verbose: Print operations
    """
    if verbose:
        print(f"\n→ Converting to Bagel schema...")

    bagel_dict = convert_to_bagel_schema(
        parsed_annotations, phenotype_mappings)

    with open(output_path, 'w') as f:
        json.dump(bagel_dict, f, indent=2)

    if verbose:
        print(f"✓ Saved Bagel-compliant dictionary: {output_path}")
        print(f"  Columns: {list(bagel_dict.keys())}")
