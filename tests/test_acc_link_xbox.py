import asyncio
import unittest

from cogs.acc_link import AccountLink


class FakeChannel:
    id = 1


class FakeAuthor:
    id = 123


class FakeContext:
    guild = None
    channel = FakeChannel()
    author = FakeAuthor()

    def __init__(self):
        self.replied = None

    async def reply(self, *args, **kwargs):
        self.replied = args[0] if args else kwargs.get("content")


class AccountLinkXboxTests(unittest.TestCase):
    def test_normalize_platform_accepts_xbox(self):
        self.assertEqual(AccountLink._normalize_platform("xbox"), "xbox")

    def test_normalize_platform_accepts_spotify(self):
        self.assertEqual(AccountLink._normalize_platform("spotify"), "spotify")

    def test_spotify_profile_url_preserves_public_profile_link(self):
        url = "https://open.spotify.com/user/spotify?si=abc123"
        self.assertEqual(AccountLink._spotify_profile_url(url), url.rstrip("/"))

    def test_fetch_spotify_profile_uses_page_title_when_no_display_name_exists(self):
        html = "<title>Spotify on Spotify</title><meta property='og:title' content='Spotify'/>"
        original = AccountLink._safe_urlopen
        AccountLink._safe_urlopen = lambda url: html
        try:
            profile = AccountLink._fetch_spotify_profile("https://open.spotify.com/user/spotify")
            self.assertIsNotNone(profile)
            self.assertEqual(profile["display_name"], "Spotify on Spotify")
            self.assertNotEqual(profile["display_name"], "spotify")
        finally:
            AccountLink._safe_urlopen = original

    def test_link_xbox_replies_with_playstation_message(self):
        cog = AccountLink(bot=type("Bot", (), {})())
        ctx = FakeContext()

        async def run_link():
            await AccountLink.link_account.callback(cog, ctx, "xbox", "some-user")

        asyncio.run(run_link())
        self.assertEqual(ctx.replied, "Why not switch over to Playstation?")


if __name__ == "__main__":
    unittest.main()
