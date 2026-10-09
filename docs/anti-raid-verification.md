# Verification and anti-raid setup

Restart Observer after updating and sync slash commands through the project's normal sync procedure. Existing verification panels continue to work.

## Verification

In `/setup` → Verification configure verified/unverified roles, account age, a retry cooldown (default 30 seconds), and an optional private attempt-log channel. Run `/verify check`, then `/autorole setup` to post a panel. Observer needs Manage Roles and a role above every configured role. Missing, managed, overlapping, and @everyone roles fail validation.

Verification grants access before removing the unverified role. Join autoroles no longer grant configured verified roles while verification is enabled, and bot accounts do not receive unverified roles. Other join roles must not grant access that bypasses verification. When unverified roles are configured, a member must have one to verify. Failures receive an OBS error ID. SQLite stores the latest attempt per member for cooldowns, removing records older than 30 days on subsequent attempts; the optional Discord channel holds the attempt history under the server's own retention rules.

## Automatic anti-raid

In `/setup` → Anti-Raid and Honeypot choose a private alert channel, trusted bypass role, join threshold/window, verification-hold duration, and timeout duration. Enable Automatic anti-raid when ready. Defaults: 10 eligible joins in 30 seconds, a 5-minute verification hold, and a 10-minute timeout for recent arrivals. Set timeout duration to 0 for verification holds and alerts only.

At the threshold, Observer pauses button verification and restricts eligible members from the join window. New arrivals during the hold are also restricted. The hold deadline is persisted; it expires automatically after restarts. Join-window counters restart with the process. Permissions and role hierarchy are checked before enforcement, and successful actions create moderation cases. Existing longer timeouts are preserved. Owner, bot accounts, members with administrator/manage-server/moderate-members/ban-members/manage-messages/kick-members permissions, and the bypass role are excluded.

`/raid-status` shows status; `/raid-status release:true` releases verification early and clears the join window. It does not remove individual timeouts. This feature does not alter channel overwrites or the separate Security cog's manual lockdown.

## Honeypot

Create a dedicated channel such as `do-not-post-here`. Post and pin a clear warning: **Do not send messages in this channel. Posting here triggers automatic moderation.** Give the target member roles View Channel and Send Messages so automated accounts that spam every channel can enter the trap. Do not select a normal chat, verification, ticket, relay, or announcement channel.

Select that channel in `/setup` → Anti-Raid and Honeypot and enable Honeypot. By default any non-exempt member who posts there gets a 1-hour timeout and their triggering message is deleted. Enable **Ban honeypot posters** for automatic bans instead. Bans do not purge message history. Observer needs Moderate Members or Ban Members as appropriate, Manage Messages to delete the triggering post, and access to the alert channel. Enforcement failures appear in alerts with error IDs; failed bans do not create successful moderation cases. Bot accounts and webhooks are ignored; human accounts operated by spam automation are eligible.

A honeypot reacts to posting, not to verified scam content: a human who ignores the warning can also trigger it. Both new protection toggles default off. Configure them before enabling.

## Validation

Run `python -m pytest -q`. Tests use mocked Discord operations and temporary databases; real Discord role permissions, hierarchy, rate limits, and channel visibility require a test-server check after deployment.
