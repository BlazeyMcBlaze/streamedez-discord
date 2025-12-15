"""
Config file for streamedez-discord. Add your own discord webhooks, if you want all sports no other changes are needed. You can run discord_create_webhooks.py to create all the discord channels and webhooks.
Just create a discord bot and add to your server, additional information in that file.
"""

# ==========================
# 1. API CONFIGURATION
# ==========================
MAIN_API_CONFIG = {
    "public_api_url": "https://streamed.pk/api",
    "data_ttl": 3600, #Time between API checks
}

RATE_LIMIT_CONFIG = {
    "max_locks": 1000,
}

# ==========================
# 2. FILE PATHS
# ==========================
FILE_PATHS = {
    "matches_file": "data/matches.json",
    "streams_file": "data/streams.json",
    "images_dir": "data/images",
    "message_log_file": "data/message_audit.log"
}

# ==========================
# 3. STREAM SETTINGS
# ==========================
STREAM_CONFIG = {
    "resolve_concurrency": 10, #Number of API calls to make at once
    "resolve_window_seconds": 3600,
    
    # Image Settings
    "image_retention_seconds": 21600, # 6 hours
    
    # Extraction Logic
    "min_viewers_to_extract": 1, #If the source does not have this many viewers it will be ignored, unless all sources for the stream are below this
    "timezone_offset_correction": 0, #Use if your discord messages always seem to be off by hours
    
    # Priority Source Handling (for Date-0 matches and notifications)
    "priority_providers": ["admin"],
    "priority_stream_index": 1,
}
#Leave alone
SOURCE_ENDPOINT_MAP = {
    "admin": "admin", "alpha": "alpha", "bravo": "bravo", "charlie": "charlie",
    "delta": "delta", "echo": "echo", "foxtrot": "foxtrot", "golf": "golf",
    "hotel": "hotel", "intel": "intel",
}

# Set to false to lower number of streams, suggest all set to true
ENABLED_PROVIDERS = {
    "admin": True, "alpha": True,
    "bravo": True, "charlie": True, "delta": True,
    "echo": True, "foxtrot": True, "golf": True,
    "hotel": True, "intel": True,
}

# ==========================
# 4. FILTERING & NOTIFICATIONS
# ==========================
TARGET_SPORTS = [
    "basketball", "football", "american-football", "hockey", "baseball",
    "motor-sports", "fight", "tennis", "rugby", "golf", "billiards",
    "afl", "darts", "cricket", "other"
]

NOTIFICATION_CONFIG = {
    "enabled": True,

    # Logic Flags
    "notify_upcoming_matches": True, #Pre match warning

    
    # Time Settings
    "notify_minutes_ahead": 30,
    "notify_lookback_seconds": 0,

    # -----------------------------------------------------------
    # WEBHOOKS (User to Provide)  "discord_schedule_webhook_url" acts as fallback for sports with no matches.
    # -----------------------------------------------------------
    "discord_webhook_url": "",
    "discord_schedule_webhook_url": "",

    "sport_webhooks": {
        "american-football": "",
        "racing": "",
        "basketball": "",
        "football": "",
        "hockey": "",
        "baseball": "",
        "fight": "",
        "tennis": "",
        "rugby": "",
        "golf": "",
        "billiards": "",
        "afl": "",
        "darts": "",
        "cricket": "",
        "other": "",
    },
}

# ==========================
# 5. SCHEDULER
# ==========================
SCHEDULER_CONFIG = {
    # Interval in minutes
    "list_update_interval_minutes": 30,
    "notification_cycle_interval_minutes": 5,
    "game_time_alert_interval_minutes": 1,
    "live_update_interval_minutes": 2,
    "image_cleanup_interval_minutes": 60,
    "schedule_refresh_interval_minutes": 240,
    
    # Lookahead settings
    "schedule_lookahead_hours": 12
}
