"""Codex transport labels; original tool and Pydantic decoders remain intact."""
from copy import deepcopy
from .cli_codec import decode_cli_output  # noqa: F401: consumed by graph worker
from .native_codec import CodecError

__all__ = ['decode_cli_output', 'safe_terminal_metadata']


def safe_terminal_metadata(response):
    terminal = response.get('codex_terminal')
    if not isinstance(terminal, dict) or terminal.get('type') != 'turn.completed' or terminal.get('turn_count') != 1:
        raise CodecError('Codex requires exactly one real successful completed turn')
    return deepcopy(terminal)
