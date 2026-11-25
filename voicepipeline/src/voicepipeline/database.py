import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from uuid import uuid4


class NotFoundError(Exception):
    pass


class SlotUnavailableError(Exception):
    pass


class AmbiguousAppointmentError(Exception):
    def __init__(self, appointments: list[dict]):
        super().__init__("More than one active appointment matches this phone number.")
        self.appointments = appointments


class PhoneConfirmationError(Exception):
    pass


def normalize_phone(phone_number: str) -> str:
    normalized = re.sub(r"[\s().-]", "", phone_number.strip())
    if not re.fullmatch(r"\+?\d{7,15}", normalized):
        raise ValueError("Enter a phone number containing 7 to 15 digits.")
    return normalized.lstrip("+")


def _utc_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="minutes")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS doctors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    specialty TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slots (
                    id TEXT PRIMARY KEY,
                    doctor_id TEXT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
                    starts_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 10 AND 240),
                    created_at TEXT NOT NULL,
                    UNIQUE(doctor_id, starts_at)
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL REFERENCES slots(id),
                    phone_number TEXT NOT NULL,
                    normalized_phone TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('booked', 'cancelled')),
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_appointment_per_slot
                    ON appointments(slot_id) WHERE status = 'booked';
                """
            )
            if connection.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
                now = datetime.now(timezone.utc)
                doctors = [
                    ("d1", "Dr. Maya Sharma", "Cardiology"),
                    ("d2", "Dr. Aarav Patel", "Dermatology"),
                ]
                connection.executemany(
                    "INSERT INTO doctors (id, name, specialty, created_at) VALUES (?, ?, ?, ?)",
                    [(doctor_id, name, specialty, now.isoformat()) for doctor_id, name, specialty in doctors],
                )
                slots = [
                    ("s1", "d1", (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)),
                    ("s2", "d1", (now + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)),
                    ("s3", "d2", (now + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)),
                    ("s4", "d2", (now + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)),
                ]
                connection.executemany(
                    "INSERT INTO slots (id, doctor_id, starts_at, duration_minutes, created_at) "
                    "VALUES (?, ?, ?, 30, ?)",
                    [
                        (slot_id, doctor_id, _utc_datetime(starts_at), now.isoformat())
                        for slot_id, doctor_id, starts_at in slots
                    ],
                )

    def list_doctors(self) -> list[dict]:
        with self.connect() as connection:
            doctors = connection.execute(
                "SELECT id, name, specialty FROM doctors ORDER BY name"
            ).fetchall()
            return [
                {
                    **dict(doctor),
                    "available_slots": [
                        dict(slot)
                        for slot in connection.execute(
                            """
                            SELECT s.id, s.starts_at, s.duration_minutes
                            FROM slots s
                            WHERE s.doctor_id = ?
                              AND NOT EXISTS (
                                  SELECT 1 FROM appointments a
                                  WHERE a.slot_id = s.id AND a.status = 'booked'
                              )
                            ORDER BY s.starts_at
                            """,
                            (doctor["id"],),
                        ).fetchall()
                    ],
                }
                for doctor in doctors
            ]

    def create_doctor(self, name: str, specialty: str) -> dict:
        doctor = {
            "id": uuid4().hex,
            "name": name.strip(),
            "specialty": specialty.strip(),
        }
        try:
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO doctors (id, name, specialty, created_at) VALUES (?, ?, ?, ?)",
                    (doctor["id"], doctor["name"], doctor["specialty"], datetime.now(timezone.utc).isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("A doctor with that name already exists.") from exc
        return doctor

    def create_slot(self, doctor_id: str, starts_at: datetime, duration_minutes: int) -> dict:
        normalized_start = (
            starts_at.replace(tzinfo=timezone.utc)
            if starts_at.tzinfo is None
            else starts_at
        )
        slot = {
            "id": uuid4().hex,
            "doctor_id": doctor_id,
            "starts_at": _utc_datetime(normalized_start),
            "duration_minutes": duration_minutes,
        }
        try:
            with self.connect() as connection:
                doctor = connection.execute(
                    "SELECT id FROM doctors WHERE id = ?", (doctor_id,)
                ).fetchone()
                if doctor is None:
                    raise NotFoundError("Doctor not found.")
                if normalized_start.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                    raise ValueError("Appointment slots must start in the future.")
                connection.execute(
                    "INSERT INTO slots (id, doctor_id, starts_at, duration_minutes, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        slot["id"],
                        doctor_id,
                        slot["starts_at"],
                        duration_minutes,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("This doctor already has a slot at that time.") from exc
        return slot

    def list_appointments(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT a.id, a.phone_number, a.status, a.created_at,
                           s.id AS slot_id, s.starts_at, s.duration_minutes,
                           d.id AS doctor_id, d.name AS doctor_name, d.specialty
                    FROM appointments a
                    JOIN slots s ON s.id = a.slot_id
                    JOIN doctors d ON d.id = s.doctor_id
                    ORDER BY CASE a.status WHEN 'booked' THEN 0 ELSE 1 END,
                             s.starts_at
                    """
                ).fetchall()
            ]

    def book_appointment(
        self,
        phone_number: str,
        doctor_id: str,
        slot_id: str,
        phone_confirmed: bool,
    ) -> dict:
        if not phone_confirmed:
            raise PhoneConfirmationError("Confirm the phone number with the caller before booking.")
        normalized_phone = normalize_phone(phone_number)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            slot = connection.execute(
                """
                SELECT s.id AS slot_id, s.starts_at, s.duration_minutes,
                       d.id AS doctor_id, d.name AS doctor_name, d.specialty
                FROM slots s JOIN doctors d ON d.id = s.doctor_id
                WHERE s.id = ? AND d.id = ?
                """,
                (slot_id, doctor_id),
            ).fetchone()
            if slot is None:
                doctor_exists = connection.execute(
                    "SELECT id FROM doctors WHERE id = ?", (doctor_id,)
                ).fetchone()
                if doctor_exists is None:
                    raise NotFoundError("Doctor not found.")
                raise NotFoundError("Appointment slot not found.")
            if connection.execute(
                "SELECT 1 FROM appointments WHERE slot_id = ? AND status = 'booked'",
                (slot_id,),
            ).fetchone():
                raise SlotUnavailableError("That appointment slot has already been booked.")
            appointment_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO appointments
                    (id, slot_id, phone_number, normalized_phone, status, created_at)
                VALUES (?, ?, ?, ?, 'booked', ?)
                """,
                (appointment_id, slot_id, phone_number.strip(), normalized_phone, now),
            )
            return {
                "id": appointment_id,
                "phone_number": phone_number.strip(),
                "status": "booked",
                **dict(slot),
            }

    def cancel_appointment(self, phone_number: str, appointment_id: str | None = None) -> dict:
        normalized_phone = normalize_phone(phone_number)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conditions = ["a.normalized_phone = ?", "a.status = 'booked'"]
            values: list[str] = [normalized_phone]
            if appointment_id:
                conditions.append("a.id = ?")
                values.append(appointment_id)
            appointments = connection.execute(
                """
                SELECT a.id, a.phone_number, s.starts_at, d.name AS doctor_name
                FROM appointments a
                JOIN slots s ON s.id = a.slot_id
                JOIN doctors d ON d.id = s.doctor_id
                WHERE """ + " AND ".join(conditions) + """
                ORDER BY s.starts_at
                """,
                values,
            ).fetchall()
            if not appointments:
                raise NotFoundError("No active appointment was found for that phone number.")
            if appointment_id is None and len(appointments) > 1:
                raise AmbiguousAppointmentError([dict(row) for row in appointments])
            appointment = appointments[0]
            connection.execute(
                "UPDATE appointments SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), appointment["id"]),
            )
            return dict(appointment)
