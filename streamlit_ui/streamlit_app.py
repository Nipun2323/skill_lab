"""SyncroMove on Streamlit: next-place prediction on a real map.

A second front end for the same backend: it reuses predictor/ (the algorithm), app/services
(repository, prediction, check-in, demo) and app/validators.py, so predictions and rules are
identical to the Flask app. Run locally with:

    streamlit run streamlit_ui/streamlit_app.py

The map is drawn with pydeck (bundled with Streamlit) on CARTO's free street basemap.
"""
from __future__ import annotations

import html
import json
import math
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app import validators as v  # noqa: E402
from app.db import get_db  # noqa: E402
from app.services import checkin_service, demo, prediction_service  # noqa: E402
from app.services import repository as repo  # noqa: E402
from app.services.samples import pick_sample, sample_histories  # noqa: E402
from predictor.algorithm import haversine_km  # noqa: E402

RANK_COLOURS = ["#E3A008", "#23935F", "#6E4FC4"]
RANK_RGB = [[227, 160, 8], [35, 147, 95], [110, 79, 196]]
TEAL, INK = [15, 110, 140], [23, 33, 43]
# pydeck turns plain strings into per-point formulas ("@@=..."), so literal string props must be wrapped
PX = pdk.types.String("pixels")
SIGNAL_NAMES = {"transition": "After this place", "time": "At this time of day",
                "frequency": "How often you go", "recency": "Been there recently"}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
AVATAR_COLOURS = {"aarav": "#0F6E8C", "priya": "#C2410C", "rohan": "#6E4FC4"}

