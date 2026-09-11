import tempfile
import unittest
from pathlib import Path

from database import ModerationActionStore


class ModerationActionStoreTests(unittest.TestCase):
    def test_action_is_scoped_and_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "test.db")
            store = ModerationActionStore(database_path)
            action_id = store.create(10, "ban", 20, 30)
            store.set_log_message(action_id, 40)

            action = store.get_by_log_message(40)
            self.assertEqual(action["guild_id"], 10)
            self.assertEqual(action["target_id"], 20)
            self.assertTrue(store.claim(action_id))
            self.assertFalse(store.claim(action_id))

            store.complete(action_id, 50)
            self.assertEqual(
                store.get_by_log_message(40)["status"],
                "undone",
            )


if __name__ == "__main__":
    unittest.main()