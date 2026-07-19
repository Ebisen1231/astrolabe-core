import httpx
import pytest

from astrolabe.anon_guard import PROTECTED_TABLES, AnonGuardError, verify_anon_denied


def test_all_protected_tables_must_return_401_or_403():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(403, json={"message": "denied"}, request=request)

    statuses = verify_anon_denied(
        "https://project.supabase.co",
        "anon-key",
        transport=httpx.MockTransport(handler),
    )
    assert statuses == {table: 403 for table in PROTECTED_TABLES}
    assert paths == [f"/rest/v1/{table}" for table in PROTECTED_TABLES]


def test_even_empty_200_response_is_treated_as_exposure():
    def handler(request):
        return httpx.Response(200, json=[], request=request)

    with pytest.raises(AnonGuardError, match="table=events status=200"):
        verify_anon_denied(
            "https://project.supabase.co",
            "anon-key",
            transport=httpx.MockTransport(handler),
        )
