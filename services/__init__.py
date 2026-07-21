"""Service modules for super_bot.

Each module backs a set of routes / Discord commands and owns its own
client logic for a backing microservice. `app.py` is a thin router that
delegates to these modules.
"""