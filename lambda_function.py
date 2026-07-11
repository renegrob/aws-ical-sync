"""
Syncs events from a public ical feed into a Google Calendar.

Uses Google Calendar's events.import() method, which matches on iCalUID.
This means Google itself handles "insert if new / update if it already
exists" - no separate database is needed to track what's been synced.

Deletion of events that disappeared from the source feed is handled by
tagging every event we create with a private extendedProperty, then
diffing the set of UIDs currently on the calendar against the UIDs
currently in the feed.
"""

import json
import os
import urllib.request

import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build
from icalendar import Calendar

SSM_PARAM_NAME = os.environ["SERVICE_ACCOUNT_PARAM"]
SOURCE_TAG = "myicehockey-sync"


def get_service_account_info():
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=SSM_PARAM_NAME, WithDecryption=True)
    return json.loads(resp["Parameter"]["Value"])


def get_calendar_service():
    info = get_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_ical(ical_url: str):
    req = urllib.request.Request(ical_url, headers={"User-Agent": "hockey-sync-lambda"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return Calendar.from_ical(data)


def normalize_uid(raw_uid: str, uid_prefix: str) -> str:
    # Namespace the UID so it can't collide with anything else on the calendar,
    # and so it's safe/valid regardless of what the source feed puts in there.
    safe = "".join(c for c in raw_uid if c.isalnum() or c in "-_@.")
    return f"{uid_prefix}{safe}"


def event_to_google_body(component, uid: str) -> dict:
    summary = str(component.get("summary", "Hockey Event"))
    location = component.get("location")
    description = component.get("description")

    dtstart = component.get("dtstart").dt
    dtend_field = component.get("dtend")
    dtend = dtend_field.dt if dtend_field else dtstart

    body = {
        "iCalUID": uid,
        "summary": summary,
        "status": "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
    }
    if location:
        body["location"] = str(location)
    if description:
        body["description"] = str(description)

    # All-day (date only) vs timed (datetime) events need different fields
    if hasattr(dtstart, "hour"):
        body["start"] = {"dateTime": dtstart.isoformat()}
        body["end"] = {"dateTime": dtend.isoformat()}
    else:
        body["start"] = {"date": dtstart.isoformat()}
        body["end"] = {"date": dtend.isoformat()}

    return body


def list_existing_synced_events(service, calendar_id: str, uid_prefix: str) -> dict:
    """Return {iCalUID: google_event_id} for events previously synced by us matching the prefix."""
    existing = {}
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty=f"source={SOURCE_TAG}",
                pageToken=page_token,
                maxResults=250,
                showDeleted=False,
            )
            .execute()
        )
        for ev in resp.get("items", []):
            uid = ev.get("iCalUID")
            if uid and uid.startswith(uid_prefix):
                existing[uid] = ev["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return existing


def sync_feed(service, ical_url: str, calendar_id: str, uid_prefix: str) -> dict:
    cal = fetch_ical(ical_url)

    current_uids = set()
    imported = 0

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        raw_uid = str(component.get("uid"))
        uid = normalize_uid(raw_uid, uid_prefix)
        current_uids.add(uid)

        body = event_to_google_body(component, uid)
        service.events().import_(calendarId=calendar_id, body=body).execute()
        imported += 1

    existing = list_existing_synced_events(service, calendar_id, uid_prefix)
    deleted = 0
    for uid, event_id in existing.items():
        if uid not in current_uids:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            deleted += 1

    return {"imported": imported, "deleted": deleted, "total_in_feed": len(current_uids)}


def handler(event, context):
    service = get_calendar_service()

    # Parse configurations
    configs = []
    sync_configs_str = os.environ.get("SYNC_CONFIGS")
    if sync_configs_str:
        try:
            configs = json.loads(sync_configs_str)
        except Exception as e:
            print(f"Error parsing SYNC_CONFIGS JSON: {e}")
            raise e
    else:
        fallback_url = os.environ.get("ICAL_URL")
        fallback_cal = os.environ.get("GOOGLE_CALENDAR_ID")
        fallback_prefix = os.environ.get("UID_PREFIX", "myicehockey-")
        if fallback_url and fallback_cal:
            configs = [{
                "ical_url": fallback_url,
                "calendar_id": fallback_cal,
                "uid_prefix": fallback_prefix
            }]
        else:
            raise ValueError("No sync configuration found. Set SYNC_CONFIGS or (ICAL_URL and GOOGLE_CALENDAR_ID).")

    results = []
    overall_success = True
    for idx, config in enumerate(configs):
        ical_url = config.get("ical_url")
        calendar_id = config.get("calendar_id")
        uid_prefix = config.get("uid_prefix", "myicehockey-")

        if not ical_url or not calendar_id:
            print(f"Config at index {idx} is missing ical_url or calendar_id: {config}")
            results.append({"config_index": idx, "status": "failed", "error": "Missing parameters"})
            overall_success = False
            continue

        print(f"Syncing feed {ical_url} -> calendar {calendar_id} (prefix: {uid_prefix})")
        try:
            res = sync_feed(service, ical_url, calendar_id, uid_prefix)
            res["config_index"] = idx
            res["status"] = "success"
            print(f"Success syncing feed {ical_url}: {res}")
            results.append(res)
        except Exception as e:
            print(f"Failed to sync feed {ical_url}: {e}")
            results.append({
                "config_index": idx,
                "status": "failed",
                "error": str(e)
            })
            overall_success = False

    print(json.dumps({"overall_success": overall_success, "results": results}))
    if not overall_success:
        raise RuntimeError("One or more feeds failed to sync.")
    return results

