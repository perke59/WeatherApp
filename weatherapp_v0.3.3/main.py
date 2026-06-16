from flask import Flask, make_response, send_file, jsonify, request, session, redirect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from waitress import serve
import requests
import sqlite3
import time
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVE_DIR = os.path.join(BASE_DIR, 'serve')
DB_PATH = os.path.join(BASE_DIR, 'weather_app.db')

with open(os.path.join(BASE_DIR, 'api_key.txt')) as f:
    api_key = f.read().strip(' \n')
assert len(api_key)==32

app = Flask(__name__)
app.secret_key = os.environ.get('WEATHER_APP_SECRET_KEY', 'weather-app-dev-secret-key')


def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith(("/get_data", "/history", "/favorites", "/current_user")):
                return jsonify({"error": "Login required"}), 401
            return redirect("/login")
        return route_function(*args, **kwargs)

    return wrapper


class WeatherDatabase:
    """Handles users, persistent search history, and favorite cities."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.initialize()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    city TEXT NOT NULL,
                    searched_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS favorite_cities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    city TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            self.ensure_column(conn, "search_history", "user_id", "INTEGER")
            self.migrate_favorites_table(conn)

    @staticmethod
    def ensure_column(conn, table_name, column_name, column_type):
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if column_name not in [column["name"] for column in columns]:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    @staticmethod
    def migrate_favorites_table(conn):
        columns = conn.execute("PRAGMA table_info(favorite_cities)").fetchall()
        column_names = [column["name"] for column in columns]
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'favorite_cities'"
        ).fetchone()["sql"]

        if "user_id" in column_names and "UNIQUE(user_id, city)" in table_sql:
            return

        conn.execute("ALTER TABLE favorite_cities RENAME TO favorite_cities_old")
        conn.execute("""
            CREATE TABLE favorite_cities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                city TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, city),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

    def create_user(self, username, password):
        username = username.strip()
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        password_hash = generate_password_hash(password)

        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (username, password_hash, created_at)
                )
            return None
        except sqlite3.IntegrityError:
            return "Username already exists"

    def get_user_by_username(self, username):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (username.strip(),)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, username FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_history(self, user_id, city):
        searched_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_history (user_id, city, searched_at)
                VALUES (?, ?, ?)
                """,
                (user_id, city, searched_at)
            )

    def get_history(self, user_id, limit=20):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT city, searched_at
                FROM search_history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_history(self, user_id):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM search_history WHERE user_id = ?",
                (user_id,)
            )

    def add_favorite(self, user_id, city):
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO favorite_cities (user_id, city, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, city, created_at)
            )

    def remove_favorite(self, user_id, city):
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM favorite_cities WHERE user_id = ? AND city = ?",
                (user_id, city)
            )

    def get_favorites(self, user_id):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT city, created_at
                FROM favorite_cities
                WHERE user_id = ?
                ORDER BY city COLLATE NOCASE
                """,
                (user_id,)
            ).fetchall()
        return [dict(row) for row in rows]


class WeatherService:
    """Validates input, calls the weather API, caches results, and processes data."""

    WEATHER_URL = 'https://api.openweathermap.org/data/2.5/weather'
    FORECAST_URL = 'https://api.openweathermap.org/data/2.5/forecast'

    def __init__(self, api_key, database, cache_ttl=3600):
        self.api_key = api_key
        self.database = database
        self.cache_ttl = cache_ttl
        self.data_cache = {}

    def get_weather_data(self, user_id, city_user_entered, save_history=True):
        city_user_entered = city_user_entered.strip()
        validation_error = self.validate_city(city_user_entered)
        if validation_error:
            return {"error": validation_error}

        city_key = city_user_entered.lower()
        cached_data = self.data_cache.get(city_key)

        if cached_data:
            last_update = time.time() - cached_data["last_updated"]
            if last_update <= self.cache_ttl:
                print(f'cache hit: {city_key}, last update was {(datetime(1970, 1, 1) + timedelta(seconds=last_update)).strftime("%H:%M:%S")} ago')
                if user_id and save_history:
                    self.database.add_history(user_id, cached_data["weather"]["name"])
                return cached_data

        print(f'fetching new data: {city_key}')
        data = self.fetch_data(city_user_entered)
        if "error" in data:
            return data

        self.data_cache[city_key] = data
        if user_id and save_history:
            self.database.add_history(user_id, data["weather"]["name"])
        return data

    @staticmethod
    def validate_city(city):
        if not city:
            return "Empty city name"

        forbidden_chars = set('_/\\-+0123456789')
        if any((c in forbidden_chars) for c in city):
            return "Invalid characters"

        return None

    @staticmethod
    def process_forecast(forecast_json, timezone_offset):
        daily_groups = {}

        for item in forecast_json['list']:
            local_ts = item['dt'] + timezone_offset
            dt_obj = datetime.utcfromtimestamp(local_ts)
            date_key = dt_obj.strftime('%Y-%m-%d')
            hour = dt_obj.hour
            temp = item['main']['temp']

            if date_key not in daily_groups:
                daily_groups[date_key] = {
                    'day_name': dt_obj.strftime('%a'),
                    'temps': []
                }

            if 10 <= hour <= 17:
                daily_groups[date_key]['temps'].append(temp)

        final_result = []
        for day in sorted(daily_groups.keys())[:7]:
            group = daily_groups[day]
            if group['temps']:
                avg_temp = sum(group['temps']) / len(group['temps'])
                final_result.append({
                    'day': group['day_name'],
                    'temp': round(avg_temp)
                })

        return final_result

    def fetch_data(self, city):
        try:
            params = {
                "q": city,
                "appid": self.api_key,
                "units": "metric"
            }

            weather_res = requests.get(self.WEATHER_URL, params=params, timeout=10)
            if not weather_res.ok:
                return {"error": "Weather API error"}
            weather_data = weather_res.json()

            forecast_res = requests.get(self.FORECAST_URL, params=params, timeout=10)
            if not forecast_res.ok:
                return {"error": "Forecast API error"}
            forecast_data = forecast_res.json()

            processed_days = self.process_forecast(forecast_data, weather_data['timezone'])

            return {
                "last_updated": time.time(),
                "weather": {
                    "name": weather_data['name'],
                    "temp": weather_data['main']['temp'],
                    "humidity": weather_data['main']['humidity'],
                    "wind": weather_data['wind']['speed'],
                    "lat": weather_data['coord']['lat'],
                    "lon": weather_data['coord']['lon'],
                    "condition": weather_data['weather'][0]['main'],
                    "description": weather_data['weather'][0]['description'],
                    "icon": weather_data['weather'][0]['icon']
                },
                "forecast": processed_days
            }
        except Exception as e:
            return {"error": str(e)}


