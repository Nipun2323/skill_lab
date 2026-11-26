"""SyncroMove on Streamlit.

A second front end for the same backend: it reuses predictor/ (the algorithm), app/services
(repository, prediction and check-in services) and app/validators.py, so predictions, rules and
the database schema are identical to the Flask app. Run locally with:

    streamlit run streamlit_ui/streamlit_app.py
"""
from __future__ import annotations

import html
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app import validators as v  # noqa: E402
from app.services.samples import pick_sample, sample_histories  # noqa: E402
from app.services import checkin_service, prediction_service  # noqa: E402
from app.services import repository as repo  # noqa: E402

RANK_COLOURS = ["#E3A008", "#23935F", "#6E4FC4"]
SIGNAL_NAMES = {"transition": "After your current place", "time": "At this time of day",
                "frequency": "How often you go", "recency": "Been there recently"}

st.set_page_config(page_title="SyncroMove", page_icon="🚇", layout="wide")


# ----------------------------------------------------------------------------- backend

@st.cache_resource
def backend():
    """One Flask app per server process: creates the schema and loads venues on first start."""
    return create_app()


def ctx():
    return backend().app_context()


def browser_tz() -> int:
    """Minutes to add to UTC for the viewer's local time (Streamlit reports the JS-style opposite)."""
    off = getattr(st.context, "timezone_offset", None)
    return -int(off) if off is not None else 0


def fmt_local(ts: int, tz_min: int) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=tz_min)
    return d.strftime("%-d %b %Y, %H:%M") if sys.platform != "win32" else d.strftime("%d %b %Y, %H:%M")


def fmt_km(x) -> str:
    if x is None:
        return ""
    return f"{round(x * 1000)} m away" if x < 1 else f"{x:.1f} km away"


# ----------------------------------------------------------------------------- auth

def show_auth() -> None:
    st.title("SyncroMove")
    st.caption("Predicts the three places you're most likely to visit next, from your own check-ins.")
    login_tab, register_tab = st.tabs(["Log in", "Create an account"])

    with login_tab, st.form("login"):
        email = st.text_input("Email", max_chars=254)
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            login(email.strip(), pw)

    with register_tab, st.form("register"):
        email = st.text_input("Email", max_chars=254, key="reg_email")
        pw = st.text_input("Password", type="password", key="reg_pw",
                           help="At least 8 characters, with a letter and a number.")
        confirm = st.text_input("Confirm password", type="password", key="reg_confirm")
        with ctx():
            has_samples = bool(sample_histories())
        sample = st.checkbox("Load a sample history", value=has_samples, disabled=not has_samples)
        st.caption("Fills your account with a made-up year of check-ins at real New York places (the places "
                   "in our data), ending this week, so predictions work straight away. You can delete them any time.")
        if st.form_submit_button("Create account", type="primary"):
            register(email, pw, confirm, sample)


def login(email: str, pw: str) -> None:
    if not email or not pw:
        st.error("Enter your email and password.")
        return
    with ctx():
        cfg = backend().config
        window = int(cfg["LOGIN_WINDOW_MINUTES"]) * 60
        now = int(time.time())
        ip = getattr(st.context, "ip_address", None) or "local"
        by_email, by_ip, oldest = repo.recent_failures(email, ip, now - window)
        if max(by_email, by_ip) >= int(cfg["LOGIN_MAX_ATTEMPTS"]):
            wait = max(1, math.ceil(((oldest or now) + window - now) / 60))
            st.error(f"Too many failed attempts. Try again in {wait} minute{'s' if wait != 1 else ''}.")
            return
        row = repo.get_auth_row(email)
        if row is None or not check_password_hash(row["password_hash"], pw):
            repo.record_failure(email, ip, now)
            st.error("Email or password is incorrect.")
            return
        repo.clear_failures(email, now - window)
    st.session_state.clear()
    st.session_state.user_id = row["user_id"]
    st.rerun()


