# Unreleased Steam alerts draft

Rebuilt from the shared conversation on 22 October 2026. This is new code,
not a recovery of the previous session's source or its reported test results.

The draft checks Steam every 30 minutes for normally paid games and DLC
temporarily offered at 100% off, directly claimable on Steam and free to keep.
It excludes demos, free weekends, permanently free content and key giveaways.
Steam's embedded purchase data must confirm a positive original price, zero
current price, 100% discount, free-to-keep status and a future deadline.
Unknown page formats fail closed. DLC posts link the required base game.
The game's actual Steam header image appears full-width below the fields.
The URL is read from the store page, preserving Steam's asset hash, with its
social image as a fallback. If neither is available, a warning is logged and
the verified offer still posts with its text and claim link.

Steam requests retry transient connection errors, timeouts, HTTP 429 and 5xx
responses up to three attempts. Short numeric Retry-After values are respected;
long or unrecognized deadlines defer work until the next scheduled scan.
Malformed search results and missing embedded item data are logged and skipped.
Failure to look up a DLC's base-game name falls back to its verified app link.
Missing Discord permissions skip that channel. Unexpected failures use Observer's
shared error handler and OBS error IDs, with guild/channel/app or message context.
An error affecting one offer or guild does not stop the scheduler.

After a successful Discord send, failed history writes are kept in memory and
retried without sending another message. Pending writes are retried at the start
of every scan, including when an offer has since expired. Discord sends are not
blindly retried after HTTP errors; discord.py manages Discord rate limits.

The draft is absent from bot.py's extension list, help, README and setup UI.
Its RELEASED constant is False; attempting to load it raises before adding a
cog, opening a session or creating tables. No bot token is needed for tests.

At release, integrate the enabled/channel/country settings into the existing
setup UI, enable the release flag and add the extension.
Configuration is stored under steam_free; enabled must be the JSON boolean
true, channel a text channel ID, and country a two-letter Steam region code.
Default region is Slovenia (si). No new configuration commands are added.

Limitations: Steam's embedded store markup is undocumented and can change.
Discovery is capped at 250 results per region per scan. Offers are verified
again before sending; polling cannot guarantee all short promotions are found.
SQLite history prevents normal repeat posts across restarts and distinguishes
later promotions by their deadline. A crash between Discord delivery and the
history write can still produce a repeat; delivery is not exactly once.
Run only one bot process for this draft. Region availability may vary.
