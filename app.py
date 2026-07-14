# -*- coding: utf-8 -*-
import mimetypes
import os

import db
import seed_data
from db import CITY_AVG_PRICE_M2
from miniweb import App, Request, Response, redirect, abort
import miniweb
from security import generate_password_hash, check_password_hash, secure_filename
from translations import t

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

CATEGORIES = ["apartment", "house", "villa", "land", "commercial"]
LISTING_TYPES = ["sale", "rent"]

db.init_db()
seed_data.run_seed()

app = App(TEMPLATE_DIR, STATIC_DIR)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def current_lang(request):
    return request.session.get("lang", "fr")


def load_current_user(app_, request):
    uid = request.session.get("user_id")
    request.user = db.get_user_by_id(uid) if uid else db.AnonymousUser()


miniweb.before_user_hook = load_current_user


@app.context_processor
def inject_globals(request):
    lang = current_lang(request)
    return {
        "t": lambda key: t(key, lang),
        "lang": lang,
        "is_rtl": lang == "ar",
        "cities": sorted(CITY_AVG_PRICE_M2.keys()),
        "categories": CATEGORIES,
        "listing_types": LISTING_TYPES,
    }


def login_required(fn):
    def wrapper(request, *args, **kwargs):
        if not request.user or not request.user.is_authenticated:
            return redirect(app.url_for("login") + "?next=" + request.path)
        return fn(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------
@app.route("/set_language/<lang_code>", endpoint="set_language")
def set_language(request, lang_code):
    if lang_code in ("fr", "ar", "en"):
        request.session["lang"] = lang_code
    return redirect(request.referrer or app.url_for("index"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/register", endpoint="register", methods=("GET", "POST"))
def register(request):
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        if db.get_user_by_email(email):
            app.flash(request, t("email_taken", current_lang(request)), "danger")
            return app.render_template(request, "register.html")

        pw_hash = generate_password_hash(password)
        uid = db.create_user(name, email, phone, pw_hash)
        request.session["user_id"] = uid
        return redirect(app.url_for("index"))

    return app.render_template(request, "register.html")


@app.route("/login", endpoint="login", methods=("GET", "POST"))
def login(request):
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if user and check_password_hash(user.password_hash, password):
            request.session["user_id"] = user.id
            next_url = request.args.get("next") or app.url_for("index")
            return redirect(next_url)
        app.flash(request, t("login_error", current_lang(request)), "danger")
    return app.render_template(request, "login.html")


@app.route("/logout", endpoint="logout")
def logout(request):
    request.session.pop("user_id", None)
    return redirect(app.url_for("index"))


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------
@app.route("/", endpoint="index")
def index(request):
    filters = {
        "city": request.args.get("city"),
        "type": request.args.get("type"),
        "price_min": request.args.get("price_min"),
        "price_max": request.args.get("price_max"),
        "q": request.args.get("q"),
    }
    listings = db.list_listings(filters)
    return app.render_template(request, "index.html", listings=listings, filters=request.args)


@app.route("/listing/<int:listing_id>", endpoint="listing_detail")
def listing_detail(request, listing_id):
    listing = db.get_listing(listing_id)
    if not listing:
        abort(404)
    return app.render_template(request, "listing_detail.html", listing=listing)


@app.route("/listing/create", endpoint="create_listing", methods=("GET", "POST"))
@login_required
def create_listing(request):
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        listing_type = request.form.get("listing_type")
        category = request.form.get("category")
        city = request.form.get("city")
        surface = request.form.get("surface")
        price = request.form.get("price")
        rooms = request.form.get("rooms")
        lat = request.form.get("lat")
        lng = request.form.get("lng")

        files = [f for f in request.files.get("photos", []) if f and f.filename]

        errors = []
        if not files:
            errors.append(t("error_photo_required", current_lang(request)))

        try:
            surface_val = float(surface)
            price_val = float(price)
        except (TypeError, ValueError):
            surface_val, price_val = 0, 0
            errors.append("Surface / prix invalide")

        try:
            lat_val = float(lat)
            lng_val = float(lng)
        except (TypeError, ValueError):
            lat_val, lng_val = None, None
            errors.append(t("error_location_required", current_lang(request)))

        if errors:
            for e in errors:
                app.flash(request, e, "danger")
            return app.render_template(request, "create_listing.html", form=request.form)

        listing_id = db.create_listing(
            user_id=request.user.id,
            title=title,
            description=description,
            listing_type=listing_type,
            category=category,
            city=city,
            surface=surface_val,
            price=price_val,
            rooms=int(rooms) if rooms else None,
            lat=lat_val,
            lng=lng_val,
            published=True,
        )

        for f in files:
            if allowed_file(f.filename):
                filename = secure_filename(f.filename)
                mimetype = mimetypes.guess_type(filename)[0] or "image/jpeg"
                db.add_photo(listing_id, filename, f.read_bytes(), mimetype)

        app.flash(request, t("listing_created", current_lang(request)), "success")
        return redirect(app.url_for("listing_detail", listing_id=listing_id))

    return app.render_template(request, "create_listing.html", form={})


@app.route("/photo/<int:photo_id>", endpoint="photo")
def photo(request, photo_id):
    result = db.get_photo(photo_id)
    if not result:
        abort(404)
    data, mimetype = result
    return Response(data, content_type=mimetype)


@app.route("/dashboard", endpoint="dashboard")
@login_required
def dashboard(request):
    listings = db.list_listings_by_user(request.user.id)
    return app.render_template(request, "dashboard.html", listings=listings)


@app.route("/listing/<int:listing_id>/delete", endpoint="delete_listing", methods=("POST",))
@login_required
def delete_listing(request, listing_id):
    ok = db.delete_listing(listing_id, request.user.id)
    if not ok:
        abort(403)
    return redirect(app.url_for("dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
