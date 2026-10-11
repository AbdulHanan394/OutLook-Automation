from types import SimpleNamespace

from app.auth.microsoft import exchange_code, profile_email


def test_profile_email_uses_graph_when_id_token_omits_email():
    assert profile_email(
        {"oid": "account-id"},
        {"mail": None, "userPrincipalName": "person@example.com"},
    ) == "person@example.com"


def test_profile_email_rejects_non_email_principal_name():
    assert profile_email({}, {"userPrincipalName": "live.com#person"}) is None


def test_code_exchange_does_not_resubmit_scopes(monkeypatch):
    captured = {}

    class Response:
        is_error = False

        @staticmethod
        def json():
            return {"access_token": "token"}

    def post(url, data, timeout):
        captured.update(data)
        return Response()

    monkeypatch.setattr("httpx.post", post)
    settings = SimpleNamespace(
        ms_client_id="client-id",
        ms_client_secret=SimpleNamespace(get_secret_value=lambda: "client-secret"),
        ms_tenant="consumers",
    )

    exchange_code(settings, "code", "verifier", ["openid", "https://graph.microsoft.com/Mail.Read"], "http://callback")

    assert "scope" not in captured
