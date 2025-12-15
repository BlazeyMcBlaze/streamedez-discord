"""
Main Worker Script for Sports Streaming Monitor.
Runs as a background process to fetch data, process matches, and send Discord notifications.
"""
import asyncio
import logging
import signal
import sys
import json
import os
from datetime import datetime, timezone
from collections import defaultdict
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configuration
from config import (
    FILE_PATHS,
    STREAM_CONFIG,
    TARGET_SPORTS,
    NOTIFICATION_CONFIG,
    SCHEDULER_CONFIG
)

# Core Logic Modules
from data_manager import DataManager
from notifications import notifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SportsWorker")

# Initialize Data Manager
data_manager = DataManager()

# ==============================================================================
# LOGIC MONITORS
# ==============================================================================

class NotificationMonitor:
    def __init__(self, dm: DataManager):
        self.dm = dm
        self.lookahead_minutes = NOTIFICATION_CONFIG.get("notify_minutes_ahead", 30)
        self.schedule_log_file = "data/schedule_log.json"
        self.schedule_log = self._load_schedule_log()

    def _load_schedule_log(self):
        try:
            if os.path.exists(self.schedule_log_file):
                with open(self.schedule_log_file, 'r') as f: return json.load(f)
        except Exception: pass
        return []

    def _save_schedule_log(self):
        try:
            with open(self.schedule_log_file, 'w') as f: json.dump(self.schedule_log, f)
        except Exception as e: logger.error(f"Failed to save schedule log: {e}")

    def _get_utc_now(self):
        return datetime.now(timezone.utc).timestamp()

    async def _prepare_match_assets(self, match, force_refresh=False):
        """Resolves streams and downloads poster if needed."""
        match_id = match.get("id")
        if force_refresh:
            await self.dm.resolve_sources_for_match(match_id)
        
        poster_path = None
        if match.get("poster"):
            poster_path = await self.dm.ensure_poster_image(match_id, match.get("poster"))
            
        return poster_path

    # -------------------------
    # Cycle: Schedule
    # -------------------------
    async def run_schedule_cycle(self):
        logger.info("📅 Starting Full Schedule Cycle...")
        
        if not self.dm.matches_data: await self.dm.load_data()
        
        # Group existing Discord messages by sport to allow editing them
        existing_by_sport = defaultdict(list)
        for log in self.schedule_log:
            if 'sport' in log: existing_by_sport[log['sport']].append(log)

        current = self._get_utc_now()
        lookahead = SCHEDULER_CONFIG.get("schedule_lookahead_hours", 12) * 3600
        target_end = current + lookahead

        # Group valid matches by sport
        sports_matches = defaultdict(list)
        for m in self.dm.matches_data.get("matches", []):
            cat = m.get("category", "").lower()
            if TARGET_SPORTS and cat not in TARGET_SPORTS: continue
            
            md = m.get("date", 0)
            if md > 100_000_000_000: md //= 1000
            
            if current <= md <= target_end:
                sports_matches[cat].append(m)

        # Send/Update messages
        new_logs = []
        for sport, matches in sports_matches.items():
            matches.sort(key=lambda x: x.get("date", 0))
            logger.info(f"  -> Schedule: {sport.title()} ({len(matches)} matches)")
            
            msgs = await notifier.post_sport_schedule(sport, matches, existing_msgs=existing_by_sport.get(sport, []))
            
            for m in msgs: m['sport'] = sport
            new_logs.extend(msgs)
            
            if sport in existing_by_sport: del existing_by_sport[sport]

        # Cleanup sports that no longer have matches
        for sport, msgs in existing_by_sport.items():
            logger.info(f"🗑️ Cleaning up schedule for {sport}")
            for msg in msgs:
                await notifier.delete_message(msg['url'], msg['id'], "SCHED_CLEAN")

        self.schedule_log = new_logs
        self._save_schedule_log()
        logger.info("✅ Schedule Cycle Complete")

    # -------------------------
    # Cycle: Pre-Match Notifications
    # -------------------------
    async def run_notification_cycle(self):
        if not NOTIFICATION_CONFIG.get("notify_upcoming_matches"): return
        if not self.dm.matches_data: await self.dm.load_data()
        await self.dm.load_data() # Ensure streams are loaded

        current = self._get_utc_now()
        target = current + (self.lookahead_minutes * 60)
        lookback = NOTIFICATION_CONFIG.get("notify_lookback_seconds", 1800)

        for match in self.dm.matches_data.get("matches", []):
            mid = str(match.get("id"))
            cat = match.get("category", "").lower()
            if TARGET_SPORTS and cat not in TARGET_SPORTS: continue

            md = match.get("date", 0)
            if md > 100_000_000_000: md //= 1000

            # If match is starting soon
            if current - lookback <= md <= target:
                await self._handle_pre_match_alert(match, mid)

    async def _handle_pre_match_alert(self, match, match_id):
        stream_entry = self._get_stream_entry(match_id)
        notif = stream_entry.get("notifications", {}) if stream_entry else {}
        
        if notif.get("pre_match_id"): return # Already sent

        poster_path = await self._prepare_match_assets(match, force_refresh=False)
        
        # Reload streams after potential refresh
        await self.dm.load_data()
        stream_entry = self._get_stream_entry(match_id)
        streams = stream_entry.get("sources", []) if stream_entry else []

        resp = await notifier.post_match_alert(match, streams, "upcoming", file_path=poster_path)
        if resp:
            await self._update_notification_state(match_id, "pre_match_id", resp["id"], resp["url"])
            logger.info(f"📢 Sent Pre-Match Warning for {match_id}")

    # -------------------------
    # Cycle: Game Time & Live Updates
    # -------------------------
    async def check_game_time_alerts(self):
        if not NOTIFICATION_CONFIG.get("notify_upcoming_matches"): return
        if not self.dm.matches_data: await self.dm.load_data()
        
        # Ensure we have the latest stream status (especially for date 0 checks)
        if not self.dm.streams_data: await self.dm.load_data()

        current = self._get_utc_now()
        
        for match in self.dm.matches_data.get("matches", []):
            mid = str(match.get("id"))
            cat = match.get("category", "").lower()
            if TARGET_SPORTS and cat not in TARGET_SPORTS: continue

            md = match.get("date", 0)
            if md > 100_000_000_000: md //= 1000

            # Check standard notification window (10s before to 130s after start)
            is_time_trigger = (current - 10) <= md <= (current + 130)

            # Check for active Date-0 matches enabled by background resolution
            is_date_zero_trigger = False
            if md == 0:
                s_entry = self._get_stream_entry(mid)
                if s_entry and s_entry.get("enabled_source"):
                    is_date_zero_trigger = True

            if is_time_trigger or is_date_zero_trigger:
                await self._handle_game_start_alert(match, mid)

    async def _handle_game_start_alert(self, match, match_id):
        stream_entry = self._get_stream_entry(match_id)
        notif = stream_entry.get("notifications", {}) if stream_entry else {}
        
        if notif.get("game_time_id"): return # Already live

        # Delete pre-match message if exists
        pre_id = notif.get("pre_match_id")
        pre_url = notif.get("pre_match_url")
        if pre_id and pre_url:
            await notifier.delete_message(pre_url, pre_id, match_id)
            await self._update_notification_state(match_id, "pre_match_id", None, None)

        # Force refresh streams for live event
        poster_path = await self._prepare_match_assets(match, force_refresh=True)
        
        await self.dm.load_data()
        stream_entry = self._get_stream_entry(match_id)
        streams = stream_entry.get("sources", []) if stream_entry else []

        resp = await notifier.post_match_alert(match, streams, "live", file_path=poster_path)
        if resp:
            await self._update_notification_state(match_id, "game_time_id", resp["id"], resp["url"], {"last_data_hash": resp.get("last_hash")})
            logger.info(f"🚨 Sent Game Time Alert for {match_id}")

    async def update_live_notifications(self):
        """Updates the viewer counts/sources on the Live Message."""
        if not self.dm.streams_data: await self.dm.load_data()
        current = self._get_utc_now()

        for entry in self.dm.streams_data.get("streams", []):
            notif = entry.get("notifications", {})
            msg_id = notif.get("game_time_id")
            url = notif.get("game_time_url")
            
            if not msg_id or not url: continue

            match_id = entry.get("id")
            match = next((m for m in self.dm.matches_data.get("matches", []) if str(m["id"]) == str(match_id)), None)
            
            if match:
                md = match.get("date", 0)
                if md > 100_000_000_000: md /= 1000
                
                # Stop updating if match started more than 4 hours ago, unless it's a Date-0 match
                if md != 0 and (current - md > 14400): 
                    continue

                new_hash = await notifier.update_live_alert(
                    match, entry.get("sources", []), msg_id, url, last_hash=notif.get("last_data_hash")
                )

                if new_hash and new_hash != notif.get("last_data_hash"):
                    await self._update_notification_state(match_id, "game_time_id", msg_id, url, {"last_data_hash": new_hash})

    # -------------------------
    # Helpers
    # -------------------------
    def _get_stream_entry(self, match_id):
        if not self.dm.streams_data: return None
        return next((s for s in self.dm.streams_data.get("streams", []) if str(s.get("id")) == str(match_id)), None)

    async def _update_notification_state(self, match_id, key, msg_id, url, extra=None):
        if not self.dm.streams_data: await self.dm.load_data()
        for s in self.dm.streams_data.get("streams", []):
            if str(s.get("id")) == str(match_id):
                if "notifications" not in s: s["notifications"] = {}
                s["notifications"][key] = msg_id
                if url: s["notifications"][key.replace("_id", "_url")] = url
                if extra: s["notifications"].update(extra)
                await self.dm.save_streams_data()
                break