def register(email: str, pw: str, confirm: str, sample: bool) -> None:
    errors = []
    try:
        email = v.email(email)
    except v.ValidationError as e:
        errors.append(e.message)
    try:
        v.password(pw, confirm, minimum=int(backend().config["PASSWORD_MIN"]))
    except v.ValidationError as e:
        errors.append(e.message)
    if errors:
        for msg in errors:
            st.error(msg)
        return
    with ctx():
        if repo.email_exists(email):
            st.error("An account with this email already exists. Log in instead.")
            return
        key, rows = pick_sample() if sample else (None, [])
        uid = repo.create_user(email, generate_password_hash(pw), key)
        if key:
            repo.copy_sample(uid, rows)
    st.session_state.clear()
    st.session_state.user_id = uid
    st.session_state.flash = ("Welcome aboard. We loaded a sample year of check-ins so you can see predictions now."
                              if key else "Your line starts with your first check-in.")
    st.rerun()


# ----------------------------------------------------------------------------- transit map

def map_svg(route: list, preds: list) -> str:
    """Last check-ins as a subway line, predictions as dashed branches ending in numbered bullets."""
    W, H, M = 800, 420, 56
    pts = [(c["lat"], c["lon"]) for c in route] + [(p["lat"], p["lon"]) for p in preds]
    lat0 = sum(p[0] for p in pts) / len(pts)
    kx = math.cos(math.radians(lat0))
    xs = [lon * kx for _, lon in pts]
    ys = [-lat for lat, _ in pts]
    span_x, span_y = max(max(xs) - min(xs), 0.006), max(max(ys) - min(ys), 0.006)
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = min((W - 2 * M) / span_x, (H - 2 * M) / span_y)

    def proj(lat, lon):
        return W / 2 + (lon * kx - cx) * scale, H / 2 + (-lat - cy) * scale

    here = proj(route[-1]["lat"], route[-1]["lon"])
    # keep bullets clear of the current stop and each other
    bullets = []
    for i, p in enumerate(preds):
        x, y = proj(p["lat"], p["lon"])
        dx, dy = x - here[0], y - here[1]
        dist = math.hypot(dx, dy)
        ang = math.atan2(dy, dx) if dist > 1 else -math.pi / 2 + i * 2 * math.pi / 3
        r = max(dist, 46)
        pt = (x, y) if dist >= 46 else (here[0] + math.cos(ang) * r, here[1] + math.sin(ang) * r)
        t = 1
        while t <= 12 and any(math.hypot(pt[0] - q[0], pt[1] - q[1]) < 36 for q in bullets):
            a = ang + (1 if t % 2 else -1) * math.ceil(t / 2) * math.pi / 6
            pt = (here[0] + math.cos(a) * r, here[1] + math.sin(a) * r)
            t += 1
        bullets.append((min(max(pt[0], 18), W - 18), min(max(pt[1], 18), H - 18)))

    e = html.escape
    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'style="width:100%;height:auto;background:#F4F7F9;border:1px solid #D3DCE2;border-radius:12px;'
           f'font-family:Overpass,system-ui,sans-serif">',
           f'<title>Your recent check-ins and predicted next stops</title>']
    out += [f'<line x1="{gx}" y1="0" x2="{gx}" y2="{H}" stroke="#E2E9EE"/>' for gx in range(40, W, 40)]
    out += [f'<line x1="0" y1="{gy}" x2="{W}" y2="{gy}" stroke="#E2E9EE"/>' for gy in range(40, H, 40)]
    for i, (bx, by) in enumerate(bullets):
        out.append(f'<path d="M{here[0]:.1f} {here[1]:.1f} L{bx:.1f} {by:.1f}" fill="none" stroke="{RANK_COLOURS[i]}" '
                   f'stroke-width="5" stroke-dasharray="2 12" stroke-linecap="round"/>')
    if len(route) > 1:
        d = " ".join(("M" if i == 0 else "L") + "{:.1f} {:.1f}".format(*proj(c["lat"], c["lon"]))
                     for i, c in enumerate(route))
        out.append(f'<path d="{d}" fill="none" stroke="#fff" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>')
        out.append(f'<path d="{d}" fill="none" stroke="#0F6E8C" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>')
    for i, c in enumerate(route):
        x, y = proj(c["lat"], c["lon"])
        last = i == len(route) - 1
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{11 if last else 7}" fill="{"#17212B" if last else "#fff"}" '
                   f'stroke="{"#fff" if last else "#17212B"}" stroke-width="{4 if last else 3.5}">'
                   f'<title>{e(c["category"])}</title></circle>')
    anchor = "end" if here[0] > W - 220 else "start"
    lx = here[0] - 18 if anchor == "end" else here[0] + 18
    halo = 'paint-order="stroke" stroke="#F4F7F9" stroke-width="5" stroke-linejoin="round"'
    out.append(f'<text x="{lx:.1f}" y="{here[1] + 16:.1f}" text-anchor="{anchor}" font-size="11" font-weight="700" '
               f'fill="#5B6875" letter-spacing="1" {halo}>YOU ARE HERE</text>')
    out.append(f'<text x="{lx:.1f}" y="{here[1] + 34:.1f}" text-anchor="{anchor}" font-size="15" font-weight="700" '
               f'fill="#17212B" {halo}>{e(route[-1]["category"])}</text>')
    for i, ((bx, by), p) in enumerate(zip(bullets, preds)):
        ta = "end" if bx > W - 180 else "start"
        tx = bx - 21 if ta == "end" else bx + 21
        out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="15" fill="{RANK_COLOURS[i]}" stroke="#fff" stroke-width="3"/>')
        out.append(f'<text x="{bx:.1f}" y="{by + 5.5:.1f}" text-anchor="middle" font-size="16" font-weight="800" fill="#fff">{i + 1}</text>')
        out.append(f'<text x="{tx:.1f}" y="{by + 5:.1f}" text-anchor="{ta}" font-size="13" font-weight="700" '
                   f'fill="#17212B" {halo}>{e(p["category"])}</text>')
    km_px = scale / 111.32
    bar = ([k for k in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20) if k * km_px <= 140] or [0.1])[-1]
    out.append(f'<line x1="20" y1="{H - 20}" x2="{20 + bar * km_px:.1f}" y2="{H - 20}" stroke="#5B6875" stroke-width="3"/>')
    out.append(f'<text x="20" y="{H - 28}" font-size="11" fill="#5B6875">{int(bar * 1000)} m</text>' if bar < 1 else
               f'<text x="20" y="{H - 28}" font-size="11" fill="#5B6875">{bar:g} km</text>')
    out.append("</svg>")
    return "".join(out)


