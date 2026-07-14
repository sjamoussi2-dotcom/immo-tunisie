# -*- coding: utf-8 -*-
import os
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# If DATABASE_URL is set (e.g. on Render with a linked Postgres database),
# use Postgres so data survives free-plan restarts/spin-downs. Otherwise
# fall back to a local SQLite file (e.g. for local development).
DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = bool(DATABASE_URL)

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

DB_PATH = os.environ.get("IMMO_DB_PATH", os.path.join(BASE_DIR, "immo.db"))

CITY_AVG_PRICE_M2 = {
    "Tunis (Centre)": 3800,
    "La Marsa": 4700,
    "Jardins de Carthage": 5400,
    "Ariana": 3200,
    "Ben Arous": 2600,
    "Sousse": 3200,
    "Monastir": 2900,
    "Sfax": 2000,
    "Nabeul": 2800,
    "Hammamet": 3500,
    "Djerba": 2700,
    "Bizerte": 2300,
    "Kairouan": 1400,
    "Gabès": 1500,
}


def get_conn():
    if IS_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q(sql):
    """Translate sqlite-style '?' placeholders to psycopg2-style '%s' when needed."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def run(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(q(sql), params)
    return cur


def init_db():
    conn = get_conn()
    if IS_POSTGRES:
        conn.cursor().execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS listings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                listing_type TEXT NOT NULL,
                category TEXT NOT NULL,
                city TEXT NOT NULL,
                surface REAL NOT NULL,
                price REAL NOT NULL,
                rooms INTEGER,
                lat REAL,
                lng REAL,
                created_at TEXT,
                published INTEGER DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS photos (
                id SERIAL PRIMARY KEY,
                listing_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mimetype TEXT DEFAULT 'image/jpeg',
                data BYTEA NOT NULL,
                FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
            );
            """
        )
    else:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                listing_type TEXT NOT NULL,
                category TEXT NOT NULL,
                city TEXT NOT NULL,
                surface REAL NOT NULL,
                price REAL NOT NULL,
                rooms INTEGER,
                lat REAL,
                lng REAL,
                created_at TEXT,
                published INTEGER DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mimetype TEXT DEFAULT 'image/jpeg',
                data BLOB NOT NULL,
                FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
            );
            """
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Model wrappers
# ---------------------------------------------------------------------------
class Owner:
    def __init__(self, id, name, email, phone):
        self.id = id
        self.name = name
        self.email = email
        self.phone = phone


class Photo:
    def __init__(self, id, filename):
        self.id = id
        self.filename = filename


class Listing:
    def __init__(self, row, owner, photos):
        self.id = row["id"]
        self.user_id = row["user_id"]
        self.title = row["title"]
        self.description = row["description"]
        self.listing_type = row["listing_type"]
        self.category = row["category"]
        self.city = row["city"]
        self.surface = row["surface"]
        self.price = row["price"]
        self.rooms = row["rooms"]
        self.lat = row["lat"]
        self.lng = row["lng"]
        self.created_at = row["created_at"]
        self.published = bool(row["published"])
        self.owner = owner
        self.photos = photos

    @property
    def price_per_m2(self):
        return self.price / self.surface if self.surface else None

    def price_evaluation(self):
        avg = CITY_AVG_PRICE_M2.get(self.city)
        ppm2 = self.price_per_m2
        if not avg or not ppm2:
            return ("unknown", None, avg)
        ratio = ppm2 / avg
        if ratio > 1.15:
            return ("high", ratio, avg)
        if ratio < 0.85:
            return ("low", ratio, avg)
        return ("realistic", ratio, avg)


class User:
    def __init__(self, row):
        self.id = row["id"]
        self.name = row["name"]
        self.email = row["email"]
        self.phone = row["phone"]
        self.password_hash = row["password_hash"]
        self.is_authenticated = True


class AnonymousUser:
    is_authenticated = False
    name = None
    id = None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def create_user(name, email, phone, password_hash):
    conn = get_conn()
    created_at = datetime.utcnow().isoformat()
    sql = "INSERT INTO users (name, email, phone, password_hash, created_at) VALUES (?,?,?,?,?)"
    if IS_POSTGRES:
        cur = run(conn, sql + " RETURNING id", (name, email, phone, password_hash, created_at))
        uid = cur.fetchone()["id"]
    else:
        cur = run(conn, sql, (name, email, phone, password_hash, created_at))
        uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def get_user_by_email(email):
    conn = get_conn()
    cur = run(conn, "SELECT * FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return User(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    cur = run(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return User(row) if row else None


def _hydrate_listing(conn, row):
    cur = run(conn, "SELECT * FROM users WHERE id = ?", (row["user_id"],))
    owner_row = cur.fetchone()
    owner = Owner(owner_row["id"], owner_row["name"], owner_row["email"], owner_row["phone"])
    cur = run(conn, "SELECT id, filename FROM photos WHERE listing_id = ? ORDER BY id", (row["id"],))
    photo_rows = cur.fetchall()
    photos = [Photo(p["id"], p["filename"]) for p in photo_rows]
    return Listing(row, owner, photos)


def create_listing(user_id, title, description, listing_type, category, city,
                    surface, price, rooms, lat=None, lng=None, published=True):
    conn = get_conn()
    created_at = datetime.utcnow().isoformat()
    params = (user_id, title, description, listing_type, category, city, surface,
              price, rooms, lat, lng, created_at, int(published))
    sql = """INSERT INTO listings
             (user_id, title, description, listing_type, category, city, surface,
              price, rooms, lat, lng, created_at, published)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    if IS_POSTGRES:
        cur = run(conn, sql + " RETURNING id", params)
        listing_id = cur.fetchone()["id"]
    else:
        cur = run(conn, sql, params)
        listing_id = cur.lastrowid
    conn.commit()
    conn.close()
    return listing_id


