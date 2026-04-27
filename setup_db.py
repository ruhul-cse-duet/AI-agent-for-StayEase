"""
setup_db.py
-----------
Run this once to:
  1. Create the 'stayease' database (if it doesn't exist)
  2. Create all 3 tables
  3. Insert dummy listings, bookings, and conversations for testing
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import json
from datetime import datetime, timezone

# ══════════════════════════════════════════════════════════════════════
# ✏️  EDIT THESE to match your local PostgreSQL setup
# ══════════════════════════════════════════════════════════════════════
PG_HOST     = "localhost"
PG_PORT     = 5432
PG_USER     = "postgres"        # your postgres username
PG_PASSWORD = "204085"        # your postgres password
DB_NAME     = "stayease"
# ══════════════════════════════════════════════════════════════════════


def create_database():
    """Connect to 'postgres' default DB and create 'stayease' if missing."""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname="postgres"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    exists = cur.fetchone()
    if not exists:
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
        print(f"✅ Database '{DB_NAME}' created.")
    else:
        print(f"ℹ️  Database '{DB_NAME}' already exists — skipping create.")
    cur.close()
    conn.close()


def get_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        user=PG_USER, password=PG_PASSWORD,
        dbname=DB_NAME
    )


def create_tables(conn):
    """Create listings, bookings, conversations tables."""
    sql = """
    CREATE TABLE IF NOT EXISTS listings (
        id                  SERIAL PRIMARY KEY,
        name                VARCHAR(200)   NOT NULL,
        location            VARCHAR(100)   NOT NULL,
        address             TEXT,
        description         TEXT,
        price_per_night_bdt NUMERIC(10,2)  NOT NULL,
        max_guests          INT            NOT NULL DEFAULT 2,
        amenities           JSONB          NOT NULL DEFAULT '[]',
        host_name           VARCHAR(100),
        host_phone          VARCHAR(20),
        is_active           BOOLEAN        NOT NULL DEFAULT TRUE,
        created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS bookings (
        id              SERIAL PRIMARY KEY,
        listing_id      INT            NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
        guest_name      VARCHAR(100)   NOT NULL,
        guest_phone     VARCHAR(20)    NOT NULL,
        check_in        DATE           NOT NULL,
        check_out       DATE           NOT NULL,
        guests          INT            NOT NULL,
        total_price_bdt NUMERIC(12,2)  NOT NULL,
        status          VARCHAR(20)    NOT NULL DEFAULT 'confirmed'
                            CHECK (status IN ('pending','confirmed','cancelled')),
        created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
        CONSTRAINT chk_dates CHECK (check_out > check_in)
    );

    CREATE TABLE IF NOT EXISTS conversations (
        id          VARCHAR(100) PRIMARY KEY,
        messages    JSONB        NOT NULL DEFAULT '[]',
        intent_last VARCHAR(20),
        escalated   BOOLEAN      NOT NULL DEFAULT FALSE,
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("✅ Tables created (listings, bookings, conversations).")


def seed_listings(conn):
    """Insert 10 dummy listings across popular BD locations."""
    listings = [
        # Cox's Bazar
        ("Sea Pearl Beach Resort",   "Cox's Bazar", "Marine Drive Rd, Cox's Bazar 4700",
         "৫ তারকা বিলাসবহুল বিচফ্রন্ট রিসোর্ট। সমুদ্রের ঢেউয়ের শব্দ শুনতে শুনতে ঘুমান।",
         8500, 4, ["WiFi","AC","Pool","Restaurant","Sea View","Spa","Gym"], "Karim Hossain",   "+8801711000001"),
        ("Long Beach Hotel",          "Cox's Bazar", "Kolatoli Beach Rd, Cox's Bazar",
         "মিড-রেঞ্জ হোটেল, সরাসরি সমুদ্র দর্শন। পরিবারের জন্য আদর্শ।",
         5200, 2, ["WiFi","AC","Sea View","Parking","Room Service"],        "Nadia Islam",     "+8801721000002"),
        ("Sayeman Beach Resort",      "Cox's Bazar", "Himchori, Cox's Bazar 4701",
         "বাজেট-ফ্রেন্ডলি রিসোর্ট। সুন্দর বাগান ও রেস্তোরাঁ সহ।",
         4800, 3, ["WiFi","AC","Restaurant","Garden","Parking"],             "Reza Ahmed",      "+8801731000003"),
        ("Coral Reef Guest House",    "Cox's Bazar", "Sugandha Beach, Cox's Bazar",
         "ছোট ও আরামদায়ক গেস্ট হাউস। বন্ধু বা কাপলের জন্য পারফেক্ট।",
         2800, 2, ["WiFi","AC","Breakfast Included"],                        "Sumaiya Khatun",  "+8801741000004"),

        # Sylhet
        ("Rose View Hotel Sylhet",    "Sylhet",      "Airport Rd, Sylhet 3100",
         "সিলেটের কেন্দ্রে আধুনিক বিজনেস হোটেল। চা-বাগান ভ্রমণের বেস হিসেবে আদর্শ।",
         3500, 2, ["WiFi","AC","Gym","Conference Room","Restaurant"],        "Fatema Begum",    "+8801751000005"),
        ("Nazimgarh Garden Resort",   "Sylhet",      "Salutikor, Sylhet 3100",
         "প্রকৃতির মাঝে পাহাড়ি রিসোর্ট। চা বাগান ও ঝরনা কাছেই।",
         6000, 4, ["WiFi","AC","Pool","Garden","Trekking","BBQ"],            "Touhid Rahman",   "+8801761000006"),

        # Sajek
        ("Sajek Valley Resort",       "Sajek",       "Sajek Valley, Rangamati 4500",
         "মেঘের রাজ্যে পাহাড়ের চূড়ায় ইকো রিসোর্ট। সূর্যোদয় অবিশ্বাস্য সুন্দর!",
         6200, 4, ["WiFi","Mountain View","Bonfire","Trekking","Generator"], "Tanvir Alam",     "+8801771000007"),
        ("Meghna Cottage Sajek",      "Sajek",       "Ruihlui Para, Sajek, Rangamati",
         "সাজেকের নিরিবিলি কটেজ। মেঘ ও সূর্যাস্তের অপূর্ব দৃশ্য।",
         3800, 2, ["Mountain View","Bonfire","Breakfast","Generator"],       "Mitu Chakma",     "+8801781000008"),

        # Sundarbans
        ("Sundarban Tiger Camp",      "Sundarbans",  "Kotka, Bagerhat, Khulna 9300",
         "সুন্দরবনের ভেতরে ইকো-ক্যাম্প। বাঘ দেখার সুযোগ আছে!",
         7500, 6, ["Generator","Boat Tour","Meals Included","Guide Service"],"Jahangir Molla",  "+8801791000009"),

        # Bandarban
        ("Nilgiri Hill Resort",       "Bandarban",   "Nilgiri, Bandarban 4600",
         "বাংলাদেশের সর্বোচ্চ রিসোর্ট (২২০০ ফুট)। মেঘের উপরে ঘুম!",
         9000, 2, ["WiFi","AC","Mountain View","Restaurant","Army Managed"], "Minhaz Sarkar",   "+8801801000010"),
    ]

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM listings")
        count = cur.fetchone()[0]
        if count > 0:
            print(f"ℹ️  Listings already has {count} rows — skipping seed.")
            return

        cur.executemany(
            """INSERT INTO listings
               (name, location, address, description, price_per_night_bdt,
                max_guests, amenities, host_name, host_phone)
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            [(n, loc, addr, desc, price, mg, json.dumps(am, ensure_ascii=False), hn, hp)
             for n, loc, addr, desc, price, mg, am, hn, hp in listings]
        )
    conn.commit()
    print(f"✅ Inserted {len(listings)} listings.")


def seed_bookings(conn):
    """Insert a few sample bookings (some confirmed, one cancelled)."""
    bookings = [
        # listing_id, guest_name, guest_phone, check_in, check_out, guests, total, status
        (1, "Rahim Uddin",     "+8801911111111", "2025-08-10", "2025-08-12", 2,  17000, "confirmed"),
        (2, "Sultana Begum",   "+8801922222222", "2025-08-15", "2025-08-17", 2,  10400, "confirmed"),
        (5, "Arif Hossain",    "+8801933333333", "2025-08-20", "2025-08-22", 1,   7000, "confirmed"),
        (7, "Priya Chakraborty","+8801944444444", "2025-08-05", "2025-08-07", 3,  12400, "cancelled"),
        (3, "Monir Khan",      "+8801955555555", "2025-09-01", "2025-09-03", 2,   9600, "pending"),
    ]
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bookings")
        if cur.fetchone()[0] > 0:
            print("ℹ️  Bookings already seeded — skipping.")
            return
        cur.executemany(
            """INSERT INTO bookings
               (listing_id, guest_name, guest_phone, check_in, check_out,
                guests, total_price_bdt, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            bookings
        )
    conn.commit()
    print(f"✅ Inserted {len(bookings)} sample bookings.")


def seed_conversations(conn):
    """Insert a sample multi-turn conversation for testing the history endpoint."""
    now = datetime.now(timezone.utc).isoformat()
    messages = [
        {"role": "user",      "content": "I need a room in Cox's Bazar for 2 nights for 2 guests", "timestamp": now},
        {"role": "assistant", "content": "Found 4 properties in Cox's Bazar! Sea Pearl Beach Resort (৳8,500/night), Long Beach Hotel (৳5,200/night)...", "timestamp": now},
        {"role": "user",      "content": "Tell me more about Long Beach Hotel", "timestamp": now},
        {"role": "assistant", "content": "Long Beach Hotel is located at Kolatoli Beach Rd. Price: ৳5,200/night. Max 2 guests. Amenities: WiFi, AC, Sea View, Parking, Room Service. Host: Nadia Islam (+8801721000002)", "timestamp": now},
    ]
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM conversations")
        if cur.fetchone()[0] > 0:
            print("ℹ️  Conversations already seeded — skipping.")
            return
        cur.execute(
            """INSERT INTO conversations (id, messages, intent_last, escalated)
               VALUES (%s, %s::jsonb, %s, %s)""",
            ("conv_demo_001", json.dumps(messages, ensure_ascii=False), "details", False)
        )
    conn.commit()
    print("✅ Inserted 1 sample conversation (id: conv_demo_001).")


def verify(conn):
    """Print row counts so you can confirm everything was inserted."""
    print("\n📊 Row counts:")
    with conn.cursor() as cur:
        for table in ["listings", "bookings", "conversations"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"   {table:20s} → {cur.fetchone()[0]} rows")

    print("\n🏨 Sample listings (first 5):")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, price_per_night_bdt, max_guests
            FROM listings ORDER BY id LIMIT 5
        """)
        rows = cur.fetchall()
        print(f"   {'ID':<4} {'Name':<30} {'Location':<15} {'Price/night (BDT)':<20} {'Max Guests'}")
        print("   " + "-"*80)
        for r in rows:
            print(f"   {r[0]:<4} {r[1]:<30} {r[2]:<15} ৳{r[3]:<19} {r[4]}")


def main():
    print("🚀 StayEase DB Setup Starting...\n")
    create_database()

    conn = get_conn()
    try:
        create_tables(conn)
        seed_listings(conn)
        seed_bookings(conn)
        seed_conversations(conn)
        verify(conn)
    finally:
        conn.close()

    print("\n✅ Setup complete!")
    print(f"   DATABASE_URL = postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{DB_NAME}")


if __name__ == "__main__":
    main()
