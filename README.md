# SkyCast — Advanced Weather Forecast Application

A Flask weather application upgraded from the original Weather Forecast Cloud API project. It retains city search and seven-day forecasts while adding hourly forecasts/charts, air quality, forecast-based tips, favorites, optional accounts, saved preferences, caching, and an interactive map with RainViewer radar tiles.

## Stack
- Python + Flask backend
- HTML, CSS and JavaScript frontend
- Open-Meteo geocoding, forecast and air-quality APIs
- Leaflet + OpenStreetMap + RainViewer radar layer
- Chart.js for charts
- SQLite for accounts, favorites and preferences
- In-memory 10-minute API cache (per app process)

## Features
- Current weather, feels-like temperature, humidity, wind direction/speed, pressure, visibility, cloud cover and UV index
- Next 24 hours and 7-day forecast with temperature/rain charts
- Air quality index and selected pollutants when data is available
- Simple forecast-based recommendations and advisories (not official emergency alerts)
- City autocomplete, browser geolocation, Celsius/Fahrenheit
- Favorites in local browser storage; signed-in favorites sync to SQLite
- Account registration/login with hashed passwords; saved theme and temperature unit
- Responsive layout, dark/light mode, animations, loading/error states
- SQLite database, request timeout, bounded in-memory caching, CSRF checks and basic security headers
- `/health` endpoint for deployment checks

## Windows setup
1. Install Python 3.11 or newer and extract this ZIP.
2. Open PowerShell or Command Prompt in this folder.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, use Command Prompt and run `.venv\\Scripts\\activate.bat`, or run `.venv\\Scripts\\python.exe` directly.
4. Install dependencies:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
5. Create your local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   Edit `.env` and set `SECRET_KEY` to a long random value. Example generation: `python -c "import secrets; print(secrets.token_hex(32))"`.
6. Start the server:

   ```powershell
   python app.py
   ```
7. Open `http://127.0.0.1:5000` in your browser.

The SQLite database is created automatically at `instance/weather.db`. You do not need a paid weather API key for the default Open-Meteo endpoints. Internet access is required for live data and CDN assets.

## Cloud deployment (Render / similar Python host)
- Build command: `pip install -r requirements.txt`
- Start command on a Linux cloud host: `gunicorn --bind 0.0.0.0:$PORT app:app` (if the host does not provide `PORT`, use `gunicorn --bind 0.0.0.0:8000 app:app`)
- Set environment variables: `SECRET_KEY` (long random secret), `COOKIE_SECURE=1` when HTTPS is enabled, and optionally `WEATHER_CACHE_SECONDS=600`.
- Mount a persistent disk and set `DATABASE_PATH` to a file on that disk if SQLite data must survive deploys. Without persistent storage, local SQLite may be reset by the host.
- The current cache is per process; use Redis or another shared cache if you later scale to multiple workers.
- This starter app is suitable for a student project/demo. Before a public production launch, add stronger rate limiting, email verification/password reset, a production database, and a formal privacy/retention policy.

## Data and alert limitations
- Weather and air quality are model-based estimates from Open-Meteo; values can differ from local instruments.
- App-generated advisories are simple thresholds based on forecast data, not official government alerts.
- Radar imagery depends on RainViewer coverage and availability. Attribution is shown in the app.
- Geolocation works on localhost and HTTPS sites and requires user permission.

## API routes
- `GET /` — web app
- `GET /health` — health check
- `GET /api/weather?city=Pune` — weather by city
- `GET /api/weather?lat=18.52&lon=73.85&label=Pune` — weather by coordinates
- `GET /api/suggest?city=Pun` — autocomplete locations
- `GET /api/me` — current account/preferences
- `GET|POST|DELETE /api/favorites` — user favorites
- `POST /api/preferences` — save unit/theme
- `GET /login`, `POST /login`, `GET /register`, `POST /register`, `POST /logout` — account actions

## Project structure
```
Weather_Forecast_Cloud_API_Advanced/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── instance/              # SQLite DB is created here; do not commit it
├── templates/
│   ├── index.html
│   └── auth.html
└── static/
    ├── style.css
    └── script.js
```


## Mini game: Cloud Catcher
The home page includes a small weather-themed arcade game. Start a 60-second round, move the cloud bucket with the left/right arrow keys or A/D, catch raindrops for 10 points, and avoid lightning. On touch devices, use the on-screen arrows or drag across the playfield. Pause and resume at any time. Best score is stored locally in the browser and does not require an account or server endpoint.
