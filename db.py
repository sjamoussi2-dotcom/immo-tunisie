# -*- coding: utf-8 -*-
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
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
            created_at TEXT,
            published INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
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
    def __init__(self, filename):
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
    cur = conn.execute(
        "INSERT INTO users (name, email, phone, password_hash, created_at) VALUES (?,?,?,?,?)",
        (name, email, phone, password_hash, datetime.utcnow().isoformat()),
    )
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return User(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return User(row) if row else None


def _hydrate_listing(conn, row):
    owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    owner = Owner(owner_row["id"], owner_row["name"], owner_row["email"], owner_row["phone"])
    photo_rows = conn.execute(
        "SELECT filename FROM photos WHERE listing_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    photos = [Photo(p["filename"]) for p in photo_rows]
    return Listing(row, owner, photos)


def create_listing(user_id, title, description, listing_type, category, city,
                    surface, price, rooms, published=True):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO listings
           (user_id, title, description, listing_type, category, city, surface,
            price, rooms, created_at, published)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, title, description, listing_type, category, city, surface,
         price, rooms, datetime.utcnow().isoformat(), int(published)),
    )
    conn.commit()
    listing_id = cur.lastrowid
    conn.close()
    return listing_id


def add_photo(listing_id, filename):
    conn = get_conn()
    conn.execute("INSERT INTO photos (listing_id, filename) VALUES (?,?)", (listing_id, filename))
    conn.commit()
    conn.close()


def get_listing(listing_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
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
        like = f"%{filters['q']}%"
        params.extend([like, like])
    query += " ORDER BY created_at DESC"

    conn = get_conn()
    rows = conn.execute(query, params).fetchall()
    result = [_hydrate_listing(conn, r) for r in rows]
    conn.close()
    return result


def list_listings_by_user(user_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    result = [_hydrate_listing(conn, r) for r in rows]
    conn.close()
    return result


def delete_listing(listing_id, user_id):
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if not row or row["user_id"] != user_id:
        conn.close()
        return False
    conn.execute("DELETE FROM photos WHERE listing_id = ?", (listing_id,))
    conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
    conn.commit()
    conn.close()
    return True

