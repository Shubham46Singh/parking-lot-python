# Parking Lot Management

**Language:** Python (FastAPI) &nbsp;|&nbsp; **Needs:** Postgres + Redis

This is a **starter**. The application already works. Your job is everything
that gets it building, tested and running in CI.

---

## You do not need Python installed

You will build this into a container, and the container brings its own
Python 3.12. You are not being asked to extend the app — you are being asked
to ship it.

---

## 1. What this app needs

| | |
|---|---|
| **Runtime** | Python 3.12 |
| **Install dependencies** | `pip install -r requirements.txt` |
| **Start the app** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| **Listens on** | port 8080, bound to `0.0.0.0` |
| **Environment variables** | `DATABASE_URL`, `REDIS_URL` |
| **Needs running first** | Postgres, Redis, and the migrations applied |

### What it does

A vehicle arrives, gets the smallest free slot it actually fits in, and is given a ticket. On the way out the fee is worked out from how long it stayed - with thirty free minutes, an hourly rate, a cheaper overnight rate and a daily cap. The dashboard shows live occupancy by floor and by vehicle size.

### Endpoints

```
GET  /health
POST /checkin                     {"plate":"KA01AB1234","kind":"sedan"}
POST /checkout                    {"plate":"KA01AB1234"}
GET  /occupancy                   live counts by floor and by size
GET  /tickets/{plate}             the open ticket, with the fee so far
GET  /quote?kind=suv&hours=26     what a stay would cost, before committing to it
GET  /slots?free=true&size=large
```

`/health` reports Postgres and Redis **separately**. If it says
`postgres: false` the app started fine and your compose wiring is wrong —
do not go looking in the application code.

### Migrations

`migrations/` holds `.sql` files applied **in filename order** before the app
starts. They create the tables and insert sample data. A container running
`psql` over them in order is enough; you do not need a migration tool.

---

## 2. What you must write

| File | What it has to do |
|---|---|
| `Dockerfile` | Install dependencies **before** copying source, pin the base image, do not run as root. |
| `docker-compose.yml` | App + Postgres + Redis + a migration step, one `docker compose up`. |
| `.circleci/config.yml` | lint → unit tests → integration tests → secret scan → image build |
| Unit tests | For `app/pricing.py`. No database, no network. |
| Integration tests | Against a real Postgres and Redis as CircleCI service containers. |

Then push your image to **your own Docker Hub account**, tagged `:1.0`.

### When it works

```bash
docker compose up --build
curl localhost:8080/health
```

```json
{"status":"ok","postgres":true,"redis":true}
```

---

## Where the marks are

`app/pricing.py` is **pure logic** — plain functions over plain data, no
database and no HTTP. Start your tests there. Use pytest:
`pytest --cov=app --cov-report=term-missing`. Minimum 70%.

The fee function is where the marks are. Test the free half hour at 29, 30 and 31 minutes. Test that a part hour rounds up. Test the overnight rate on a stay that starts before 22:00 and ends after 06:00, so both rates appear in one bill. Then test 24h, 24h01m, 26h and 49h against whatever cap rule you decided on - those four numbers are the whole argument.

## Why Redis is here

Occupancy is read on every page load of the dashboard and changes only when a vehicle enters or leaves. Counting forty thousand ticket rows to render a number that changed twice a minute ago is the wrong shape, so the counters live in a Redis hash and move by one on each entry and exit.

Counters that drift are worse than no counters, so there is a sentinel key: when it is missing - a flushed cache, a fresh container, the very first request against a seeded database - the counters are recounted from Postgres before they are served. Postgres stays the truth.

## The hard part

**A vehicle parked for 26 hours. Does the daily cap apply once or twice?**

There is no right answer, only a defensible one. Say what your rule does at 24h00m, 24h01m and 49h, and why a car in at 23:00 and out at 01:00 should not be charged for two days.

Write your answer in your README. It is worth more marks than the feature.

---

## Getting unstuck

| Symptom | Almost always |
|---|---|
| `/health` says `postgres: false` | Wrong hostname. In compose the host is the **service name**, not `localhost`. |
| Page will not load, logs fine | No `ports:` mapping, or bound to `127.0.0.1` not `0.0.0.0`. |
| `relation "..." does not exist` | Migrations did not run, or the app started before they finished. |
| Build takes minutes each time | `COPY . .` is above your dependency install. |
| CI cannot reach the database | In CircleCI service containers the host **is** `localhost` — opposite of compose. |
