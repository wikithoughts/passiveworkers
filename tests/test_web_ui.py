"""R36/D52 — smoke tests for the two coordinator-served web UIs (marketplace map + operator
dashboard), which had no coverage. They pin the render AND the `/*__…__*/` substitution contract that
the shared-CSS consolidation (passiveworkers/net/ui_common.py) leans on — a broken placeholder replace would
now fail a test instead of silently shipping. No Ollama."""
import pytest


@pytest.mark.parametrize("route", ["/", "/dashboard"])
def test_web_ui_renders_and_substitutes(coord_client, route):
    r = coord_client.get(route)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Passive Workers" in body
    assert "__PW_VERSION__" not in body      # version token substituted
    assert "/*__" not in body                # every JS-comment placeholder (ESC/CENTROIDS/THEME/PWC/MAP_TILE) replaced
    assert "<!--__" not in body              # every HTML-comment placeholder (FOOTER/LEAFLET_CSS/LEAFLET_JS) replaced
    assert '<footer class="pwfoot"' in body  # the shared footer actually rendered (not a no-op'd placeholder)


def test_serve_desk_substitutes():
    # the research desk (a separate app) shares the same THEME/FOOTER/ESC substitution — pin it too
    import passiveworkers.serve as serve
    body = serve.SERVE_HTML.replace("__PW_VERSION__", "x")
    assert "/*__" not in body and "<!--__" not in body
    assert ":root{" in body and '<footer class="pwfoot"' in body
