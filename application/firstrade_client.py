"""Safe wrapper around the unofficial `firstrade` Python package.

The upstream package is a reverse-engineered client. This module keeps the
platform boundary explicit and defaults every order path to preview mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from time import time
from typing import Any, Callable
from urllib.parse import urlsplit

from application.account_payload_utils import flatten_values, float_or_none
from application.state_persistence import GcsStateStore


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
    reuse_session: bool = False
    session_cache_ttl_seconds: int = 21_600
    persist_session_cache: bool = False
    gcs_state_bucket: str = ""
    gcs_state_prefix: str = "firstrade-platform"
    debug: bool = False

    @classmethod
    def from_env(
        cls, env: Callable[[str, str | None], str | None] = os.getenv,
        *, include_login_credentials: bool = True,
    ) -> "FirstradeCredentials":
        def _get_credential(secret_name: str, env_var: str) -> str:
            try:
                from quant_platform_kit.cloud import get_secret_store

                return get_secret_store().get_secret(secret_name, project_id="firstradequant")
            except Exception:
                return env(env_var, "") or ""

        username = _get_credential("firstrade-username", "FIRSTRADE_USERNAME")
        password = _get_credential("firstrade-password", "FIRSTRADE_PASSWORD") if include_login_credentials else ""
        return cls(
            username=username.strip(),
            password=password,
            pin=(env("FIRSTRADE_PIN", "") or "") if include_login_credentials else "",
            email=(env("FIRSTRADE_MFA_EMAIL", "") or "") if include_login_credentials else "",
            phone=(env("FIRSTRADE_MFA_PHONE", "") or "") if include_login_credentials else "",
            mfa_secret=_get_credential("firstrade-mfa-secret", "FIRSTRADE_MFA_SECRET") if include_login_credentials else "",
            mfa_code=(env("FIRSTRADE_MFA_CODE", "") or "") if include_login_credentials else "",
            cookie_dir=env("FIRSTRADE_COOKIE_DIR", ".runtime/firstrade-cookies")
            or ".runtime/firstrade-cookies",
            reuse_session=(env("FIRSTRADE_REUSE_SESSION", "false") or "").strip().lower() == "true",
            session_cache_ttl_seconds=_coerce_positive_int(
                env("FIRSTRADE_SESSION_CACHE_TTL_SECONDS", "21600"),
                default=21_600,
            ),
            persist_session_cache=(env("FIRSTRADE_PERSIST_SESSION_CACHE", "false") or "")
            .strip()
            .lower()
            == "true",
            gcs_state_bucket=(env("FIRSTRADE_GCS_STATE_BUCKET", "") or "").strip(),
            gcs_state_prefix=env("FIRSTRADE_STATE_PREFIX", "firstrade-platform")
            or "firstrade-platform",
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
    max_notional_usd: float | None = None


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


def _coerce_positive_int(value: str | None, *, default: int) -> int:
    try:
        coerced = int(str(value or "").strip())
    except ValueError:
        return default
    return coerced if coerced > 0 else default


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
    max_notional_usd = _coerce_positive_float(request.max_notional_usd, "max_notional_usd")

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
        if max_notional_usd is not None and notional_usd > max_notional_usd:
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
        if max_notional_usd is not None and estimated_notional > max_notional_usd:
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
        session_cache_store: GcsStateStore | None = None,
    ) -> None:
        self.credentials = credentials
        self.live_trading_enabled = live_trading_enabled
        self._session_factory = session_factory
        self._account_data_factory = account_data_factory
        self._order_factory = order_factory
        self._quote_factory = quote_factory
        self._ohlc_factory = ohlc_factory
        self._session_cache_store = session_cache_store
        self.session: Any | None = None
        self.account_data: Any | None = None
        self.session_reused = False

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
        session = self._build_session(session_factory, cookie_dir)
        if self.credentials.reuse_session and self._try_cached_session(
            session,
            account_data_factory=account_data_factory,
            cookie_dir=cookie_dir,
        ):
            return self

        needs_mfa_code = bool(session.login())
        if needs_mfa_code:
            if not self.credentials.mfa_code:
                raise FirstradeMfaRequired(
                    "Firstrade requested MFA. Set FIRSTRADE_MFA_CODE and retry this one validation run."
                )
            session.login_two(self.credentials.mfa_code)
        self.session = session
        self.account_data = account_data_factory(session)
        self.session_reused = False
        self._save_session_cache(cookie_dir)
        return self

    def connect_read_only(self) -> "FirstradeBrokerClient":
        """Reuse cached authentication for account reads; never log in or change the cache."""
        if (
            self.live_trading_enabled or self.session is not None or self.account_data is not None
            or not self.credentials.username.strip() or not self.credentials.reuse_session
        ):
            raise FirstradeSafetyError("Firstrade cached-only connection requires a fresh non-trading client.")
        payload = self._load_session_cache(Path(self.credentials.cookie_dir))
        if not payload or not isinstance(payload.get("access-token"), str) or not payload["access-token"]:
            raise FirstradePlatformError("Firstrade cached session unavailable.")
        from firstrade.account import FTAccountData, FTSession

        session_factory = self._session_factory or FTSession
        account_data_factory = self._account_data_factory or FTAccountData
        session = session_factory(username="", password="", save_session=False, debug=False)
        transport = session.session
        transport.trust_env = False
        original_request = transport.request

        def read_only_request(method, url, **kwargs):
            parsed = urlsplit(url)
            if str(method).lower() != "get" or (
                parsed.scheme != "https" or parsed.netloc != "api3x.firstrade.com"
                or parsed.path not in {
                    "/private/userinfo", "/private/acct_list", "/private/balances",
                    "/private/positions", "/private/order_status",
                }
            ):
                raise FirstradeSafetyError("Firstrade read-only request denied.")
            kwargs.update(timeout=(5, 15), allow_redirects=False)
            response = original_request(method, url, **kwargs)
            if not 200 <= response.status_code < 300:
                raise FirstradePlatformError("Firstrade account read unavailable.")
            body = response.json()
            if not isinstance(body, (dict, list)) or (isinstance(body, dict) and body.get("error")):
                raise FirstradePlatformError("Firstrade account read unavailable.")
            return response

        transport.request = read_only_request
        try:
            session.build_session_from_tokens(payload)
            account_data = account_data_factory(session)
        except Exception:
            transport.close()
            raise FirstradePlatformError("Firstrade cached session unavailable.") from None
        self.session, self.account_data = session, account_data
        self.session_reused = True
        return self

    def close(self) -> None:
        session, self.session = self.session, None
        self.account_data = None
        self.session_reused = False
        if session is not None:
            session.session.close()

    def _build_session(self, session_factory: Callable[..., Any], cookie_dir: Path) -> Any:
        return session_factory(
            username=self.credentials.username,
            password=self.credentials.password,
            pin=self.credentials.pin,
            email=self.credentials.email,
            phone=self.credentials.phone,
            mfa_secret=self.credentials.mfa_secret,
            profile_path=str(cookie_dir),
            debug=self.credentials.debug,
        )

    def _session_cache_path(self, cookie_dir: Path) -> Path:
        return cookie_dir / f"ft_session_{self._session_cache_identity()}.json"

    def _load_session_cache(self, cookie_dir: Path) -> dict[str, Any] | None:
        path = self._session_cache_path(cookie_dir)
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = None
        if self._is_valid_session_cache_payload(payload):
            return payload
        store = self._session_state_store()
        if store is None:
            return None
        try:
            persisted_payload = store.read_json(self._session_state_key())
        except Exception:
            return None
        if self._is_valid_session_cache_payload(persisted_payload):
            return persisted_payload
        return None

    def _is_valid_session_cache_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        try:
            saved_at = float(payload.get("saved_at") or 0.0)
        except (TypeError, ValueError):
            return False
        ttl = max(1, int(self.credentials.session_cache_ttl_seconds or 1))
        age = time() - saved_at
        if not isfinite(saved_at) or saved_at <= 0.0 or not 0 <= age <= ttl:
            return False
        return all(isinstance(payload.get(key), str) and payload[key].strip() for key in ("ftat", "sid"))

    def _try_cached_session(
        self,
        session: Any,
        *,
        account_data_factory: Callable[[Any], Any],
        cookie_dir: Path,
    ) -> bool:
        payload = self._load_session_cache(cookie_dir)
        if not payload:
            return False
        try:
            from firstrade import urls

            if hasattr(session, "build_session_from_tokens"):
                session.build_session_from_tokens(payload)
            else:
                session.session.headers.update(urls.session_headers())
                session.session.headers["access-token"] = str(
                    payload.get("access-token") or urls.access_token()
                )
                session.session.headers["ftat"] = str(payload["ftat"])
                session.session.headers["sid"] = str(payload["sid"])
                cookies = payload.get("cookies")
                if isinstance(cookies, dict) and hasattr(session.session, "cookies"):
                    session.session.cookies.update(cookies)
            account_data = account_data_factory(session)
        except Exception:
            try:
                self._session_cache_path(cookie_dir).unlink()
            except OSError:
                pass
            return False
        self.session = session
        self.account_data = account_data
        self.session_reused = True
        self._save_session_cache(cookie_dir)
        return True

    def _save_session_cache(self, cookie_dir: Path) -> None:
        if not self.credentials.reuse_session or self.session is None:
            return
        session_obj = getattr(self.session, "session", None)
        headers = getattr(session_obj, "headers", {}) or {}
        cookies = {}
        if hasattr(session_obj, "cookies"):
            try:
                cookies = session_obj.cookies.get_dict()
            except Exception:
                cookies = {}
        payload = {
            "access-token": headers.get("access-token"),
            "ftat": headers.get("ftat"),
            "sid": headers.get("sid"),
            "cookies": cookies,
            "saved_at": time(),
        }
        if not payload["ftat"] or not payload["sid"]:
            return
        try:
            self._session_cache_path(cookie_dir).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass
        store = self._session_state_store()
        if store is None:
            return
        try:
            store.write_json(self._session_state_key(), payload)
        except Exception:
            return

    def _session_state_store(self) -> GcsStateStore | None:
        if self._session_cache_store is not None:
            return self._session_cache_store
        if not self.credentials.persist_session_cache or not self.credentials.gcs_state_bucket:
            return None
        return GcsStateStore(
            bucket=self.credentials.gcs_state_bucket,
            prefix=self.credentials.gcs_state_prefix,
        )

    def _session_state_key(self) -> str:
        return f"sessions/{self._session_cache_identity()}/latest.json"

    def _session_cache_identity(self) -> str:
        username = str(self.credentials.username or "").strip().lower()
        if not username:
            return "unknown"
        return sha256(username.encode("utf-8")).hexdigest()[:16]

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
        balances = dict(account_data.get_account_balances(account))
        account_balances = dict(getattr(account_data, "account_balances", {}) or {})
        account_list_total_value = account_balances.get(account)
        if account_list_total_value is not None and "account_list_total_value" not in balances:
            balances["account_list_total_value"] = account_list_total_value
        return balances

    def get_positions(self, account: str) -> dict[str, Any]:
        _, account_data = self.require_connected()
        return dict(account_data.get_positions(account))

    def get_orders(self, account: str, *, per_page: int = 0) -> list[dict[str, Any]]:
        _, account_data = self.require_connected()
        payload = account_data.get_orders(account, per_page=per_page)
        if isinstance(payload, dict):
            for key in ("items", "orders", "data", "result"):
                value = payload.get(key)
                if isinstance(value, list):
                    payload = value
                    break
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise FirstradePlatformError("Firstrade returned an invalid order response.")
        return [dict(row) for row in payload]

    def get_order_status(self, account: str, order_id: str) -> dict[str, Any] | None:
        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id:
            return None
        for row in self.get_orders(account):
            if not _payload_contains_order_id(row, normalized_order_id):
                continue
            status = _first_text_from_payload(
                row,
                "status",
                "order_status",
                "state",
                "status_description",
                "description",
            )
            executed_qty = _first_numeric_from_payload(
                row,
                "executed_qty",
                "executed_quantity",
                "filled_quantity",
                "filled_qty",
                "filled",
                "filled_shares",
                "executed_shares",
                "quantity_filled",
                "quantity",
                "shares",
                "qty",
            )
            executed_price = _first_numeric_from_payload(
                row,
                "executed_price",
                "average_fill_price",
                "avg_fill_price",
                "avg_price",
                "average_price",
                "fill_price",
                "filled_price",
                "price",
                "limit_price",
            )
            return {
                "status": status or "",
                "executed_qty": max(0.0, float(executed_qty or 0.0)),
                "executed_price": max(0.0, float(executed_price or 0.0)),
                "broker_order_id": normalized_order_id,
                "raw_payload": dict(row),
            }
        return None

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


def _sanitize_payload_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _first_payload_value(payload: Any, *candidate_keys: str) -> Any:
    flattened = flatten_values(payload)
    candidates = {_sanitize_payload_key(key) for key in candidate_keys}
    for key, value in flattened.items():
        if _sanitize_payload_key(key.rsplit(".", 1)[-1]) in candidates:
            return value
    return None


def _first_text_from_payload(payload: Any, *candidate_keys: str) -> str | None:
    value = _first_payload_value(payload, *candidate_keys)
    text = str(value or "").strip()
    return text or None


def _first_numeric_from_payload(payload: Any, *candidate_keys: str) -> float | None:
    return float_or_none(_first_payload_value(payload, *candidate_keys))


def _payload_contains_order_id(payload: Any, order_id: str) -> bool:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return False
    for key, value in flatten_values(payload).items():
        key_normalized = _sanitize_payload_key(key)
        if "order" not in key_normalized:
            continue
        if not any(token in key_normalized for token in ("id", "number", "orderno")):
            continue
        if str(value or "").strip() == normalized_order_id:
            return True
    return False
