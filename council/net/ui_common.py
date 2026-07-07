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