# ----------------------------------------------------------------------------- dashboard

def show_dashboard(uid: int) -> None:
    with ctx():
        user = repo.get_user(uid)
    if user is None:  # account deleted elsewhere
        st.session_state.clear()
        st.rerun()

    with st.sidebar:
        st.markdown("### SyncroMove")
        st.write(user["email"])
        page = st.radio("Go to", ["Dashboard", "Account"], label_visibility="collapsed")
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
        st.caption("Predictions use a fixed rule (no training). Data: Foursquare NYC check-ins, "
                   "2012–2013 (Yang et al., 2015).")

    if msg := st.session_state.pop("flash", None):
        st.success(msg)
    if page == "Account":
        show_account(uid, user)
        return

    tz = browser_tz()
    # ---- prediction time picker
    head, picker = st.columns([2, 3])
    head.title("Your line")
    with picker:
        use_now = st.toggle("Predict for now", value=True)
        query_ts = int(time.time())
        if not use_now:
            local_now = datetime.now(timezone.utc) + timedelta(minutes=tz)
            c1, c2 = st.columns(2)
            day = c1.date_input("Date", value=local_now.date())
            at = c2.time_input("Time", value=local_now.time().replace(second=0, microsecond=0))
            local = datetime.combine(day, at)
            query_ts = int(local.replace(tzinfo=timezone.utc).timestamp()) - tz * 60

    with ctx():
        k = int(backend().config["TOP_K"])
        data = prediction_service.predict_for_user(uid, query_ts, tz, k)
        recent = [dict(r) for r in repo.recent_checkins(uid, 10)]
        stats = repo.checkin_stats(uid)
    preds = data["predictions"]

    st.caption(("Now: " if use_now else "Predicting for ") + data["time_bucket"])
    if recent:
        st.markdown(map_svg(list(reversed(recent[:8])), preds), unsafe_allow_html=True)
        st.caption("Not enough history yet: showing popular New York places until you check in."
                   if data["cold_start"] else
                   f"Based on your last {data['history_used']} check-ins. Sample histories are made up, at real New York places.")
    else:
        st.info("Your line starts with your first check-in. Search for a place below.")

    # ---- next stops
    st.subheader("Next stops")
    if not preds:
        st.write("No predictions yet. Check in somewhere to start your line.")
    max_p = max((p["probability"] for p in preds), default=1) or 1
    for p in preds:
        badge, body = st.columns([1, 12])
        badge.markdown(f'<div style="width:40px;height:40px;border-radius:50%;background:{RANK_COLOURS[p["rank"] - 1]};'
                       f'color:#fff;font-weight:800;font-size:1.15rem;display:grid;place-items:center">{p["rank"]}</div>',
                       unsafe_allow_html=True)
        with body:
            st.markdown(f"**{p['category']}** &nbsp; <span style='color:#5B6875'>{fmt_km(p['distance_km'])}</span>",
                        unsafe_allow_html=True)
            st.write(p["reason"])
            st.progress(min(1.0, p["probability"] / max_p), text=f"{p['probability'] * 100:.0f}% likely")
            with st.expander("Why?"):
                rows = [{"Signal": name, "Value": f"{p['signals'][key]:.3f}",
                         "Weight": f"× {data['weights'][key]:.2f}",
                         "Adds": f"{p['signals'][key] * data['weights'][key]:.3f}"}
                        for key, name in SIGNAL_NAMES.items()]
                st.table(rows)
                st.caption(f"Each signal is this place's share among the places you've been (0 to 1). "
                           f"Score {p['score']:.3f}; city popularity adds a tiny tie-breaker.")

    checkin_col, visits_col, acc_col = st.columns(3)
    with checkin_col:
        show_checkin(uid, tz, recent)
    with visits_col:
        st.subheader("Recent visits")
        if not recent:
            st.caption("No visits yet. Your check-ins will appear here.")
        for c in recent:
            r = c["predicted_rank"]
            tag = (f"Predicted #{r}" if r and 1 <= r <= 3 else "Missed" if r is not None
                   else "Sample" if c["source"] == "sample" else "")
            st.markdown(f"**{c['category']}**  \n<span style='color:#5B6875;font-size:.875rem'>"
                        f"{fmt_local(c['ts_utc'], c['tz_offset_min'])}{' · ' + tag if tag else ''}</span>",
                        unsafe_allow_html=True)
    with acc_col:
        show_accuracy(stats)


