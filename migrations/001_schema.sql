CREATE TABLE IF NOT EXISTS slots (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    floor INT NOT NULL,
    size TEXT NOT NULL CHECK (size IN ('bike','compact','large','oversize')));

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    plate TEXT NOT NULL,
    vehicle_kind TEXT NOT NULL,
    slot_id INT NOT NULL REFERENCES slots(id),
    entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    exited_at TIMESTAMPTZ,
    fee_paise BIGINT,
    receipt JSONB,
    CHECK (exited_at IS NULL OR exited_at >= entered_at));

-- The database, not the application, is what stops one slot holding two cars
-- and one plate holding two tickets. A partial index only covers open tickets,
-- so a slot can be reused once the vehicle has gone.
CREATE UNIQUE INDEX IF NOT EXISTS tickets_one_car_per_slot
    ON tickets (slot_id) WHERE exited_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS tickets_one_ticket_per_plate
    ON tickets (plate) WHERE exited_at IS NULL;
CREATE INDEX IF NOT EXISTS tickets_history ON tickets (plate, id DESC);
