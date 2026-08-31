"""`pworkers config` — the persistent settings store (passiveworkers/config.py). Isolated to a tmp PW_HOME so it
never touches the real ~/.passiveworkers. Covers roundtrip, 0600 perms, apply_to_env precedence,
secret masking, malformed-key rejection, and corrupt-file tolerance."""
import json
import os

import pytest

import passiveworkers.config as C


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_HOME", str(tmp_path))
    return tmp_path


def test_set_get_unset_roundtrip():
    assert C.get("PW_WEB_BACKEND") is None
    C.set("PW_WEB_BACKEND", "ddgs")
    assert C.get("PW_WEB_BACKEND") == "ddgs"
    assert json.loads(C.path().read_text())["PW_WEB_BACKEND"] == "ddgs"
    assert C.unset("PW_WEB_BACKEND") is True
    assert C.get("PW_WEB_BACKEND") is None
    assert C.unset("PW_WEB_BACKEND") is False        # idempotent


def test_file_is_owner_only_0600():
    C.set("PW_TAVILY_KEY", "tvly-secret-123456")
    mode = C.path().stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)                  # holds API keys — must never be world-readable


def test_apply_to_env_precedence(monkeypatch):
    # config value fills an UNSET var...
    monkeypatch.delenv("PW_WEB_BACKEND", raising=False)
    C.set("PW_WEB_BACKEND", "tavily")
    C.apply_to_env()
    assert os.environ["PW_WEB_BACKEND"] == "tavily"
    monkeypatch.delenv("PW_WEB_BACKEND", raising=False)   # clean the leak apply_to_env just made

    # ...but an explicitly exported env var WINS (setdefault never overwrites it)
    monkeypatch.setenv("PW_WEB_BACKEND", "serper")
    C.apply_to_env()
    assert os.environ["PW_WEB_BACKEND"] == "serper"


def test_apply_to_env_is_crash_safe_on_corrupt_file():
    C.path().parent.mkdir(parents=True, exist_ok=True)
    C.path().write_text("{ this is not: valid json ]")
    assert C.load() == {}          # tolerant: never breaks the CLI
    C.apply_to_env()               # must not raise


def test_list_masks_secrets(capsys):
    C.set("PW_TAVILY_KEY", "tvly-abcdefgh9999")
    C.set("PW_WEB_BACKEND", "ddgs")
    assert C.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "tvly-abcdefgh9999" not in out          # raw secret never printed
    assert "9999" in out and "••••" in out          # masked (last-4) shown
    assert "ddgs" in out                            # non-secret shown in clear


def test_get_secret_is_masked(capsys):
    C.set("OPENROUTER_API_KEY", "sk-or-verylongsecret")
    assert C.main(["get", "OPENROUTER_API_KEY"]) == 0
    out = capsys.readouterr().out
    assert "verylongsecret" not in out and "cret" in out


def test_malformed_key_rejected(capsys):
    assert C.main(["set", "pw_web_backend", "ddgs"]) == 2   # lowercase → rejected
    out = capsys.readouterr().out
    assert "invalid key" in out.lower() and "PW_WEB_BACKEND" in out   # did-you-mean hint
    assert C.get("pw_web_backend") is None                  # nothing persisted


def test_wellformed_custom_key_accepted_with_notice(capsys):
    assert C.main(["set", "PW_CUSTOM_OPS_VAR", "1"]) == 0    # not in KNOWN but well-formed → allowed
    out = capsys.readouterr().out
    assert "not a recognized setting" in out.lower()
    assert C.get("PW_CUSTOM_OPS_VAR") == "1"


def test_mask_hides_short_values_fully():
    # a "mask" must never disclose a short secret (regression: last-4 on a 5-char value showed 4/5)
    assert C.mask("abcd") == "••••"                 # 4 chars → fully hidden
    assert C.mask("short99") == "••••"              # 7 chars → fully hidden
    assert C.mask("sk-or-verylongkey1234")[-4:] == "1234"   # long key → last-4 tail survives
    assert "verylong" not in C.mask("sk-or-verylongkey1234")


def test_display_strips_url_basic_auth_credentials(capsys):
    # a non-secret URL field must not print inline user:pass@ credentials in clear (regression)
    C.set("PW_SEARXNG_URL", "https://user:s3cr3tpass@searx.internal:8080/")
    assert C.main(["get", "PW_SEARXNG_URL"]) == 0
    out = capsys.readouterr().out
    assert "s3cr3tpass" not in out and "user:" not in out
    assert "••••@searx.internal" in out             # scheme + host still visible
    assert C.main(["list"]) == 0
    assert "s3cr3tpass" not in capsys.readouterr().out


def test_secret_detection_by_name():
    assert C.is_secret("PW_BRAVE_KEY") and C.is_secret("PW_TAVILY_KEY")
    assert C.is_secret("SOME_UNKNOWN_TOKEN")     # heuristic: *_TOKEN masked even if unknown
    assert not C.is_secret("PW_WEB_BACKEND")


def test_set_value_tolerates_spaces():
    C.set("PW_EDITOR_MODEL", "openai/gpt-5-chat")
    assert C.main(["set", "PW_COUNTRY", "United", "Arab", "Emirates"]) == 0
    assert C.get("PW_COUNTRY") == "United Arab Emirates"


def test_path_command(capsys):
    assert C.main(["path"]) == 0
    assert "config.json" in capsys.readouterr().out


def test_known_includes_new_review_round_knobs(capsys):
    # R12/R15/R17 review: these must be discoverable via `pworkers config list`, not env-var-only.
    for key in ("PW_CONTEXTUAL_CHUNKS", "PW_PAGE_EVIDENCE", "PW_DDG_BREAKER"):
        assert key in C.KNOWN
    assert C.main(["list"]) == 0
    out = capsys.readouterr().out
    for key in ("PW_CONTEXTUAL_CHUNKS", "PW_PAGE_EVIDENCE", "PW_DDG_BREAKER"):
        assert key in out
