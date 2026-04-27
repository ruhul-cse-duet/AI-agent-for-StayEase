-- schema.sql
-- Run once against your PostgreSQL database to create the StayEase tables.

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

CREATE TABLE IF NOT EXISTS bookings (
    id              SERIAL PRIMARY KEY,
    listing_id      INT             NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
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

CREATE TABLE IF NOT EXISTS conversations (
    id          VARCHAR(100)    PRIMARY KEY,
    messages    JSONB           NOT NULL DEFAULT '[]',
    intent_last VARCHAR(20),
    escalated   BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Sample data for development / testing
INSERT INTO listings (name, location, address, description, price_per_night_bdt, max_guests, amenities, host_name, host_phone)
VALUES
  ('Sea Pearl Beach Resort',  'Cox''s Bazar', 'Marine Drive, Cox''s Bazar',  'Luxury beachfront resort',  8500, 4, '["WiFi","AC","Pool","Restaurant","Sea View"]', 'Karim Hossain', '+8801711000001'),
  ('Long Beach Hotel',        'Cox''s Bazar', 'Kolatoli Beach, Cox''s Bazar', 'Mid-range sea-view hotel',  5200, 2, '["WiFi","AC","Sea View","Parking"]',            'Nadia Islam',   '+8801721000002'),
  ('Sayeman Beach Resort',    'Cox''s Bazar', 'Himchori, Cox''s Bazar',       'Budget-friendly resort',    4800, 3, '["WiFi","AC","Restaurant","Garden"]',            'Reza Ahmed',    '+8801731000003'),
  ('Rose View Hotel Sylhet',  'Sylhet',       'Airport Road, Sylhet',         'Business hotel near airport',3500, 2, '["WiFi","AC","Gym","Conference Room"]',          'Fatema Begum',  '+8801741000004'),
  ('Sajek Valley Resort',     'Sajek',        'Sajek Valley, Rangamati',       'Hill-top eco resort',       6200, 4, '["WiFi","Mountain View","Bonfire","Trekking"]',  'Tanvir Alam',   '+8801751000005');