def show_checkin(uid: int, tz: int, recent: list) -> None:
    st.subheader("Check in")
    q = st.text_input("Kind of place", max_chars=60, placeholder="coffee, gym, subway…",
                      help="Places are New York venues from the dataset, listed nearest to your last check-in.")
    choice = None
    if q.strip():
        with ctx():
            rows = repo.search_venues(uid, v.search_query(q))
            last = repo.last_checkin(uid)
            origin = prediction_service.venue_dict(last["venue_id"], uid) if last else None
        from predictor.algorithm import haversine_km
        items = []
        for r in rows:
            if last is not None and r["venue_id"] == last["venue_id"]:
                continue  # you're already here
            d = haversine_km(origin["lat"], origin["lon"], r["latitude"], r["longitude"]) if origin else None
            items.append((d if d is not None else 0, -r["popularity"], r["venue_id"], r["category"], d,
                          r["latitude"], r["longitude"]))
        items.sort()
        items = items[:8]
        if not items:
            st.warning(f'No places match "{q.strip()}". Try a broader word, like food or park.')
        else:
            labels = {i[2]: f"{i[3]} · {fmt_km(i[4]) or 'popular'} · {i[5]:.4f}, {i[6]:.4f}" for i in items}
            choice = st.radio("Pick a place", list(labels), format_func=labels.get)

    local_now = datetime.now(timezone.utc) + timedelta(minutes=tz)
    c1, c2 = st.columns(2)
    day = c1.date_input("Date", value=local_now.date(), key="ci_day")
    at = c2.time_input("Time", value=local_now.time().replace(second=0, microsecond=0), key="ci_time")
    if st.button("Check in", type="primary"):
        if not choice:
            st.error("Choose a place from the search results.")
            return
        local = datetime.combine(day, at).replace(tzinfo=timezone(timedelta(minutes=tz)))
        ts = int(local.timestamp())
        if recent and ts < recent[0]["ts_utc"] and recent[0]["ts_utc"] - ts < 60:
            ts = recent[0]["ts_utc"]  # same minute as the last check-in
        try:
            with ctx():
                res = checkin_service.record_checkin(uid, choice, datetime.fromtimestamp(ts, timezone.utc).isoformat(), tz)
        except v.ValidationError as e:
            st.error(e.message)
            return
        rank = res["predicted_rank"]
        extra = (f" It was our #{rank} prediction." if rank and 1 <= rank <= 3
                 else " We didn't predict that one." if rank == 0 else "")
        st.session_state.flash = f"Checked in at {res['category']}.{extra}"
        st.rerun()


