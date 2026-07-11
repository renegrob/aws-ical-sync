"""
Example configuration file for aws-ical-sync.

Copy this file to sync_configs.py and customize it with your iCal feeds.
The sync_configs.py file will be bundled with the Lambda deployment.
"""

CONFIGS = [
    {
        # URL of the iCal feed to sync
        "ical_url": "https://example.com/calendar.ics",

        # Google Calendar ID (use "primary" for your main calendar,
        # or the calendar ID for a secondary calendar)
        "calendar_id": "primary",

        # Prefix to namespace UIDs - must be unique per feed to prevent
        # conflicts between different feeds
        "uid_prefix": "example1-",

        # Optional: Format string for event titles. Use {summary} as placeholder.
        # Examples: "{summary}", "📅 {summary}", "{summary} (work)", "EHC 🏒 {summary}"
        "summary_format": "{summary}",

        # Optional: Google Calendar color ID (1-11). See lambda_function.py COLOR_REFERENCE
        # for the mapping of IDs to color names.
        # "color_id": "11",
    },
    # Add more feeds as needed:
    # {
    #     "ical_url": "https://another-example.com/calendar.ics",
    #     "calendar_id": "your.email@gmail.com",
    #     "uid_prefix": "example2-",
    #     "summary_format": "🎉 {summary}",
    #     "color_id": "5",
    # },
]
