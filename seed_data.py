# -*- coding: utf-8 -*-
"""
Generates demo/sample listings so the site isn't empty on first boot.
Runs only once: if the database already has at least `target_count`
listings, it does nothing (idempotent, safe to call on every startup).

Photos are generated locally with Pillow (simple colored placeholder
images with the listing title) instead of being copied from real estate
sites, to avoid any copyright/scraping concerns.
"""
import io
import random

from PIL import Image, ImageDraw, ImageFont

import db
from security import generate_password_hash

CITY_COORDS = {
    "Tunis (Centre)": (36.8065, 10.1815),
    "La Marsa": (36.8781, 10.3247),
    "Jardins de Carthage": (36.8508, 10.3122),
    "Ariana": (36.8625, 10.1956),
    "Ben Arous": (36.7469, 10.2278),
    "Sousse": (35.8256, 10.6412),
    "Monastir": (35.7643, 10.8113),
    "Sfax": (34.7406, 10.7603),
    "Nabeul": (36.4561, 10.7376),
    "Hammamet": (36.4000, 10.6167),
    "Djerba": (33.8076, 10.8451),
    "Bizerte": (37.2744, 9.8739),
    "Kairouan": (35.6781, 10.0963),
    "Gabès": (33.8814, 10.0982),
}

CATEGORY_COLORS = {
    "apartment": [(41, 98, 128), (58, 124, 158)],
    "house": [(120, 79, 55), (150, 105, 75)],
    "villa": [(30, 110, 90), (45, 140, 115)],
    "land": [(100, 120, 40), (130, 150, 60)],
    "commercial": [(90, 60, 120), (120, 85, 150)],
}

TITLES = {
    "apartment": ["Bel appartement lumineux", "Appartement moderne", "Appartement familial",
                  "Appartement avec balcon", "Appartement refait a neuf", "Appartement standing"],
    "house": ["Maison de ville", "Maison avec jardin", "Maison traditionnelle",
              "Maison individuelle", "Duplex neuf"],
    "villa": ["Villa avec piscine", "Villa de charme", "Villa moderne",
              "Villa avec jardin", "Villa vue mer"],
    "land": ["Terrain constructible", "Terrain agricole", "Terrain viabilise", "Grand terrain"],
    "commercial": ["Local commercial", "Bureau professionnel",
                   "Local en rez-de-chaussee", "Depot / entrepot"],
}

DESCRIPTIONS = [
    "Bien situe, proche de toutes commodites (ecoles, commerces, transports).",
    "Finitions soignees, lumineux, ideal pour famille ou investissement locatif.",
    "Quartier calme et recherche, a proximite des axes principaux.",
    "Bonne opportunite, vente/location rapide souhaitee.",
    "Prestations de qualite, entretien impeccable.",
]

_font_cache = {}


def _font(size):
    if size in _font_cache:
        return _font_cache[size]
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    f = None
    for c in candidates:
        try:
            f = ImageFont.truetype(c, size)
            break
        except Exception:
            continue
    if f is None:
        f = ImageFont.load_default()
    _font_cache[size] = f
    return f


def _make_image_bytes(title, subtitle, category):
    W, H = 900, 600
    c1, c2 = CATEGORY_COLORS.get(category, [(70, 70, 70), (100, 100, 100)])
    img = Image.new("RGB", (W, H), c1)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    draw.polygon([(650, 450), (650, 300), (750, 220), (850, 300), (850, 450)], fill=(255, 255, 255))
    draw.rectangle([650, 450, 850, 470], fill=(255, 255, 255))
    draw.text((40, 40), "ImmoTunisie", font=_font(44), fill=(255, 255, 255))
    draw.text((40, H - 140), title[:30], font=_font(38), fill=(255, 255, 255))
    draw.text((40, H - 80), subtitle, font=_font(26), fill=(230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def run_seed(target_count=110):
    demo_user = db.get_user_by_email("demo@immotunisie.tn")
    if not demo_user:
        uid = db.create_user(
            "ImmoTunisie Demo", "demo@immotunisie.tn", "20123456",
            generate_password_hash("DemoPass123!"),
        )
    else:
        uid = demo_user.id

    # Backfill contact_phone on any demo listings created before this field
    # existed, so "Contact seller" / WhatsApp always works on demo data.
    db.backfill_missing_contact_phone(uid, "20123456")

    existing = db.list_listings()
    if len(existing) >= target_count:
        return 0

    rng = random.Random(42)
    cities = list(db.CITY_AVG_PRICE_M2.keys())
    categories = list(CATEGORY_COLORS.keys())
    needed = target_count - len(existing)
    created = 0

    for _ in range(needed):
        category = rng.choice(categories)
        city = rng.choice(cities)
        listing_type = rng.choices(["sale", "rent"], weights=[0.75, 0.25])[0]
        avg = db.CITY_AVG_PRICE_M2[city]

        if category == "land":
            surface = rng.randint(150, 1000)
            rooms = None
        elif category == "commercial":
            surface = rng.randint(30, 250)
            rooms = None
        elif category == "villa":
            surface = rng.randint(180, 500)
            rooms = rng.randint(4, 8)
        elif category == "house":
            surface = rng.randint(100, 250)
            rooms = rng.randint(3, 6)
        else:
            surface = rng.randint(45, 160)
            rooms = rng.randint(1, 4)

        # Mix of realistic / above-market / below-market evaluations
        ratio = rng.choice([
            rng.uniform(0.6, 0.82),
            rng.uniform(0.88, 1.12),
            rng.uniform(1.18, 1.5),
        ])
        if listing_type == "rent":
            price = round(surface * rng.uniform(4, 12), -1)
        else:
            price = round(surface * avg * ratio, -2)

        title = f"{rng.choice(TITLES[category])} - {city}"
        description = rng.choice(DESCRIPTIONS) + " " + rng.choice(DESCRIPTIONS)
        lat_base, lng_base = CITY_COORDS.get(city, (34.0, 9.0))
        lat = round(lat_base + rng.uniform(-0.02, 0.02), 6)
        lng = round(lng_base + rng.uniform(-0.02, 0.02), 6)

        listing_id = db.create_listing(
            user_id=uid, title=title, description=description,
            listing_type=listing_type, category=category, city=city,
            surface=float(surface), price=float(price),
            rooms=rooms, lat=lat, lng=lng, contact_phone="20123456", published=True,
        )
        img_bytes = _make_image_bytes(title, city, category)
        db.add_photo(listing_id, f"demo_{listing_id}.jpg", img_bytes, "image/jpeg")
        created += 1

    return created
