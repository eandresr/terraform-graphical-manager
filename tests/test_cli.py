"""
Tests for app/cli.py — 'tgm' CLI entry point.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.cli import _build_parser, main


class TestParserStructure:
    def test_tgm_help_exits_cleanly(self):
        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args(["--help"])
        assert exc.value.code == 0

    def test_no_subcommand_exits_with_error(self):
        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args([])
        assert exc.value.code != 0

    def test_unknown_subcommand_exits_with_error(self):
        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args(["stop"])
        assert exc.value.code != 0

    def test_start_subcommand_exists(self):
        args = _build_parser().parse_args(["start"])
        assert args.command == "start"

    def test_start_help_exits_cleanly(self):
        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args(["start", "--help"])
        assert exc.value.code == 0


class TestStartDefaults:
    def test_default_port_is_5005(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PORT", None)
            args = _build_parser().parse_args(["start"])
        assert args.port == 5005

    def test_default_host_is_all_interfaces(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HOST", None)
            args = _build_parser().parse_args(["start"])
        assert args.host == "0.0.0.0"

    def test_debug_default_is_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEBUG", None)
            args = _build_parser().parse_args(["start"])
        assert args.debug is False


class TestStartArguments:
    def test_custom_port(self):
        args = _build_parser().parse_args(["start", "--port", "8080"])
        assert args.port == 8080

    def test_custom_host(self):
        args = _build_parser().parse_args(["start", "--host", "127.0.0.1"])
        assert args.host == "127.0.0.1"

    def test_debug_flag(self):
        args = _build_parser().parse_args(["start", "--debug"])
        assert args.debug is True

    def test_all_flags_combined(self):
        args = _build_parser().parse_args(
            ["start", "--port", "9000", "--host", "127.0.0.1", "--debug"]
        )
        assert args.port == 9000
        assert args.host == "127.0.0.1"
        assert args.debug is True

    def test_port_must_be_integer(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["start", "--port", "notanumber"])


class TestStartEnvVars:
    def test_port_from_env(self):
        with patch.dict(os.environ, {"PORT": "7777"}):
            # Rebuild parser inside the env context so default is re-evaluated
            from importlib import reload
            import app.cli as cli_module
            reload(cli_module)
            args = cli_module._build_parser().parse_args(["start"])
        assert args.port == 7777

    def test_debug_from_env(self):
        with patch.dict(os.environ, {"DEBUG": "true"}):
            from importlib import reload
            import app.cli as cli_module
            reload(cli_module)
            args = cli_module._build_parser().parse_args(["start"])
        assert args.debug is True

    def test_host_from_env(self):
        with patch.dict(os.environ, {"HOST": "192.168.1.1"}):
            from importlib import reload
            import app.cli as cli_module
            reload(cli_module)
            args = cli_module._build_parser().parse_args(["start"])
        assert args.host == "192.168.1.1"


class TestMainFunction:
    def test_main_start_calls_socketio_run(self):
        mock_socketio = MagicMock()
        mock_app = MagicMock()

        with patch("app.cli._cmd_start") as mock_cmd:
            main(["start", "--port", "6000"])
            mock_cmd.assert_called_once()
            call_args = mock_cmd.call_args[0][0]
            assert call_args.port == 6000
            assert call_args.command == "start"

    def test_main_passes_host(self):
        with patch("app.cli._cmd_start") as mock_cmd:
            main(["start", "--host", "127.0.0.1"])
            args = mock_cmd.call_args[0][0]
            assert args.host == "127.0.0.1"

    def test_main_passes_debug(self):
        with patch("app.cli._cmd_start") as mock_cmd:
            main(["start", "--debug"])
            args = mock_cmd.call_args[0][0]
            assert args.debug is True

    def test_main_no_args_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_cmd_start_launches_server(self):
        """_cmd_start creates the app and calls socketio.run with correct params."""
        import argparse
        from app.cli import _cmd_start

        mock_socketio = MagicMock()
        mock_app = MagicMock()
        args = argparse.Namespace(
            port=5500, host="0.0.0.0", debug=False, func=_cmd_start
        )

        with patch("app.app.create_app", return_value=mock_app), \
             patch("app.app.socketio", mock_socketio):
            _cmd_start(args)

        mock_socketio.run.assert_called_once_with(
            mock_app,
            host="0.0.0.0",
            port=5500,
            debug=False,
            allow_unsafe_werkzeug=True,
        )
