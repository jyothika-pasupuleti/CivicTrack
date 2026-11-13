# CivicTrack

CivicTrack is a free, locally run civic-complaint tracker. Citizens can register, submit a complaint with a required location and optional photo, receive in-app notifications, and follow its status. Administrators can configure departments and categories, review notifications, assign complaints, manage SLA policies, and see live analytics.

The application uses FastAPI, SQLAlchemy, MySQL Community Server, static HTML/CSS/vanilla JavaScript, Leaflet, OpenStreetMap tiles, and local image storage. It does not use paid APIs, subscriptions, or paid hosting. Department and admin user registration is deliberately not public: create an admin locally and let citizens use the public registration form.

## Requirements

- Python 3.11 or newer
- MySQL Community Server 8.x (or a compatible MySQL server)
- Git is optional; all application dependencies are free/open-source

## 1. Create a local MySQL database

Connect to MySQL as a local database administrator and run the following, replacing the example password with a strong local password:

```sql
CREATE DATABASE civictrack CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'civictrack'@'127.0.0.1' IDENTIFIED BY 'replace-with-a-strong-local-password';
GRANT ALL PRIVILEGES ON civictrack.* TO 'civictrack'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Only the local CivicTrack database is granted to the application user.

## 2. Configure and install

From the project directory, create and activate a virtual environment in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

Edit `.env` and set `CIVICTRACK_DATABASE_URL` to the MySQL username/password you created. Generate a signing key using:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `CIVICTRACK_SECRET_KEY`. Keep `.env` private; it is ignored by Git.

Install dependencies and initialize the schema and starter categories:

```powershell
python -m pip install -r requirements.txt
python -m app.cli init-db
```

## 3. Create the initial administrator

```powershell
python -m app.cli create-admin
```

Enter the administrator's name, email, and a password of at least 10 characters when prompted. The password prompt does not echo the password. Admin creation is local-only; there is no public admin signup endpoint.

## 4. Run CivicTrack

```powershell
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. Citizens can create an account from the page. Administrators sign in with the locally created account. Add a department in the admin workspace before assigning it to complaints.

The `data\uploads` directory is created as needed. Images are size/type/dimension checked, decoded, stripped of EXIF metadata, converted to JPEG, and stored locally under generated names. Complaint image access requires the owner or an administrator session.

### Existing database upgrade

The schema setup uses SQLAlchemy `create_all`, which creates missing tables but does not alter existing ones. Existing MVP databases already contain the coordinate, category-routing, notification and SLA columns/tables. The address-entry feature adds a nullable `issues.location_address` column. Before running the updated app against an existing MySQL database, apply the one-time migration in [`migrations/001_add_issue_location_address.sql`](./migrations/001_add_issue_location_address.sql); it preserves existing complaints and leaves their new address value `NULL`. New databases receive the column through `init-db`. The advanced features also add the `verified_category_labels` table. After updating the code, rerun:

```powershell
python -m app.cli init-db
```

This safely creates any missing tables and adds default SLA policies for categories that do not yet have a baseline. Back up important local data before schema changes.

## Advanced features

- **In-app notifications:** submission notifies the reporting citizen and active admins; assignment/category/status changes notify the reporter. Notifications are private to the signed-in recipient and can be marked read.
- **Department routing:** admins add departments, coordinates, and the categories each department handles. A new complaint is matched only to active departments mapped to its category. If both complaint and department coordinates are available, Haversine distance selects the nearest department within its optional service radius. If the location or eligible department data is insufficient, it remains unassigned for an admin.
- **Location and maps:** every new complaint must include an address or a latitude/longitude pair. Citizens may type a street, area, landmark or full address without sharing GPS coordinates, choose a point on the Leaflet map, or explicitly click “Use my location” to invoke the browser’s location permission flow. Addresses are stored as entered (after trimming); no geocoding service is used. Existing complaints without a location remain unchanged. Admin maps show only reports with stored coordinates; address-only reports are never assigned fabricated map points or used for distance calculations. Leaflet is loaded from its open-source CDN; map tiles use OpenStreetMap without a key. Attribution is displayed. Public OSM tile servers have a usage policy and are not an unlimited production tile-hosting guarantee.
- **Analytics:** administrator counts and category/department charts are calculated from current database records; no demo counts or sample complaints are inserted.
- **SLA:** `init-db` adds a baseline 24-hour response / 72-hour resolution policy per category. Admins can add or override category policies by minimum priority (1–5). Applicable deadlines are attached to new issues and recalculated when an admin changes the issue category or priority. Active overdue reports are flagged for escalation; completed/rejected reports are not counted as overdue. Policy changes do not retroactively recalculate existing complaints.
- **Category suggestions and labels:** there is no suitable labeled dataset or trained model in this project. The app therefore offers transparent keyword suggestions with matched terms; these are not ML predictions and citizens choose the final category. When admins change a complaint category, CivicTrack stores a text/category/reviewer snapshot in the verified-label collection. Labels are not used to train a model automatically.
- **Date/time handling:** application timestamps and SLA deadlines are UTC. MySQL `DATETIME` does not retain timezone metadata, so CivicTrack normalizes values to UTC when storing and restores UTC awareness when loading; existing UTC-naive CivicTrack timestamps are interpreted as UTC. API timestamps include an explicit UTC offset. The browser converts them to `Asia/Kolkata` only for display.

## Tests and checks

```powershell
python -m pytest
python -m ruff check app tests
python -m ruff format --check app tests
node --check frontend\js\app.js
node --check frontend\js\date-time.js
node --test tests/test_datetime.cjs
```

Automated workflow tests use an isolated in-memory SQLite database so they do not alter your MySQL data. They cover authentication/permissions, complaint creation, required address/coordinate validation, address-only and combined locations, GPS/map-pin coordinate preservation, notifications, coordinate routing, SLA deadlines/overdue detection, database-backed analytics, category suggestions, and verified-label collection. MySQL is the primary application database and should also be tested locally by following the setup steps above. The app does not automatically create production tables at startup; use `python -m app.cli init-db` after configuring MySQL.

## MVP scope and limitations

- Complaint categories are seeded by `init-db`; new categories require a database change.
- Complaint statuses are submitted, under review, in progress, resolved, closed, and rejected.
- Email/SMS notifications are not included. Geolocation requires a supported browser and user permission; manual map selection is also available.
- The displayed hero artwork and workflow explanation are illustrative, not fabricated complaint records, operational counts, or service-performance statistics.
- Use HTTPS and set `CIVICTRACK_COOKIE_SECURE=true` when serving behind a properly configured TLS terminator. Do not expose a development server directly to the public internet.
