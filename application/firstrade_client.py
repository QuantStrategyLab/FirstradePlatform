"""Safe wrapper around the unofficial `firstrade` Python package.

The upstream package is a reverse-engineered client. This module keeps the
platform boundary explicit and defaults every order path to preview mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class FirstradePlatformError(RuntimeError):
    """Base error for platform integration failures."""


class FirstradeCredentialsError(FirstradePlatformError):
    """Raised when required credentials are missing."""


class FirstradeMfaRequired(FirstradePlatformError):
    """Raised when interactive MFA is required but no MFA code was provided."""


class FirstradeSafetyError(FirstradePlatformError):
    """Raised when an order violates local safety controls."""


@dataclass(frozen=True)
class FirstradeCredentials:
    username: str
    password: str
    pin: str = ""
    email: str = ""
    phone: str = ""
    mfa_secret: str = ""
    mfa_code: str = ""
    cookie_dir: str = ".runtime/firstrade-cookies"
    debug: bool = False

    @classmethod
    def from_env(cls, env: Callable[[str, str | None], str | None] = os.getenv) -> "FirstradeCredentials":
        username = env("FIRSTRADE_USERNAME", "") or ""
        password = env("FIRSTRADE_PASSWORD", "") or ""
        return cls(
            username=username.strip(),
            password=password,
            pin=env("FIRSTRADE_PIN", "") or "",
            email=env("FIRSTRADE_MFA_EMAIL", "") or "",
            phone=env("FIRSTRADE_MFA_PHONE", "") or "",
            mfa_secret=env("FIRSTRADE_MFA_SECRET", "") or "",
            mfa_code=env("FIRSTRADE_MFA_CODE", "") or "",
            cookie_dir=env("FIRSTRADE_COOKIE_DIR", ".runtime/firstrade-cookies")
            or ".runtime/firstrade-cookies",
            debug=(env("FIRSTRADE_DEBUG", "false") or "").lower() == "true",
        )

    def require_login_fields(self) -> None:
        missing = []
        if not self.username:
            missing.append("FIRSTRADE_USERNAME")
        if not self.password:
            missing.append("FIRSTRADE_PASSWORD")
        if missing:
            raise FirstradeCredentialsError(
                "Missing required Firstrade credential environment variables: "
                + ", ".join(missing)
            )


@dataclass(frozen=True)
class StockOrderRequest:
    account: str
    symbol: str
    side: str
    quantity: int | None = None
    notional_usd: float | None = None
    price_type: str = "market"
    duration: str = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    max_notional_usd: float = 25.0


def is_live_trading_enabled(env: Callable[[str, str | None], str | None] = os.getenv) -> bool:
    return (env("FIRSTRADE_ENABLE_LIVE_TRADING", "false") or "").strip().lower() == "true"


def mask_account_id(account_id: str) -> str:
    value = str(account_id or "")
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * max(0, len(value) - 4)}{value[-4:]}"


def _coerce_positive_float(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    coerced = float(value)
    if coerced <= 0:
        raise FirstradeSafetyError(f"{field} must be positive.")
    return coerced


def validate_stock_order(
    request: StockOrderRequest,
    *,
    dry_run: bool,
    live_trading_enabled: bool = False,
    explicit_live_ack: bool = False,
) -> None:
    symbol = str(request.symbol or "").strip().upper()
    if not symbol:
        raise FirstradeSafetyError("Order symbol must be non-empty.")
    side = str(request.side or "").strip().lower()
    if side not in {"buy", "sell"}:
        raise FirstradeSafetyError("Order side must be either 'buy' or 'sell'.")

    quantity_set = request.quantity is not None
    notional_set = request.notional_usd is not None
    if quantity_set == notional_set:
        raise FirstradeSafetyError("Set exactly one of quantity or notional_usd.")

    if request.quantity is not None and int(request.quantity) <= 0:
        raise FirstradeSafetyError("quantity must be a positive integer.")
    notional_usd = _coerce_positive_float(request.notional_usd, "notional_usd")
    max_notional_usd = _coerce_positive_float(request.max_notional_usd, "max_notional_usd") or 25.0

    price_type = str(request.price_type or "").strip().lower()
    if price_type not in {"market", "limit", "stop", "stop_limit"}:
        raise FirstradeSafetyError("price_type must be market, limit, stop, or stop_limit.")
    duration = str(request.duration or "").strip().lower()
    if duration not in {"day", "day_ext", "overnight", "gt90"}:
        raise FirstradeSafetyError("duration must be day, day_ext, overnight, or gt90.")
    if notional_usd is not None:
        if side != "buy":
            raise FirstradeSafetyError("Notional orders are restricted to buy-side validation.")
        if price_type != "market":
            raise FirstradeSafetyError("Notional validation only supports market preview/orders.")
        if notional_usd > max_notional_usd:
            raise FirstradeSafetyError(
                f"notional_usd {notional_usd:.2f} exceeds max_notional_usd {max_notional_usd:.2f}."
            )

    if price_type in {"limit", "stop_limit"}:
        _coerce_positive_float(request.limit_price, "limit_price")
    if price_type in {"stop", "stop_limit"}:
        _coerce_positive_float(request.stop_price, "stop_price")

    if dry_run:
        return

    if not live_trading_enabled:
        raise FirstradeSafetyError("Live trading is blocked unless FIRSTRADE_ENABLE_LIVE_TRADING=true.")
    if not explicit_live_ack:
        raise FirstradeSafetyError("Live trading requires --yes-i-understand-unofficial-api-risk.")
    if request.quantity is not None:
        if request.limit_price is None:
            raise FirstradeSafetyError("Live quantity orders must use a limit price for local notional checks.")
        estimated_notional = int(request.quantity) * float(request.limit_price)
        if estimated_notional > max_notional_usd:
            raise FirstradeSafetyError(
                f"estimated order notional {estimated_notional:.2f} exceeds max_notional_usd "
                f"{max_notional_usd:.2f}."
            )


class FirstradeBrokerClient:
    def __init__(
        self,
        credentials: FirstradeCredentials,
        *,
        live_trading_enabled: bool = False,
        session_factory: Callable[..., Any] | None = None,
        account_data_factory: Callable[[Any], Any] | None = None,
        order_factory: Callable[[Any], Any] | None = None,
        quote_factory: Callable[[Any, str, str], Any] | None = None,
        ohlc_factory: Callable[[Any, str, str], Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.live_trading_enabled = live_trading_enabled
        self._session_factory = session_factory
        self._account_data_factory = account_data_factory
        self._order_factory = order_factory
        self._quote_factory = quote_factory
        self._ohlc_factory = ohlc_factory
        self.session: Any | None = None
        self.account_data: Any | None = None

    def connect(self) -> "FirstradeBrokerClient":
        self.credentials.require_login_fields()
        session_factory = self._session_factory
        account_data_factory = self._account_data_factory
        if session_factory is None or account_data_factory is None:
            from firstrade.account import FTAccountData, FTSession

            session_factory = FTSession
            account_data_factory = FTAccountData

        cookie_dir = Path(self.credentials.cookie_dir)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        session = session_factory(
            username=self.credentials.username,
            password=self.credentials.password,
            pin=self.credentials.pin,
            email=self.credentials.email,
            phone=self.credentials.phone,
            mfa_secret=self.credentials.mfa_secret,
            profile_path=str(cookie_dir),
            debug=self.credentials.debug,
        )
        needs_mfa_code = bool(session.login())
        if needs_mfa_code:
            if not self.credentials.mfa_code:
                raise FirstradeMfaRequired(
                    "Firstrade requested MFA. Set FIRSTRADE_MFA_CODE and retry this one validation run."
                )
            session.login_two(self.credentials.mfa_code)
        self.session = session
        self.account_data = account_data_factory(session)
        return self

    def require_connected(self) -> tuple[Any, Any]:
        if self.session is None or self.account_data is None:
            raise FirstradePlatformError("Firstrade client is not connected.")
        return self.session, self.account_data

    def account_numbers(self) -> list[str]:
        _, account_data = self.require_connected()
        return list(getattr(account_data, "account_numbers", []))

    def select_account(self, requested_account: str | None = None) -> str:
        accounts = self.account_numbers()
        if requested_account:
            if requested_account not in accounts:
                raise FirstradePlatformError(
                    f"Requested account {mask_account_id(requested_account)} was not returned by Firstrade."
                )
            return requested_account
        if len(accounts) == 1:
            return accounts[0]
        if not accounts:
            raise FirstradePlatformError("Firstrade returned no accounts.")
        raise FirstradePlatformError(
            "Multiple Firstrade accounts are available; set FIRSTRADE_ACCOUNT or pass --account."
        )

    def list_account_summaries(self) -> list[dict[str, Any]]:
        _, account_data = self.require_connected()
        balances = dict(getattr(account_data, "account_balances", {}) or {})
        return [
            {
                "account": mask_account_id(account),
                "total_value": balances.get(account),
            }
            for account in self.account_numbers()
        ]

    def get_balances(self, account: str) -> dict[str, Any]:
        _, account_data = self.require_connected()
        return dict(account_data.get_account_balances(account))

    def get_positions(self, account: str) -> dict[str, Any]:
        _, account_data = self.require_connected()
        return dict(account_data.get_positions(account))

    def get_quote(self, account: str, symbol: str) -> dict[str, Any]:
        session, _ = self.require_connected()
        quote_factory = self._quote_factory
        if quote_factory is None:
            from firstrade.symbols import SymbolQuote

            quote_factory = SymbolQuote
        quote = quote_factory(session, account, symbol.upper())
        return {
            "symbol": getattr(quote, "symbol", symbol.upper()),
            "last": getattr(quote, "last", None),
            "bid": getattr(quote, "bid", None),
            "ask": getattr(quote, "ask", None),
            "quote_time": getattr(quote, "quote_time", None),
            "last_trade_time": getattr(quote, "last_trade_time", None),
            "company_name": getattr(quote, "company_name", None),
            "exchange": getattr(quote, "exchange", None),
            "is_fractional": getattr(quote, "is_fractional", None),
            "realtime": getattr(quote, "realtime", None),
        }

    def get_ohlc(self, symbol: str, range_: str = "1d") -> list[tuple[Any, ...]]:
        session, _ = self.require_connected()
        ohlc_factory = self._ohlc_factory
        if ohlc_factory is None:
            from firstrade.symbols import SymbolOHLC

            ohlc_factory = SymbolOHLC
        ohlc = ohlc_factory(session, symbol.upper(), range_)
        return list(getattr(ohlc, "candles", []))

    def place_stock_order(
        self,
        request: StockOrderRequest,
        *,
        dry_run: bool = True,
        explicit_live_ack: bool = False,
    ) -> dict[str, Any]:
        validate_stock_order(
            request,
            dry_run=dry_run,
            live_trading_enabled=self.live_trading_enabled,
            explicit_live_ack=explicit_live_ack,
        )
        session, _ = self.require_connected()
        order_factory = self._order_factory
        if order_factory is None:
            from firstrade.order import Duration, Order, OrderType, PriceType
        else:
            from firstrade.order import Duration, OrderType, PriceType
            Order = order_factory

        price_type = {
            "market": PriceType.MARKET,
            "limit": PriceType.LIMIT,
            "stop": PriceType.STOP,
            "stop_limit": PriceType.STOP_LIMIT,
        }[request.price_type.lower()]
        duration = {
            "day": Duration.DAY,
            "day_ext": Duration.DAY_EXT,
            "overnight": Duration.OVERNIGHT,
            "gt90": Duration.GT90,
        }[request.duration.lower()]
        order_type = {
            "buy": OrderType.BUY,
            "sell": OrderType.SELL,
        }[request.side.lower()]

        order = Order(session)
        notional = request.notional_usd is not None
        price = float(request.notional_usd if notional else (request.limit_price or 0.0))
        return dict(
            order.place_order(
                account=request.account,
                symbol=request.symbol.upper(),
                price_type=price_type,
                order_type=order_type,
                duration=duration,
                quantity=int(request.quantity or 0),
                price=price,
                stop_price=request.stop_price,
                dry_run=dry_run,
                notional=notional,
            )
        )
