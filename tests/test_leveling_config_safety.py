import asyncio
import unittest

from cogs.leveling import Leveling


class FakeBot:
    pass


class FakeGuild:
    id = 42


class FakeContext:
    guild = FakeGuild()

    async def send(self, *args, **kwargs):
        return None


class LevelingConfigSafetyTests(unittest.TestCase):
    def test_mirrored_keys_include_enabled_and_xp_cooldown(self):
        from cogs import leveling

        self.assertIn("enabled", leveling.MIRRORED_KEYS)
        self.assertIn("xp_cooldown", leveling.MIRRORED_KEYS)

    def test_get_guild_config_preserves_defaults_without_erasing_legacy_values(self):
        cog = object.__new__(Leveling)
        cog.store = type("Store", (), {"get_module": lambda self, *_: {}})()
        cog._db_lock = asyncio.Lock()

        async def run_check():
            config = await cog._get_guild_config(42)
            self.assertIn("enabled", config)
            self.assertIn("xp_cooldown", config)
            self.assertIn("level_up_message", config)

        asyncio.run(run_check())


if __name__ == "__main__":
    unittest.main()
