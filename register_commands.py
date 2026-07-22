"""
Register (or update) super_bot's slash commands with Discord.

Interactive (default): prompts for any missing values.
    python register_commands.py

Headless / CI: set env vars and it won't prompt.
    DISCORD_APPLICATION_ID=123 DISCORD_BOT_TOKEN=xyz python register_commands.py
    DISCORD_APPLICATION_ID=123 DISCORD_BOT_TOKEN=xyz DISCORD_GUILD_ID=456 python register_commands.py

Fetches the available sets from ml-runner so the /gpt `set` option shows a
dropdown of real sets (not free-text). Re-run whenever the set list changes
or after adding a new set to ml-runner.

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
DEFAULT_ML_RUNNER_URL = "http://10.0.0.163:7070"


def _build_commands(set_choices):
    """Build the command definitions, embedding the fetched set choices."""
    return [
        {
            "name": "gpt",
            "description": "Generate text from a GPT-2 fine-tuned set.",
            "options": [
                {
                    "name": "set",
                    "description": "The trained set to generate from.",
                    "type": 3,  # STRING
                    "required": True,
                    "choices": set_choices,
                },
                {
                    "name": "prefix",
                    "description": "Text to seed the generation with.",
                    "type": 3,  # STRING
                    "required": False,
                },
                {
                    "name": "count",
                    "description": "How many generations to produce and post.",
                    "type": 4,  # INTEGER
                    "required": False,
                    "choices": [
                        {"name": "{}".format(n), "value": n}
                        for n in range(1, 11)
                    ],
                },
            ],
        },
        {
            "name": "gpt-sets",
            "description": "List the available trained GPT-2 sets.",
        },
    ]


def _fetch_set_choices(ml_runner_url):
    """Fetch sets from ml-runner /sets and build Discord choice objects."""
    try:
        r = requests.get(f"{ml_runner_url}/sets", timeout=5)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        sys.exit("Could not fetch sets from ml-runner ({}): {}".format(
            ml_runner_url, e))

    sets = r.json().get("sets") or []
    if not sets:
        sys.exit("ml-runner returned no sets — nothing to register.")

    choices = []
    for s in sets:
        name = s.get("name")
        if not name:
            continue
        # Use the description as the display name if available, else the
        # set name itself. The value is always the set name (what super_bot
        # passes to ml-runner /generate).
        label = s.get("description") or name
        # Discord limits choice names to 100 chars.
        if len(label) > 100:
            label = label[:100]
        choices.append({"name": label, "value": name})

    if not choices:
        sys.exit("No valid sets found in ml-runner response.")
    return choices


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

    # ml-runner URL: fetch the set list so /gpt shows a dropdown.
    ml_runner_url = os.environ.get("ML_RUNNER_URL") or _ask(
        "ml-runner URL (to fetch the set list)", DEFAULT_ML_RUNNER_URL)

    print("Fetching sets from {}...".format(ml_runner_url))
    set_choices = _fetch_set_choices(ml_runner_url)
    print("Found {} set(s): {}".format(
        len(set_choices), ", ".join(c["value"] for c in set_choices)))

    commands = _build_commands(set_choices)

    headers = {"Authorization": "Bot {}".format(bot_token)}
    if guild_id:
        url = ("https://discord.com/api/applications/{}/guilds/{}/commands"
               .format(app_id, guild_id))
        scope = "guild {}".format(guild_id)
    else:
        url = "https://discord.com/api/applications/{}/commands".format(app_id)
        scope = "global (may take up to 1 hour to appear)"

    r = requests.put(url, headers=headers, json=commands, timeout=10)
    if not r.ok:
        sys.exit("Failed to register commands ({}): {}".format(r.status_code, r.text))
    print("Registered {} command(s) to {}:".format(len(commands), scope))
    for c in commands:
        print("  /{} — {}".format(c["name"], c["description"]))


if __name__ == "__main__":
    main()