def show_accuracy(stats: dict) -> None:
    st.subheader("How accurate is this?")
    path = Path(backend().config["EVAL_RESULTS_PATH"])
    if not path.is_file():
        st.caption("No evaluation results yet. Run: python scripts/evaluate.py")
        return
    d = json.loads(path.read_text(encoding="utf-8"))
    full, base, ex = d["methods"]["full"], d["methods"]["most_visited"], d["full_extras"]
    st.caption(f"Measured by replaying the last 20% of the New York data ({d['dataset']['test_checkins']:,} "
               "check-ins): how often the place someone actually went was in our list.")
    for label, key in (("Top 1", "top1"), ("Top 3", "top3"), ("Top 5", "top5")):
        st.progress(min(1.0, full[key] / 0.5), text=f"{label}: {full[key] * 100:.1f}%")
    st.write(f"Baseline (your most-visited places): {base['top3'] * 100:.1f}% top 3. This rule is "
             f"{(full['top3'] - base['top3']) * 100:.1f} points better.")
    st.caption(f"Easier targets: right kind of place in the top 3 categories {ex['top3_category'] * 100:.1f}%; "
               f"a top-3 place within 1 km of the real one {ex['top3_within_1km'] * 100:.1f}%.")
    if stats["scored"]:
        st.markdown(f"**You: {stats['hits_top3']} of {stats['scored']} check-ins were in the top 3 we showed "
                    f"({stats['hits_top3'] / stats['scored'] * 100:.0f}%).**")
    else:
        st.markdown("**You: check in after viewing predictions to see your own hit rate.**")


# ----------------------------------------------------------------------------- account

def show_account(uid: int, user) -> None:
    st.title("Your account")
    with ctx():
        stats = repo.checkin_stats(uid)
        export = repo.export_user(uid)
    st.write(f"**Email:** {user['email']}  \n**Member since:** {user['created_at'][:10]}  \n"
             f"**Check-ins:** {stats['total']} ({stats['own']} added by you)  \n"
             f"**Sample history:** {user['sample_source'] or 'None'}")

    st.subheader("Download your data")
    st.download_button("Download JSON", json.dumps(export, indent=2), file_name="syncromove-export.json",
                       mime="application/json")

    for action, title, note, label in (
        ("delete_checkins", "Delete your check-ins", "Removes all check-ins and prediction logs. Your account stays.",
         "Delete check-ins"),
        ("delete_account", "Delete your account",
         "Removes your account and everything linked to it. This can't be undone.", "Delete account"),
    ):
        st.subheader(title)
        st.caption(note)
        with st.form(action):
            pw = st.text_input("Password", type="password")
            if st.form_submit_button(label):
                with ctx():
                    stored = repo.get_password_hash(uid)
                    if not pw:
                        st.error("Enter your password to confirm.")
                    elif not stored or not check_password_hash(stored, pw):
                        st.error("That password isn't right.")
                    elif action == "delete_checkins":
                        repo.delete_checkins(uid)
                        st.session_state.flash = "All your check-ins and predictions were deleted."
                        st.rerun()
                    else:
                        repo.delete_user(uid)
                        st.session_state.clear()
                        st.rerun()


# ----------------------------------------------------------------------------- main

uid = st.session_state.get("user_id")
if uid is None:
    show_auth()
else:
    show_dashboard(uid)
