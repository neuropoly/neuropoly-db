import pytest

from npdb.annotation import AnnotationConfig
from npdb.managers.neurobagel import NeurobagelAnnotator


class TestAnnotationConfig:
    """Tests for AnnotationConfig validation."""

    def test_config_default_manual_mode(self):
        """Test default config uses manual mode."""
        config = AnnotationConfig()
        assert config.mode == "manual"
        assert config.headless is True
        assert config.timeout == 300

    def test_config_custom_mode(self):
        """Test config with custom mode."""
        config = AnnotationConfig(mode="auto", timeout=600)
        assert config.mode == "auto"
        assert config.timeout == 600

    def test_config_with_ai_provider(self):
        """Test config with AI provider settings."""
        config = AnnotationConfig(
            mode="assist",
            ai_provider="ollama",
            ai_model="neural-chat"
        )
        assert config.ai_provider == "ollama"
        assert config.ai_model == "neural-chat"


class TestAnnotationManager:
    """Tests for AnnotationManager initialization and routing."""

    def test_manager_init_manual_mode(self):
        """Test manager initializes in manual mode."""
        config = AnnotationConfig(mode="manual")
        manager = NeurobagelAnnotator(config)
        assert manager.config.mode == "manual"

    def test_manager_rejects_ai_in_manual_mode(self):
        """Test that AI config is rejected in manual mode."""
        config = AnnotationConfig(
            mode="manual",
            ai_provider="ollama"
        )
        with pytest.raises(ValueError):
            NeurobagelAnnotator(config)

    def test_manager_init_assist_mode(self):
        """Test manager initializes in assist mode."""
        config = AnnotationConfig(mode="assist")
        manager = NeurobagelAnnotator(config)
        assert manager.config.mode == "assist"

    def test_manager_init_auto_mode(self):
        """Test manager initializes in auto mode."""
        config = AnnotationConfig(mode="auto")
        manager = NeurobagelAnnotator(config)
        assert manager.config.mode == "auto"

    def test_manager_init_full_auto_mode(self):
        """Test manager initializes in full-auto mode."""
        config = AnnotationConfig(mode="full-auto")
        manager = NeurobagelAnnotator(config)
        assert manager.config.mode == "full-auto"
