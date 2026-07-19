import httpx
import pytest

from astrolabe.public_api.auth import (
    AuthenticationError,
    AuthUnavailableError,
    SupabaseAuthVerifier,
)


def _verifier(handler, *, owner="owner", sleeps=None):
    return SupabaseAuthVerifier(
        "https://project.supabase.co",
        "anon-test-key",
        owner,
        transport=httpx.MockTransport(handler),
        sleeper=(sleeps.append if sleeps is not None else lambda _: None),
    )


@pytest.mark.parametrize(
    ("response", "owner"),
    [
        (lambda request: httpx.Response(401, json={"message": "bad"}, request=request), "owner"),
        (lambda request: httpx.Response(200, json={"id": "other"}, request=request), "owner"),
    ],
)
def test_invalid_token_and_owner_mismatch_are_indistinguishable(response, owner):
    verifier = _verifier(response, owner=owner)
    try:
        with pytest.raises(AuthenticationError) as exc:
            verifier.verify("Bearer token")
    finally:
        verifier.close()
    assert str(exc.value) == "unauthorized"


def test_auth_requires_strict_bearer_without_network_call():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"id": "owner"}, request=request)

    verifier = _verifier(handler)
    try:
        with pytest.raises(AuthenticationError):
            verifier.verify("token")
    finally:
        verifier.close()
    assert calls == 0


def test_auth_retries_one_5xx_then_succeeds():
    calls = 0
    sleeps = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={}, request=request)
        return httpx.Response(200, json={"id": "owner"}, request=request)

    verifier = _verifier(handler, sleeps=sleeps)
    try:
        assert verifier.verify("Bearer token") == "owner"
    finally:
        verifier.close()
    assert calls == 2
    assert sleeps == [0.5]


def test_auth_outage_does_not_expose_supabase_url():
    def handler(request):
        raise httpx.ConnectError("https://project.supabase.co/internal", request=request)

    verifier = _verifier(handler)
    try:
        with pytest.raises(AuthUnavailableError) as exc:
            verifier.verify("Bearer token")
    finally:
        verifier.close()
    assert str(exc.value) == "auth unavailable"
    assert "supabase" not in str(exc.value)
