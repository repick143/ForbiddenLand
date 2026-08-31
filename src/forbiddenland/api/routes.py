"""Thin HTTP routes; business logic stays in application services."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ..application.analysis_history_service import AnalysisHistoryService
from ..application.market_service import MarketDataNotFound, MarketDataProviderError
from ..domain.market import Adjustment, AssetType, MarketQuery
from ..infrastructure.analysis_history import AnalysisHistoryDataError, AnalysisHistoryNotFound
from .schemas import (
    AnalysisHistoryListResponse,
    AnalysisHistorySummaryResponse,
    AnalysisRecordResponse,
    HealthResponse,
    MarketAssetListResponse,
    MarketAssetResponse,
    MarketBarsResponse,
    SecurityListResponse,
    SecurityResponse,
)

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    service = request.app.state.market_data_service
    return HealthResponse(
        status="ok",
        service="forbiddenland-api",
        version=request.app.version,
        backend=service.backend,
        data_policy=(
            "remote provider by default; local snapshots require explicit review and approval"
        ),
    )


@router.get("/market/securities", response_model=SecurityListResponse, tags=["market"])
def securities(request: Request) -> SecurityListResponse:
    service = request.app.state.market_data_service
    return SecurityListResponse(
        items=[SecurityResponse.from_domain(item) for item in service.list_securities()]
    )


@router.get("/market/assets", response_model=MarketAssetListResponse, tags=["market"])
def assets(
    request: Request,
    asset_type: AssetType = "stock",
    query: Annotated[str, Query(max_length=64)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MarketAssetListResponse:
    service = request.app.state.market_data_service
    try:
        items = service.search_assets(asset_type, query=query, limit=limit)
    except MarketDataProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return MarketAssetListResponse(items=[MarketAssetResponse.from_domain(item) for item in items])


@router.get("/market/bars", response_model=MarketBarsResponse, tags=["market"])
def market_bars(
    request: Request,
    symbol: Annotated[str, Query(min_length=1, max_length=64)],
    start_date: date,
    end_date: date,
    asset_type: AssetType = "stock",
    adjust: Adjustment = "",
) -> MarketBarsResponse:
    try:
        query = MarketQuery(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            asset_type=asset_type,
            adjust=adjust,
        )
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    try:
        result = request.app.state.market_data_service.get_history(query)
    except MarketDataNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MarketDataProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return MarketBarsResponse.from_domain(result)


@router.get(
    "/analysis/history",
    response_model=AnalysisHistoryListResponse,
    tags=["analysis"],
)
def analysis_history(
    request: Request,
    query: Annotated[str, Query(max_length=120)] = "",
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AnalysisHistoryListResponse:
    service: AnalysisHistoryService = request.app.state.analysis_history_service
    try:
        result = service.list_history(
            query=query,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except AnalysisHistoryDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return AnalysisHistoryListResponse(
        items=[AnalysisHistorySummaryResponse.from_domain(item) for item in result.items],
        total=result.total,
        warnings=list(result.warnings),
    )


@router.get(
    "/analysis/history/{symbol}/{analysis_date}",
    response_model=AnalysisRecordResponse,
    tags=["analysis"],
)
def analysis_history_detail(
    request: Request,
    symbol: Annotated[str, Path(min_length=1, max_length=32)],
    analysis_date: date,
) -> AnalysisRecordResponse:
    service: AnalysisHistoryService = request.app.state.analysis_history_service
    try:
        record = service.get_history(symbol, analysis_date)
    except AnalysisHistoryNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AnalysisHistoryDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return AnalysisRecordResponse.from_domain(record)
