import base64

from backend.auth import basic_auth_valid, credentials_configured


def _header(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_valid_credentials():
    assert basic_auth_valid(_header("u", "p"), "u", "p")


def test_wrong_user_or_password():
    assert not basic_auth_valid(_header("x", "p"), "u", "p")
    assert not basic_auth_valid(_header("u", "x"), "u", "p")


def test_unset_credentials_never_match():
    # An empty expected value must not be satisfiable, even by an empty-credentials header.
    assert not basic_auth_valid(_header("", ""), "", "")
    assert not basic_auth_valid(_header("u", "p"), "u", "")
    assert not credentials_configured("u", "")
    assert not credentials_configured("", "p")
    assert credentials_configured("u", "p")


def test_malformed_headers():
    assert not basic_auth_valid("", "u", "p")
    assert not basic_auth_valid("Bearer abc", "u", "p")
    assert not basic_auth_valid("Basic !!!notbase64", "u", "p")
    assert not basic_auth_valid(
        "Basic " + base64.b64encode(b"\xff\xfe:").decode(), "u", "p"
    )


def test_non_ascii_password():
    assert basic_auth_valid(_header("u", "pä§"), "u", "pä§")
