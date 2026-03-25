"""Tests for the FastAPI ratings endpoints."""

from unittest.mock import patch

import pytest


@pytest.fixture
def mock_ratings_model():
    """Prevent real DB calls for Ratings methods."""
    with (
        patch("openlibrary.fastapi.internal.api.models.Ratings.add", autospec=True) as add_mock,
        patch("openlibrary.fastapi.internal.api.models.Ratings.remove", autospec=True) as remove_mock,
    ):
        yield add_mock, remove_mock


class TestRatingsEndpoints:
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

    def test_post_ratings_adds_rating(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, remove_mock = mock_ratings_model
        response = fastapi_client.post(
            "/works/OL123W/ratings.json",
            data={"rating": "5", "edition_id": "/books/OL42M"},
        )

        assert response.status_code == 200
        assert response.json() == {"success": "rating added"}
        add_mock.assert_called_once_with(
            username="testuser",
            work_id=123,
            rating=5,
            edition_id=42,
        )
        remove_mock.assert_not_called()

    def test_post_ratings_removes_rating_when_rating_is_missing(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, remove_mock = mock_ratings_model
        response = fastapi_client.post("/works/OL123W/ratings", data={})

        assert response.status_code == 200
        assert response.json() == {"success": "removed rating"}
        remove_mock.assert_called_once_with("testuser", 123)
        add_mock.assert_not_called()

    def test_post_ratings_requires_authentication(self, fastapi_client):
        response = fastapi_client.post("/works/OL123W/ratings", data={"rating": "4"})

        assert response.status_code == 401

    def test_post_ratings_invalid_rating_returns_422(self, fastapi_client, mock_authenticated_user):
        response = fastapi_client.post("/works/OL123W/ratings", data={"rating": "10"})

        assert response.status_code == 422

    def test_post_ratings_redirects_to_redir_url_with_page(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, _ = mock_ratings_model
        response = fastapi_client.post(
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
        assert response.headers["location"] == "/account/books/already-read?page=2"
        add_mock.assert_called_once()

    def test_post_ratings_honors_query_string_redirect_options(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, _ = mock_ratings_model
        response = fastapi_client.post(
            "/works/OL123W/ratings?redir=true&redir_url=/account/books/already-read&page=2",
            data={"rating": "4"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/account/books/already-read?page=2"
        add_mock.assert_called_once()

    def test_post_ratings_accepts_query_string_rating_and_edition_id(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, remove_mock = mock_ratings_model
        response = fastapi_client.post("/works/OL123W/ratings?rating=4&edition_id=OL7M", data={})

        assert response.status_code == 200
        assert response.json() == {"success": "rating added"}
        add_mock.assert_called_once_with(
            username="testuser",
            work_id=123,
            rating=4,
            edition_id=7,
        )
        remove_mock.assert_not_called()

    def test_post_ratings_uses_legacy_page_coercion_for_query_string_redirects(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, _ = mock_ratings_model
        response = fastapi_client.post(
            "/works/OL123W/ratings?redir=true&redir_url=/account/books/already-read&page=not-a-number",
            data={"rating": "4"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/account/books/already-read"
        add_mock.assert_called_once()

    def test_post_ratings_redirects_after_removing_a_rating(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, remove_mock = mock_ratings_model
        response = fastapi_client.post(
            "/works/OL123W/ratings",
            data={
                "redir": "true",
                "redir_url": "/account/books/already-read",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/account/books/already-read"
        remove_mock.assert_called_once_with("testuser", 123)
        add_mock.assert_not_called()

    def test_post_ratings_ajax_suppresses_redirect(self, fastapi_client, mock_authenticated_user, mock_ratings_model):
        add_mock, _ = mock_ratings_model
        response = fastapi_client.post(
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
        add_mock.assert_called_once()
