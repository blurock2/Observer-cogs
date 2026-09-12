import json
import tempfile
import unittest
from pathlib import Path

from database import RelayConfigStore


class RelayConfigStoreTests(unittest.TestCase):
    def test_legacy_config_is_backed_up_and_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "message_relay_config.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "links": [
                            {"source_guild": 1, "target_guilds": [2]}
                        ],
                        "sources": {"1": [10]},
                        "targets": {"1": [20]},
                    }
                ),
                encoding="utf-8",
            )

            store = RelayConfigStore(str(root / "bot.db"), legacy_path)

            self.assertEqual(store.load()["links"][0]["source_guild"], 1)
            self.assertTrue(list(root.glob("message_relay_config.json.backup-*")))
            self.assertTrue((root / "message_relay_config.json.migrated").exists())

    def test_targets_are_loaded_by_target_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RelayConfigStore(str(root / "bot.db"), root / "missing.json")
            store.save(
                {
                    "links": [
                        {"source_guild": 1, "target_guilds": [2, 3]},
                    ],
                    "sources": {"1": [10]},
                    "targets": {"2": [20], "3": [30]},
                }
            )

            loaded = store.load()

            self.assertEqual(loaded["targets"], {"2": ["20"], "3": ["30"]})

    def test_legacy_source_keyed_targets_are_migrated_to_each_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "message_relay_config.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "links": [{"source_guild": 1, "target_guilds": [2, 3]}],
                        "sources": {"1": [10]},
                        "targets": {"1": [20]},
                    }
                ),
                encoding="utf-8",
            )

            loaded = RelayConfigStore(str(root / "bot.db"), legacy_path).load()

            self.assertEqual(loaded["targets"], {"2": ["20"], "3": ["20"]})


if __name__ == "__main__":
    unittest.main()