"""
This file should be for internal APIs which Open Library requires for
its experience. This does not include public facing APIs with LTS
(long term support)

# Will include code from openlibrary.plugins.openlibrary.api
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal
from urllib.parse import urljoin

import web
from fastapi import APIRouter, Depends, Form, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, BeforeValidator, Field

from openlibrary.core import helpers as h
from openlibrary.core import lending, models
from openlibrary.core.models import Booknotes
from openlibrary.fastapi.auth import (
    AuthenticatedUser,
    get_authenticated_user,
    require_authenticated_user,
)
from openlibrary.fastapi.models import (
    Pagination,
    parse_comma_separated_list,
)
from openlibrary.plugins.openlibrary.api import ratings as legacy_ratings
from openlibrary.utils import extract_numeric_id_from_olid
from openlibrary.utils.request_context import site as site_ctx
from openlibrary.views.loanstats import SINCE_DAYS, get_trending_books

SHOW_INTERNAL_IN_SCHEMA = os.getenv("LOCAL_DEV") is not None
router = APIRouter(tags=["internal"], include_in_schema=SHOW_INTERNAL_IN_SCHEMA)

# Valid period values — mirrors SINCE_DAYS keys
# IMPORTANT: Keep this Literal in sync with the keys of views.loanstats.SINCE_DAYS!
# This guarantees that our path parameter validated by FastAPI is always
# a safe dictionary key when we pass it to the legacy backend function.
TrendingPeriod = Literal["now", "daily", "weekly", "monthly", "yearly", "forever"]


@router.get("/availability/v2")
async def book_availability():
    pass


class TrendingRequestParams(Pagination):
    limit: int = Field(100, ge=0, le=1000, description="Maximum number of results per page.")
    hours: int = Field(0, ge=0, description="Custom number of hours to look back.")
    days: int = Field(0, ge=0, description="Custom number of days to look back.")
    sort_by_count: bool = Field(True, description="Sort results by total log count (most-logged first).")
    minimum: int = Field(0, ge=0, description="Minimum log count a book must have to be included.")
    fields: Annotated[list[str] | None, BeforeValidator(parse_comma_separated_list)] = Field(
        None,
        description="Comma-separated list of Solr fields to include in each work.",
    )


class SolrWork(BaseModel):
    key: str
    title: str | None = None
    author_name: list[str] | None = None

    model_config = {"extra": "allow"}


class TrendingResponse(BaseModel):
    query: str = Field(..., description="Echo of the request path, e.g. /trending/daily")
    works: list[SolrWork] = Field(..., description="Trending work documents from Solr")
    days: int | None = Field(default=None, description="Look-back window in days (None = all-time)")
    hours: int = Field(..., description="Look-back window in hours")


@router.get(
    "/trending/{period}.json",
    response_model=TrendingResponse,
    response_model_exclude_none=True,
    description="Returns works sorted by recent activity (reads, loans, etc.)",
)
def trending_books_api(
    period: Annotated[TrendingPeriod, Path(description="The time period for trending books")],
    params: Annotated[TrendingRequestParams, Query()],
) -> TrendingResponse:
    """Fetch trending books for the given period."""
    # ``period`` is always a key in SINCE_DAYS — guaranteed by the Literal type above.
    since_days: int | None = SINCE_DAYS.get(period, params.days)

    # Setting web.ctx.site is an ANTIPATTERN and we should avoid it elsewhere.
    # It will be removed via #12178
    web.ctx.site = site_ctx.get()

    works = get_trending_books(
        since_days=since_days,
        since_hours=params.hours,
        limit=params.limit,
        page=params.page,
        sort_by_count=params.sort_by_count,
        minimum=params.minimum,
        fields=params.fields,
    )

    return TrendingResponse(
        query=f"/trending/{period}",
        works=[SolrWork(**dict(work)) for work in works],
        days=since_days,
        hours=params.hours,
    )


@router.get("/browse.json")
async def browse(
    pagination: Annotated[Pagination, Depends()],
    q: Annotated[str, Query()] = "",
    subject: Annotated[str, Query()] = "",
    sorts: Annotated[str, Query()] = "",
) -> dict:
    """
    Dynamically fetches the next page of books and checks if they are
    available to be borrowed from the Internet Archive without having
    to reload the whole web page.
    """
    sorts_list = [s.strip() for s in sorts.split(",") if s.strip()]

    url = lending.compose_ia_url(
        query=q,
        limit=pagination.limit,
        page=pagination.page,
        subject=subject,
        sorts=sorts_list,
    )

    works = lending.get_available(url=url) if url else []
    return {"query": url, "works": [work.dict() for work in works]}


async def _get_rating_post_data(request: Request) -> dict[str, Any]:
    data: dict[str, Any] = dict(request.query_params)
    content_type = request.headers.get("content-type", "").split(";", 1)[0]

    if content_type == "application/json":
        body_data = await request.json()
        if isinstance(body_data, dict):
            data.update(body_data)
        return data

    form_data = await request.form()
    data.update(dict(form_data))
    return data


def _get_rating_redirect_key(work_id: int, edition_id: str | None, redir_url: str | None) -> str:
    return redir_url or edition_id or f"/works/OL{work_id}W"


def _get_absolute_redirect_url(request: Request, path: str) -> str:
    return urljoin(str(request.base_url), path)


def _build_rating_redirect_response(request: Request, key: str, page: Any) -> RedirectResponse:
    if page:
        redirect_page = h.safeint(page, 1)
        query_params = f"?page={redirect_page}" if redirect_page > 1 else ""
        return RedirectResponse(_get_absolute_redirect_url(request, f"{key}{query_params}"), status_code=303)

    return RedirectResponse(_get_absolute_redirect_url(request, key), status_code=303)


@router.get("/works/OL{work_id}W/ratings.json", tags=["internal"], include_in_schema=SHOW_INTERNAL_IN_SCHEMA)
async def get_ratings(work_id: Annotated[int, Path()]) -> dict:
    """Get ratings summary for a work."""
    return legacy_ratings.get_ratings_summary(work_id)


@router.post("/works/OL{work_id}W/ratings", tags=["internal"], include_in_schema=SHOW_INTERNAL_IN_SCHEMA, response_model=None)
@router.post("/works/OL{work_id}W/ratings.json", tags=["internal"], include_in_schema=SHOW_INTERNAL_IN_SCHEMA, response_model=None)
async def post_ratings(
    request: Request,
    work_id: Annotated[int, Path()],
    user: Annotated[AuthenticatedUser | None, Depends(get_authenticated_user)],
) -> Any:
    """Register or remove a rating for a work.

    If rating is None, the existing rating is removed.
    If rating is provided, it must be in the valid range (1-5).
    """
    data = await _get_rating_post_data(request)
    key = _get_rating_redirect_key(work_id, data.get("edition_id"), data.get("redir_url"))

    if not user:
        return RedirectResponse(_get_absolute_redirect_url(request, f"/account/login?redirect={key}"), status_code=303)

    edition_id_int = int(extract_numeric_id_from_olid(data["edition_id"])) if data.get("edition_id") else None

    if data.get("rating") is None:
        models.Ratings.remove(user.username, work_id)
        response: dict[str, str] = {"success": "removed rating"}
    else:
        try:
            rating = int(data["rating"])
            if rating not in models.Ratings.VALID_STAR_RATINGS:
                raise ValueError
        except (TypeError, ValueError):
            return {"error": "invalid rating"}

        models.Ratings.add(
            username=user.username,
            work_id=work_id,
            rating=rating,
            edition_id=edition_id_int,
        )
        response = {"success": "rating added"}

    if data.get("redir") and not data.get("ajax"):
        return _build_rating_redirect_response(request, key, data.get("page"))

    return response


class BooknoteResponse(BaseModel):
    success: str = Field(..., description="Status message")


@router.post("/works/OL{work_id}W/notes", response_model=BooknoteResponse)
async def booknotes_post(
    work_id: Annotated[int, Path(gt=0)],
    user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    notes: Annotated[str | None, Form()] = None,
    edition_id: Annotated[str | None, Form(pattern=r"(?i)^OL\d+M$")] = None,
) -> BooknoteResponse:
    """
    Add or remove a note for a work (and optionally a specific edition).

    - If `notes` is provided: create or update the note.
    - If `notes` is omitted: remove the existing note.
    """
    resolved_edition_id = Booknotes.NULL_EDITION_VALUE
    if edition_id:
        resolved_edition_id = int(extract_numeric_id_from_olid(edition_id))

    if not notes:
        Booknotes.remove(user.username, work_id, edition_id=resolved_edition_id)
        return BooknoteResponse(success="removed note")

    Booknotes.add(
        username=user.username,
        work_id=work_id,
        notes=notes,
        edition_id=resolved_edition_id,
    )
    return BooknoteResponse(success="note added")


async def work_bookshelves():
    pass


async def work_editions():
    pass


async def author_works():
    pass


async def price_api():
    pass


async def patrons_follows_json():
    pass


async def patrons_observations():
    pass


async def public_observations():
    pass


async def bestbook_award():
    pass


async def bestbook_count():
    pass


async def unlink_ia_ol():
    pass


async def monthly_logins():
    pass
