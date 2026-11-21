#!/usr/bin/env python3
"""Build tv.html: single-screen TV view of the Show Tracker, in the main
tracker's visual language (logos, buttons, tap-to-pack chips) + 7-day gross
per channel. Logos are copied verbatim from index.html so they never drift."""
import re

SRC = "/home/hatch/workspace/show-tracker-pages/index.html"
OUT = "/home/hatch/workspace/show-tracker-pages/tv.html"

html = open(SRC).read()

def grab(pattern, name):
    m = re.search(pattern, html, re.S)
    assert m, f"could not find {name} in index.html"
    return m.group(0)

ebay_path = grab(r'var EBAY_PATH = "[^"]*";', "EBAY_PATH")
tiktok_path = grab(r'var TIKTOK_PATH = "[^"]*";', "TIKTOK_PATH")
# logoHTML function: from 'function logoHTML' to the line '      }\n      function platformOf'
logo_fn = grab(r'function logoHTML\(chan\) \{.*?\n      \}\n', "logoHTML")

GROSS = {
    "Whatnot Footy": 170144.80,
    "Whatnot Main": 1816986,
    "Whatnot Galaxy": 110450.95,
    "Whatnot Card Shop": 840118.21,
    "Whatnot Ice": 74812.49,
    "Fanatics Baseball": 344958,
    "Fanatics Jamz": 632644,
    "Fanatics Blitz": 1191443,
    "Fanatics Evolution": 124917,
    "Fanatics Repack": 1017971,
    "eBay Live": 214914.03,
    "TikTok Card Shop": 180463.07,
}
GROSS_WINDOW = "Nov 14–20"

import json
gross_js = "var GROSS = " + json.dumps(GROSS) + ";\n      var GROSS_WINDOW = " + json.dumps(GROSS_WINDOW) + ";"

template = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="dark" />
    <title>Show Tracker — TV</title>
    <link href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600;700&display=swap" rel="stylesheet" />
    <style>
      * { box-sizing: border-box; }
      html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; background: #000; color: #f4f5f6;
        font-family: "Barlow", system-ui, sans-serif; }
      body { display: flex; flex-direction: column; height: 100vh; width: 100vw; }

      header.band { background: #22262b; padding: 1vh 1.5vw 0.8vh; flex: 0 0 auto; }
      .band-in { display: flex; align-items: center; gap: 2vw; }
      .wordmark { font-size: 1.1vw; color: #b9c0c7; text-transform: uppercase; letter-spacing: .06em; }
      .wordmark b { color: #f4f5f6; }
      h1 { font-family: "Barlow Semi Condensed", sans-serif; font-size: 2.6vw; margin: 0;
        text-transform: uppercase; letter-spacing: .02em; }
      .totals { display: flex; gap: 2vw; margin-left: auto; align-items: baseline; }
      .totals .t { text-align: right; }
      .totals .num { font-family: "Barlow Semi Condensed", sans-serif; font-weight: 700; font-size: 2.6vw; line-height: 1; }
      .totals .num.orange { color: #f06a24; }
      .totals .lbl { font-size: 0.95vw; color: #b9c0c7; text-transform: uppercase; letter-spacing: .08em; }
      .updated { font-size: 1vw; color: #b9c0c7; text-align: right; }
      .updated strong { color: #f4f5f6; }
      .filters { display: flex; gap: 0.6vw; margin-top: 0.8vh; }
      .filter-btn { font-family: "Barlow Semi Condensed", sans-serif; font-size: 1.15vw; font-weight: 600;
        text-transform: uppercase; letter-spacing: .04em; padding: 0.35vh 1vw; border-radius: 999px;
        border: 1px solid #4a5058; background: transparent; color: #f4f5f6; cursor: pointer; }
      .filter-btn[aria-pressed="true"] { background: #f4f5f6; color: #141619; border-color: #f4f5f6; }

      main { flex: 1 1 auto; display: grid; grid-template-columns: repeat(4, 1fr);
        grid-template-rows: repeat(3, 1fr); gap: 0.8vw; padding: 0.9vw; min-height: 0; }
      .card { background: #141619; border: 1px solid #2a2e33; border-radius: 0.5vw;
        padding: 0.9vh 0.9vw; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
      .card.hot { border-top: 4px solid #f06a24; }
      .card.done { border-top: 4px solid #1e7d46; justify-content: center; }
      .card-head { display: flex; align-items: center; gap: 0.6vw; }
      .plat-logo img, .plat-logo svg { width: 2.4vw; height: 2.4vw; display: block; }
      .plat-logo svg { fill: #f4f5f6; }
      .card h2 { font-family: "Barlow Semi Condensed", sans-serif; font-size: 1.55vw; line-height: 1.05;
        margin: 0; text-transform: uppercase; }
      .chips { display: flex; flex-wrap: wrap; gap: 0.4vw; margin-top: 0.8vh; }
      .chip { font-family: "Barlow Semi Condensed", sans-serif; font-size: 0.95vw; font-weight: 600;
        background: #000; color: #f4f5f6; border: 1px solid #4a5058; border-radius: 0.35vw;
        padding: 0.25vh 0.45vw; cursor: pointer; }
      .chip:hover { border-color: #f06a24; }
      .chip .pk { color: #b9c0c7; font-weight: 500; }
      .gross { margin-top: 0.7vh; font-size: 1.2vw; white-space: nowrap; overflow: hidden; }
      .gross strong { color: #4cc38a; font-family: "Barlow Semi Condensed", sans-serif; font-size: 1.45vw; }
      .gross .win { color: #b9c0c7; font-size: 0.95vw; }
      .oldest { margin-top: auto; font-size: 1vw; color: #b9c0c7; padding-top: 0.5vh; }
      .oldest strong { color: #f4f5f6; }
      .done-msg { font-family: "Barlow Semi Condensed", sans-serif; font-weight: 700; color: #4cc38a;
        font-size: 1.5vw; text-transform: uppercase; margin-top: 0.6vh; }
      .counts { font-size: 1.1vw; color: #b9c0c7; margin-top: 0.4vh; }
      .counts strong { color: #f4f5f6; }

      footer { flex: 0 0 auto; display: flex; align-items: center; gap: 0.8vw; padding: 0.7vh 1.5vw;
        border-top: 1px solid #2a2e33; background: #0f1113; min-height: 4.5vh; }
      footer .lbl { font-size: 1.05vw; color: #b9c0c7; text-transform: uppercase; letter-spacing: .06em; }
      footer .packed-chips { display: flex; flex-wrap: wrap; gap: 0.4vw; }
      .packed-chip { font-size: 1vw; background: #1e7d46; color: #fff; border: none; border-radius: 0.35vw;
        padding: 0.3vh 0.6vw; cursor: pointer; font-family: "Barlow Semi Condensed", sans-serif; font-weight: 600; }
      #loadError { display: none; position: fixed; inset: 0; background: #000; color: #f4f5f6;
        align-items: center; justify-content: center; font-size: 2vw; text-align: center; padding: 5vw; z-index: 9; }
    </style>
  </head>
  <body>
    <header class="band">
      <div class="band-in">
        <div>
          <div class="wordmark"><b>WeTheHobby</b> &nbsp;·&nbsp; Operations</div>
          <h1>Show Tracker</h1>
        </div>
        <div class="totals">
          <div class="t"><div class="num orange" id="totalShows">–</div><div class="lbl">Shows in house</div></div>
          <div class="t"><div class="num" id="totalPkgs">–</div><div class="lbl">Packages</div></div>
          <div class="t"><div class="num" id="totalGross">–</div><div class="lbl">7-day gross</div></div>
        </div>
        <div class="updated">Last updated<br /><strong id="lastUpdated">—</strong></div>
      </div>
      <div class="filters" id="filters" role="group" aria-label="Filter by platform">
        <button class="filter-btn" data-f="All" aria-pressed="true">All</button>
        <button class="filter-btn" data-f="Whatnot" aria-pressed="false">Whatnot</button>
        <button class="filter-btn" data-f="Fanatics" aria-pressed="false">Fanatics</button>
        <button class="filter-btn" data-f="eBay" aria-pressed="false">eBay</button>
        <button class="filter-btn" data-f="TikTok" aria-pressed="false">TikTok</button>
      </div>
    </header>
    <main id="grid"></main>
    <footer>
      <span class="lbl">Packed (<span id="packedCount">0</span>) — tap to put back</span>
      <span class="packed-chips" id="packedChips"></span>
    </footer>
    <div id="loadError">Could not load show-tracker-data.json.<br />Check the file is in the same folder, then this page picks it up.</div>

    <script>
      // TV / wall-monitor view: same look and buttons as the main tracker,
      // all channels side by side on one screen, no scroll.
      var CHANNELS = [
        "Whatnot Footy", "Whatnot Main", "Whatnot Galaxy", "Whatnot Card Shop",
        "Whatnot Ice", "Fanatics Baseball", "Fanatics Jamz", "Fanatics Blitz",
        "Fanatics Evolution", "Fanatics Repack", "eBay Live", "TikTok Card Shop"
      ];
      __GROSS__
      var DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
      var MON = ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"];
      var packed = {};   // show id -> show (local taps, like the main tracker)
      var shows = [];
      var activeFilter = "All";

      function money(n) { return "$" + Math.round(n).toLocaleString("en-US"); }
      function fmtClock(d) {
        var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
        h = h % 12; if (h === 0) h = 12;
        return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
      }
      function fmtLong(d) {
        return DOW[d.getDay()] + ", " + MON[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
      }
      function fmtShort(d) {
        return DOW[d.getDay()].slice(0, 3) + " " + (d.getMonth() + 1) + "/" + d.getDate() + " " + fmtClock(d);
      }
      function hoursAgo(d) {
        var h = Math.floor((Date.now() - d.getTime()) / 3600000);
        return h < 1 ? "under an hour ago" : h + "h ago";
      }
      function platformOf(chan) {
        if (chan.indexOf("Whatnot") === 0) return "Whatnot";
        if (chan.indexOf("Fanatics") === 0) return "Fanatics";
        if (chan.indexOf("eBay") === 0) return "eBay";
        if (chan.indexOf("TikTok") === 0) return "TikTok";
        return "";
      }

      __EBAY_PATH__
      __TIKTOK_PATH__
      __LOGO_FN__

      function visibleChannels() {
        return CHANNELS.filter(function (c) {
          return activeFilter === "All" || platformOf(c) === activeFilter;
        });
      }

      function render(json) {
        var byChannel = {};
        CHANNELS.forEach(function (c) { byChannel[c] = []; });
        shows.forEach(function (s) { if (byChannel[s.platform]) byChannel[s.platform].push(s); });

        var inHouse = shows.filter(function (s) { return !packed[s.id]; });
        var totalPkgs = inHouse.reduce(function (a, s) { return a + (s.pkgs || 0); }, 0);
        document.getElementById("totalShows").textContent = inHouse.length;
        document.getElementById("totalPkgs").textContent = totalPkgs.toLocaleString("en-US");
        var grossTotal = CHANNELS.reduce(function (a, c) { return a + (GROSS[c] || 0); }, 0);
        document.getElementById("totalGross").textContent = money(grossTotal);

        var g = json.generatedAt ? new Date(json.generatedAt) : null;
        document.getElementById("lastUpdated").textContent =
          (g && !isNaN(g.getTime())) ? fmtLong(g) + " at " + fmtClock(g) : "—";

        var vis = visibleChannels();
        var grid = document.getElementById("grid");
        grid.style.gridTemplateColumns = "repeat(" + (vis.length >= 9 ? 4 : vis.length >= 5 ? 3 : 2) + ", 1fr)";
        grid.innerHTML = "";
        vis.forEach(function (name) {
          var list = byChannel[name].filter(function (s) { return !packed[s.id]; });
          var allPkgs = byChannel[name].reduce(function (a, s) { return a + (s.pkgs || 0); }, 0);
          var card = document.createElement("div");
          var gross = GROSS[name];
          if (!list.length) {
            card.className = "card done";
            card.innerHTML = "<div class=\"card-head\">" + logoHTML(name) + "<h2>" + name + "</h2></div>" +
              "<div class=\"done-msg\">&#10003; All caught up</div>" +
              "<div class=\"counts\">Nothing in house</div>" +
              (gross ? "<div class=\"gross\">7-day gross <strong>" + money(gross) + "</strong> <span class=\"win\">(" + GROSS_WINDOW + ")</span></div>" : "");
          } else {
            var pkgs = list.reduce(function (a, s) { return a + (s.pkgs || 0); }, 0);
            var oldest = null;
            list.forEach(function (s) {
              var e = s.end ? new Date(s.end) : null;
              if (e && !isNaN(e.getTime()) && (!oldest || e < oldest)) oldest = e;
            });
            var chips = list.map(function (s) {
              var d = s.date ? new Date(s.date + "T12:00:00") : null;
              var label = (d && !isNaN(d.getTime()) ? (d.getMonth() + 1) + "/" + d.getDate() : "Show") +
                (s.part ? " " + s.part : "");
              return "<button class=\"chip\" data-id=\"" + s.id + "\">" + label +
                " &middot; " + s.pkgs + "</button>";
            }).join("");
            card.className = "card hot";
            card.innerHTML = "<div class=\"card-head\">" + logoHTML(name) + "<h2>" + name + "</h2></div>" +
              "<div class=\"chips\">" + chips + "</div>" +
              "<div class=\"counts\"><strong>" + list.length + "</strong> " + (list.length === 1 ? "show" : "shows") +
              " in house &middot; <strong>" + pkgs.toLocaleString("en-US") + "</strong> packages</div>" +
              (gross ? "<div class=\"gross\">7-day gross <strong>" + money(gross) + "</strong> <span class=\"win\">(" + GROSS_WINDOW + ")</span></div>" : "") +
              "<div class=\"oldest\">Oldest: <strong>" + (oldest ? fmtShort(oldest) + " (" + hoursAgo(oldest) + ")" : "—") + "</strong></div>";
          }
          grid.appendChild(card);
        });

        // packed footer
        var packedIds = Object.keys(packed);
        document.getElementById("packedCount").textContent = packedIds.length;
        var pc = document.getElementById("packedChips");
        pc.innerHTML = "";
        packedIds.forEach(function (id) {
          var s = packed[id];
          var b = document.createElement("button");
          b.className = "packed-chip";
          b.textContent = s.platform + " " + (s.part || "") + " — Put back";
          b.setAttribute("data-id", id);
          pc.appendChild(b);
        });
      }

      function applyData(json) {
        shows = (json.shows || []).map(function (raw) {
          return { id: raw.id, platform: raw.platform,
            date: raw.date || "", end: raw.end || "", pkgs: raw.pkgs || 0,
            part: raw.part || "" };
        });
        render(json);
      }

      function load() {
        fetch("show-tracker-data.json", { cache: "no-store" })
          .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
          .then(function (json) {
            document.getElementById("loadError").style.display = "none";
            applyData(json);
          })
          .catch(function () {
            document.getElementById("loadError").style.display = "flex";
          });
      }

      document.getElementById("grid").addEventListener("click", function (e) {
        var chip = e.target.closest(".chip");
        if (!chip) return;
        var id = chip.getAttribute("data-id");
        var s = shows.find(function (x) { return String(x.id) === String(id); });
        if (s) { packed[id] = s; render({ generatedAt: window._genAt }); }
      });
      document.getElementById("packedChips").addEventListener("click", function (e) {
        var chip = e.target.closest(".packed-chip");
        if (!chip) return;
        delete packed[chip.getAttribute("data-id")];
        render({ generatedAt: window._genAt });
      });
      document.getElementById("filters").addEventListener("click", function (e) {
        var btn = e.target.closest(".filter-btn");
        if (!btn) return;
        activeFilter = btn.getAttribute("data-f");
        document.querySelectorAll(".filter-btn").forEach(function (b) {
          b.setAttribute("aria-pressed", String(b === btn));
        });
        render({ generatedAt: window._genAt });
      });

      // keep generatedAt across re-renders
      var _origApply = applyData;
      applyData = function (json) { window._genAt = json.generatedAt; _origApply(json); };

      load();
      setInterval(load, 5 * 60 * 1000);
    </script>
  </body>
</html>
"""

out = (template
       .replace("__GROSS__", gross_js)
       .replace("__EBAY_PATH__", ebay_path)
       .replace("__TIKTOK_PATH__", tiktok_path)
       .replace("__LOGO_FN__", logo_fn.rstrip()))

open(OUT, "w").write(out)
print(f"wrote {OUT} ({len(out)} bytes)")