def add_photo(listing_id, filename, data, mimetype="image/jpeg"):
    conn = get_conn()
    payload = psycopg2.Binary(data) if IS_POSTGRES else data
    run(conn, "INSERT INTO photos (listing_id, filename, mimetype, data) VALUES (?,?,?,?)",
        (listing_id, filename, mimetype, payload))
    conn.commit()
    conn.close()


def get_photo(photo_id):
    conn = get_conn()
    cur = run(conn, "SELECT data, mimetype FROM photos WHERE id = ?", (photo_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    data = bytes(row["data"])
    return data, (row["mimetype"] or "image/jpeg")


def get_listing(listing_id):
    conn = get_conn()
    cur = run(conn, "SELECT * FROM listings WHERE id = ?", (listing_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    listing = _hydrate_listing(conn, row)
    conn.close()
    return listing


def list_listings(filters=None):
    filters = filters or {}
    query = "SELECT * FROM listings WHERE published = 1"
    params = []
    if filters.get("city"):
        query += " AND city = ?"
        params.append(filters["city"])
    if filters.get("type"):
        query += " AND listing_type = ?"
        params.append(filters["type"])
    if filters.get("price_min"):
        query += " AND price >= ?"
        params.append(float(filters["price_min"]))
    if filters.get("price_max"):
        query += " AND price <= ?"
        params.append(float(filters["price_max"]))
    if filters.get("q"):
        query += " AND (title LIKE ? OR city LIKE ?)"
        like = f"%${filters['q']}%"
        params.extend([like, like])
    query += " ORDER BY created_at DESC"

    conn = get_conn()
    cur = run(conn, query, params)
    rows = cur.fetchall()
    result = [_hydrate_listing(conn, r) for r in rows]
    conn.close()
    return result


def list_listings_by_user(user_id):
    conn = get_conn()
    cur = run(conn, "SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = cur.fetchall()
    result = [_hydrate_listing(conn, r) for r in rows]
    conn.close()
    return result


def delete_listing(listing_id, user_id):
    conn = get_conn()
    cur = run(conn, "SELECT user_id FROM listings WHERE id = ?", (listing_id,))
    row = cur.fetchone()
    if not row or row["user_id"] != user_id:
        conn.close()
        return False
    run(conn, "DELETE FROM photos WHERE listing_id = ?", (listing_id,))
    run(conn, "DELETE FROM listings WHERE id = ?", (listing_id,))
    conn.commit()
    conn.close()
    return True
