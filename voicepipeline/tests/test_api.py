import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voicepipeline.api import create_app
from voicepipeline.database import Database


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Database(Path(self.temp_dir.name) / "api.sqlite3")
        self.client_context = TestClient(create_app(database))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_doctors_slots_and_appointments_update_through_api(self):
        response = self.client.post(
            "/api/doctors",
            json={"name": "Dr. Jamie Chen", "specialty": "Family Medicine"},
        )
        self.assertEqual(response.status_code, 201)
        doctor = response.json()

        slot_response = self.client.post(
            f"/api/doctors/{doctor['id']}/slots",
            json={
                "starts_at": (datetime.now(timezone.utc) + timedelta(days=4)).isoformat(),
                "duration_minutes": 30,
            },
        )
        self.assertEqual(slot_response.status_code, 201)
        slot = slot_response.json()

        booking = self.client.post(
            "/api/appointments",
            json={
                "phone_number": "555 014 8820",
                "doctor_id": doctor["id"],
                "slot_id": slot["id"],
                "phone_confirmed": True,
            },
        )
        self.assertEqual(booking.status_code, 201)
        appointment_id = booking.json()["id"]

        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["appointments"][0]["id"], appointment_id)
        self.assertNotIn(
            slot["id"],
            [item["id"] for item in next(item for item in dashboard["doctors"] if item["id"] == doctor["id"])["available_slots"]],
        )

        cancelled = self.client.post(
            "/api/appointments/cancel",
            json={"phone_number": "5550148820", "appointment_id": appointment_id},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["id"], appointment_id)

    def test_duplicate_booking_and_unconfirmed_phone_are_rejected(self):
        doctor = self.client.get("/api/doctors").json()[0]
        slot = doctor["available_slots"][0]
        appointment = {
            "phone_number": "5550148820",
            "doctor_id": doctor["id"],
            "slot_id": slot["id"],
            "phone_confirmed": False,
        }
        self.assertEqual(self.client.post("/api/appointments", json=appointment).status_code, 400)
        appointment["phone_confirmed"] = True
        self.assertEqual(self.client.post("/api/appointments", json=appointment).status_code, 201)
        second = {**appointment, "phone_number": "5550148821"}
        self.assertEqual(self.client.post("/api/appointments", json=second).status_code, 409)

    def test_cancel_without_appointment_id_rejects_ambiguous_phone_matches(self):
        doctor = self.client.get("/api/doctors").json()[0]
        phone_number = "5550148820"
        for day in (5, 6):
            slot = self.client.post(
                f"/api/doctors/{doctor['id']}/slots",
                json={
                    "starts_at": (datetime.now(timezone.utc) + timedelta(days=day)).isoformat(),
                    "duration_minutes": 30,
                },
            ).json()
            self.client.post(
                "/api/appointments",
                json={
                    "phone_number": phone_number,
                    "doctor_id": doctor["id"],
                    "slot_id": slot["id"],
                    "phone_confirmed": True,
                },
            )

        response = self.client.post("/api/appointments/cancel", json={"phone_number": phone_number})

        self.assertEqual(response.status_code, 409)
        self.assertIn("Select an appointment", response.json()["detail"])
        self.assertTrue(all(item["status"] == "booked" for item in self.client.get("/api/appointments").json()))

    def test_health_does_not_expose_provider_keys(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "groq-secret",
                "ELEVENLABS_API_KEY": "elevenlabs-secret",
                "ELEVENLABS_VOICE_ID": "voice-secret",
            },
        ):
            health = self.client.get("/api/health").json()

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["voice_ready"])
        self.assertNotIn("secret", str(health).lower())

    def test_health_requires_groq_instead_of_google_for_voice_calls(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "ELEVENLABS_API_KEY": "configured",
                "ELEVENLABS_VOICE_ID": "configured",
            },
        ):
            health = self.client.get("/api/health").json()

        self.assertFalse(health["voice_ready"])
        self.assertEqual(health["missing_voice_settings"], ["GROQ_API_KEY"])

    def test_blank_doctor_names_are_rejected_after_trimming(self):
        response = self.client.post(
            "/api/doctors",
            json={"name": "   ", "specialty": "  "},
        )

        self.assertEqual(response.status_code, 422)

    def test_transcript_websocket_rejects_unconfigured_origins(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/api/transcript/test-session",
                headers={"origin": "https://untrusted.example"},
            ):
                self.fail("An untrusted origin should not be accepted.")


if __name__ == "__main__":
    unittest.main()