database = WeatherDatabase(DB_PATH)
weather_service = WeatherService(api_key, database)


# >>> b = data['forecast']['list'][0]['dt']+data['weather']['timezone']
# >>> datetime.datetime.utcfromtimestamp(b).strftime('%Y-%m-%d %A %H:%M:%S')
# '2026-06-07 Sunday 05:00:00'
# >>> b = data['forecast']['list'][1]['dt']+data['weather']['timezone']
# >>> datetime.datetime.utcfromtimestamp(b).strftime('%Y-%m-%d %A %H:%M:%S')
# '2026-06-07 Sunday 08:00:00'
# >>> b = data['forecast']['list'][-1]['dt']+data['weather']['timezone']
# >>> datetime.datetime.utcfromtimestamp(b).strftime('%Y-%m-%d %A %H:%M:%S')
# '2026-06-12 Friday 02:00:00'
# >>>

# exit()

@app.route("/")
def index():
    return send_file(os.path.join(SERVE_DIR, 'index.html'), as_attachment=False)


@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect("/")
    return send_file(os.path.join(SERVE_DIR, 'login.html'), as_attachment=False)


@app.route("/register")
def register_page():
    if "user_id" in session:
        return redirect("/")
    return send_file(os.path.join(SERVE_DIR, 'register.html'), as_attachment=False)

@app.route("/favicon.ico")
def favicon():
    return send_file(os.path.join(SERVE_DIR, 'favicon.ico'), as_attachment=False)

@app.route("/contents/<string:filename>", methods=['GET'])
def contents(filename):
    filename = secure_filename(filename)  # Sanitize the filename
    file_path = os.path.join(SERVE_DIR, filename)
    if os.path.isfile(file_path):
        return send_file(file_path, as_attachment=False)
    else:
        return make_response(f"File '{filename}' not found.", 404)

@app.route("/get_data/<string:country_user_input>", methods=['GET'])
def get_data(country_user_input):
    try:
        save_history = request.args.get("save", "1") != "0"
        res = weather_service.get_weather_data(
            session.get("user_id"),
            country_user_input,
            save_history
        )
        return jsonify(res)

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": "An unexpected server error occurred."})


@app.route("/history", methods=['GET'])
@login_required
def get_history():
    return jsonify(database.get_history(session["user_id"]))


@app.route("/history", methods=['DELETE'])
@login_required
def clear_history():
    database.clear_history(session["user_id"])
    return jsonify({"message": "History cleared"})


@app.route("/favorites", methods=['GET'])
@login_required
def get_favorites():
    return jsonify(database.get_favorites(session["user_id"]))


@app.route("/favorites", methods=['POST'])
@login_required
def add_favorite():
    data = request.get_json(silent=True) or {}
    city = (data.get("city") or "").strip()
    validation_error = WeatherService.validate_city(city)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    database.add_favorite(session["user_id"], city)
    return jsonify({"city": city})


@app.route("/favorites/<string:city>", methods=['DELETE'])
@login_required
def remove_favorite(city):
    database.remove_favorite(session["user_id"], city)
    return jsonify({"city": city})


@app.route("/api/register", methods=['POST'])
def register_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    error = database.create_user(username, password)
    if error:
        return jsonify({"error": error}), 400

    user = database.get_user_by_username(username)
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"username": user["username"]})


@app.route("/api/login", methods=['POST'])
def login_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = database.get_user_by_username(username)

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"username": user["username"]})


@app.route("/api/logout", methods=['POST'])
def logout_user():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/current_user", methods=['GET'])
def current_user():
    if "user_id" not in session:
        return jsonify({"logged_in": False})

    return jsonify({
        "id": session["user_id"],
        "username": session["username"],
        "logged_in": True
    })



if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=5000)

# if __name__ == '__main__':  # pragma: no cover
#     app.run(port=80)
