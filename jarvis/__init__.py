"""Jarvis: voice interface to Claude Code tmux sessions."""


def main() -> None:
    """Console entry point (``uv run jarvis``): start the hook/telephony server."""
    from jarvis.server import main as server_main

    server_main()
