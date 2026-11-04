import httpx
import pytest

from moodle_academic_mcp.errors import (
    FunctionUnavailableError,
    NetworkRestrictionError,
    PermissionDeniedError,
    TokenInvalidError,
    WriteOutcomeUnknownError,
)
from moodle_academic_mcp.moodle.client import Endpoint, MoodleClient
from moodle_academic_mcp.moodle.params import flatten
from tests.fake_moodle import parse_php

EP = Endpoint("http://fake", allow_insecure_http=True, allow_private_networks=True)


def test_flatten_nested_lists_and_bools():
    pairs = flatten(
        {"courseids": [1, 2], "data": [{"name": "a", "value": "x"}], "o": {"k": True}, "skip": None}
    )
    assert pairs == [
        ("courseids[0]", "1"),
        ("courseids[1]", "2"),
        ("data[0][name]", "a"),
        ("data[0][value]", "x"),
        ("o[k]", "1"),
    ]


def test_roundtrip_through_independent_php_parser():
    params = {
        "plugindata": {"onlinetext_editor": {"text": "hi", "format": 1, "itemid": 0}, "files_filemanager": 9},
        "ids": [5, 6],
    }
    parsed = parse_php(flatten(params))
    assert parsed["plugindata"]["onlinetext_editor"]["text"] == "hi"
    assert parsed["plugindata"]["files_filemanager"] == "9"
    assert parsed["ids"] == ["5", "6"]


async def test_success_and_error_in_http_200(http):
    c = MoodleClient(EP, "tokA", http=http)
    info = await c.call("core_webservice_get_site_info")
    assert info["userid"] == 101
    with pytest.raises(TokenInvalidError):
        await MoodleClient(EP, "nope", http=http).call("core_webservice_get_site_info")


async def test_revoked_token(http, fake_state):
    fake_state.revoked.add("tokA")
    with pytest.raises(TokenInvalidError):
        await MoodleClient(EP, "tokA", http=http).call("core_webservice_get_site_info")


async def test_function_missing_vs_permission(http):
    att = MoodleClient(EP, "tokAtt", "attendance", http=http)
    with pytest.raises(FunctionUnavailableError):  # debug info says the function is not in the service
        await att.call("core_webservice_get_site_info")
    mobile = MoodleClient(EP, "tokA", http=http)
    with pytest.raises(PermissionDeniedError):  # generic access exception without detail -> permission
        await mobile.call("mod_attendance_get_sessions", {"attendanceid": 1})
    with pytest.raises(FunctionUnavailableError):  # function unknown to Moodle
        await mobile.call("mod_nonexistent_fn")


async def test_html_instead_of_json_is_network_restriction(http, fake_state):
    fake_state.html_mode = True
    with pytest.raises(NetworkRestrictionError):
        await MoodleClient(EP, "tokA", http=http, read_retries=1).call("core_webservice_get_site_info")


async def test_redirect_is_reported(fake):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://sso.example/login?token=SECRET"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as h:
        with pytest.raises(NetworkRestrictionError) as e:
            await MoodleClient(EP, "tokA", http=h, read_retries=1).call("x")
    assert "SECRET" not in str(e.value.to_dict())


async def test_read_retries_on_5xx_but_write_does_not():
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as h:
        c = MoodleClient(EP, "tokA", http=h, read_retries=3)
        with pytest.raises(Exception):
            await c.call("core_webservice_get_site_info")
        assert n["c"] == 3
        n["c"] = 0
        with pytest.raises(Exception):
            await c.call("mod_assign_save_submission", write=True)
        assert n["c"] == 1


async def test_write_timeout_is_not_retried_and_flagged(fake, fake_state):
    from tests.conftest import LoseResponseOnce

    t = LoseResponseOnce(httpx.ASGITransport(app=fake[0]), "mod_forum_add_discussion")
    async with httpx.AsyncClient(transport=t, base_url="http://fake") as h:
        c = MoodleClient(EP, "tokA", http=h)
        with pytest.raises(WriteOutcomeUnknownError):
            await c.call(
                "mod_forum_add_discussion", {"forumid": 7, "subject": "s", "message": "m"}, write=True
            )
    # the server applied it exactly once and the client did not resend
    assert len(fake_state.posts) == 1


async def test_token_goes_in_body_never_url(http, fake_state):
    seen = {}
    orig = http.post

    async def spy(url, **kw):
        seen["url"] = str(url)
        return await orig(url, **kw)

    http.post = spy
    await MoodleClient(EP, "tokA", http=http).call("core_webservice_get_site_info")
    assert "tokA" not in seen["url"]
