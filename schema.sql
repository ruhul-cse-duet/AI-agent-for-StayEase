-- schema.sql (v3 — customer_id based)
-- Run fresh, OR run the migration section at the bottom for existing DBs.

-- ─────────────────────────────────────────────────────────────────
-- 1. CUSTOMERS  (one row per real guest)
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customers (
    id          VARCHAR(50)     PRIMARY KEY,
    -- e.g. "cust_01", a phone number, or any unique string the
    -- frontend assigns. Kept as VARCHAR so callers can use their
    -- own ID scheme (UUID, phone, etc.).
    name        VARCHAR(100),
    phone       VARCHAR(20),
    email       VARCHAR(150),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────
-- 2. LISTINGS
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listings (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(200)    NOT NULL,
    location            VARCHAR(100)    NOT NULL,
    address             TEXT,
    description         TEXT,
    price_per_night_bdt NUMERIC(10, 2)  NOT NULL,
    max_guests          INT             NOT NULL DEFAULT 2,
    amenities           JSONB           NOT NULL DEFAULT '[]',
    host_name           VARCHAR(100),
    host_phone          VARCHAR(20),
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────
-- 3. BOOKINGS
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bookings (
    id              SERIAL PRIMARY KEY,
    listing_id      INT             NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
    customer_id     VARCHAR(50)     REFERENCES customers(id) ON DELETE SET NULL,
    guest_name      VARCHAR(100)    NOT NULL,
    guest_phone     VARCHAR(20)     NOT NULL,
    check_in        DATE            NOT NULL,
    check_out       DATE            NOT NULL,
    guests          INT             NOT NULL,
    total_price_bdt NUMERIC(12, 2)  NOT NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'confirmed'
                        CHECK (status IN ('pending', 'confirmed', 'cancelled')),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dates CHECK (check_out > check_in)
);

-- ─────────────────────────────────────────────────────────────────
-- 4. CONVERSATIONS  (one row per session; linked to customer)
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id              SERIAL          PRIMARY KEY,
    customer_id     VARCHAR(50)     NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    -- Full chat history: [{role, content, timestamp}, ...]
    messages        JSONB           NOT NULL DEFAULT '[]',
    -- Partial booking data accumulated across turns; cleared on success
    pending_booking JSONB           NOT NULL DEFAULT '{}',
    intent_last     VARCHAR(20),
    escalated       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Fast lookups by customer
CREATE INDEX IF NOT EXISTS idx_conversations_customer_id
    ON conversations(customer_id);

-- ─────────────────────────────────────────────────────────────────
-- 5. SAMPLE DATA
-- ─────────────────────────────────────────────────────────────────
INSERT INTO listings (name, location, address, description, price_per_night_bdt, max_guests, amenities, host_name, host_phone)
VALUES
  ('Sea Pearl Beach Resort',  'Cox''s Bazar', 'Marine Drive, Cox''s Bazar',   'Luxury beachfront resort with stunning ocean views.',  8500, 4, '["WiFi","AC","Pool","Restaurant","Sea View","Room Service"]', 'Karim Hossain',  '+8801711000001'),
  ('Long Beach Hotel',        'Cox''s Bazar', 'Kolatoli Beach, Cox''s Bazar', 'Mid-range sea-view hotel, steps from the beach.',       5200, 2, '["WiFi","AC","Sea View","Parking","Breakfast"]',              'Nadia Islam',    '+8801721000002'),
  ('Sayeman Beach Resort',    'Cox''s Bazar', 'Himchori, Cox''s Bazar',       'Budget-friendly resort with restaurant and gardens.',   4800, 3, '["WiFi","AC","Restaurant","Garden","Parking"]',               'Reza Ahmed',     '+8801731000003'),
  ('Rose View Hotel Sylhet',  'Sylhet',       'Airport Road, Sylhet',         'Business hotel near Osmani International Airport.',     3500, 2, '["WiFi","AC","Gym","Conference Room","Restaurant"]',          'Fatema Begum',   '+8801741000004'),
  ('Sajek Valley Resort',     'Sajek',        'Sajek Valley, Rangamati',      'Eco-resort perched in the clouds of the hill tracts.', 6200, 4, '["WiFi","Mountain View","Bonfire","Trekking","Restaurant"]',  'Tanvir Alam',    '+8801751000005')
ON CONFLICT DO NOTHING;

-- ─────────────────────────────────────────────────────────────────
-- 6. MIGRATION (run only if upgrading from schema v1 or v2)
-- ─────────────────────────────────────────────────────────────────
-- Step 1: create customers table (already done above with IF NOT EXISTS)
-- Step 2: add customer_id to bookings
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS customer_id VARCHAR(50)
        REFERENCES customers(id) ON DELETE SET NULL;

-- Step 3: conversations — add customer_id, pending_booking columns
-- (For old rows we cannot recover the customer, so they are left orphaned.
--  Drop and recreate conversations if you don't need old data.)
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS customer_id     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS pending_booking JSONB NOT NULL DEFAULT '{}';

-- Rename old primary-key column 'id' (was the old conversation_id string).
-- If your old conversations.id was VARCHAR, do a data migration here.
-- For a clean start, simply truncate:
-- TRUNCATE TABLE conversations RESTART IDENTITY CASCADE;
