import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import apollo_monitor as monitor


class WhatsAppSubscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="whatsapp-subscriber-test-")
        self.root = Path(self.temp_dir.name)
        self.subscribers_patch = patch.object(monitor, "SUBSCRIBERS_FILE", self.root / "subscribers.json")
        self.state_patch = patch.object(monitor, "STATE_FILE", self.root / "state.json")
        self.subscribers_patch.start()
        self.state_patch.start()
        self.subscriber_chat = "972501234567@c.us"

    def tearDown(self) -> None:
        self.subscribers_patch.stop()
        self.state_patch.stop()
        self.temp_dir.cleanup()

    def test_phrase_registers_phone_and_returns_confirmation(self) -> None:
        response = monitor.handle_whatsapp_command(
            "subscribe", self.subscriber_chat, "+972 50-123-4567"
        )["messages"][0]
        self.assertIn("התקבל", response)
        self.assertIn("שמרתי", response)
        self.assertEqual(
            monitor.load_whatsapp_subscribers(),
            [{"chat_id": self.subscriber_chat, "phone": "972501234567"}],
        )
        duplicate = monitor.handle_whatsapp_command(
            "subscribe", self.subscriber_chat, "972501234567"
        )["messages"][0]
        self.assertIn("כבר רשום", duplicate)

    def test_help_lists_only_the_requested_hebrew_commands(self) -> None:
        help_text = monitor.handle_whatsapp_command("help")["messages"][0]
        for phrase in (
            "חיפוש משמרת חדשה",
            "רשימת פקודות",
            "רשימת עדכונים",
            "צילום מסך",
            "שלח לי עדכונים",
            "הפסק עדכונים",
        ):
            self.assertIn(phrase, help_text)
        self.assertNotIn("סריקה מיידית", help_text)
        self.assertNotIn("כמה סריקות", help_text)

    def test_new_event_and_time_update_are_sent_to_subscribers(self) -> None:
        event = {
            "title": "אירוע בדיקה",
            "date": "יום א 01.01.2027",
            "place": "ירושלים",
            "time": "09:00",
            "seats": 2,
            "raw": "אירוע בדיקה",
        }
        monitor.subscribe_whatsapp_sender(self.subscriber_chat, "972501234567")
        monitor.STATE_FILE.write_text(
            json.dumps({"snapshot": {"events": []}, "sent_events": {}}), encoding="utf-8"
        )
        sent: list[tuple[str, str | None]] = []
        original_baseline = monitor._startup_baseline_captured
        monitor._startup_baseline_captured = True
        try:
            with (
                patch.object(
                    monitor,
                    "login_and_capture",
                    return_value={
                        "url": monitor.EVENTS_URL + "/open-events",
                        "title": "אירועים",
                        "body_text": "",
                        "events": [dict(event)],
                    },
                ),
                patch.object(monitor, "send_whatsapp_to", side_effect=lambda text, chat_id=None: sent.append((text, chat_id))),
            ):
                result = monitor.one_scan()
                self.assertEqual(len(result["new_events"]), 1)
                self.assertTrue(any(chat_id == self.subscriber_chat and "אירוע בדיקה" in text for text, chat_id in sent))

                sent.clear()
                changed_event = dict(event, time="10:00")
                with patch.object(
                    monitor,
                    "login_and_capture",
                    return_value={
                        "url": monitor.EVENTS_URL + "/open-events",
                        "title": "אירועים",
                        "body_text": "",
                        "events": [changed_event],
                    },
                ):
                    result = monitor.one_scan()
                self.assertEqual(len(result["updated_events"]), 1)
                self.assertTrue(
                    any(
                        chat_id == self.subscriber_chat and "עדכון לאירוע קיים" in text
                        for text, chat_id in sent
                    )
                )
        finally:
            monitor._startup_baseline_captured = original_baseline

    def test_unsubscribe_removes_subscriber_and_group_chat_is_rejected(self) -> None:
        monitor.subscribe_whatsapp_sender(self.subscriber_chat, "972501234567")
        response = monitor.handle_whatsapp_command(
            "unsubscribe", self.subscriber_chat, "972501234567"
        )["messages"][0]
        self.assertIn("הסרתי אותך", response)
        self.assertEqual(monitor.load_whatsapp_subscribers(), [])
        with self.assertRaises(ValueError):
            monitor.subscribe_whatsapp_sender("972501234567@g.us", "972501234567")

    def test_default_schedule_aligns_to_the_local_full_hour(self) -> None:
        with patch.object(monitor, "POLL_INTERVAL_SECONDS", 3600):
            self.assertEqual(
                monitor.seconds_until_next_scan(datetime(2026, 9, 29, 23, 59, 59)), 1.0
            )
            self.assertEqual(
                monitor.seconds_until_next_scan(datetime(2026, 9, 29, 23, 0, 0)), 3600.0
            )


if __name__ == "__main__":
    unittest.main()
