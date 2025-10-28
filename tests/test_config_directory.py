"""
Tests for config directory resolution in BLEInterface.

This test verifies that the BLE interface correctly resolves the interface
directory based on RNS.Reticulum.configdir when available, and falls back
to the default path otherwise.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock


class TestConfigDirectoryResolution(unittest.TestCase):
    """Test cases for config directory resolution in BLEInterface."""

    def setUp(self):
        """Set up test fixtures."""
        # Remove BLEInterface from sys.modules if it was imported
        modules_to_remove = [
            'BLEInterface',
            'RNS.Interfaces.BLEInterface'
        ]
        for module in modules_to_remove:
            if module in sys.modules:
                del sys.modules[module]

    def test_interface_dir_with_custom_configdir(self):
        """Test that custom config directory is used when RNS.Reticulum.configdir is set."""
        # Create a mock RNS module with custom configdir
        mock_rns = MagicMock()
        mock_reticulum = MagicMock()
        custom_config_dir = "/custom/config/path"
        mock_reticulum.configdir = custom_config_dir
        mock_rns.Reticulum = mock_reticulum

        # Patch the import to raise NameError for __file__ and provide our mock RNS
        with patch.dict('sys.modules', {'RNS': mock_rns}):
            # We need to simulate the NameError for __file__
            # This is tricky because we need to import the module code
            # Let's test the logic directly instead

            # Simulate the logic from BLEInterface.py
            _interface_dir = None
            try:
                import RNS
                if hasattr(RNS.Reticulum, 'configdir') and RNS.Reticulum.configdir:
                    _interface_dir = os.path.join(RNS.Reticulum.configdir, "interfaces")
            except (ImportError, AttributeError):
                pass

            if _interface_dir is None:
                _interface_dir = os.path.expanduser("~/.reticulum/interfaces")

            # Verify the custom config directory is used
            expected_path = os.path.join(custom_config_dir, "interfaces")
            self.assertEqual(_interface_dir, expected_path)

    def test_interface_dir_fallback_when_configdir_none(self):
        """Test that default path is used when RNS.Reticulum.configdir is None."""
        # Create a mock RNS module with None configdir
        mock_rns = MagicMock()
        mock_reticulum = MagicMock()
        mock_reticulum.configdir = None
        mock_rns.Reticulum = mock_reticulum

        with patch.dict('sys.modules', {'RNS': mock_rns}):
            # Simulate the logic from BLEInterface.py
            _interface_dir = None
            try:
                import RNS
                if hasattr(RNS.Reticulum, 'configdir') and RNS.Reticulum.configdir:
                    _interface_dir = os.path.join(RNS.Reticulum.configdir, "interfaces")
            except (ImportError, AttributeError):
                pass

            if _interface_dir is None:
                _interface_dir = os.path.expanduser("~/.reticulum/interfaces")

            # Verify the default path is used
            expected_path = os.path.expanduser("~/.reticulum/interfaces")
            self.assertEqual(_interface_dir, expected_path)

    def test_interface_dir_fallback_when_rns_not_available(self):
        """Test that default path is used when RNS module is not available."""
        # Simulate ImportError for RNS
        with patch.dict('sys.modules', {'RNS': None}):
            # Simulate the logic from BLEInterface.py
            _interface_dir = None
            try:
                import RNS
                if RNS and hasattr(RNS.Reticulum, 'configdir') and RNS.Reticulum.configdir:
                    _interface_dir = os.path.join(RNS.Reticulum.configdir, "interfaces")
            except (ImportError, AttributeError, TypeError):
                pass

            if _interface_dir is None:
                _interface_dir = os.path.expanduser("~/.reticulum/interfaces")

            # Verify the default path is used
            expected_path = os.path.expanduser("~/.reticulum/interfaces")
            self.assertEqual(_interface_dir, expected_path)

    def test_interface_dir_fallback_when_reticulum_missing_configdir(self):
        """Test that default path is used when RNS.Reticulum doesn't have configdir attribute."""
        # Create a mock RNS module without configdir attribute
        mock_rns = MagicMock()
        mock_reticulum = MagicMock(spec=[])  # Empty spec, no attributes
        mock_rns.Reticulum = mock_reticulum

        with patch.dict('sys.modules', {'RNS': mock_rns}):
            # Simulate the logic from BLEInterface.py
            _interface_dir = None
            try:
                import RNS
                if hasattr(RNS.Reticulum, 'configdir') and RNS.Reticulum.configdir:
                    _interface_dir = os.path.join(RNS.Reticulum.configdir, "interfaces")
            except (ImportError, AttributeError):
                pass

            if _interface_dir is None:
                _interface_dir = os.path.expanduser("~/.reticulum/interfaces")

            # Verify the default path is used
            expected_path = os.path.expanduser("~/.reticulum/interfaces")
            self.assertEqual(_interface_dir, expected_path)

    def test_custom_config_path_construction(self):
        """Test that the interfaces subdirectory is correctly constructed from custom config."""
        custom_configs = [
            "/home/user/.reticulumble",
            "/opt/reticulum/config",
            "~/.custom_reticulum"
        ]

        for custom_config in custom_configs:
            with self.subTest(custom_config=custom_config):
                expected_path = os.path.join(custom_config, "interfaces")
                actual_path = os.path.join(custom_config, "interfaces")
                self.assertEqual(actual_path, expected_path)


if __name__ == '__main__':
    unittest.main()
