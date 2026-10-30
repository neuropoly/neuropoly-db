"""
CLI regression tests to verify the nested convert/bagel/gitea workflow.

These tests ensure that the CLI still exposes the expected command hierarchy
after the conversion and facade refactors.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner
from typer.testing import CliRunner as RepairRunner

from bids_repair.cli import app as repair_app
from npdb.annotation import AnnotationConfig
from npdb.cli.cli import npdb

runner = CliRunner()
GITEA_COMMAND = ["convert", "bagel", "gitea"]
LOCAL_COMMAND = ["convert", "bagel", "local"]


class TestCLIStructure:
    """Tests for CLI interface structure and help."""

    def test_console_entry_delegates_to_app(self):
        from npdb.cli.cli import run

        with patch("npdb.cli.cli.npdb") as app:
            run()
        app.assert_called_once_with()

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

        with patch(
            "npdb.cli.facade.DatasetConversionFacade.run", new_callable=AsyncMock
        ) as mock_run:
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
        mock_run.assert_awaited_once()
        assert mock_run.await_args.kwargs["online_url"] == "https://example.com/repo"
        assert mock_run.await_args.kwargs["access_type"] == "restricted"
        assert "Conversion complete!" in result.stdout


class TestStandardizeSubcommand:
    """Standardization moved to bids-repair, without Neurobagel automation."""

    def test_cli_has_standardize_subcommand(self):
        """BIDS-only standardization is no longer registered on npdb."""
        result = runner.invoke(npdb, ["--help"])
        assert result.exit_code == 0
        assert "standardize" not in result.stdout

    def test_standardize_help(self):
        """Standalone standardization is listed in bids-repair help."""
        result = RepairRunner().invoke(repair_app, ["--help"])
        assert result.exit_code == 0
        assert "standardize" in result.stdout

    def test_standardize_bids_help(self):
        """Standalone standardization accepts a dataset directory."""
        result = RepairRunner().invoke(repair_app, ["standardize", "--help"])
        assert result.exit_code == 0
        assert "DATASET" in result.stdout

    def test_standardize_bids_dry_run_flag(self):
        """Test that --dry-run flag is available."""
        result = RepairRunner().invoke(repair_app, ["standardize", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.stdout

    def test_standardize_bids_keep_annotations_flag(self):
        """Neurobagel annotations are not a standalone BIDS option."""
        result = RepairRunner().invoke(repair_app, ["standardize", "--help"])
        assert result.exit_code == 0
        assert "--keep-annotations" not in result.stdout

    def test_standardize_bids_missing_dir(self):
        """Test error when BIDS dir doesn't exist."""
        result = RepairRunner().invoke(repair_app, ["standardize", "/nonexistent/path"])
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
        from npdb.annotators import NeurobagelAnnotator

        assert NeurobagelAnnotator is not None
        assert AnnotationConfig is not None

        # Can create instances
        config = AnnotationConfig(mode="manual")
        assert config.mode == "manual"

        provider = NeurobagelAnnotator(config)
        assert provider.config.mode == "manual"
