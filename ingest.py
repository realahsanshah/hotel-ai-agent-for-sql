"""
Generates and loads the synthetic hotel PMS dataset into Postgres.

Idempotency policy: DROP + RECREATE, not upsert.
------------------------------------------------
db/schema.sql starts with `DROP TABLE IF EXISTS ... CASCADE` for every table,
and this script always re-runs that file before loading data. So running
`python ingest.py` twice in a row gives you the exact same database both
times (same seed -> same rows, same auto-generated ids) rather than
duplicating or merging data.

Why drop/recreate instead of upsert for a learning project:
- The dataset is fully synthetic and reproducible from seed=42, so there is
  nothing valuable to preserve between runs.
- Upsert logic (ON CONFLICT DO UPDATE, diffing, etc.) adds real complexity
  for zero benefit here, and would obscure the schema/data-generation code
  that's actually the point of this repo.
- In a real system with user-entered data you would NOT do this — this
  policy is specific to "seed data for a demo/learning DB".

Usage: python ingest.py   (Postgres must be reachable; see docker-compose.yml)
"""

import os
import random
import sys
import time
from datetime import date, datetime, timedelta

import psycopg
from dotenv import load_dotenv
from faker import Faker

load_dotenv()

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "hotel_pms"),
    "user": os.getenv("POSTGRES_USER", "hotel_admin"),
    "password": os.getenv("POSTGRES_PASSWORD", "hotel_dev_password"),
}

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "db", "schema.sql")

TODAY = date(2026, 7, 12)  # fixed "now" so the dataset is fully reproducible


