import os, sqlite3, time, secrets, re
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from dotenv import load_dotenv
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("DATABASE_PATH", str(INSTANCE_DIR / "weather.db")))
app = Flask(__name__, instance_path=str(INSTANCE_DIR))
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-this-secret-key"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "SkyCast-Student-Weather-App/2.0"})
_CACHE = {}
CACHE_TTL = int(os.getenv("WEATHER_CACHE_SECONDS", "600"))
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS favorites (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          city TEXT NOT NULL,
          latitude REAL NOT NULL,
          longitude REAL NOT NULL,
          UNIQUE(user_id, city)
        );
        CREATE TABLE IF NOT EXISTS preferences (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          unit TEXT NOT NULL DEFAULT 'C',
          theme TEXT NOT NULL DEFAULT 'dark'
        );
        """)
def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]
app.jinja_env.globals["csrf_token"] = csrf_token
@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            if request.path in ("/login", "/register"):
                flash("Your form expired. Please try again.", "error")
                return redirect(request.path)
            return jsonify({"error": "Security token expired. Refresh the page and try again."}), 400
def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db() as conn:
        row = conn.execute("SELECT id, username, email FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None
@app.context_processor
def inject_user():
    return {"user": current_user()}
def cache_get(key):
    item = _CACHE.get(key)
    if item and time.time() - item[0] < CACHE_TTL:
        return item[1]
    if item:
        _CACHE.pop(key, None)
    return None
def cache_set(key, value):
    # Bound the in-process cache so it cannot grow forever.
    if len(_CACHE) > 250:
        oldest = sorted(_CACHE, key=lambda k: _CACHE[k][0])[:75]
        for key_old in oldest:
            _CACHE.pop(key_old, None)
    _CACHE[key] = (time.time(), value)
def get_json(url, params):
    response = HTTP.get(url, params=params, timeout=12)
    response.raise_for_status()
    return response.json()
def geocode(city):
    key = "geo:" + city.casefold()
    cached = cache_get(key)
    if cached:
        return cached
    data = get_json(GEOCODING_URL, {"name": city, "count": 5, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        return None
    locations = [{"name": r.get("name", city), "admin1": r.get("admin1", ""),
                  "country": r.get("country", ""), "latitude": r["latitude"],
                  "longitude": r["longitude"], "timezone": r.get("timezone", "auto")}
                 for r in results]
    cache_set(key, locations)
    return locations
def condition(code):
    code = int(code or 0)
    if code == 0: return "Clear sky"
    if code in (1, 2): return "Mostly clear" if code == 1 else "Partly cloudy"
    if code == 3: return "Overcast"
    if code in (45, 48): return "Fog"
    if code in (51, 53, 55, 56, 57): return "Drizzle"
    if code in (61, 63, 65, 66, 67): return "Rain"
    if code in (71, 73, 75, 77): return "Snow"
    if code in (80, 81, 82): return "Rain showers"
    if code in (85, 86): return "Snow showers"
    if code in (95, 96, 99): return "Thunderstorm"
    return "Changeable conditions"
def build_weather(lat, lon, location_name):
    cache_key = f"weather:{round(float(lat),3)}:{round(float(lon),3)}"
    cached = cache_get(cache_key)
    if cached:
        result = dict(cached)
        result["location"] = location_name
        result["cached"] = True
        return result
    params = {
      "latitude": lat, "longitude": lon,
      "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl,visibility,cloud_cover,is_day,uv_index",
      "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,relative_humidity_2m,uv_index,visibility",
      "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,uv_index_max",
      "forecast_days": 7, "forecast_hours": 24, "timezone": "auto", "wind_speed_unit": "kmh"
    }
    data = get_json(WEATHER_URL, params)
    cur, hourly, daily = data.get("current", {}), data.get("hourly", {}), data.get("daily", {})
    def arr(obj, key): return obj.get(key, []) or []
    hourly_items = []
    times = arr(hourly, "time")
    for i, stamp in enumerate(times):
        hourly_items.append({"time": stamp, "temperature": val(hourly,"temperature_2m",i),
          "rain_chance": val(hourly,"precipitation_probability",i), "precipitation": val(hourly,"precipitation",i),
          "weather_code": val(hourly,"weather_code",i), "wind": val(hourly,"wind_speed_10m",i),
          "humidity": val(hourly,"relative_humidity_2m",i), "uv": val(hourly,"uv_index",i), "visibility": val(hourly,"visibility",i)})
    daily_items = []
    for i, stamp in enumerate(arr(daily,"time")):
        daily_items.append({"date": stamp, "max": val(daily,"temperature_2m_max",i), "min": val(daily,"temperature_2m_min",i),
          "rain_chance": val(daily,"precipitation_probability_max",i), "weather_code": val(daily,"weather_code",i),
          "sunrise": val(daily,"sunrise",i), "sunset": val(daily,"sunset",i), "uv_max": val(daily,"uv_index_max",i)})
    air = {}
    try:
        airdata = get_json(AIR_URL, {"latitude": lat, "longitude": lon,
          "current": "us_aqi,european_aqi,pm2_5,pm10,ozone,nitrogen_dioxide",
          "hourly": "us_aqi,pm2_5,pm10,uv_index", "forecast_hours": 24, "timezone": "auto"})
        ac = airdata.get("current", {})
        air = {"us_aqi": ac.get("us_aqi"), "european_aqi": ac.get("european_aqi"), "pm2_5": ac.get("pm2_5"),
               "pm10": ac.get("pm10"), "ozone": ac.get("ozone"), "nitrogen_dioxide": ac.get("nitrogen_dioxide")}
    except (requests.RequestException, ValueError, TypeError):
        air = {"unavailable": True}
    alerts = []
    max_rain = max([x["rain_chance"] for x in hourly_items if isinstance(x.get("rain_chance"),(int,float))] or [0])
    max_uv = max([x["uv"] for x in hourly_items if isinstance(x.get("uv"),(int,float))] or [0])
    if max_rain >= 60: alerts.append({"level":"info", "title":"Rain is possible", "message":f"Rain probability reaches {round(max_rain)}% in the next 24 hours. Consider carrying an umbrella."})
    if max_uv >= 6: alerts.append({"level":"warning", "title":"Elevated UV index", "message":"UV levels may be high. Consider shade, sunscreen and protective clothing during midday."})
    if any(isinstance(x.get("weather_code"),int) and x["weather_code"] in (95,96,99) for x in hourly_items):
        alerts.append({"level":"warning", "title":"Thunderstorm conditions in forecast", "message":"A thunderstorm code appears in the forecast. Check official local advisories before travelling."})
    if not alerts: alerts.append({"level":"good", "title":"No app-generated advisories", "message":"No threshold-based advisory was triggered. This is not an official warning feed."})
    result = {"location": location_name, "latitude":float(lat), "longitude":float(lon), "timezone":data.get("timezone",""),
      "updated_at":cur.get("time"), "temperature":cur.get("temperature_2m"), "feels_like":cur.get("apparent_temperature"),
      "humidity":cur.get("relative_humidity_2m"), "precipitation":cur.get("precipitation"), "wind_speed":cur.get("wind_speed_10m"),
      "wind_direction":cur.get("wind_direction_10m"), "pressure":cur.get("pressure_msl"), "visibility":(hourly_items[0].get("visibility") if hourly_items else None),
      "cloud_cover":cur.get("cloud_cover"), "uv_index":cur.get("uv_index"), "is_day":cur.get("is_day",1),
      "weather_code":cur.get("weather_code",0), "condition":condition(cur.get("weather_code",0)),
      "hourly":hourly_items, "forecast":daily_items, "air":air, "alerts":alerts, "cached":False}
    cache_set(cache_key, result)
    return result
def val(obj, key, i):
    values = obj.get(key, []) or []
    return values[i] if i < len(values) else None
@app.route("/")
def home():
    return render_template("index.html")
@app.route("/health")
def health():
    return jsonify({"status":"ok", "service":"SkyCast Weather API"})
@app.route("/api/weather")
def api_weather():
    city = re.sub(r"\s+", " ", request.args.get("city", "").strip())[:100]
    lat, lon = request.args.get("lat"), request.args.get("lon")
    try:
        if lat is not None and lon is not None:
            latf, lonf = float(lat), float(lon)
            if not (-90 <= latf <= 90 and -180 <= lonf <= 180):
                return jsonify({"error":"Location coordinates are invalid."}), 400
            label = request.args.get("label", "Your location")[:120]
        elif city:
            places = geocode(city)
            if not places:
                return jsonify({"error":"We couldn't find that city. Try adding the state or country."}), 404
            place = places[0]
            latf, lonf = place["latitude"], place["longitude"]
            label = ", ".join(p for p in [place["name"], place["admin1"], place["country"]] if p)
        else:
            return jsonify({"error":"Enter a city or allow location access."}), 400
        return jsonify(build_weather(latf, lonf, label))
    except (requests.RequestException, ValueError) as exc:
        app.logger.warning("Weather API error: %s", exc)
        return jsonify({"error":"Weather service is temporarily unavailable. Check your connection and try again."}), 503
@app.route("/api/suggest")
def api_suggest():
    city = re.sub(r"\s+", " ", request.args.get("city", "").strip())[:100]
    if len(city) < 2:
        return jsonify({"locations": []})
    try:
        return jsonify({"locations": geocode(city) or []})
    except requests.RequestException:
        return jsonify({"locations": []})
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
            flash("Username must be 3–24 letters, numbers or underscores.", "error")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "error")
        elif len(password) < 8:
            flash("Use a password with at least 8 characters.", "error")
        else:
            try:
                with db() as conn:
                    cur = conn.execute("INSERT INTO users(username,email,password_hash) VALUES(?,?,?)", (username,email,generate_password_hash(password)))
                    uid = cur.lastrowid
                    conn.execute("INSERT INTO preferences(user_id,unit,theme) VALUES(?,?,?)", (uid,"C","dark"))
                session.clear(); session["user_id"] = uid; csrf_token()
                flash("Your account is ready.", "success")
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.", "error")
    return render_template("auth.html", mode="register")
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        identity = request.form.get("identity", "").strip().lower()
        password = request.form.get("password", "")
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE lower(username)=? OR email=?", (identity,identity)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear(); session["user_id"] = row["id"]; csrf_token()
            flash("Welcome back.", "success")
            return redirect(url_for("home"))
        flash("Incorrect username/email or password.", "error")
    return render_template("auth.html", mode="login")
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))
@app.route("/api/me")
def api_me():
    user = current_user()
    if not user: return jsonify({"logged_in":False})
    with db() as conn:
        pref = conn.execute("SELECT unit,theme FROM preferences WHERE user_id=?", (user["id"],)).fetchone()
    return jsonify({"logged_in":True,"user":user,"preferences":dict(pref) if pref else {"unit":"C","theme":"dark"}})
@app.route("/api/favorites", methods=["GET","POST","DELETE"])
def api_favorites():
    user = current_user()
    if not user: return jsonify({"error":"Sign in to sync favorite cities across devices."}), 401
    if request.method == "GET":
        with db() as conn:
            rows = conn.execute("SELECT id,city,latitude,longitude FROM favorites WHERE user_id=? ORDER BY city", (user["id"],)).fetchall()
        return jsonify({"favorites":[dict(r) for r in rows]})
    payload = request.get_json(silent=True) or {}
    if request.method == "POST":
        city = str(payload.get("city", "")).strip()[:100]
        try: lat, lon = float(payload["latitude"]), float(payload["longitude"])
        except (KeyError,TypeError,ValueError): return jsonify({"error":"Invalid favorite location."}), 400
        if not city or not (-90 <= lat <= 90 and -180 <= lon <= 180): return jsonify({"error":"Invalid favorite location."}), 400
        try:
            with db() as conn:
                conn.execute("INSERT INTO favorites(user_id,city,latitude,longitude) VALUES(?,?,?,?)", (user["id"],city,lat,lon))
        except sqlite3.IntegrityError:
            pass
        return jsonify({"ok":True})
    try: fav_id = int(payload.get("id"))
    except (TypeError,ValueError): return jsonify({"error":"Favorite ID is invalid."}), 400
    with db() as conn: conn.execute("DELETE FROM favorites WHERE id=? AND user_id=?", (fav_id,user["id"]))
    return jsonify({"ok":True})
@app.route("/api/preferences", methods=["POST"])
def api_preferences():
    user = current_user()
    if not user: return jsonify({"error":"Sign in to save preferences."}), 401
    payload = request.get_json(silent=True) or {}
    unit = payload.get("unit", "C"); theme = payload.get("theme", "dark")
    if unit not in ("C","F") or theme not in ("dark","light"): return jsonify({"error":"Invalid preference."}), 400
    with db() as conn:
        conn.execute("INSERT INTO preferences(user_id,unit,theme) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET unit=excluded.unit,theme=excluded.theme", (user["id"],unit,theme))
    return jsonify({"ok":True})
@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self)"
    return response
init_db()
if __name__ == "__main__":
    # Debug is disabled by default to avoid exposing the interactive debugger.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "0") == "1")
