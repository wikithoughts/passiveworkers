#!/usr/bin/env python3
"""
council/net/ui_common.py — UI fragments shared by the three web surfaces
=======================================================================
The research desk (``council.serve``), the marketplace app (``council.net.app``), and the operator
dashboard (``council.net.dashboard``) each embed a self-contained HTML page. A few pieces were
copy-pasted across them and had started to drift; this module is the ONE definition, substituted into
each template at import time via a ``/*__NAME__*/`` placeholder. The placeholder is a JS comment, so
the raw template still parses even if a substitution were ever missed.
"""

from __future__ import annotations

# HTML-escape helper — was byte-identical (modulo a trailing ``;``) in all three surfaces.
ESC_JS = (
    '''function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,'''
    '''c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}'''
)

# World-map country centroids (marketplace app + dashboard). The 30-country table was duplicated, and
# the two copies disagreed on the fallback keys — ``'local'``/``'?'`` existed only in the dashboard,
# so an unmapped or ``local`` node landed at a DIFFERENT spot on each map. ONE table now, fallback
# keys included, so both maps resolve an unknown node the same way (``CENTROIDS['?']``).
CENTROIDS_JS = (
    "const CENTROIDS={FI:[61.9,25.7],AE:[23.4,53.8],US:[39.8,-98.6],DE:[51.2,10.4],"
    "BR:[-14.2,-51.9],GB:[54,-2],FR:[46.6,2.2],IN:[21,78],SG:[1.35,103.8],JP:[36.2,138.3],"
    "NL:[52.1,5.3],CA:[56,-106],AU:[-25,133],ZA:[-29,24],NG:[9,8],KE:[0.2,37.9],EG:[26,30],"
    "SA:[24,45],IQ:[33,44],TR:[39,35],RU:[61,105],CN:[35,105],KR:[36,128],ID:[-2,118],"
    "VN:[14,108],MX:[23,-102],ES:[40,-3.7],IT:[42.8,12.8],SE:[62,15],PL:[52,19],AR:[-38,-63],"
    "'local':[20,0],'?':[0,-20]};"
)

# ---- ONE dark theme for all three surfaces (R36/D52) ---------------------------------------------
# The `:root` palette had drifted: three different `--bg`/`--ink`/`--card` values and a `--muted`
# vs `--mut` name split, so the surfaces slowly diverged. This is the single canonical palette,
# substituted via a `/*__THEME__*/` placeholder. It carries the UNION of every token any surface
# references (so each page's existing CSS still resolves), keeps BOTH `--mut` and `--muted` as
# aliases of the one muted grey (no per-surface var() rewrites), and folds in the shared `.pwfoot`
# footer rule that was also copy-pasted three times.
THEME_CSS = (
    ":root{--bg:#0b1020;--ink:#e6ecff;--mut:#a7b6e0;--muted:#a7b6e0;--edge:#21305e;--card:#0f1730;"
    "--cardin:#0c1430;--panel:#121a33;--acc:#6ea8ff;--bad:#f87272;--good:#36d399;--warn:#fbbd23;"
    "--btn:#2447b2;--btn-edge:#2e57d6}\n"
    ".pwfoot{margin-top:18px;padding-top:12px;border-top:1px solid var(--edge);color:var(--mut);"
    "font-size:11.5px}.pwfoot a{color:var(--acc)}"
)

# The version-stamped footer, byte-identical across the three pages (`__PW_VERSION__` is substituted
# per request by each surface's route). Placeholder: an HTML comment so the raw template still parses.
FOOTER_HTML = (
    '<footer class="pwfoot" aria-label="About">Passive&nbsp;Workers v__PW_VERSION__ · '
    '<a href="https://github.com/wikithoughts/passiveworkers" target="_blank" rel="noopener">GitHub</a>'
    "</footer>"
)

# Leaflet + CARTO dark-tile bootstrap, shared by the two map surfaces (app + dashboard). The tile
# layer's attribution had drifted ('© OSM' vs '© OpenStreetMap'); one canonical form now. MAP_TILE_JS
# is the `L.tileLayer(...)` expression — a surface appends `.addTo(map)`.
LEAFLET_CSS = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>'
LEAFLET_JS = '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
MAP_TILE_JS = (
    "L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',"
    "{maxZoom:8,attribution:'© OpenStreetMap © CARTO'})"
)

# Semantic status colors, previously repeated as raw hex literals in the map/dot code of both surfaces
# (they can't consume CSS var()s — they color Leaflet markers via JS strings). ONE object now.
STATUS_COLORS_JS = "const PWC={good:'#36d399',warn:'#fbbd23',bad:'#f87272',acc:'#6ea8ff'};"
