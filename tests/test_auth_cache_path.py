import os
import pathlib
from unittest.mock import patch


def test_cache_path_defaults_to_home_directory():
    """Without env var, cache path should be ~/.microsoft_mcp_token_cache.json"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MICROSOFT_MCP_TOKEN_CACHE", None)
        import importlib
        from microsoft_mcp import auth

        importlib.reload(auth)
        expected = pathlib.Path.home() / ".microsoft_mcp_token_cache.json"
        assert auth.CACHE_FILE == expected


def test_cache_path_from_env_var():
    """MICROSOFT_MCP_TOKEN_CACHE env var should override default path"""
    custom_path = "/data/token_cache.json"
    with patch.dict(os.environ, {"MICROSOFT_MCP_TOKEN_CACHE": custom_path}):
        import importlib
        from microsoft_mcp import auth

        importlib.reload(auth)
        assert auth.CACHE_FILE == pathlib.Path(custom_path)
