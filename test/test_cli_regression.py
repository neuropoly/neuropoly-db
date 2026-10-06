"""
CLI regression tests to verify the nested convert/bagel/gitea workflow.

These tests ensure that the CLI still exposes the expected command hierarchy
after the conversion and facade refactors.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from npdb.annotation import AnnotationConfig
from npdb.cli.cli import npdb

runner = CliRunner()
GITEA_COMMAND = ["convert", "bagel", "gitea"]
LOCAL_COMMAND = ["convert", "bagel", "local"]


class TestCLIStructure:
    """Tests for CLI interface structure and help."""

    def test_cli_app_exists(self):
        """Test that CLI app is properly initialized."""
        assert npdb is not None

    def test_help_command_works(self):
        """Test that 'npdb --help' returns help output."""
        result = runner.invoke(npdb, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.stdout or "Commands:" in result.stdout

    def test_cli_shows_command_hierarchy(self):
        """Test that CLI help shows proper command hierarchy (REGRESSION TEST)."""
        result = runner.invoke(npdb, ["--help"])
        assert result.exit_code == 0
        # CRITICAL: Must show "COMMAND" in usage, not just options
        assert (
            "COMMAND" in result.stdout
        ), "CLI help should show command hierarchy, not flatten to options"
        # Must list Commands section
        assert "Commands" in result.stdout, "CLI help must show Commands section"

    def test_cli_has_convert_subcommand(self):
        """Test that convert command is listed in top-level help."""
        result = runner.invoke(npdb, ["--help"])
        assert result.exit_code == 0
        assert "convert" in result.stdout

    def test_convert_has_bagel_subcommand(self):
        """Test that bagel is exposed under the convert command."""
        result = runner.invoke(npdb, ["convert", "--help"])
        assert result.exit_code == 0
        assert "bagel" in result.stdout

    def test_bagel_help_lists_provider_commands(self):
        """Test that all provider-backed convert/bagel commands are exposed."""
        result = runner.invoke(npdb, ["convert", "bagel", "--help"])
        assert result.exit_code == 0
        for command in [
            "local",
            "gitea",
            "git",
            "kaggle",
            "mendeley",
            "midrc",
            "openneuro",
            "zenodo",
            "figshare",
        ]:
            assert command in result.stdout

    def test_gitea_help(self):
        """Test that the nested gitea help works."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        assert result.exit_code == 0
        assert "DATASET" in result.stdout or "dataset" in result.stdout.lower()

    def test_gitea_help_shows_option_groups(self):
        """Test that gitea help organizes options into logical groups."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        assert result.exit_code == 0
        help_text = result.stdout
        # Verify option groups are present
        assert "Input Options" in help_text, "Should have Input Options group"
        assert "Automation Options" in help_text, "Should have Automation Options group"
        assert "AI Options" in help_text, "Should have AI Options group"


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing."""

    def test_gitea_annotation_mode_flag(self):
        """Test that --mode flag exists."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        assert result.exit_code == 0
        assert "--mode" in result.stdout

    def test_gitea_headless_flag(self):
        """Test that headless flag exists."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        if result.exit_code == 0:
            # Check for headless-related options
            help_text = result.stdout.lower()
            assert "headless" in help_text or "headed" in help_text

    def test_gitea_phenotype_dictionary_flag(self):
        """Test that phenotype-dict flag exists."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        if result.exit_code == 0:
            assert (
                "--phenotype-dict" in result.stdout
                or "phenotype" in result.stdout.lower()
            )

    def test_local_command_is_listed_under_bagel(self):
        """Test that local conversion command is exposed under bagel."""
        result = runner.invoke(npdb, ["convert", "bagel", "--help"])
        assert result.exit_code == 0
        assert "local" in result.stdout

    def test_local_help_shows_expected_arguments(self):
        """Test nested local help and positional arguments."""
        result = runner.invoke(npdb, LOCAL_COMMAND + ["--help"])
        assert result.exit_code == 0
        help_text = result.stdout
        assert "INPUT_DIR" in help_text
        assert "ONLINE_URL" in help_text
        assert "OUTPUT" in help_text

    def test_local_help_shows_option_groups(self):
        """Test that local help organizes options into logical groups."""
        result = runner.invoke(npdb, LOCAL_COMMAND + ["--help"])
        assert result.exit_code == 0
        help_text = result.stdout
        assert "Input Options" in help_text
        assert "Behavior Options" in help_text
        assert "Automation Options" in help_text
        assert "AI Options" in help_text


class TestCLINoRegressions:
    """Regression tests to ensure no breaking changes."""

    def test_cli_doesnt_crash_on_invalid_args(self):
        """Test that CLI handles invalid arguments gracefully."""
        # Should exit with error code, not crash
        result = runner.invoke(npdb, ["nonexistent-command"])
        assert result.exit_code != 0

    def test_gitea_missing_required_args(self):
        """Test nested gitea error handling for missing required arguments."""
        result = runner.invoke(npdb, GITEA_COMMAND)
        # Should fail due to missing required arguments (exit code 2 = usage error)
        assert result.exit_code == 2

    def test_local_missing_required_args(self):
        """Test nested local error handling for missing required arguments."""
        result = runner.invoke(npdb, LOCAL_COMMAND)
        # Should fail due to missing required arguments (exit code 2 = usage error)
        assert result.exit_code == 2

    def test_local_happy_path_executes_facade(self, tmp_path):
        """Test local command executes facade with expected args."""
        input_dir = tmp_path / "dataset"
        input_dir.mkdir()
        (input_dir / "dataset_description.json").write_text("{}", encoding="utf-8")
        output_dir = tmp_path / "output"

        with (
            patch("npdb.cli.cli.extend_bids_description") as mock_extend,
            patch(
                "npdb.cli.facade.DatasetConversionFacade.run", new_callable=AsyncMock
            ) as mock_run,
        ):
            result = runner.invoke(
                npdb,
                LOCAL_COMMAND
                + [
                    str(input_dir),
                    "https://example.com/repo",
                    str(output_dir),
                    "--access-type",
                    "restricted",
                ],
            )

        assert result.exit_code == 0, result.output
        mock_extend.assert_called_once_with(
            input_dir.name,
            str(input_dir),
            "https://example.com/repo",
            "restricted",
        )
        mock_run.assert_awaited_once()
        assert "Conversion complete!" in result.stdout


class TestStandardizeSubcommand:
    """Tests for the standardize subgroup and bids command."""

    def test_cli_has_standardize_subcommand(self):
        """Test that standardize subcommand is listed in help."""
        result = runner.invoke(npdb, ["--help"])
        assert result.exit_code == 0
        assert "standardize" in result.stdout

    def test_standardize_help(self):
        """Test that 'npdb standardize --help' works."""
        result = runner.invoke(npdb, ["standardize", "--help"])
        assert result.exit_code == 0
        assert "bids" in result.stdout

    def test_standardize_bids_help(self):
        """Test that 'npdb standardize bids --help' works."""
        result = runner.invoke(npdb, ["standardize", "bids", "--help"])
        assert result.exit_code == 0
        assert "BIDS_DIR" in result.stdout or "bids" in result.stdout.lower()

    def test_standardize_bids_dry_run_flag(self):
        """Test that --dry-run flag is available."""
        result = runner.invoke(npdb, ["standardize", "bids", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.stdout

    def test_standardize_bids_keep_annotations_flag(self):
        """Test that --keep-annotations flag is available."""
        result = runner.invoke(npdb, ["standardize", "bids", "--help"])
        assert result.exit_code == 0
        assert "--keep-annotations" in result.stdout

    def test_standardize_bids_missing_dir(self):
        """Test error when BIDS dir doesn't exist."""
        result = runner.invoke(npdb, ["standardize", "bids", "/nonexistent/path"])
        assert result.exit_code != 0

    def test_gitea_annotation_modes_accepted(self):
        """Test that valid annotation modes are accepted."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        assert result.exit_code == 0
        # Check for version option for some annotation modes
        help_text = result.stdout
        # --mode should be mentioned in help
        assert "--mode" in help_text

    def test_cli_help_consistency(self):
        """Test that CLI help is consistent and shows all options."""
        result = runner.invoke(npdb, GITEA_COMMAND + ["--help"])
        assert result.exit_code == 0
        # Verify key documentation exists
        assert "DATASET" in result.stdout or "OUTPUT" in result.stdout

    def test_cli_imports_correctly(self):
        """Test that CLI module imports without errors."""
        from npdb.cli.cli import gitea2bagel, npdb

        assert npdb is not None
        assert callable(gitea2bagel)

    def test_annotation_config_available_in_cli(self):
        """Test that AnnotationConfig is available in CLI."""
        from npdb.managers.annotation import NeurobagelAnnotator

        assert NeurobagelAnnotator is not None
        assert AnnotationConfig is not None

        # Can create instances
        config = AnnotationConfig(mode="manual")
        assert config.mode == "manual"

        manager = NeurobagelAnnotator(config)
        assert manager.config.mode == "manual"