def wait_for_postgres(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Poll until Postgres accepts connections. docker-compose's healthcheck
    covers container startup, but there's no built-in wait when you invoke
    this script by hand right after `docker-compose up -d`, so we poll here
    too rather than requiring the caller to time it themselves."""
    for attempt in range(1, max_attempts + 1):
        try:
            conn = psycopg.connect(**DB_CONFIG, connect_timeout=3)
            conn.close()
            print(f"Postgres is ready (attempt {attempt}).")
            return
        except psycopg.OperationalError as exc:
            print(f"Waiting for Postgres... (attempt {attempt}/{max_attempts}): {exc}")
            time.sleep(delay_seconds)
    print("Postgres never became ready. Is docker-compose up?", file=sys.stderr)
    sys.exit(1)


def apply_schema(conn: psycopg.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()
    print("Schema applied (tables dropped + recreated).")


# ---------------------------------------------------------------------------
# Data generation. Each function inserts its rows and returns the list of
# generated ids (in insertion order) so downstream tables can reference them
# by foreign key. We fetch ids via RETURNING rather than assuming SERIAL
# sequence values, so this stays correct even if someone changes insertion
# order later.
# ---------------------------------------------------------------------------

PROPERTIES = [
    {"name": "Harborview Grand", "city": "Boston", "country": "USA", "timezone": "America/New_York"},
    {"name": "Sakura Palace", "city": "Osaka", "country": "Japan", "timezone": "Asia/Tokyo"},
    {"name": "Alpine Ridge Lodge", "city": "Zurich", "country": "Switzerland", "timezone": "Europe/Zurich"},
]

ROOM_TYPE_TEMPLATES = [
    ("Standard Queen", 2),
    ("Standard King", 2),
    ("Deluxe Suite", 4),
    ("Executive Suite", 3),
]

# Intentionally inconsistent casing/format — see db/schema.sql header.
ROOM_STATUSES = ["Clean", "Clean", "Clean", "dirty", "dirty", "Out_Of_Order"]

RATE_PLAN_TEMPLATES = [
    ("Best Available Rate", 1.0, True),
    ("Advance Purchase", 0.8, False),
    ("Corporate Rate", 0.9, True),
]

RESERVATION_STATUSES = ["Booked", "CheckedIn", "checked-out", "Cancelled", "No_Show"]
RESERVATION_STATUS_WEIGHTS = [0.15, 0.10, 0.60, 0.10, 0.05]

CHARGE_TYPES = ["Room", "Tax", "Minibar", "Spa", "Parking"]

PAYMENT_METHODS = ["card", "CARD", "cash", "Cash", "online"]

HOUSEKEEPING_STATUSES = ["Completed", "completed", "Pending", "pending", "In_Progress"]


def insert_properties(cur) -> list[int]:
    ids = []
    for p in PROPERTIES:
        cur.execute(
            "INSERT INTO properties (name, city, country, timezone) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (p["name"], p["city"], p["country"], p["timezone"]),
        )
        ids.append(cur.fetchone()[0])
    return ids


def insert_room_types(cur, property_ids: list[int]) -> dict[int, list[int]]:
    """Returns {property_id: [room_type_id, ...]}"""
    by_property = {}
    for pid in property_ids:
        by_property[pid] = []
        for type_name, capacity in ROOM_TYPE_TEMPLATES:
            cur.execute(
                "INSERT INTO room_types (property_id, type_name, base_capacity) "
                "VALUES (%s, %s, %s) RETURNING id",
                (pid, type_name, capacity),
            )
            by_property[pid].append(cur.fetchone()[0])
    return by_property


def insert_rooms(cur, property_ids: list[int], room_types_by_property: dict) -> dict[int, list[int]]:
    """~40 rooms per property across 8 floors. Returns {property_id: [room_id, ...]}"""
    by_property = {}
    for pid in property_ids:
        by_property[pid] = []
        room_types = room_types_by_property[pid]
        rooms_per_floor = 5
        floors = 8
        for floor in range(1, floors + 1):
            for i in range(1, rooms_per_floor + 1):
                room_number = f"{floor}{i:02d}"
                room_type_id = random.choice(room_types)
                status = random.choice(ROOM_STATUSES)
                cur.execute(
                    "INSERT INTO rooms (property_id, room_type_id, room_number, floor, room_status) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (pid, room_type_id, room_number, floor, status),
                )
                by_property[pid].append(cur.fetchone()[0])
    return by_property


def insert_loyalty_accounts(cur, count: int = 150) -> list[int]:
    ids = []
    tiers = ["Silver"] * 60 + ["Gold"] * 30 + ["Platinum"] * 10  # weighted pool
    for _ in range(count):
        tier = random.choice(tiers)
        points = random.randint(0, 50000)
        cur.execute(
            "INSERT INTO loyalty_accounts (tier, points_balance) VALUES (%s, %s) RETURNING id",
            (tier, points),
        )
        ids.append(cur.fetchone()[0])
    return ids


def insert_guests(cur, loyalty_ids: list[int], count: int = 350) -> list[int]:
    """~40% of guests are NOT enrolled in the loyalty program (loyalty_id NULL).
    Each loyalty account is used by at most one guest, mirroring a real 1:1
    guest<->loyalty-account relationship."""
    ids = []
    available_loyalty_ids = loyalty_ids.copy()
    random.shuffle(available_loyalty_ids)
    for i in range(count):
        enrolled = random.random() > 0.4 and available_loyalty_ids
        loyalty_id = available_loyalty_ids.pop() if enrolled else None
        cur.execute(
            "INSERT INTO guests (name, email, phone, loyalty_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (fake.name(), fake.email(), fake.phone_number(), loyalty_id),
        )
        ids.append(cur.fetchone()[0])
    return ids


def insert_rate_plans(cur, property_ids: list[int]) -> dict[int, list[tuple[int, float]]]:
    """Returns {property_id: [(rate_plan_id, nightly_rate), ...]}"""
    by_property = {}
    for pid in property_ids:
        by_property[pid] = []
        base_rate = random.uniform(120, 260)
        for plan_name, multiplier, refundable in RATE_PLAN_TEMPLATES:
            nightly_rate = round(base_rate * multiplier, 2)
            cur.execute(
                "INSERT INTO rate_plans (property_id, plan_name, nightly_rate, refundable) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (pid, plan_name, nightly_rate, refundable),
            )
            rate_plan_id = cur.fetchone()[0]
            by_property[pid].append((rate_plan_id, nightly_rate))
    return by_property


def insert_reservations_and_folios(
    cur,
    property_ids: list[int],
    guest_ids: list[int],
    rooms_by_property: dict,
    rate_plans_by_property: dict,
    count: int = 400,
):
    """Creates reservations, one folio per reservation, folio_charges, and
    payments (some deliberately underpaid). Returns nothing — this is the
    core of the dataset and everything downstream (charges/payments) is
    generated inline per-reservation so the numbers stay internally
    consistent (room charge based on nights * nightly_rate, etc.)."""
    for _ in range(count):
        pid = random.choice(property_ids)
        guest_id = random.choice(guest_ids)
        rate_plan_id, nightly_rate = random.choice(rate_plans_by_property[pid])

        # Spread stays across a 9-month window centered on "today" so we get
        # a realistic mix of past (checked-out), current, and future (booked).
        stay_start_offset = random.randint(-180, 90)
        checkin_date = TODAY + timedelta(days=stay_start_offset)
        nights = random.randint(1, 7)
        checkout_date = checkin_date + timedelta(days=nights)

        # Status distribution is weighted, but we override it to stay
        # internally consistent with the dates: a reservation in the future
        # cannot be 'checked-out', and one far in the past is unlikely to
        # still be 'Booked'.
        if checkin_date > TODAY:
            status = random.choices(["Booked", "Cancelled"], weights=[0.85, 0.15])[0]
        elif checkout_date < TODAY:
            status = random.choices(
                ["checked-out", "Cancelled", "No_Show"], weights=[0.80, 0.10, 0.10]
            )[0]
        else:
            status = random.choices(["CheckedIn", "Cancelled"], weights=[0.9, 0.1])[0]

        # Rooms are only assigned at check-in, so only CheckedIn/checked-out
        # reservations get a room_id — everything else stays NULL.
        room_id = None
        if status in ("CheckedIn", "checked-out"):
            room_id = random.choice(rooms_by_property[pid])

        cur.execute(
            "INSERT INTO reservations "
            "(property_id, guest_id, room_id, rate_plan_id, checkin_date, checkout_date, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (pid, guest_id, room_id, rate_plan_id, checkin_date, checkout_date, status),
        )
        reservation_id = cur.fetchone()[0]

        opened_at = datetime.combine(checkin_date, datetime.min.time()) + timedelta(hours=14)
        closed_at = None
        if status == "checked-out":
            closed_at = datetime.combine(checkout_date, datetime.min.time()) + timedelta(hours=11)

        cur.execute(
            "INSERT INTO folios (reservation_id, opened_at, closed_at) VALUES (%s, %s, %s) RETURNING id",
            (reservation_id, opened_at, closed_at),
        )
        folio_id = cur.fetchone()[0]

        # Cancelled/No_Show reservations still get a folio row (a real PMS
        # creates the folio at booking time) but no charges/payments — there
        # was no stay to bill.
        if status in ("Cancelled", "No_Show"):
            continue

        total_charges = 0.0

        room_charge = round(nightly_rate * nights, 2)
        cur.execute(
            "INSERT INTO folio_charges (folio_id, charge_type, amount, charge_date) VALUES (%s, %s, %s, %s)",
            (folio_id, "Room", room_charge, checkin_date),
        )
        total_charges += room_charge

        tax_charge = round(room_charge * 0.12, 2)
        cur.execute(
            "INSERT INTO folio_charges (folio_id, charge_type, amount, charge_date) VALUES (%s, %s, %s, %s)",
            (folio_id, "Tax", tax_charge, checkin_date),
        )
        total_charges += tax_charge

        # 0-3 incidental charges (Minibar/Spa/Parking) on random days of the stay.
        for _ in range(random.randint(0, 3)):
            charge_type = random.choice(["Minibar", "Spa", "Parking"])
            amount = round(random.uniform(8, 120), 2)
            charge_day = checkin_date + timedelta(days=random.randint(0, max(nights - 1, 0)))
            cur.execute(
                "INSERT INTO folio_charges (folio_id, charge_type, amount, charge_date) VALUES (%s, %s, %s, %s)",
                (folio_id, charge_type, amount, charge_day),
            )
            total_charges += amount

        # Payments: most folios pay in full (in 1-2 installments), but ~15%
        # are deliberately left underpaid to model an outstanding balance.
        underpay = random.random() < 0.15
        amount_to_collect = round(total_charges * random.uniform(0.4, 0.9), 2) if underpay else total_charges

        remaining = amount_to_collect
        num_payments = 1 if remaining <= 0 else random.randint(1, 2)
        for i in range(num_payments):
            if remaining <= 0:
                break
            portion = round(remaining, 2) if i == num_payments - 1 else round(remaining * random.uniform(0.3, 0.7), 2)
            portion = max(portion, 0.01)
            method = random.choice(PAYMENT_METHODS)
            paid_at = opened_at + timedelta(hours=random.randint(0, max(nights * 24 - 1, 1)))
            cur.execute(
                "INSERT INTO payments (folio_id, amount, method, paid_at) VALUES (%s, %s, %s, %s)",
                (folio_id, portion, method, paid_at),
            )
            remaining -= portion


def insert_housekeeping_tasks(cur, rooms_by_property: dict):
    """5-15 tasks per room over the last ~60 days."""
    staff_names = [fake.first_name() for _ in range(12)]
    for room_ids in rooms_by_property.values():
        for room_id in room_ids:
            num_tasks = random.randint(5, 15)
            for _ in range(num_tasks):
                task_date = TODAY - timedelta(days=random.randint(0, 60))
                status = random.choice(HOUSEKEEPING_STATUSES)
                assigned_to = random.choice(staff_names)
                cur.execute(
                    "INSERT INTO housekeeping_tasks (room_id, task_date, status, assigned_to) "
                    "VALUES (%s, %s, %s, %s)",
                    (room_id, task_date, status, assigned_to),
                )


def main():
    wait_for_postgres()

    conn = psycopg.connect(**DB_CONFIG)
    try:
        apply_schema(conn)

        with conn.cursor() as cur:
            property_ids = insert_properties(cur)
            room_types_by_property = insert_room_types(cur, property_ids)
            rooms_by_property = insert_rooms(cur, property_ids, room_types_by_property)
            loyalty_ids = insert_loyalty_accounts(cur)
            guest_ids = insert_guests(cur, loyalty_ids)
            rate_plans_by_property = insert_rate_plans(cur, property_ids)
            insert_reservations_and_folios(
                cur, property_ids, guest_ids, rooms_by_property, rate_plans_by_property
            )
            insert_housekeeping_tasks(cur, rooms_by_property)
        conn.commit()
        print("Seed data loaded successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