st.set_page_config(page_title="SyncroMove", page_icon="🚇", layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
.block-container {padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1320px;}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none;}
.sm-hero {background: linear-gradient(135deg, #0F6E8C 0%, #12465A 55%, #17212B 100%); color: #fff;
  border-radius: 20px; padding: 30px 34px; margin-bottom: 22px; box-shadow: 0 10px 30px rgba(15,110,140,.25);}
.sm-hero h1 {color: #fff; font-size: 2.4rem; margin: 0 0 .3rem; font-weight: 800; letter-spacing: -.02em; padding: 0;}
.sm-hero p {color: #D8E9F0; font-size: 1.05rem; margin: 0; max-width: 760px;}
.sm-kicker {text-transform: uppercase; letter-spacing: .12em; font-size: .75rem; font-weight: 700; color: #F5C451; margin-bottom: .4rem;}
.sm-card {background: #fff; border-radius: 18px; padding: 18px 20px; box-shadow: 0 1px 0 #D3DCE2, 0 8px 26px rgba(23,33,43,.06); margin-bottom: 14px;}
.person {display: flex; gap: 14px; align-items: center; margin-bottom: 10px;}
.avatar {flex: none; width: 54px; height: 54px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 1.2rem;}
.person-name {font-weight: 800; font-size: 1.15rem; line-height: 1.2;}
.person-role {color: #5B6875; font-size: .9rem;}
.person-story {font-size: .92rem; color: #2B3A47; min-height: 6.4em;}
.person-role {min-height: 2.7em;}
.stat-row {display: flex; gap: 8px; flex-wrap: wrap; margin: .6rem 0 .2rem;}
.chip {display: inline-flex; align-items: center; gap: 6px; background: #F1F5F8; border: 1px solid #DCE4EA; border-radius: 99px;
  padding: 3px 11px; font-size: .82rem; color: #17212B; white-space: nowrap;}
.chip b {font-variant-numeric: tabular-nums;}
.topbar {display: flex; align-items: center; gap: 14px; flex-wrap: wrap;}
.topbar .brand {font-weight: 800; font-size: 1.35rem; color: #0F6E8C; margin-right: 6px;}
.panel-title {font-weight: 800; font-size: 1.15rem; margin: 0 0 .2rem;}
.panel-sub {color: #5B6875; font-size: .85rem; margin-bottom: .8rem;}
.rank-card {display: flex; gap: 14px; background: #fff; border-radius: 16px; padding: 14px 16px; margin-bottom: 12px;
  box-shadow: 0 1px 0 #D3DCE2, 0 8px 22px rgba(23,33,43,.06); border-left: 6px solid var(--c);}
.rank-badge {flex: none; width: 46px; height: 46px; border-radius: 50%; background: var(--c); color: #fff; font-weight: 800;
  font-size: 1.3rem; display: grid; place-items: center; box-shadow: 0 0 0 4px #fff, 0 0 0 6px var(--c);}
.rank-body {flex: 1; min-width: 0;}
.rank-title {font-weight: 800; font-size: 1.02rem; line-height: 1.25; overflow-wrap: anywhere;}
.rank-meta {color: #5B6875; font-size: .82rem; margin-top: 2px;}
.rank-reason {font-size: .9rem; margin: .35rem 0 .45rem; color: #17212B;}
.rank-bar {display: flex; align-items: center; gap: 10px;}
.bar {flex: 1; height: 9px; background: #E4EAEE; border-radius: 99px; overflow: hidden;}
.bar > span {display: block; height: 100%; background: var(--c, #0F6E8C); border-radius: 99px;}
.pct {font-weight: 800; font-variant-numeric: tabular-nums; min-width: 3.2ch; text-align: right;}
.sig {display: grid; grid-template-columns: 9rem 1fr 3.2rem; gap: 6px 10px; align-items: center; font-size: .82rem; margin-top: .4rem;}
.sig .bar {height: 7px;}
details summary {cursor: pointer; margin-top: .45rem;}
.tl {list-style: none; margin: 0; padding: 0 0 0 4px; position: relative;}
.tl:before {content: ""; position: absolute; left: 11px; top: 10px; bottom: 10px; width: 5px; background: #0F6E8C; border-radius: 3px;}
.tl li {position: relative; display: flex; gap: 12px; align-items: center; padding: 7px 0;}
.tl .dot {flex: none; width: 19px; height: 19px; border-radius: 50%; background: #fff; border: 4px solid #17212B; z-index: 1;}
.tl .what {font-weight: 700;} .tl .when {color: #5B6875; font-size: .82rem; font-variant-numeric: tabular-nums;}
.tag {font-size: .74rem; font-weight: 700; padding: 2px 9px; border-radius: 99px; margin-left: auto; white-space: nowrap;}
.tag.hit {background: #DFF2E8; color: #155C3B;} .tag.miss {background: #EEF1F3; color: #5B6875;}
.tag.sample {border: 1px solid #DCE4EA; color: #5B6875;}
.legend {display: flex; gap: 14px; flex-wrap: wrap; font-size: .8rem; color: #5B6875; margin-top: 6px;}
.legend span {display: inline-flex; align-items: center; gap: 6px;}
.legend i {display: inline-block; width: 12px; height: 12px; border-radius: 50%;}
.legend i.line {width: 22px; height: 5px; border-radius: 3px; background: #0F6E8C;}
.small-note {color: #5B6875; font-size: .8rem;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------- backend

@st.cache_resource
def backend():
    """One Flask app per server process: schema, venues and the Mumbai demo people on first start."""
    return create_app()


def ctx():
    return backend().app_context()


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def browser_tz() -> int:
    off = getattr(st.context, "timezone_offset", None)
    return -int(off) if off is not None else 0


def place_label(row) -> str:
    return row.get("name") or row["category"]


def fmt_local(ts: int, tz_min: int, fmt: str = "%a %d %b, %H:%M") -> str:
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=tz_min)).strftime(fmt)


def fmt_km(x) -> str:
    if x is None:
        return ""
    return f"{round(x * 1000)} m away" if x < 1 else f"{x:.1f} km away"


def initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2]).upper()


# ----------------------------------------------------------------------------- landing / auth

def show_landing() -> None:
    st.markdown(
        '<div class="sm-hero"><div class="sm-kicker">Next-place prediction · no training</div>'
        '<h1>SyncroMove</h1><p>Where will you go next? SyncroMove reads your own check-ins and ranks the '
        'three places you are most likely to visit, with the reason for each. Pick a demo person to see '
        'six months of their life in Mumbai on the map.</p></div>', unsafe_allow_html=True)

    users = backend().config.get("DEMO_USERS") or {}
    if users:
        st.markdown("#### Explore the Mumbai demo")
        cols = st.columns(3)
        with ctx():
            stats = {k: repo.checkin_stats(uid) for k, uid in users.items()}
            places = {k: len(repo.place_visits(uid)) for k, uid in users.items()}
        for col, (key, person) in zip(cols, demo.PEOPLE.items()):
            if key not in users:
                continue
            with col:
                st.markdown(
                    f'<div class="sm-card"><div class="person"><div class="avatar" '
                    f'style="background:{AVATAR_COLOURS[key]}">{initials(person["name"])}</div>'
                    f'<div><div class="person-name">{e(person["name"])}</div>'
                    f'<div class="person-role">{e(person["role"])} · {e(person["area"])}</div></div></div>'
                    f'<div class="person-story">{e(person["story"])}</div>'
                    f'<div class="stat-row"><span class="chip"><b>{stats[key]["total"]}</b> check-ins</span>'
                    f'<span class="chip"><b>{places[key]}</b> places</span>'
                    f'<span class="chip">past 6 months</span></div></div>', unsafe_allow_html=True)
                if st.button(f"Explore as {person['name'].split()[0]}", key=f"demo_{key}", type="primary",
                             use_container_width=True):
                    st.session_state.clear()
                    st.session_state.user_id = users[key]
                    st.rerun()
        st.caption("Demo people and their routines are made up. Places are real Mumbai landmarks; homes, "
                   "offices and gyms are invented spots in real neighbourhoods.")

    st.markdown("#### Or use your own account")
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
        st.caption("Fills your account with a made-up year of check-ins at real New York places, ending this "
                   "week, so predictions work straight away. You can delete them any time.")
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
    except v.ValidationError as err:
        errors.append(err.message)
    try:
        v.password(pw, confirm, minimum=int(backend().config["PASSWORD_MIN"]))
    except v.ValidationError as err:
        errors.append(err.message)
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


def load_sample_into(uid: int) -> int:
    if repo.checkin_stats(uid)["total"]:
        return 0
    key, rows = pick_sample()
    if not key:
        return 0
    repo.copy_sample(uid, rows)
    repo.set_sample_source(uid, key)
    return len(rows)


# ----------------------------------------------------------------------------- map

def view_for(points: list) -> pdk.ViewState:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    span_lon = max(max(lons) - min(lons), 0.012) * 1.3
    span_lat = max(max(lats) - min(lats), 0.012) * 1.3
    z_lon = math.log2(720 * 360 / (512 * span_lon))  # deck.gl: world = 512 px at zoom 0
    z_lat = math.log2(520 * 360 / (512 * span_lat / math.cos(math.radians(sum(lats) / len(lats)))))
    zoom = max(3.0, min(15.5, min(z_lon, z_lat)))
    return pdk.ViewState(latitude=(min(lats) + max(lats)) / 2, longitude=(min(lons) + max(lons)) / 2,
                         zoom=zoom, pitch=0, bearing=0)


def build_deck(places: list, route: list, here: dict, preds: list) -> pdk.Deck:
    max_visits = max((p["visits"] for p in places), default=1)
    visited = [{"lon": p["lon"], "lat": p["lat"], "name": place_label(p),
                "detail": f'{p["category"]} · {p["visits"]} visits',
                "r": 5 + 13 * math.sqrt(p["visits"] / max_visits)} for p in places]
    path = [{"path": [[c["lon"], c["lat"]] for c in route], "name": "Your recent route",
             "detail": f"Last {len(route)} check-ins"}] if len(route) > 1 else []
    stops = [{"lon": c["lon"], "lat": c["lat"], "name": c["label"], "detail": c["when"]} for c in route]
    arcs = [{"src": [here["lon"], here["lat"]], "dst": [p["lon"], p["lat"]], "c": RANK_RGB[i] + [235],
             "name": f'#{i + 1} {p["name"] or p["category"]}', "detail": f'{p["probability"] * 100:.0f}% likely'}
            for i, p in enumerate(preds)]
    bullets = [{"lon": p["lon"], "lat": p["lat"], "c": RANK_RGB[i] + [255], "num": str(i + 1),
                "name": f'#{i + 1} {p["name"] or p["category"]}',
                "detail": f'{p["category"]} · {p["probability"] * 100:.0f}% likely · {fmt_km(p["distance_km"])}',
                "label": f'#{i + 1}  ' + (p["name"] or p["category"]),
                "off": [22, (i - 1) * 19]} for i, p in enumerate(preds)]  # stagger labels of nearby places
    here_pt = [{"lon": here["lon"], "lat": here["lat"], "name": "You are here", "detail": here["label"],
                "label": "You are here · " + here["label"], "off": [22, 30]}]
    font = pdk.types.String("Arial, Helvetica, sans-serif")

    layers = [
        pdk.Layer("ScatterplotLayer", billboard=True, data=visited, get_position="[lon, lat]", get_radius="r", radius_units=PX,
                  get_fill_color=TEAL + [70], get_line_color=TEAL + [200], stroked=True, line_width_min_pixels=1.5,
                  pickable=True),
        pdk.Layer("PathLayer", path, get_path="path", get_width=7, width_units=PX,
                  get_color=TEAL + [230], cap_rounded=True, joint_rounded=True, pickable=True),
        pdk.Layer("ScatterplotLayer", billboard=True, data=stops, get_position="[lon, lat]", get_radius=5, radius_units=PX,
                  get_fill_color=[255, 255, 255, 255], get_line_color=INK + [255], stroked=True,
                  line_width_min_pixels=2.5, pickable=True),
        pdk.Layer("ArcLayer", arcs, get_source_position="src", get_target_position="dst",
                  get_source_color="c", get_target_color="c", get_width=5, width_units=PX,
                  get_height=0.25, get_tilt=90, pickable=True),
        pdk.Layer("ScatterplotLayer", billboard=True, data=here_pt, get_position="[lon, lat]", get_radius=24, radius_units=PX,
                  get_fill_color=INK + [45], stroked=False),
        pdk.Layer("ScatterplotLayer", billboard=True, data=here_pt, get_position="[lon, lat]", get_radius=10, radius_units=PX,
                  get_fill_color=INK + [255], get_line_color=[255, 255, 255, 255], stroked=True,
                  line_width_min_pixels=3.5, pickable=True),
        pdk.Layer("ScatterplotLayer", billboard=True, data=bullets, get_position="[lon, lat]", get_radius=15, radius_units=PX,
                  get_fill_color="c", get_line_color=[255, 255, 255, 255], stroked=True,
                  line_width_min_pixels=3, pickable=True),
        pdk.Layer("TextLayer", bullets, get_position="[lon, lat]", get_text="num", get_size=16,
                  size_units=PX, get_color=[255, 255, 255, 255], font_weight=pdk.types.String("bold"), font_family=font,
                  get_text_anchor=pdk.types.String("middle"),
                  get_alignment_baseline=pdk.types.String("center")),
        pdk.Layer("TextLayer", bullets + here_pt, get_position="[lon, lat]", get_text="label", get_size=13,
                  size_units=PX, get_color=INK + [255], font_weight=700, font_family=font,
                  get_pixel_offset="off", get_text_anchor=pdk.types.String("start"),
                  character_set=pdk.types.String("auto"),
                  get_alignment_baseline=pdk.types.String("center"),
                  background=True, get_background_color=[255, 255, 255, 235], background_padding=[6, 3, 6, 3]),
    ]
    points = [(p["lat"], p["lon"]) for p in places] + [(p["lat"], p["lon"]) for p in preds] + \
             [(here["lat"], here["lon"])]
    return pdk.Deck(
        layers=layers, initial_view_state=view_for(points), map_provider="carto",
        map_style=pdk.map_styles.CARTO_ROAD,
        tooltip={"html": "<b>{name}</b><br/>{detail}",
                 "style": {"backgroundColor": "#17212B", "color": "#fff", "fontSize": "13px",
                           "borderRadius": "8px", "padding": "6px 10px"}})


# ----------------------------------------------------------------------------- ranking

def ranking_html(preds: list, weights: dict, cold: bool) -> str:
    if not preds:
        return '<div class="sm-card">No predictions yet. Check in somewhere to start your line.</div>'
    max_p = max(p["probability"] for p in preds) or 1
    cards = []
    for i, p in enumerate(preds):
        c = RANK_COLOURS[i]
        title = p["name"] or p["category"]
        meta = " · ".join(x for x in (p["category"] if p["name"] else "", fmt_km(p["distance_km"])) if x)
        sig = ""
        if not cold:
            rows = []
            for key, label in SIGNAL_NAMES.items():
                val, w = p["signals"][key], weights[key]
                rows.append(f'<span>{label}</span><div class="bar"><span style="width:{val * 100:.0f}%"></span>'
                            f'</div><span class="small-note">+{val * w:.3f}</span>')
            sig = (f'<details><summary class="small-note">Why?</summary><div class="sig">{"".join(rows)}</div>'
                   f'<div class="small-note">Bar: this place\'s share of each signal. Right: weight × share. '
                   f'Score {p["score"]:.3f}.</div></details>')
        cards.append(
            f'<div class="rank-card" style="--c:{c}"><div class="rank-badge">{i + 1}</div><div class="rank-body">'
            f'<div class="rank-title">{e(title)}</div><div class="rank-meta">{e(meta)}</div>'
            f'<div class="rank-reason">{e(p["reason"])}</div>'
            f'<div class="rank-bar"><div class="bar"><span style="width:{max(6, p["probability"] / max_p * 100):.0f}%">'
            f'</span></div><span class="pct">{p["probability"] * 100:.0f}%</span></div>{sig}</div></div>')
    return "".join(cards)


# ----------------------------------------------------------------------------- dashboard

def query_time(day_name: str | None, at: dtime | None, tz: int) -> int:
    """'Now', or the most recent `day_name` at time `at` in the user's local time (within the past week)."""
    if day_name is None:
        return int(time.time())
    local_now = datetime.now(timezone.utc) + timedelta(minutes=tz)
    back = (local_now.weekday() - DAY_NAMES.index(day_name)) % 7
    local = (local_now - timedelta(days=back)).replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return int(local.replace(tzinfo=timezone.utc).timestamp()) - tz * 60


def show_dashboard(uid: int) -> None:
    with ctx():
        user = repo.get_user(uid)
    if user is None:
        st.session_state.clear()
        st.rerun()
    person_key = demo.person_for_user(user["sample_source"])
    person = demo.PEOPLE.get(person_key) if person_key else None

    # ---- top bar
    left, right = st.columns([5, 2])
    with left:
        if person:
            st.markdown(
                f'<div class="topbar"><span class="brand">SyncroMove</span><div class="avatar" '
                f'style="width:42px;height:42px;font-size:1rem;background:{AVATAR_COLOURS[person_key]}">'
                f'{initials(person["name"])}</div><div><div class="person-name">{e(person["name"])}</div>'
                f'<div class="person-role">{e(person["role"])} · {e(person["area"])} · demo person</div></div></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="topbar"><span class="brand">SyncroMove</span>'
                        f'<span class="person-role">{e(user["email"])}</span></div>', unsafe_allow_html=True)
    with right:
        b1, b2 = st.columns(2)
        if b1.button("Switch person" if person else "Demo people", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        if b2.button("Log out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    if msg := st.session_state.pop("flash", None):
        st.success(msg)

    with ctx():
        places = [dict(r) for r in repo.place_visits(uid)]
        recent = [dict(r) for r in repo.recent_checkins(uid, 30)]
        stats = repo.checkin_stats(uid)
    tz = recent[0]["tz_offset_min"] if recent else browser_tz()  # the user's local time, from their data

    # ---- what-if controls
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2.4, 1.3, 1.3, 1.6])
        labels = {p["venue_id"]: f'{place_label(p)} ({p["visits"]} visits)' for p in places}
        options = ["__last__"] + [p["venue_id"] for p in places]
        labels["__last__"] = (f"Where I last checked in: {place_label(recent[0])}" if recent else "No check-ins yet")
        at_choice = c1.selectbox("Where are you?", options if recent else ["__last__"], format_func=labels.get)
        when_mode = c2.radio("When?", ["Now", "Pick a time"], horizontal=True)
        day_name, at_time = None, None
        if when_mode == "Pick a time":
            local_now = datetime.now(timezone.utc) + timedelta(minutes=tz)
            day_name = c3.selectbox("Day", DAY_NAMES, index=local_now.weekday())
            at_time = c4.slider("Time", value=dtime(local_now.hour, (local_now.minute // 15) * 15),
                                min_value=dtime(0, 0), max_value=dtime(23, 45), step=timedelta(minutes=15),
                                format="HH:mm")
        else:
            c3.markdown(f'<div class="small-note" style="padding-top:2.1rem">Local time '
                        f'{fmt_local(int(time.time()), tz, "%a %H:%M")} (UTC{tz / 60:+.1f})</div>',
                        unsafe_allow_html=True)

    qts = query_time(day_name, at_time, tz)
    at_venue = None if at_choice == "__last__" else at_choice
    with ctx():
        data = prediction_service.predict_for_user(uid, qts, tz, int(backend().config["TOP_K"]), at_venue=at_venue)
    preds = data["predictions"]
    here = data["last_place"]

    # ---- map + ranking
    map_col, rank_col = st.columns([1.85, 1], gap="large")
    with map_col:
        if here:
            route = [{"lon": c["lon"], "lat": c["lat"], "label": place_label(c),
                      "when": fmt_local(c["ts_utc"], c["tz_offset_min"])} for c in reversed(recent[:10])]
            here_label = here["name"] or here["category"]
            if at_venue:
                route.append({"lon": here["lon"], "lat": here["lat"], "label": here_label, "when": "what-if"})
            st.pydeck_chart(build_deck(places, route, {"lon": here["lon"], "lat": here["lat"], "label": here_label},
                                       preds), height=560, use_container_width=True)
            st.markdown(
                '<div class="legend"><span><i class="line"></i>Recent route</span>'
                '<span><i style="background:rgba(15,110,140,.35);border:1.5px solid #0F6E8C"></i>Your places '
                '(bigger = more visits)</span><span><i style="background:#17212B"></i>You are here</span>'
                + "".join(f'<span><i style="background:{c}"></i>#{i + 1}</span>' for i, c in enumerate(RANK_COLOURS))
                + '</div>', unsafe_allow_html=True)
        else:
            st.info("Your account has no check-ins yet, so the predictions are just New York's most popular "
                    "places. Load a sample year of check-ins, or check in somewhere in the Check in tab.")
            with ctx():
                can_load = bool(sample_histories())
            if can_load and st.button("Load a sample year of check-ins", type="primary"):
                with ctx():
                    n = load_sample_into(uid)
                st.session_state.flash = (f"Loaded {n} made-up check-ins from the past year." if n else
                                          "Your account already has check-ins, so no sample was added.")
                st.rerun()
    with rank_col:
        where = (here["name"] or here["category"]) if here else "nowhere yet"
        when = "now" if day_name is None else f"{day_name} {at_time.strftime('%H:%M')}"
        st.markdown(f'<div class="panel-title">Top 3 next stops</div><div class="panel-sub">From '
                    f'<b>{e(where)}</b>, {e(when)} · {e(data["time_bucket"])}'
                    f'{" · what-if" if at_venue else ""}</div>', unsafe_allow_html=True)
        st.markdown(ranking_html(preds, data["weights"], data["cold_start"]), unsafe_allow_html=True)
        st.markdown(f'<div class="stat-row"><span class="chip"><b>{stats["total"]}</b> check-ins</span>'
                    f'<span class="chip"><b>{len(places)}</b> places</span>'
                    f'<span class="chip">using last <b>{data["history_used"]}</b></span></div>',
                    unsafe_allow_html=True)

    # ---- tabs
    t1, t2, t3, t4, t5 = st.tabs(["🕒 Timeline", "📍 Your places", "➕ Check in", "📈 Accuracy", "⚙️ Account"])
    with t1:
        show_timeline(recent)
    with t2:
        show_places(places, tz)
    with t3:
        show_checkin(uid, tz, recent)
    with t4:
        show_accuracy(uid, stats)
    with t5:
        show_account(uid, user, person_key)


def show_timeline(recent: list) -> None:
    if not recent:
        st.caption("No visits yet. Your check-ins will appear here.")
        return
    items = []
    for c in recent[:20]:
        r = c["predicted_rank"]
        tag = (f'<span class="tag hit">Predicted #{r}</span>' if r and 1 <= r <= 3 else
               '<span class="tag miss">Missed</span>' if r is not None else
               '<span class="tag sample">History</span>' if c["source"] == "sample" else "")
        items.append(f'<li><span class="dot"></span><div><div class="what">{e(place_label(c))}</div>'
                     f'<div class="when">{e(fmt_local(c["ts_utc"], c["tz_offset_min"]))} · {e(c["category"])}</div>'
                     f'</div>{tag}</li>')
    st.markdown(f'<ul class="tl">{"".join(items)}</ul>', unsafe_allow_html=True)


def show_places(places: list, tz: int) -> None:
    if not places:
        st.caption("No places yet.")
        return
    total = sum(p["visits"] for p in places)
    st.dataframe(
        [{"Place": place_label(p), "Type": p["category"], "Visits": p["visits"],
          "Share": f'{p["visits"] / total * 100:.1f}%', "Last visit": fmt_local(p["last_ts"], tz, "%d %b %Y")}
         for p in places], hide_index=True, use_container_width=True)


def show_checkin(uid: int, tz: int, recent: list) -> None:
    q = st.text_input("Kind of place or name", max_chars=60, placeholder="café, station, beach, mall…",
                      help="Places are listed nearest to your last check-in.")
    choice = None
    if q.strip():
        try:
            query = v.search_query(q)
        except v.ValidationError as err:
            st.error(err.message)
            return
        with ctx():
            rows = repo.search_venues(uid, query)
            last = repo.last_checkin(uid)
            origin = prediction_service.venue_dict(last["venue_id"], uid) if last else None
        items = []
        for r in rows:
            if last is not None and r["venue_id"] == last["venue_id"]:
                continue  # you're already here
            d = haversine_km(origin["lat"], origin["lon"], r["latitude"], r["longitude"]) if origin else None
            items.append((d if d is not None else 0, -r["popularity"], r["venue_id"], r["name"] or r["category"],
                          r["category"], d))
        items.sort()
        items = items[:8]
        if not items:
            st.warning(f'No places match "{q.strip()}". Try a broader word, like food or park.')
        else:
            labels = {i[2]: f"{i[3]} · {i[4]} · {fmt_km(i[5]) or 'popular'}" for i in items}
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
            ts = recent[0]["ts_utc"]
        try:
            with ctx():
                res = checkin_service.record_checkin(uid, choice, datetime.fromtimestamp(ts, timezone.utc).isoformat(), tz)
        except v.ValidationError as err:
            st.error(err.message)
            return
        rank = res["predicted_rank"]
        extra = (f" It was our #{rank} prediction." if rank and 1 <= rank <= 3
                 else " We didn't predict that one." if rank == 0 else "")
        st.session_state.flash = f"Checked in at {res['name'] or res['category']}.{extra}"
        st.rerun()


@st.cache_data(show_spinner=False, max_entries=64)
def own_replay(rows: tuple) -> dict | None:
    """Replay the last 20% of one history, exactly like scripts/evaluate.py does for the dataset."""
    from predictor.types import AlgoConfig
    from scripts.evaluate import metrics, replay
    if len(rows) < 20:
        return None
    cfg = AlgoConfig.from_settings(backend().config)
    venues = {}
    with ctx():
        for vid, _, _ in rows:
            d = prediction_service.venue_dict(vid)
            venues[vid] = (d["category"], d["lat"], d["lon"])
    res = replay({"me": list(rows)}, rows[int(len(rows) * 0.8)][1], cfg, {}, venues)
    return {m: metrics(res["ranks"][m]) for m in ("most_visited", "markov", "full")}


def show_accuracy(uid: int, stats: dict) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown('<div class="panel-title">On your own history</div><div class="panel-sub">The last 20% of '
                    'your check-ins replayed in order; each prediction uses only earlier check-ins.</div>',
                    unsafe_allow_html=True)
        with ctx():
            rows = tuple((r["venue_id"], r["ts_utc"], r["tz_offset_min"]) for r in repo.history(uid, 0))
        res = own_replay(rows)
        if res is None:
            st.caption("Needs at least 20 check-ins.")
        else:
            full, base = res["full"], res["most_visited"]
            for label, key in (("Top 1", "top1"), ("Top 3", "top3"), ("Top 5", "top5")):
                st.progress(min(1.0, full[key]), text=f"{label}: {full[key] * 100:.1f}%")
            st.caption(f"{full['n']} predictions. Most-visited baseline: {base['top3'] * 100:.1f}% top 3. "
                       "Made-up routines are far more regular than real people, so expect high numbers here.")
        if stats["scored"]:
            st.markdown(f"**Live:** {stats['hits_top3']} of {stats['scored']} check-ins you made after seeing "
                        f"predictions were in the top 3.")
    with right:
        st.markdown('<div class="panel-title">On real people</div><div class="panel-sub">Foursquare New York '
                    'data, last 20% replayed. This is the number to quote.</div>', unsafe_allow_html=True)
        path = Path(backend().config["EVAL_RESULTS_PATH"])
        if not path.is_file():
            st.caption("No evaluation results yet. Run: python scripts/evaluate.py")
            return
        d = json.loads(path.read_text(encoding="utf-8"))
        full, base = d["methods"]["full"], d["methods"]["most_visited"]
        for label, key in (("Top 1", "top1"), ("Top 3", "top3"), ("Top 5", "top5")):
            st.progress(min(1.0, full[key]), text=f"{label}: {full[key] * 100:.1f}%")
        st.caption(f"{d['dataset']['test_checkins']:,} check-ins. Most-visited baseline: {base['top3'] * 100:.1f}% "
                   f"top 3, so the rule is {(full['top3'] - base['top3']) * 100:.1f} points better.")


def show_account(uid: int, user, person_key: str | None) -> None:
    with ctx():
        stats = repo.checkin_stats(uid)
        export = repo.export_user(uid)
    st.write(f"**Account:** {user['email']}  \n**Check-ins:** {stats['total']} ({stats['own']} added by you)")
    st.download_button("Download my data (JSON)", json.dumps(export, indent=2), file_name="syncromove-export.json",
                       mime="application/json")
    if person_key:
        st.info("This is a shared demo person. Anyone using the demo can add check-ins here. "
                "Reset to put back the original six months.")
        if st.button("Reset this demo person"):
            with ctx():
                demo.ensure_demo(get_db(), force=True, only=person_key)
            st.session_state.flash = "Demo history reset to the original six months."
            st.rerun()
        return
    for action, title, note, label in (
        ("delete_checkins", "Delete your check-ins", "Removes all check-ins and prediction logs. Your account stays.",
         "Delete check-ins"),
        ("delete_account", "Delete your account",
         "Removes your account and everything linked to it. This can't be undone.", "Delete account"),
    ):
        st.markdown(f"**{title}**")
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
    show_landing()
else:
    show_dashboard(uid)