notification_monitor = NotificationMonitor(data_manager)

# ==============================================================================
# ENTRY POINT & JOBS
# ==============================================================================

async def job_wrapper(func, name):
    try: await func()
    except Exception as e: logger.error(f"Job {name} failed: {e}")

async def startup(scheduler):
    logger.info("Initializing Data...")
    await data_manager.load_data()
    if not data_manager.matches_data:
        logger.info("Fetching initial data...")
        await data_manager.fetch_and_store_data()
    
    await data_manager.cleanup_all_data()
    scheduler.start()
    logger.info("📅 Scheduler Started")

async def shutdown(signal_enum=None):
    logger.info(f"Shutting down... ({signal_enum})")
    await data_manager.close_session()
    await notifier.close()
    
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.get_event_loop().stop()

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler = AsyncIOScheduler()

    # Define jobs
    scheduler.add_job(lambda: job_wrapper(data_manager.fetch_and_store_data, "Update Lists"), "interval", minutes=SCHEDULER_CONFIG["list_update_interval_minutes"])
    scheduler.add_job(lambda: job_wrapper(notification_monitor.run_notification_cycle, "Notifs"), "interval", minutes=SCHEDULER_CONFIG["notification_cycle_interval_minutes"])
    scheduler.add_job(lambda: job_wrapper(notification_monitor.check_game_time_alerts, "Game Time"), "interval", minutes=SCHEDULER_CONFIG["game_time_alert_interval_minutes"])
    scheduler.add_job(lambda: job_wrapper(notification_monitor.update_live_notifications, "Live Updates"), "interval", minutes=SCHEDULER_CONFIG["live_update_interval_minutes"])
    scheduler.add_job(lambda: job_wrapper(data_manager.cleanup_local_images, "Image Cleanup"), "interval", minutes=SCHEDULER_CONFIG["image_cleanup_interval_minutes"])
    
    # Check Date 0 matches periodically
    scheduler.add_job(lambda: job_wrapper(data_manager.resolve_date_zero_matches, "Date Zero Check"), "interval", minutes=30)

    # Schedule Refresh
    scheduler.add_job(
        lambda: job_wrapper(notification_monitor.run_schedule_cycle, "Full Schedule"),
        "interval",
        minutes=SCHEDULER_CONFIG["schedule_refresh_interval_minutes"],
        next_run_time=datetime.now()
    )

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    try:
        loop.run_until_complete(startup(scheduler))
        logger.info("🚀 Sports Monitor Worker Started. Press Ctrl+C to exit.")
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit): pass
    finally: logger.info("Goodbye.")

if __name__ == "__main__":
    main()
