import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.groq.llm import GroqLLMService

from voicepipeline.bot import OPENING_GREETING, SYSTEM_PROMPT, create_tools, queue_opening_greeting
from voicepipeline.database import Database
from voicepipeline.transcripts import TranscriptManager


class ToolParams:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, result):
        self.result = result


class BotToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "clinic.sqlite3")
        self.database.initialize()
        self.schemas, self.handlers = create_tools(self.database)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_opening_greeting_is_queued_for_speech(self):
        tts = SimpleNamespace(queue_frame=AsyncMock())

        await queue_opening_greeting(tts)

        frame = tts.queue_frame.await_args.args[0]
        self.assertIsInstance(frame, TTSSpeakFrame)
        self.assertEqual(frame.text, OPENING_GREETING)

    async def test_availability_uses_dynamically_added_doctors_and_slots(self):
        doctor = self.database.create_doctor("Dr. Jamie Chen", "Family Medicine")
        slot = self.database.create_slot(
            doctor["id"],
            datetime.now(timezone.utc) + timedelta(days=3),
            30,
        )
        params = ToolParams(doctor_name="Jamie Chen")

        await self.handlers["check_doctor_availability"](params)

        self.assertEqual(params.result["doctor"], doctor["name"])
        self.assertEqual(params.result["available_slots"][0]["id"], slot["id"])

    async def test_availability_can_filter_slots_by_date(self):
        doctor = self.database.list_doctors()[0]
        requested_slot = self.database.create_slot(
            doctor["id"],
            datetime.now(timezone.utc) + timedelta(days=4),
            30,
        )
        self.database.create_slot(
            doctor["id"],
            datetime.now(timezone.utc) + timedelta(days=5),
            30,
        )
        params = ToolParams(
            doctor_name=doctor["name"],
            date=requested_slot["starts_at"][:10],
        )

        await self.handlers["check_doctor_availability"](params)

        self.assertEqual([slot["id"] for slot in params.result["available_slots"]], [requested_slot["id"]])

    async def test_availability_rejects_malformed_date_filters(self):
        params = ToolParams(doctor_name="Dr. Maya Sharma", date="next Tuesday")

        await self.handlers["check_doctor_availability"](params)

        self.assertIn("YYYY-MM-DD", params.result["error"])

    async def test_missing_doctor_returns_available_doctors_without_failing(self):
        params = ToolParams(doctor_name="No such doctor")

        await self.handlers["check_doctor_availability"](params)

        self.assertEqual(params.result["error"], "Doctor not found.")
        self.assertTrue(params.result["available_doctors"])

    async def test_booking_and_cancellation_tools_require_phone_confirmation(self):
        doctor = self.database.list_doctors()[0]
        slot = self.database.create_slot(
            doctor["id"],
            datetime.now(timezone.utc) + timedelta(days=3),
            30,
        )
        booking_args = {
            "phone_number": "+1 555 014 8820",
            "doctor_name": doctor["name"],
            "slot_id": slot["id"],
            "phone_confirmed": False,
        }
        rejected = ToolParams(**booking_args)
        await self.handlers["book_appointment"](rejected)
        self.assertIn("error", rejected.result)
        self.assertEqual(self.database.list_appointments(), [])

        confirmed = ToolParams(**{**booking_args, "phone_confirmed": True})
        await self.handlers["book_appointment"](confirmed)
        self.assertEqual(confirmed.result["status"], "booked")

        cancellation_args = {"phone_number": "+1 555 014 8820", "phone_confirmed": False}
        rejected_cancellation = ToolParams(**cancellation_args)
        await self.handlers["cancel_appointment"](rejected_cancellation)
        self.assertIn("error", rejected_cancellation.result)

        cancellation = ToolParams(**{**cancellation_args, "phone_confirmed": True})
        await self.handlers["cancel_appointment"](cancellation)
        self.assertEqual(cancellation.result["status"], "cancelled")
        self.assertEqual(self.database.list_appointments()[0]["status"], "cancelled")

    async def test_cancellation_requires_selection_when_phone_has_multiple_appointments(self):
        doctor = self.database.list_doctors()[0]
        phone_number = "+1 555 014 8820"
        slots = [
            self.database.create_slot(
                doctor["id"],
                datetime.now(timezone.utc) + timedelta(days=3 + offset),
                30,
            )
            for offset in range(2)
        ]
        appointments = [
            self.database.book_appointment(phone_number, doctor["id"], slot["id"], True)
            for slot in slots
        ]
        params = ToolParams(phone_number=phone_number, phone_confirmed=True)

        await self.handlers["cancel_appointment"](params)

        self.assertEqual(params.result["status"], "selection_required")
        self.assertEqual(
            {item["appointment_id"] for item in params.result["appointments"]},
            {appointment["id"] for appointment in appointments},
        )
        self.assertTrue(all(item["status"] == "booked" for item in self.database.list_appointments()))

        selected = ToolParams(
            phone_number=phone_number,
            phone_confirmed=True,
            appointment_id=appointments[1]["id"],
        )
        await self.handlers["cancel_appointment"](selected)

        self.assertEqual(selected.result["status"], "cancelled")
        statuses = {item["id"]: item["status"] for item in self.database.list_appointments()}
        self.assertEqual(statuses[appointments[0]["id"]], "booked")
        self.assertEqual(statuses[appointments[1]["id"]], "cancelled")

    async def test_booking_and_cancellation_publish_phone_screen_events(self):
        doctor = self.database.list_doctors()[0]
        slot = self.database.create_slot(
            doctor["id"],
            datetime.now(timezone.utc) + timedelta(days=3),
            30,
        )
        client = Mock()
        client.accept = AsyncMock()
        client.send_json = AsyncMock()
        transcripts = TranscriptManager()
        await transcripts.connect("session-1", client)
        _, handlers = create_tools(self.database, transcripts, "session-1")

        booking = ToolParams(
            phone_number="+1 555 014 8820",
            phone_confirmed=True,
            doctor_name=doctor["name"],
            slot_id=slot["id"],
        )
        await handlers["book_appointment"](booking)
        booking_event = client.send_json.await_args_list[0].args[0]["event"]
        appointment_id = self.database.list_appointments()[0]["id"]

        cancellation = ToolParams(phone_number="+1 555 014 8820", phone_confirmed=True)
        await handlers["cancel_appointment"](cancellation)
        cancellation_event = client.send_json.await_args_list[1].args[0]["event"]

        self.assertEqual(booking_event["type"], "appointment_booked")
        self.assertEqual(booking_event["appointment_id"], appointment_id)
        self.assertEqual(cancellation_event["type"], "appointment_cancelled")
        self.assertEqual(cancellation_event["appointment_id"], appointment_id)

    async def test_groq_service_configuration_matches_the_pipeline_settings(self):
        settings = GroqLLMService.Settings(
            model="openai/gpt-oss-20b",
            reasoning_effort="low",
            system_instruction=SYSTEM_PROMPT,
        )

        self.assertEqual(settings.model, "openai/gpt-oss-20b")
        self.assertEqual(settings.reasoning_effort, "low")
        self.assertTrue(settings.system_instruction)
        self.assertEqual(len(self.schemas.standard_tools), 3)


if __name__ == "__main__":
    unittest.main()
