# Random wake

Proactive outreach is always **wake** (never job) in code, docs, API, and UI.
APScheduler `add_job` appears only inside scheduler wrappers.

## Manual vs auto

| Source | How | Random policy |
|--------|-----|---------------|
| `manual` | `POST /wakes`, `wake_set` tool | **Not** bound — fires when armed if proactive is on |
| `auto` | `ensure_auto_wake` date-arms next slot | Quiet windows, min interval, daily cap, recent chat |

## Prefs (`data/wake_prefs.json`)

Loaded/saved via `app.schedule.prefs`. Defaults:

- **Master** `proactive_enabled`: seeded from `.env` `PROACTIVE_ENABLED` on first write; thereafter prefs win
- **Quiet windows** (default on): `23:00-08:00`; UI can add more (e.g. `11:00-13:00`)
- **Min interval** (default on): 30m-24h step 30m, default **4h** between auto fires
- **Daily cap** (default on): max **3** auto fires per local day
- **Recent chat** (default on): skip auto for **45m** after last user message
- **Random** (default on): pick a random slot within horizon (default 18h)
- Each of the four rules (quiet / min interval / daily cap / recent chat) has its own on/off
- Optional `policy_patrol_minutes` (default **0**): not the main path; main path is date-arm next wake

## Scheduler

- On start / prefs save / after auto fire or skip / after chat: `ensure_auto_wake` cancels pending auto rows and date-arms the next allowed slot
- At fire time, `source=auto` re-checks `can_fire_now`; if blocked, cancel and re-arm

## API / UI

- `GET` / `PUT /settings/wake` — load/save prefs; PUT calls `reload_wake_policy`
- Page: `/settings` (`web/settings.html`)
- `/health` reports prefs-backed `proactive_enabled` and a prefs summary

## Quick test (Mac)

```bash
# enable proactive in settings UI or PUT, then:
curl -s http://localhost:8787/settings/wake | jq
curl -s -X PUT http://localhost:8787/settings/wake \
  -H 'Content-Type: application/json' \
  -d '{"proactive_enabled":true,"min_interval":{"enabled":true,"minutes":30}}'

# manual wake still works as a test (not random policy):
curl -s -X POST http://localhost:8787/wakes \
  -H 'Content-Type: application/json' \
  -d '{"delay_seconds":20,"note":"manual test wake"}'

curl -s http://localhost:8787/health | jq
curl -s 'http://localhost:8787/wakes?status=pending' | jq
```

Open `http://localhost:8787/settings` on phone/desktop, toggle rules, add a quiet window with `+`, Save, then confirm `/health` and pending auto wake.
