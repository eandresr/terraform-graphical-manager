"""
Tests for run.py — CLI argument parsing.
"""
import os
from unittest.mock import patch

import pytest


class TestArgumentParser:
    def _parser(self):
        # Import here so sys.path manipulation in run.py has happened
        import run
        return run._build_parser()

    def test_default_port_is_5005(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PORT", None)
            args = self._parser().parse_args([])
        assert args.port == 5005

    def test_custom_port_via_flag(self):
        args = self._parser().parse_args(["--port", "8080"])
        assert args.port == 8080

    def test_port_from_env(self):
        with patch.dict(os.environ, {"PORT": "9000"}):
            # Re-import to pick up env at parse time
            import importlib
            import run as run_module
            importlib.reload(run_module)
            args = run_module._build_parser().parse_args([])
        assert args.port == 9000

    def test_debug_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEBUG", None)
            args = self._parser().parse_args([])
        assert args.debug is False

    def test_debug_flag_enables(self):
        args = self._parser().parse_args(["--debug"])
        assert args.debug is True

    def test_debug_from_env(self):
        with patch.dict(os.environ, {"DEBUG": "true"}):
            import importlib
            import run as run_module
            importlib.reload(run_module)
            args = run_module._build_parser().parse_args([])
        assert args.debug is True

    def test_port_must_be_integer(self):
        import argparse
        with pytest.raises(SystemExit):
            self._parser().parse_args(["--port", "notanumber"])


class TestMainFunction:
    def test_main_calls_socketio_run_with_correct_port(self):
        from unittest.mock import MagicMock, patch
        import run

        mock_run = MagicMock()
        with patch.object(run.socketio, "run", mock_run):
            run.main(["--port", "7777"])

        _, kwargs = mock_run.call_args
        assert kwargs["port"] == 7777

    def test_main_default_port(self):
        from unittest.mock import MagicMock, patch
        import run

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PORT", None)
            mock_run = MagicMock()
            with patch.object(run.socketio, "run", mock_run):
                run.main([])

        _, kwargs = mock_run.call_args
        assert kwargs["port"] == 5005
