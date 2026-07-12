-- Hotel PMS schema — plain SQL, no ORM, on purpose (see AGENT_PLAN.md for why
-- this project avoids SQLAlchemy models: the agent talks directly to
-- information_schema, so there's no ORM metadata layer to keep in sync).
--
-- Deliberately messy: several columns below use inconsistent casing for
-- status-like values (e.g. 'Clean'/'dirty'/'Out_Of_Order'). This is NOT a bug
-- to fix — it's the point of the dataset. The agent must run a
-- `SELECT DISTINCT status FROM ...` before filtering on any such column,
-- rather than assuming a clean enum. Do not "clean up" these values.

-- Drop order matters: children before parents (see ingest.py for the
-- drop/recreate policy — this file only defines structure).
DROP TABLE IF EXISTS housekeeping_tasks CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS folio_charges CASCADE;
DROP TABLE IF EXISTS folios CASCADE;
DROP TABLE IF EXISTS reservations CASCADE;
DROP TABLE IF EXISTS rate_plans CASCADE;
DROP TABLE IF EXISTS guests CASCADE;
DROP TABLE IF EXISTS loyalty_accounts CASCADE;
DROP TABLE IF EXISTS rooms CASCADE;
DROP TABLE IF EXISTS room_types CASCADE;
DROP TABLE IF EXISTS properties CASCADE;

CREATE TABLE properties (
    id       SERIAL PRIMARY KEY,
    name     TEXT NOT NULL,
    city     TEXT NOT NULL,
    country  TEXT NOT NULL,
    timezone TEXT NOT NULL  -- IANA tz name, e.g. 'America/New_York'
);

CREATE TABLE room_types (
    id            SERIAL PRIMARY KEY,
    property_id   INTEGER NOT NULL REFERENCES properties(id),
    type_name     TEXT NOT NULL,       -- e.g. 'Standard Queen', 'Deluxe Suite'
    base_capacity INTEGER NOT NULL
);

-- room_status is intentionally inconsistent casing/format:
-- 'Clean', 'dirty', 'Out_Of_Order' (see file header note).
CREATE TABLE rooms (
    id           SERIAL PRIMARY KEY,
    property_id  INTEGER NOT NULL REFERENCES properties(id),
    room_type_id INTEGER NOT NULL REFERENCES room_types(id),
    room_number  TEXT NOT NULL,
    floor        INTEGER NOT NULL,
    room_status  TEXT NOT NULL
);

CREATE TABLE loyalty_accounts (
    id             SERIAL PRIMARY KEY,
    tier           TEXT NOT NULL,   -- 'Silver' / 'Gold' / 'Platinum'
    points_balance INTEGER NOT NULL
);

-- loyalty_id is nullable: not every guest is enrolled in the loyalty program.
CREATE TABLE guests (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    phone       TEXT NOT NULL,
    loyalty_id  INTEGER REFERENCES loyalty_accounts(id)
);

CREATE TABLE rate_plans (
    id            SERIAL PRIMARY KEY,
    property_id   INTEGER NOT NULL REFERENCES properties(id),
    plan_name     TEXT NOT NULL,       -- e.g. 'Best Available Rate', 'Advance Purchase'
    nightly_rate  NUMERIC(10,2) NOT NULL,
    refundable    BOOLEAN NOT NULL
);

-- room_id is nullable: rooms are only assigned at check-in time, so a
-- 'Booked' reservation legitimately has no room yet.
-- status is intentionally inconsistent: 'Booked' / 'CheckedIn' /
-- 'checked-out' / 'Cancelled' / 'No_Show'.
CREATE TABLE reservations (
    id            SERIAL PRIMARY KEY,
    property_id   INTEGER NOT NULL REFERENCES properties(id),
    guest_id      INTEGER NOT NULL REFERENCES guests(id),
    room_id       INTEGER REFERENCES rooms(id),
    rate_plan_id  INTEGER NOT NULL REFERENCES rate_plans(id),
    checkin_date  DATE NOT NULL,
    checkout_date DATE NOT NULL,
    status        TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- One folio per reservation. closed_at is nullable — open folios belong to
-- reservations that haven't checked out (or that never got closed out).
CREATE TABLE folios (
    id             SERIAL PRIMARY KEY,
    reservation_id INTEGER NOT NULL UNIQUE REFERENCES reservations(id),
    opened_at      TIMESTAMP NOT NULL,
    closed_at      TIMESTAMP
);

-- No precomputed total column anywhere on purpose: a folio's total must
-- always be derived with SUM(amount) over its charges. This forces the
-- agent to write an aggregation query instead of reading a cached field
-- that could (in a real PMS) drift out of sync with the line items.
CREATE TABLE folio_charges (
    id          SERIAL PRIMARY KEY,
    folio_id    INTEGER NOT NULL REFERENCES folios(id),
    charge_type TEXT NOT NULL,   -- Room / Tax / Minibar / Spa / Parking
    amount      NUMERIC(10,2) NOT NULL,
    charge_date DATE NOT NULL
);

-- method casing is intentionally inconsistent ('card' vs 'CARD').
-- Some folios are deliberately underpaid relative to SUM(folio_charges.amount)
-- — that's a realistic PMS condition (outstanding balance), not a data bug.
CREATE TABLE payments (
    id        SERIAL PRIMARY KEY,
    folio_id  INTEGER NOT NULL REFERENCES folios(id),
    amount    NUMERIC(10,2) NOT NULL,
    method    TEXT NOT NULL,
    paid_at   TIMESTAMP NOT NULL
);

-- status casing is intentionally inconsistent, same reasoning as rooms.room_status.
CREATE TABLE housekeeping_tasks (
    id          SERIAL PRIMARY KEY,
    room_id     INTEGER NOT NULL REFERENCES rooms(id),
    task_date   DATE NOT NULL,
    status      TEXT NOT NULL,
    assigned_to TEXT NOT NULL
);

CREATE INDEX idx_reservations_guest ON reservations(guest_id);
CREATE INDEX idx_reservations_room ON reservations(room_id);
CREATE INDEX idx_folio_charges_folio ON folio_charges(folio_id);
CREATE INDEX idx_payments_folio ON payments(folio_id);
CREATE INDEX idx_housekeeping_room ON housekeeping_tasks(room_id);
