import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from voicepipeline.database import (
    Database,
    NotFoundError,
    PhoneConfirmationError,
    SlotUnavailableError,
    normalize_phone,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "clinic.sqlite3"
        self.database = Database(self.database_path)
        self.database.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_future_slot(self, doctor_id):
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        return self.database.create_slot(doctor_id, starts_at, 30)

    def test_seed_data_persists_between_database_instances(self):
        first = self.database.list_doctors()
        second = Database(self.database_path)
        second.initialize()

        self.assertEqual(len(first), 2)
        self.assertEqual(first, second.list_doctors())

    def test_doctors_and_slots_can_be_added_and_persisted(self):
        doctor = self.database.create_doctor("Dr. Jamie Chen", "Family Medicine")
        slot = self.create_future_slot(doctor["id"])
        reopened = Database(self.database_path)
        reopened.initialize()

        saved = next(item for item in reopened.list_doctors() if item["id"] == doctor["id"])
        self.assertEqual(saved["specialty"], "Family Medicine")
        self.assertEqual(saved["available_slots"][0]["id"], slot["id"])

    def test_booking_requires_confirmation_and_frees_slot_when_cancelled(self):
        doctor = self.database.list_doctors()[0]
        slot = self.create_future_slot(doctor["id"])

        with self.assertRaises(PhoneConfirmationError):
            self.database.book_appointment("+1 555 014 8820", doctor["id"], slot["id"], False)

        appointment = self.database.book_appointment(
            "+1 555 014 8820", doctor["id"], slot["id"], True
        )
        self.assertEqual(appointment["status"], "booked")
        available = next(
            item for item in self.database.list_doctors() if item["id"] == doctor["id"]
        )["available_slots"]
        self.assertNotIn(slot["id"], [item["id"] for item in available])

        cancelled = self.database.cancel_appointment("15550148820")
        self.assertEqual(cancelled["id"], appointment["id"])
        self.assertEqual(
            self.database.list_appointments()[0]["status"],
            "cancelled",
        )
        available = next(
            item for item in self.database.list_doctors() if item["id"] == doctor["id"]
        )["available_slots"]
        self.assertIn(slot["id"], [item["id"] for item in available])

    def test_slot_can_only_be_booked_once(self):
        doctor = self.database.list_doctors()[0]
        slot = self.create_future_slot(doctor["id"])
        self.database.book_appointment("5550148820", doctor["id"], slot["id"], True)

        with self.assertRaises(SlotUnavailableError):
            self.database.book_appointment("5550148821", doctor["id"], slot["id"], True)

    def test_past_slots_are_rejected(self):
        doctor = self.database.list_doctors()[0]

        with self.assertRaisesRegex(ValueError, "future"):
            self.database.create_slot(
                doctor["id"],
                datetime.now(timezone.utc) - timedelta(days=1),
                30,
            )

    def test_cancellation_requires_matching_phone_number(self):
        doctor = self.database.list_doctors()[0]
        slot = self.create_future_slot(doctor["id"])
        self.database.book_appointment("5550148820", doctor["id"], slot["id"], True)

        with self.assertRaises(NotFoundError):
            self.database.cancel_appointment("5550148821")

    def test_phone_number_normalization_rejects_malformed_numbers(self):
        self.assertEqual(normalize_phone("+1 (555) 014-8820"), "15550148820")

        with self.assertRaises(ValueError):
            normalize_phone("+++1 555 014 8820")


if __name__ == "__main__":
    unittest.main()
