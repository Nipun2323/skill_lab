/* SyncroMove dashboard: transit-map hero, next stops, check-in, recent visits, accuracy. */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var RANK_COLOURS = ["#E3A008", "#23935F", "#6E4FC4"];
  var SIGNAL_NAMES = {
    transition: "After your current place",
    time: "At this time of day",
    frequency: "How often you go",
    recency: "Been there recently"
  };
  var csrf = document.querySelector('meta[name="csrf-token"]').getAttribute("content");
  var tz = -new Date().getTimezoneOffset();
  var state = { checkins: [], predictions: null, selected: null, queryAt: null };

  // ------------------------------------------------------------------ helpers
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function svg(tag, attrs, text) {
    var n = document.createElementNS(SVG_NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function api(path, opts) {
    opts = opts || {};
    var headers = { "Accept": "application/json" };
    if (opts.body) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf;
    }
    return fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin"
    }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login?next=/dashboard"; }
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function toLocalInput(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function parseLocalInput(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) return null;
    var d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }

  function formatVisitTime(ts, offsetMin) {
    var d = new Date((ts + offsetMin * 60) * 1000);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " +
      pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  }

  function pct(x, digits) { return (x * 100).toFixed(digits === undefined ? 0 : digits) + "%"; }

  function km(x) {
    if (x === null || x === undefined) return "";
    return x < 1 ? Math.round(x * 1000) + " m away" : x.toFixed(1) + " km away";
  }

  // ------------------------------------------------------------------ map
  function renderMap() {
    var map = $("map");
    var route = state.checkins.slice(0, 8).reverse();
    var preds = (state.predictions && state.predictions.predictions) || [];
    // remove everything except title/desc
    Array.prototype.slice.call(map.childNodes).forEach(function (n) {
      if (n.nodeName !== "title" && n.nodeName !== "desc") map.removeChild(n);
    });
    var empty = route.length === 0;
    $("map-empty").hidden = !empty;
    map.classList.toggle("is-empty", empty);
    var desc = map.querySelector("desc");
    if (empty) { desc.textContent = "No check-ins yet."; return; }

    var pts = route.map(function (c) { return { lat: c.lat, lon: c.lon }; })
      .concat(preds.map(function (p) { return { lat: p.lat, lon: p.lon }; }));
    var lat0 = pts.reduce(function (s, p) { return s + p.lat; }, 0) / pts.length;
    var kx = Math.cos(lat0 * Math.PI / 180);
    var xs = pts.map(function (p) { return p.lon * kx; });
    var ys = pts.map(function (p) { return -p.lat; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var MIN_SPAN = 0.006;
    var spanX = Math.max(maxX - minX, MIN_SPAN), spanY = Math.max(maxY - minY, MIN_SPAN);
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    var narrow = map.parentNode.clientWidth < 600;
    var W = narrow ? 420 : 800, H = narrow ? 440 : 420, M = narrow ? 48 : 56;
    map.setAttribute("viewBox", "0 0 " + W + " " + H);
    map.classList.toggle("narrow", narrow);
    var scale = Math.min((W - 2 * M) / spanX, (H - 2 * M) / spanY);
    function proj(p) {
      return [W / 2 + (p.lon * kx - cx) * scale, H / 2 + (-p.lat - cy) * scale];
    }

    // faint grid, like map paper
    var grid = svg("g", { "class": "map-grid", "aria-hidden": "true" });
    for (var gx = 40; gx < W; gx += 40) grid.appendChild(svg("line", { x1: gx, y1: 0, x2: gx, y2: H }));
    for (var gy = 40; gy < H; gy += 40) grid.appendChild(svg("line", { x1: 0, y1: gy, x2: W, y2: gy }));
    map.appendChild(grid);

    var here = proj(route[route.length - 1]);

    // predicted branches (drawn under the main line)
    var branches = svg("g", { "class": "branches" });
    preds.forEach(function (p, i) {
      var xy = proj(p);
      branches.appendChild(svg("path", {
        d: "M" + here[0] + " " + here[1] + " L" + xy[0] + " " + xy[1],
        "class": "branch", stroke: RANK_COLOURS[i] || "#5B6875"
      }));
    });
    map.appendChild(branches);

    // the line through recent check-ins
    if (route.length > 1) {
      var d = route.map(function (c, i) { var xy = proj(c); return (i ? "L" : "M") + xy[0] + " " + xy[1]; }).join(" ");
      map.appendChild(svg("path", { d: d, "class": "line-casing" }));
      map.appendChild(svg("path", { d: d, "class": "line" }));
    }

    var stations = svg("g", { "class": "stations" });
    route.forEach(function (c, i) {
      var xy = proj(c);
      var last = i === route.length - 1;
      var s = svg("circle", { cx: xy[0], cy: xy[1], r: last ? 11 : 7, "class": last ? "station here" : "station" });
      s.appendChild(svg("title", null, c.category + " · " + formatVisitTime(c.ts_utc, c.tz_offset_min)));
      stations.appendChild(s);
    });
    map.appendChild(stations);

    // Labels are placed through a tiny collision check so close places stay readable.
    var placed = [];
    function boxFor(x, y, w, h, anchor) {
      var x0 = anchor === "end" ? x - w : (anchor === "middle" ? x - w / 2 : x);
      return { x0: x0, x1: x0 + w, y0: y - h, y1: y + 4 };
    }
    function hits(b) {
      return placed.some(function (p) { return b.x0 < p.x1 && b.x1 > p.x0 && b.y0 < p.y1 && b.y1 > p.y0; });
    }
    function inside(b) { return b.x0 >= 4 && b.x1 <= W - 4 && b.y0 >= 4 && b.y1 <= H - 4; }
    // Try each candidate {x, y, anchor}; take the first that fits without overlapping.
    function place(cands, w, h) {
      for (var i = 0; i < cands.length; i++) {
        var c = cands[i], b = boxFor(c.x, c.y, w, h, c.anchor);
        if (!hits(b) && inside(b)) { placed.push(b); return c; }
      }
      var c0 = cands[0];
      placed.push(boxFor(c0.x, c0.y, w, h, c0.anchor));
      return c0;
    }
    function around(x, y, gap, h) {
      return [
        { x: x + gap, y: y + 5, anchor: "start" },
        { x: x - gap, y: y + 5, anchor: "end" },
        { x: x, y: y - gap + 2, anchor: "middle" },
        { x: x, y: y + gap + h - 2, anchor: "middle" }
      ];
    }
    var FS = narrow ? 1.35 : 1;  // matches the .map.narrow font sizes in app.css
    function textWidth(t, size) { return t.length * size * FS * 0.56; }

    // keep prediction bullets clear of the current stop and of each other
    var MIN_PX = 46;
    var bulletXY = [];
    preds.forEach(function (p, i) {
      var xy = proj(p);
      var dx = xy[0] - here[0], dy = xy[1] - here[1];
      var dist = Math.sqrt(dx * dx + dy * dy);
      var ang = dist > 1 ? Math.atan2(dy, dx) : (-Math.PI / 2 + i * 2 * Math.PI / 3);
      var r = Math.max(dist, MIN_PX);
      function clash(pt) {
        if (Math.hypot(pt[0] - here[0], pt[1] - here[1]) < MIN_PX - 0.5) return true;
        return bulletXY.some(function (q) { return Math.hypot(pt[0] - q[0], pt[1] - q[1]) < 36; });
      }
      var pt = dist < MIN_PX ? [here[0] + Math.cos(ang) * r, here[1] + Math.sin(ang) * r] : xy;
      for (var t = 1; clash(pt) && t <= 12; t++) {
        var a = ang + (t % 2 ? 1 : -1) * Math.ceil(t / 2) * (Math.PI / 6);
        pt = [here[0] + Math.cos(a) * r, here[1] + Math.sin(a) * r];
      }
      pt = [Math.min(Math.max(pt[0], 18), W - 18), Math.min(Math.max(pt[1], 18), H - 18)];
      bulletXY.push(pt);
    });
    // redraw branch ends to the adjusted bullet positions
    Array.prototype.forEach.call(branches.childNodes, function (path, i) {
      path.setAttribute("d", "M" + here[0] + " " + here[1] + " L" + bulletXY[i][0] + " " + bulletXY[i][1]);
    });

    // reserve the station and bullet discs first
    placed.push({ x0: here[0] - 13, x1: here[0] + 13, y0: here[1] - 13, y1: here[1] + 13 });
    bulletXY.forEach(function (xy) { placed.push({ x0: xy[0] - 17, x1: xy[0] + 17, y0: xy[1] - 17, y1: xy[1] + 17 }); });

    // "You are here" label
    var lastC = route[route.length - 1];
    var hereText = lastC.category;
    var hw = Math.max(textWidth(hereText, 15), textWidth("YOU ARE HERE", 11));
    var hc = place([
      { x: here[0] + 18, y: here[1] + 34, anchor: "start" },
      { x: here[0] - 18, y: here[1] + 34, anchor: "end" },
      { x: here[0] + 18, y: here[1] - 4, anchor: "start" },
      { x: here[0] - 18, y: here[1] - 4, anchor: "end" },
      { x: here[0], y: here[1] + 52, anchor: "middle" },
      { x: here[0], y: here[1] - 22, anchor: "middle" }
    ], hw, 34);
    var lx = hc.x, anchor = hc.anchor;
    var label = svg("text", { x: lx, y: hc.y - 18, "class": "here-label", "text-anchor": anchor });
    label.appendChild(svg("tspan", { x: lx, dy: 0, "class": "here-kicker" }, "You are here"));
    label.appendChild(svg("tspan", { x: lx, dy: 18 }, hereText));
    map.appendChild(label);

    // subway bullets for predictions
    var bullets = svg("g", { "class": "bullets" });
    preds.forEach(function (p, i) {
      var xy = bulletXY[i];
      var g = svg("g", { "class": "bullet" });
      g.appendChild(svg("circle", { cx: xy[0], cy: xy[1], r: 15, fill: RANK_COLOURS[i] || "#5B6875" }));
      g.appendChild(svg("text", { x: xy[0], y: xy[1] + 5.5, "text-anchor": "middle", "class": "bullet-num" }, String(i + 1)));
      var tw = textWidth(p.category, 13);
      var bc = place(around(xy[0], xy[1], 21, 14), tw, 16);
      g.appendChild(svg("text", { x: bc.x, y: bc.y, "text-anchor": bc.anchor, "class": "bullet-label" }, p.category));
      g.appendChild(svg("title", null, "#" + (i + 1) + " " + p.category + " · " + pct(p.probability)));
      bullets.appendChild(g);
    });
    map.appendChild(bullets);

    // scale bar
    var pxPerKm = scale / 111.32;
    var kmLen = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20].filter(function (k) { return k * pxPerKm <= 140; }).pop() || 0.1;
    var sb = svg("g", { "class": "scalebar", "aria-hidden": "true" });
    sb.appendChild(svg("line", { x1: 20, y1: H - 20, x2: 20 + kmLen * pxPerKm, y2: H - 20 }));
    sb.appendChild(svg("text", { x: 20, y: H - 28 }, kmLen < 1 ? (kmLen * 1000) + " m" : kmLen + " km"));
    map.appendChild(sb);

    desc.textContent = "Line through your last " + route.length + " check-ins, ending at " + lastC.category +
      ". " + (preds.length ? "Predicted next: " + preds.map(function (p, i) { return (i + 1) + ". " + p.category; }).join(", ") + "." : "");
  }

  // ------------------------------------------------------------------ next stops
  function renderStops() {
    var list = $("stops");
    clear(list);
    var data = state.predictions;
    var preds = (data && data.predictions) || [];
    var empty = $("stops-empty");
    empty.hidden = preds.length > 0;
    if (!preds.length) {
      empty.textContent = "No predictions yet. Check in somewhere to start your line.";
      return;
    }
    var maxP = Math.max.apply(null, preds.map(function (p) { return p.probability; })) || 1;
    preds.forEach(function (p, i) {
      var li = el("li", { "class": "stop" });
      var bullet = el("span", { "class": "rank-bullet rank-" + (i + 1), "aria-hidden": "true" }, String(i + 1));
      var body = el("div", { "class": "stop-body" });
      var head = el("div", { "class": "stop-head" });
      head.appendChild(el("span", { "class": "stop-name" }, p.category));
      head.appendChild(el("span", { "class": "stop-dist muted" }, km(p.distance_km)));
      body.appendChild(head);
      body.appendChild(el("p", { "class": "stop-reason" }, p.reason));

      var meter = el("div", { "class": "likelihood" });
      var bar = el("div", { "class": "likelihood-bar", role: "img",
        "aria-label": "Likelihood " + pct(p.probability) });
      var fill = el("span", { "class": "likelihood-fill rank-" + (i + 1) });
      fill.style.width = Math.max(4, (p.probability / maxP) * 100) + "%";
      bar.appendChild(fill);
      meter.appendChild(bar);
      meter.appendChild(el("span", { "class": "likelihood-num num" }, pct(p.probability)));
      body.appendChild(meter);

      var whyId = "why-" + i;
      var btn = el("button", { type: "button", "class": "link-button why-toggle", "aria-expanded": "false",
        "aria-controls": whyId }, "Why?");
      var why = el("div", { id: whyId, "class": "why", hidden: "" });
      var table = el("table", { "class": "signals" });
      var cap = el("caption", { "class": "sr-only" }, "Signal values for " + p.category);
      table.appendChild(cap);
      var thead = el("thead");
      var hr = el("tr");
      ["Signal", "Value", "Weight", "Adds"].forEach(function (h) { hr.appendChild(el("th", { scope: "col" }, h)); });
      thead.appendChild(hr);
      table.appendChild(thead);
      var tbody = el("tbody");
      var weights = data.weights || {};
      Object.keys(SIGNAL_NAMES).forEach(function (name) {
        var v = p.signals[name] || 0, w = weights[name] || 0;
        var tr = el("tr", (p.reason_code === name) ? { "class": "top-signal" } : null);
        tr.appendChild(el("th", { scope: "row" }, SIGNAL_NAMES[name]));
        tr.appendChild(el("td", { "class": "num" }, v.toFixed(3)));
        tr.appendChild(el("td", { "class": "num" }, "× " + w.toFixed(2)));
        tr.appendChild(el("td", { "class": "num" }, (v * w).toFixed(3)));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      why.appendChild(table);
      why.appendChild(el("p", { "class": "muted small" },
        "Each signal is this place's share among the places you've been (0 to 1). Score " +
        p.score.toFixed(3) + "; city popularity adds a tiny tie-breaker."));
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", open ? "false" : "true");
        btn.textContent = open ? "Why?" : "Hide";
        why.hidden = open;
      });
      body.appendChild(btn);
      body.appendChild(why);

      li.appendChild(bullet);
      li.appendChild(body);
      list.appendChild(li);
    });
  }

  function renderBucket() {
    var data = state.predictions;
    if (!data) return;
    var when = state.queryAt ? "Predicting for " + data.time_bucket : "Now: " + data.time_bucket;
    $("time-bucket").textContent = when;
    var note = data.cold_start
      ? "Not enough history yet: showing popular New York places until you check in."
      : "Based on your last " + data.history_used + " check-ins. Sample histories are made up, at real New York places.";
    $("map-note").textContent = note;
  }

  // ------------------------------------------------------------------ visits
  function renderVisits() {
    var list = $("visits");
    clear(list);
    $("visits-empty").hidden = state.checkins.length > 0;
    state.checkins.forEach(function (c) {
      var li = el("li", { "class": "visit" });
      li.appendChild(el("span", { "class": "visit-dot", "aria-hidden": "true" }));
      var body = el("div", { "class": "visit-body" });
      body.appendChild(el("span", { "class": "visit-name" }, c.category));
      body.appendChild(el("span", { "class": "visit-time muted small num" }, formatVisitTime(c.ts_utc, c.tz_offset_min)));
      li.appendChild(body);
      var tag = null;
      if (c.predicted_rank >= 1 && c.predicted_rank <= 3) tag = el("span", { "class": "tag tag-hit rank-" + c.predicted_rank }, "Predicted #" + c.predicted_rank);
      else if (c.predicted_rank === 0 || c.predicted_rank > 3) tag = el("span", { "class": "tag tag-miss" }, "Missed");
      else if (c.source === "sample") tag = el("span", { "class": "tag tag-sample" }, "Sample");
      if (tag) li.appendChild(tag);
      list.appendChild(li);
    });
  }

  // ------------------------------------------------------------------ accuracy
  function renderAccuracy(res, stats) {
    var box = $("accuracy");
    clear(box);
    if (!res.ok) {
      box.appendChild(el("p", { "class": "muted small" }, (res.data.error && res.data.error.message) || "No evaluation results available."));
      return;
    }
    var d = res.data, full = d.methods.full, base = d.methods.most_visited, ex = d.full_extras;
    box.appendChild(el("p", { "class": "small" },
      "Measured by replaying the last 20% of the New York data (" + d.dataset.test_checkins.toLocaleString() +
      " check-ins): how often the place someone actually went was in our list."));
    var rows = [["Top 1", full.top1], ["Top 3", full.top3], ["Top 5", full.top5]];
    var dl = el("dl", { "class": "acc-bars" });
    rows.forEach(function (r) {
      var wrap = el("div", { "class": "acc-row" });
      wrap.appendChild(el("dt", null, r[0]));
      var dd = el("dd");
      var bar = el("span", { "class": "acc-bar" });
      var fill = el("span", { "class": "acc-fill" });
      fill.style.width = (r[1] * 100 / 0.5) + "%";  // axis 0–50%
      bar.appendChild(fill);
      dd.appendChild(bar);
      dd.appendChild(el("span", { "class": "num acc-val" }, pct(r[1], 1)));
      wrap.appendChild(dd);
      dl.appendChild(wrap);
    });
    box.appendChild(dl);
    box.appendChild(el("p", { "class": "small" },
      "Baseline (your most-visited places): " + pct(base.top3, 1) + " top 3. This rule is " +
      ((full.top3 - base.top3) * 100).toFixed(1) + " points better."));
    box.appendChild(el("p", { "class": "small muted" },
      "Easier targets: right kind of place in the top 3 categories " + pct(ex.top3_category, 1) +
      "; a top-3 place within 1 km of the real one " + pct(ex.top3_within_1km, 1) + "."));
    var you = el("p", { "class": "small you" });
    if (stats && stats.scored > 0) {
      you.textContent = "You: " + stats.hits_top3 + " of " + stats.scored + " check-ins were in the top 3 we showed (" +
        pct(stats.hits_top3 / stats.scored) + ").";
    } else {
      you.textContent = "You: check in after viewing predictions to see your own hit rate.";
    }
    box.appendChild(you);
  }

  // ------------------------------------------------------------------ loading
  function loadPredictions() {
    var q = "/api/predictions?k=3&tz=" + tz;
    if (state.queryAt) q += "&at=" + encodeURIComponent(state.queryAt.toISOString());
    return api(q).then(function (r) {
      if (r.ok) state.predictions = r.data;
      else {
        state.predictions = null;
        $("stops-empty").hidden = false;
        $("stops-empty").textContent = (r.data.error && r.data.error.message) || "Couldn't load predictions.";
      }
    });
  }

  function loadCheckins() {
    return api("/api/checkins?limit=10").then(function (r) {
      if (r.ok) { state.checkins = r.data.checkins; state.stats = r.data.stats; }
    });
  }

  function refresh() {
    return Promise.all([loadPredictions(), loadCheckins()]).then(function () {
      renderBucket(); renderMap(); renderStops(); renderVisits();
      return api("/api/accuracy").then(function (r) { renderAccuracy(r, state.stats); });
    });
  }

  // ------------------------------------------------------------------ prediction time picker
  var whenInput = $("when-input");
  whenInput.value = toLocalInput(new Date());
  $("when-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var d = parseLocalInput(whenInput.value);
    if (!d) { window.SMValidate.setError(whenInput, "Enter a valid date and time."); whenInput.focus(); return; }
    window.SMValidate.setError(whenInput, "");
    state.queryAt = d;
    loadPredictions().then(function () { renderBucket(); renderMap(); renderStops(); });
  });
  $("when-now").addEventListener("click", function () {
    state.queryAt = null;
    whenInput.value = toLocalInput(new Date());
    window.SMValidate.setError(whenInput, "");
    loadPredictions().then(function () { renderBucket(); renderMap(); renderStops(); });
  });

  // ------------------------------------------------------------------ check-in panel
  var search = $("place-search");
  var results = $("place-results");
  var timeInput = $("checkin-time");
  timeInput.value = toLocalInput(new Date());
  var searchTimer = null;

  function renderResults(venues, near) {
    clear(results);
    state.selected = null;
    $("place-results-wrap").hidden = venues.length === 0;
    venues.forEach(function (v, i) {
      var li = el("li");
      var id = "place-" + i;
      var input = el("input", { type: "radio", name: "place", id: id, value: v.venue_id });
      input.addEventListener("change", function () {
        state.selected = v;
        window.SMValidate.setError(search, "");
      });
      var label = el("label", { "for": id });
      label.appendChild(el("span", { "class": "place-name" }, v.category));
      var meta = near ? km(v.distance_km) : (v.popularity + " check-ins in the data");
      label.appendChild(el("span", { "class": "place-meta muted small" },
        meta + " · " + v.lat.toFixed(4) + ", " + v.lon.toFixed(4)));
      li.appendChild(input);
      li.appendChild(label);
      results.appendChild(li);
    });
  }

  function runSearch() {
    var q = search.value.trim();
    if (q.length > 60) { window.SMValidate.setError(search, "Search text must be 60 characters or fewer."); return; }
    if (q.length < 2) { renderResults([], false); return; }
    api("/api/venues?limit=8&q=" + encodeURIComponent(q)).then(function (r) {
      if (!r.ok) { window.SMValidate.setError(search, r.data.error ? r.data.error.message : "Search failed."); return; }
      if (!r.data.venues.length) {
        window.SMValidate.setError(search, "No places match \"" + q + "\". Try a broader word, like food or park.");
      } else window.SMValidate.setError(search, "");
      renderResults(r.data.venues, r.data.near_last_checkin);
    });
  }

  search.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 250);
  });
  search.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(searchTimer); runSearch(); }
  });

  function showCheckinMessage(kind, text) {
    $("checkin-error").hidden = kind !== "error";
    $("checkin-ok").hidden = kind !== "ok";
    $(kind === "error" ? "checkin-error" : "checkin-ok").textContent = text;
  }

  $("checkin-form").addEventListener("submit", function (e) {
    e.preventDefault();
    showCheckinMessage(null, "");
    var ok = true;
    if (!state.selected) {
      window.SMValidate.setError(search, "Choose a place from the search results.");
      ok = false;
    }
    var d = parseLocalInput(timeInput.value);
    var last = state.checkins[0];
    var msg = "";
    if (!timeInput.value) msg = "Enter the time of your check-in.";
    else if (!d) msg = "Enter a valid date and time.";
    else if (d.getTime() > Date.now() + 5 * 60 * 1000) msg = "Check-in time can't be in the future.";
    else if (last && Math.floor(d.getTime() / 1000) < last.ts_utc - 59) msg = "Check-in time can't be earlier than your last check-in (" + formatVisitTime(last.ts_utc, last.tz_offset_min) + ").";
    window.SMValidate.setError(timeInput, msg);
    if (msg) ok = false;
    if (!ok) { (state.selected ? timeInput : search).focus(); return; }

    var ts = Math.floor(d.getTime() / 1000);
    if (last && ts < last.ts_utc) ts = last.ts_utc;  // same minute as the last check-in
    var body = { venue_id: state.selected.venue_id, timestamp: new Date(ts * 1000).toISOString(), tz_offset_min: tz };
    api("/api/checkins", { method: "POST", body: body }).then(function (r) {
      if (!r.ok) {
        var err = r.data.error || {};
        if (err.field === "timestamp") window.SMValidate.setError(timeInput, err.message);
        else if (err.field === "venue_id") window.SMValidate.setError(search, err.message);
        else showCheckinMessage("error", err.message || "Check-in failed.");
        return;
      }
      var rank = r.data.predicted_rank;
      var extra = rank >= 1 && rank <= 3 ? " It was our #" + rank + " prediction." :
        (rank === 0 ? " We didn't predict that one." : "");
      showCheckinMessage("ok", "Checked in at " + r.data.category + "." + extra);
      search.value = "";
      renderResults([], false);
      timeInput.value = toLocalInput(new Date());
      state.queryAt = null;
      whenInput.value = toLocalInput(new Date());
      refresh();
    });
  });

  refresh();
})();
