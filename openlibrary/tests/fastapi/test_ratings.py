from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openlibrary.fastapi.auth import AuthenticatedUser, get_authenticated_user


@pytest.fixture
def fastapi_client(monkeypatch):
    monkeypatch.setattr("openlibrary.asgi_app.set_context_from_fastapi", lambda request: None)

    from openlibrary.asgi_app import create_app

    app = create_app()
    return TestClient(app)


@pytest.fixture
def authenticated_client(fastapi_client):
    async def authenticated_user():
        return AuthenticatedUser(
            username="test-user",
            user_key="/people/test-user",
            timestamp="2026-03-24T00:00:00",
        )

    fastapi_client.app.dependency_overrides[get_authenticated_user] = authenticated_user
    yield fastapi_client
    fastapi_client.app.dependency_overrides.clear()


class TestRatingsEndpoint:
    def test_get_ratings_returns_legacy_summary(self, fastapi_client):
        expected = {
            "summary": {
                "average": 4.5,
                "count": 2,
                "sortable": 4.2,
            },
            "counts": {
                "1": 0,
                "2": 0,
                "3": 0,
                "4": 1,
                "5": 1,
            },
        }

        with patch("openlibrary.fastapi.internal.api.legacy_ratings.get_ratings_summary", return_value=expected) as mock_summary:
            response = fastapi_client.get("/works/OL123W/ratings.json")

        assert response.status_code == 200
        assert response.json() == expected
        mock_summary.assert_called_once_with(123)

    def test_get_ratings_does_not_inject_sortable_for_empty_summary(self, fastapi_client):
        expected = {
            "summary": {
                "average": None,
                "count": 0,
            },
            "counts": {
                "1": 0,
                "2": 0,
                "3": 0,
                "4": 0,
                "5": 0,
            },
        }

        with patch("openlibrary.fastapi.internal.api.legacy_ratings.get_ratings_summary", return_value=expected):
            response = fastapi_client.get("/works/OL123W/ratings.json")

        assert response.status_code == 200
        assert response.json() == expected

    def test_post_ratings_accepts_legacy_form_data(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post(
                "/works/OL123W/ratings.json",
                data={"rating": "5", "edition_id": "/books/OL42M"},
            )

        assert response.status_code == 200
        assert response.json() == {"success": "rating added"}
        mock_add.assert_called_once_with(
            username="test-user",
            work_id=123,
            rating=5,
            edition_id=42,
        )

    def test_post_ratings_accepts_json_payloads(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post(
                "/works/OL123W/ratings",
                json={"rating": 4, "edition_id": "OL7M"},
            )

        assert response.status_code == 200
        assert response.json() == {"success": "rating added"}
        mock_add.assert_called_once_with(
            username="test-user",
            work_id=123,
            rating=4,
            edition_id=7,
        )

    def test_post_ratings_removes_rating_when_rating_is_missing(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.remove") as mock_remove:
            response = authenticated_client.post("/works/OL123W/ratings", data={})

        assert response.status_code == 200
        assert response.json() == {"success": "removed rating"}
        mock_remove.assert_called_once_with("test-user", 123)

    def test_post_ratings_returns_legacy_error_for_invalid_rating(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post("/works/OL123W/ratings", data={"rating": "10"})

        assert response.status_code == 200
        assert response.json() == {"error": "invalid rating"}
        mock_add.assert_not_called()

    def test_post_ratings_redirects_unauthenticated_users_to_login(self, fastapi_client):
        response = fastapi_client.post(
            "/works/OL123W/ratings",
            data={"rating": "4", "redir_url": "/account/books/already-read"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "http://testserver/account/login?redirect=/account/books/already-read"

    def test_post_ratings_redirects_to_redir_url_with_page(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post(
                "/works/OL123W/ratings",
                data={
                    "rating": "4",
                    "redir": "true",
                    "redir_url": "/account/books/already-read",
                    "page": "2",
                },
                follow_redirects=False,
            )

        assert response.status_code == 303
        assert response.headers["location"] == "http://testserver/account/books/already-read?page=2"
        mock_add.assert_called_once()

    def test_post_ratings_honors_query_string_redirect_options(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post(
                "/works/OL123W/ratings?redir=true&redir_url=/account/books/already-read&page=2",
                data={"rating": "4"},
                follow_redirects=False,
            )

        assert response.status_code == 303
        assert response.headers["location"] == "http://testserver/account/books/already-read?page=2"
        mock_add.assert_called_once()

    def test_post_ratings_redirects_after_removing_a_rating(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.remove") as mock_remove:
            response = authenticated_client.post(
                "/works/OL123W/ratings",
                data={
                    "redir": "true",
                    "redir_url": "/account/books/already-read",
                },
                follow_redirects=False,
            )

        assert response.status_code == 303
        assert response.headers["location"] == "http://testserver/account/books/already-read"
        mock_remove.assert_called_once_with("test-user", 123)

    def test_post_ratings_ajax_suppresses_redirect(self, authenticated_client):
        with patch("openlibrary.fastapi.internal.api.models.Ratings.add") as mock_add:
            response = authenticated_client.post(
                "/works/OL123W/ratings",
                data={
                    "rating": "4",
                    "redir": "true",
                    "ajax": "true",
                    "redir_url": "/account/books/already-read",
                    "page": "2",
                },
            )

        assert response.status_code == 200
        assert response.json() == {"success": "rating added"}
        mock_add.assert_called_once()
