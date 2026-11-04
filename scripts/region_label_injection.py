import json
import time
import subprocess
from pathlib import Path
import nibabel as nib
import numpy as np

LABEL_MAPPING = { # cf. https://github.com/neuropoly/totalspineseg/blob/main/totalspineseg/resources/labels_maps/tss_map.json
    1: "SC",
    2: "CSF",
    11: "C1", 12: "C2", 13: "C3", 14: "C4", 15: "C5", 16: "C6", 17: "C7",
    21: "T1", 22: "T2", 23: "T3", 24: "T4", 25: "T5", 26: "T6", 27: "T7", 
    28: "T8", 29: "T9", 30: "T10", 31: "T11", 32: "T12",
    41: "L1", 42: "L2", 43: "L3", 44: "L4", 45: "L5", 46: "L6", 47: "L7",
    50: "sacrum",
    63: "C2-C3", 64: "C3-C4", 65: "C4-C5", 66: "C5-C6", 67: "C6-C7",
    71: "C7-T1", 72: "T1-T2", 73: "T2-T3", 74: "T3-T4", 75: "T4-T5", 
    76: "T5-T6", 77: "T6-T7", 78: "T7-T8", 79: "T8-T9", 80: "T9-T10", 
    81: "T10-T11", 82: "T11-T12",
    91: "T12-L1", 92: "L1-L2", 93: "L2-L3", 94: "L3-L4", 95: "L4-L5", 
    100: "L5-S"
}

ISSUE_MESSAGE = ("Please make sure to open an issue at https://github.com/neuropoly/neuropoly-db so the "
                 "field content can be discussed and reviewed \n")

class ExistingBodyPartDetailsError(Exception):
    def __init__(self, conflicts, bids_path):
        self.involved_subjects = [conflicts[i][0].relative_to(bids_path).parts[0] for i in range(len(conflicts))]
        unique_subjects = np.unique(self.involved_subjects) # to make sure subjects doesn't appear multiple times
        involved_subjects_string = ", ".join(str(sub_id) for sub_id in unique_subjects)
        message = f"BodyPartDetails already contains a different value for {len(unique_subjects)} subjects in the given dataset and is not overwritten. " + ISSUE_MESSAGE + "\n" + "List of the subjects that already have a different BodyPartDetails field: " + involved_subjects_string
        super().__init__(message)

class RegionLabelBPD:
    def __init__(self, dataset_path):
        self.dataset_path = dataset_path

    def extract_vertebrae_from_segmentation(self, seg_path):
        """
        Reads a segmentation file and returns the involved vertebrae
        """
        img = nib.load(seg_path)
        data = img.get_fdata() # list of region code for each voxel
        unique_labels = np.unique(data) # keep only one time a region code if it appears multiple times

        vertebrae = []
        for label in unique_labels:
            label_int = int(label)
            if label_int in LABEL_MAPPING:
                name = LABEL_MAPPING[label_int]
                if name.startswith(("C", "T", "L", "S")) and "-" not in name and name != "SC" and name != "CSF":
                    vertebrae.append(name)

        return vertebrae

    def compute_body_part_details_field(self, vertebrae_list):
        """
        Computes the BodyPartDetails field from the vertebrae list (convert the output of extract_vertebrae_from_segmentation into the conventionnal string)
        """
        if not vertebrae_list:
            return None

        body_part_details = ",".join(str(v) for v in vertebrae_list)
        return body_part_details

    def update_bids_sidecar(self, json_path, body_part_details):
        """
        Update the sidecar with the BodyPartDetail annotation
        Actual problem: may overwrite the previous BPD field
        """

        json_path = Path(json_path)

        if json_path.exists(): # sidecar file loading
            with open(json_path, 'r', encoding='utf-8') as f:
                sidecar = json.load(f)
        else:
            sidecar = {}
        
        sidecar["BodyPartDetails"] = body_part_details # modification

        if sidecar.get("BodyPartDetails") == body_part_details:
            return

        with open(json_path, 'w', encoding='utf-8') as f: # saving of the updated sidecar
            json.dump(sidecar, f, indent=4, ensure_ascii=False)


    def process_dataset(self):
        """
        Process the whole dataset by reading every label and inject them into the BodyPartDetails field of the sidecar
        If the dataset contains no label, the function asks the TotalSpineSeg tool to generate the annotations
        """

        planned = self.plan_dataset()
        conflicts = self.find_conflicts(planned)
        if conflicts:
            raise ExistingBodyPartDetailsError(conflicts=conflicts, bids_path=self.dataset_path)
        
        for path, _, bpd_value in planned:
            self.update_bids_sidecar(path, bpd_value)
            

    def plan_dataset(self):
        """
        Compute (json_path, sub_id, bpd_value) for every image. No writing

        json_path:  path to the sidecar file
        sub_id:     id of the current subject 
        bpd_value:  content of the field (eg. "C1,C2,C3,C4,C5")
        """
        bids_path = Path(self.dataset_path)
        planned = []

        for img_path in sorted(bids_path.glob("sub-*/anat/*.nii.gz")):
            if "dseg" in img_path.name:  # escape segmentation files
                continue

            json_path = img_path.with_suffix("").with_suffix(".json") # get sidecar path
            sub_id = img_path.relative_to(bids_path).parts[0] # splits the path into a list containing the folder names
            seg_filename = img_path.name.replace(".nii.gz", "_label-spine_dseg.nii.gz") 
            seg_path = bids_path / "derivatives" / "labels" / sub_id / "anat" / seg_filename  # retrieve the segmentation path of the subject, where the labels are stored

            if not seg_path.exists():
                # execute TotalSpineSeg if the label doesn't exist
                # to do: call TotalSpineSeg tool to generate the labels
                continue

            bpd_value = self.compute_body_part_details_field(self.extract_vertebrae_from_segmentation(seg_path))
            if bpd_value:
                planned.append((json_path, sub_id, bpd_value))
        return planned

    def find_conflicts(self, planned):
        """
        Search already existing BodyPartDetails field in the dataset to raise an error and avoid overwriting it
        """
        conflicts = []
        for path, sidecar, new_value in planned:
            if path.exists(): # sidecar file loading
                with open(path, 'r', encoding='utf-8') as f:
                    sidecar = json.load(f)
            else:
                sidecar = {}
            if "BodyPartDetails" in sidecar and sidecar["BodyPartDetails"] != new_value:
                conflicts.append((path, sidecar["BodyPartDetails"], new_value))
        return conflicts

    def run_total_spine_seg(self):
        subprocess.run(["total-spine-seg"], capture_output=True, text=True) # TODO: add other arguments of the CLI in the list

if __name__=="__main__":
    start_time = time.perf_counter()

    target_dir = "/Users/maximephan/Downloads/ds005616-1.1.2" # to complete with your local path to the dataset
    annotation = RegionLabelBPD(target_dir)
    annotation.process_dataset()

    end_time = time.perf_counter()
    elapsed_time = end_time - start_time
    print(f"Execution time: {round(elapsed_time, 2)}sec")