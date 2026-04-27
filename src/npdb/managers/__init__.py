
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from npdb.managers.neurogitea import OrganizationMixin
from npdb.managers.neurobagel import BagelMixin, NeurobagelManager
from npdb.managers.bids import BIDSStandardizer
from npdb.external.neurogitea.gitea import GiteaManager


class DataNeuroPolyMTL(OrganizationMixin, GiteaManager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        GiteaManager.__init__(
            self, url=url, user=user, token=token, ssl_verify=ssl_verify)
        OrganizationMixin.__init__(
            self, organization="datasets", client=self.client)

    def clone_repository(
        self,
        dataset: str,
        local_path: str,
        light: bool = False,
        cache_dir: Optional[str] = None,
        output_callback: Optional[Callable[[str], None]] = None,
    ):
        repo = next(iter([d for d in self.datasets if d.name == dataset]))
        clone_url = f"{repo.gitea.url}/{self.organization.name}/{repo.name}.git"

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Cache-dir mode: reuse an existing clone via fetch, or do a fresh clone.
        if cache_dir:
            cached = os.path.join(cache_dir, dataset)
            if os.path.isdir(os.path.join(cached, ".git")):
                command = ["git", "-C", cached, "fetch", "--depth=1"]
                self._run_git(command, env, output_callback)
                # Symlink / copy into local_path so the rest of the pipeline
                # continues to point at the expected directory.
                if not os.path.exists(local_path):
                    import shutil
                    shutil.copytree(cached, local_path, symlinks=True)
                return
            target = cached
        else:
            target = local_path

        command = ["git"] + self.git_http_config() + ["clone"]
        if light:
            command.extend(["--depth", "1", "--filter=blob:none"])
        command.extend([clone_url, target])

        self._run_git(command, env, output_callback)

        # When using cache_dir and the clone target differs from local_path,
        # copy into local_path so callers see the expected path.
        if cache_dir and target != local_path and not os.path.exists(local_path):
            import shutil
            shutil.copytree(target, local_path, symlinks=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _run_git(
        self,
        command: list,
        env: dict,
        output_callback: Optional[Callable[[str], None]] = None,
    ):
        try:
            if output_callback is None:
                subprocess.run(command, check=True,
                               env=env, capture_output=True)
                return
            # Stream output to callback via Popen
            proc = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                output_callback(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, command)
        except subprocess.CalledProcessError as e:
            stack = f"Command: {' '.join(str(c) for c in command)}"
            raise RuntimeError(f"Failed to clone repository: {e}\n{stack}")

    def extend_description(self, dataset: str, local_clone: str):
        desc_path = os.path.join(local_clone, "dataset_description.json")
        with open(desc_path, "r") as f:
            description = json.load(f)

        # Normalize name using the argument provided
        description["Name"] = dataset

        # If the authors list is empty or missing, pull all collaborators as authors
        # if not description.get("Authors"):
        #     collaborators = repo.get_users_with_access()
        #     description["Authors"] = [c.id for c in collaborators]

        # If no keywords, add at least the dataset name
        if not description.get("Keywords"):
            description["Keywords"] = [dataset]

        # Add repository URL
        description["RepositoryURL"] = f"{self.client.url}/{self.organization.name}/{dataset}"
        # Add documentation link as AccessLink
        description["AccessInstructions"] = "Refer to the access link provided with the repository."
        description["AccessLink"] = "https://intranet.neuro.polymtl.ca/data/README.html"
        # Document all access as resctricted for now
        description["AccessType"] = "restricted"
        # Fetch repository maintainer as contact for access if not present
        # if "AccessEmail" not in description:
        #     users = repo.get_users_with_access()
        #     maintainers = repo.get_collaborators(role="maintainer")
        #     if maintainers.total > 0:
        #         description["AccessEmail"] = maintainers[0].email
        #     else:
        #         # Find owner then
        #         owners = repo.get_collaborators(role="owner")
        #         if owners.total > 0:
        #             description["AccessEmail"] = owners[0].email

        return description

    def download_subjects(
        self,
        subjects: list[tuple[str, str, str]],
        output_dir: Path,
        use_annex: bool = False,
    ) -> list[tuple[bool, str, str]]:
        """
        Download subject directories using authenticated sparse git clone.

        Multiple subjects that share the same repository are grouped together
        so that the repository is cloned exactly once, with all required sparse
        paths set in a single ``git sparse-checkout set`` call.  This avoids
        the "destination already exists" error and dramatically reduces network
        traffic for queries that return many subjects from one dataset.

        Args:
            subjects: Triples of ``(repo_url, sparse_path, dataset_name)``.
            output_dir: Base output directory.  Each dataset lands in
                        ``output_dir / dataset_name``.
            use_annex: When ``True``, run ``git annex get`` after each clone.

        Returns:
            List of ``(success, label, message)`` for each unique repository.
        """
        # Group sparse paths by (repo_url, dataset_name) so each repo is
        # cloned exactly once, regardless of how many subjects it contains.
        groups: dict[tuple[str, str], list[str]] = {}
        for repo_url, sparse_path, dataset_name in subjects:
            key = (repo_url, dataset_name)
            groups.setdefault(key, [])
            if sparse_path not in groups[key]:
                groups[key].append(sparse_path)

        results: list[tuple[bool, str, str]] = []

        for (repo_url, dataset_name), sparse_paths in groups.items():
            dest = output_dir / dataset_name
            label = f"{dataset_name} [{', '.join(sparse_paths)}]"

            try:
                self.clone_sparse(repo_url, sparse_paths, dest)
                if use_annex:
                    self.annex_get(dest, sparse_paths)
                results.append((True, label, "OK"))
            except RuntimeError as e:
                results.append((False, label, str(e)))

        return results


class BagelNeuroPolyMTL(BagelMixin, NeurobagelManager):
    def __init__(self, output_dir: str):
        NeurobagelManager.__init__(self, output_dir)
        BagelMixin.__init__(self, self.db)

    def convert_bids(
        self,
        dataset: str,
        bids_dir: str,
        phenotypes_tsv: str,
        phenotypes_annotations: str,
        dataset_description: dict
    ):
        # Generate TSV from BIDS directory
        with tempfile.NamedTemporaryFile(suffix=".tsv", mode='w+', delete=False) as tmp_file:
            with tempfile.NamedTemporaryFile(suffix=".json", mode='w+', delete=False) as tmp_desc:
                json.dump(dataset_description, tmp_desc)
                tmp_desc.flush()

                self.bids2tsv(bids_directory=bids_dir,
                              output_tsv=tmp_file.name)

                # Generate JSON-LD from TSV and phenotypes description
                self.bagel_pheno(
                    dataset_name=dataset,
                    phenotypes_tsv=phenotypes_tsv,
                    phenotypes_annotations=phenotypes_annotations,
                    dataset_description=tmp_desc.name
                )

                # Generate sub JSON-LD with BIDS metadata
                self.bagel_bids(
                    dataset_name=dataset,
                    bids_table=tmp_file.name
                )

        # Clean up temp files
        try:
            os.unlink(tmp_file.name)
        except OSError:
            pass
        try:
            os.unlink(tmp_desc.name)
        except OSError:
            pass
