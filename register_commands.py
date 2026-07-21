"""
Register (or update) super_bot's slash commands with Discord.

Usage:
    DISCORD_APPLICATION_ID=123 DISCORD_BOT_TOKEN=xyz python register_commands.py

Registers guild commands (instant, per-server) if DISCORD_GUILD_ID is set,
otherwise global commands (propagate in up to 1 hour). Re-running is safe —
Discord bulk-overwrites the command set for the scope.
"""
import json
import os
import sys

import requests

APP_ID = os.environ.get("DISCORD_APPLICATION_ID")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")  # optional; guild = instant

# Command definitions. Keep this in sync with app.py's _dispatch_command.
COMMANDS = [
    {
        "name": "gpt",
        "description": "Generate text from a GPT-2 fine-tuned set.",
        "options": [
            {
                "name": "set",
                "description": "The trained set to generate from (default: trump-tweet).",
                "type": 3,  # STRING
                "required": False,
            },
            {
                "name": "prefix",
                "description": "Text to seed the generation with.",
                "type": 3,  # STRING
                "required": False,
            },
        ],
    },
]


def main():
    missing = [v for v in ("DISCORD_APPLICATION_ID", "DISCORD_BOT_TOKEN") if not os.environ.get(v)]
    if missing:
        sys.exit("Missing env vars: {}".format(", ".join(missing)))

    headers = {"Authorization": "Bot {}".format(BOT_TOKEN)}
    if GUILD_ID:
        url = ("https://discord.com/api/applications/{}/guilds/{}/commands"
               .format(APP_ID, GUILD_ID))
        scope = "guild {}".format(GUILD_ID)
    else:
        url = "https://discord.com/api/applications/{}/commands".format(APP_ID)
        scope = "global (may take up to 1 hour to appear)"

    r = requests.put(url, headers=headers, json=COMMANDS, timeout=10)
    if not r.ok:
        sys.exit("Failed to register commands ({}): {}".format(r.status_code, r.text))
    print("Registered {} command(s) to {}:".format(len(COMMANDS), scope))
    for c in COMMANDS:
        print("  /{} — {}".format(c["name"], c["description"]))


if __name__ == "__main__":
    main()