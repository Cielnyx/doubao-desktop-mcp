from pathlib import Path
import sys
import tomllib
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from configuration import codex_fragment, server_config
from register_codex import replace_service


class ConfigurationTests(unittest.TestCase):
    def test_paths_are_generated_from_supplied_project_root(self):
        root = Path.cwd() / "另一台电脑 项目's folder"
        config = server_config(root)
        self.assertEqual(Path(config["cwd"]), root.resolve())
        self.assertEqual(Path(config["command"]), root.resolve()/".venv"/"Scripts"/"python.exe")
        self.assertEqual(tomllib.loads(codex_fragment(config))["mcp_servers"]["doubao-desktop"], config)

    def test_relocation_preserves_other_servers_and_global_settings(self):
        original = '''theme = "dark"
[mcp_servers.first]
command = "first.exe"
[mcp_servers.doubao-desktop]
command = "old.exe"
args = ["-m", "doubao_mcp"]
[mcp_servers.doubao-desktop.env]
PYTHONUTF8 = "1"
[mcp_servers.doubao-desktop.tools.doubao_ask]
approval_mode = "prompt"
[mcp_servers.last]
url = "https://example.invalid/mcp"
'''
        expected = tomllib.loads(original)
        migrated = {**expected["mcp_servers"]["doubao-desktop"], **server_config()}
        expected["mcp_servers"]["doubao-desktop"] = migrated
        result = replace_service(original, codex_fragment(migrated))
        self.assertEqual(tomllib.loads(result), expected)

    def test_unknown_config_layout_is_not_modified(self):
        with self.assertRaises(ValueError):
            replace_service('mcp_servers = {}\n', codex_fragment(server_config()))


if __name__ == "__main__":
    unittest.main()
