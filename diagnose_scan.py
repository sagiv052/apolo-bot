import json

import apollo_monitor as monitor

snapshot = monitor.login_and_capture()
print(json.dumps({
    "state": monitor.classify_state(snapshot),
    "url": snapshot.get("url"),
    "title": snapshot.get("title"),
    "event_count": len(snapshot.get("events", [])),
    "events": [
        {
            "title": event.get("title"),
            "date": event.get("date"),
            "time": event.get("time"),
            "seats": event.get("seats"),
        }
        for event in snapshot.get("events", [])
    ],
    "screenshot_exists": monitor.SCREENSHOT_FILE.exists(),
    "screenshot_bytes": monitor.SCREENSHOT_FILE.stat().st_size if monitor.SCREENSHOT_FILE.exists() else 0,
}, ensure_ascii=False, indent=2))
