"""
Register (or update) super_bot's slash commands with Discord.

Interactive (default): prompts for any missing values.
    python register_commands.py

Headless / CI: set env vars and it won't prompt.
    DISCORD_APPLICATION_ID=123 DISCORD_BOT_TOKEN=xyz python register_commands.py
    DISCORD_APPLICATION_ID=123 DISCORD_BOT_TOKEN=xyz DISCORD_GUILD_ID=456 python register_commands.py

Registers guild commands (instant, per-server) if a guild id is provided,
otherwise global commands (propagate in up to 1 hour). Re-running is safe —
Discord bulk-overwrites the command set for the scope.
"""
import getpass
import os
import sys

import requests

# Discord app credentials for the OUT OF OFFICE server. The application id
# is public, so it's a baked-in default (overridable via env). The bot token
# is a real secret — never commit it; it's read from env or interactive prompt.
DEFAULT_APP_ID = "1434750818245939281"

# Command definitions. Keep this in sync with app.py's _dispatch_command.
COMMANDS = [
    {
        "name": "gpt",
        "description": "Generate text from a GPT-2 fine-tuned set.",
        "options": [
            {
                "name": "set",
                "description": "The trained set to generate from (e.g. trump-tweet).",
                "type": 3,  # STRING
                "required": True,
            },
            {
                "name": "prefix",
                "description": "Text to seed the generation with.",
                "type": 3,  # STRING
                "required": False,
            },
            {
                "name": "count",
                "description": "How many generations to produce and post (1-10).",
                "type": 4,  # INTEGER
                "required": False,
                "min_value": 1,
                "max_value": 10,
            },
        ],
    },
]


def _ask(prompt, default=None, secret=False):
    """Prompt for a value, with an optional default. Empty input = default."""
    if default:
        suffix = " [{}]: ".format(default)
    else:
        suffix = ": "
    if secret:
        value = getpass.getpass(prompt + suffix)
    else:
        value = input(prompt + suffix).strip()
    return value or default


def main():
    # Application ID: env, then default, then prompt.
    app_id = os.environ.get("DISCORD_APPLICATION_ID") or DEFAULT_APP_ID
    if not app_id:
        app_id = _ask("Discord Application ID")
    if not app_id:
        sys.exit("Application ID is required.")

    # Bot token: env, then prompt (secret — never echoed, never defaulted).
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    if not bot_token:
        bot_token = _ask("Discord Bot Token", secret=True)
    if not bot_token:
        sys.exit("Bot token is required.")

    # Guild ID: optional. Guild = instant; omit = global (up to 1hr).
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not guild_id:
        guild_id = _ask(
            "Discord Guild ID (blank = global, takes up to 1hr to propagate)"
        ) or None

    headers = {"Authorization": "Bot {}".format(bot_token)}
    if guild_id:
        url = ("https://discord.com/api/applications/{}/guilds/{}/commands"
               .format(app_id, guild_id))
        scope = "guild {}".format(guild_id)
    else:
        url = "https://discord.com/api/applications/{}/commands".format(app_id)
        scope = "global (may take up to 1 hour to appear)"

    r = requests.put(url, headers=headers, json=COMMANDS, timeout=10)
    if not r.ok:
        sys.exit("Failed to register commands ({}): {}".format(r.status_code, r.text))
    print("Registered {} command(s) to {}:".format(len(COMMANDS), scope))
    for c in COMMANDS:
        print("  /{} — {}".format(c["name"], c["description"]))


if __name__ == "__main__":
    main()
