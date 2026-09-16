from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

from .checks import ChartInfo
from .scoreline import Notes

PROBER_BASE = "https://www.diving-fish.com/api/maimaidxprober"
AUTH_BASE = "https://auth.diving-fish.com"
DEVICE_AUTHORIZATION_URL = f"{AUTH_BASE}/oauth/device_authorization"
TOKEN_URL = f"{AUTH_BASE}/oauth/token"
REVOKE_URL = f"{AUTH_BASE}/oauth/revoke"
DEVICE_VERIFY_URL = f"{AUTH_BASE}/device"
MUSIC_DATA_URL = f"{PROBER_BASE}/music_data"
RECORDS_URL = f"{PROBER_BASE}/player/records"
QUERY_PLAYER_URL = f"{PROBER_BASE}/query/player"

OFFICIAL_CLIENT_ID = "c1fa481837042b12e2e5161979b71315"

DEFAULT_CONFIG_PATH = "config.json"

RECORDS_SCOPE = "prober.records.read"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
ON_BEHALF_OF_GRANT = "urn:diving-fish:params:oauth:grant-type:on-behalf-of"

UTAGE_MIN_ID = 100000
DEFAULT_TIMEOUT = 30.0

TOKEN_MARGIN = 30.0
ON_BEHALF_OF_TTL = 300.0
SLOW_DOWN_STEP = 5


class DataSourceError(RuntimeError):
    """取数失败。"""


class OAuthError(DataSourceError):
    """水鱼 OAuth 流程失败。"""


class AuthorizationDenied(OAuthError):
    """用户在授权页点了拒绝。"""


class DeviceCodeExpired(OAuthError):
    """用户未在有效期内完成授权。"""


class ConsentRequired(OAuthError):
    """该用户没有授权本应用，需要重新登录。"""


class TokenRevoked(ConsentRequired):
    """refresh token 已失效（撤销授权或令牌链被吊销）。"""


class RateLimited(OAuthError):
    """请求过于频繁（消息以 429 开头）。"""


def is_utage(song_id: str | int) -> bool:
    """是否为宴谱（宴会场谱面不参与校验）。"""
    try:
        return int(song_id) >= UTAGE_MIN_ID
    except (TypeError, ValueError):
        return False


@dataclass(slots=True)
class Credentials:
    """水鱼 OAuth 应用与用户凭据。"""

    client_id: str
    client_secret: str | None = None
    refresh_token: str | None = None
    subject: str | None = None

    @property
    def confidential(self) -> bool:
        """登记为机密客户端（有 client_secret）时为真。"""
        return bool(self.client_secret)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"client_id": self.client_id}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        if self.refresh_token:
            data["refresh_token"] = self.refresh_token
        if self.subject:
            data["subject"] = self.subject
        return data


def load_credentials(path: str | Path = DEFAULT_CONFIG_PATH) -> Credentials:
    config_path = Path(path)
    stored: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataSourceError(f"读取 {config_path} 失败：{exc}") from exc
        if not isinstance(loaded, dict):
            raise DataSourceError(f"{config_path} 应为 JSON 对象")
        stored = loaded
    return Credentials(
        client_id=str(stored.get("client_id") or OFFICIAL_CLIENT_ID),
        client_secret=stored.get("client_secret"),
        refresh_token=stored.get("refresh_token"),
        subject=stored.get("subject"),
    )


def save_credentials(credentials: Credentials, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """把客户端凭据与令牌落盘（公开客户端的 refresh token 必须先落盘再使用）。

    已存在的键会被保留、同名键被覆盖，所以手写的 ``client_id`` 不会因为一次登录就丢失。
    """
    config_path = Path(path)
    current: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, ValueError):
            current = {}
    current.update(credentials.to_dict())
    config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def flatten_b50(data: dict) -> list[dict]:
    """把 ``/query/player`` 的返回拍平成成绩列表（兼容多种历史形状）。"""
    out: list[dict] = []
    charts = data.get("charts") or {}
    if isinstance(charts, dict) and any(k in charts for k in ("sd", "dx")):
        for diff in ("sd", "dx"):
            entries = charts.get(diff) or []
            if isinstance(entries, dict):  # {song_id: [entry, ...]}
                for song_id, items in entries.items():
                    for entry in items or []:
                        item = dict(entry)
                        item.setdefault("song_id", song_id)
                        item.setdefault("type", diff.upper())
                        out.append(item)
            else:
                for entry in entries:
                    item = dict(entry)
                    item.setdefault("type", diff.upper())
                    out.append(item)
    elif isinstance(charts, list):
        out = [dict(entry) for entry in charts]
    return out


def records_from_file(path: str | Path) -> list[dict]:
    """从本地 JSON 读取成绩（支持 ``{"records": [...]}`` 或直接数组）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("records", []))
    return list(data)


class DivingFishClient:
    """水鱼数据客户端：统一处理本地缓存、OAuth 鉴权与重试。"""

    def __init__(
        self,
        credentials: Credentials,
        *,
        cache_dir: str | Path = "cache",
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
        log=print,
    ) -> None:
        self.credentials = credentials
        self.cache_dir = Path(cache_dir)
        self.config_path = Path(config_path)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.log = log
        self._access_token: str | None = None
        self._token_expire = 0.0

    # ------------------------------------------------------------- 缓存与底层请求

    def _load_cache(self, name: str) -> Any | None:
        path = self.cache_dir / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"[warn] 缓存 {path} 不可用，将重新请求：{exc}")
            return None

    def _save_cache(self, name: str, data: Any) -> None:
        path = self.cache_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _request(self, method: str, url: str, *, attempt: int = 3, **kwargs: Any) -> requests.Response:
        """发请求；仅对网络类异常重试，4xx/5xx 交由调用方判定。"""
        kwargs.setdefault("timeout", self.timeout)
        last: Exception | None = None
        for index in range(attempt):
            try:
                return self.session.request(method, url, **kwargs)
            except (requests.RequestException, socket.timeout) as exc:
                last = exc
                if index + 1 < attempt:
                    time.sleep(1.0 + index)
        raise DataSourceError(f"请求 {url} 失败：{last}") from last

    def _post_form(self, url: str, payload: dict) -> dict:
        """以 ``application/x-www-form-urlencoded`` 提交 OAuth 表单，不因 4xx 抛错。"""
        data = dict(payload)
        if self.credentials.confidential:
            data["client_secret"] = self.credentials.client_secret
        resp = self._request(
            "POST",
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "accept": "application/json",
            },
            attempt=1,
        )
        try:
            body = resp.json()
        except ValueError as exc:
            raise OAuthError(f"令牌端点返回了非 JSON 响应（HTTP {resp.status_code}）") from exc
        if not isinstance(body, dict):
            raise OAuthError(f"令牌端点返回了意外响应：{body!r}")
        return body

    # ----------------------------------------------------------------------- 谱面

    def music_data(self, *, refresh: bool = False) -> list[dict]:
        """谱面物量列表（``/music_data``，默认走本地缓存）。"""
        cached = None if refresh else self._load_cache("music_data.json")
        if cached is not None:
            return cached
        resp = self._request("GET", MUSIC_DATA_URL, headers={"accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        self._save_cache("music_data.json", data)
        return data

    def chart_index(self, *, refresh: bool = False) -> dict[tuple[str, str, int], ChartInfo]:
        """``(song_id, type, level_index) -> ChartInfo`` 索引。"""
        index: dict[tuple[str, str, int], ChartInfo] = {}
        for entry in self.music_data(refresh=refresh):
            song_id = str(entry.get("id"))
            ds_list = entry.get("ds") or []
            level_list = entry.get("level") or []
            for level_index, chart in enumerate(entry.get("charts") or []):
                raw_notes = chart.get("notes")
                if not raw_notes:
                    continue
                try:
                    notes = Notes.from_api(raw_notes)
                except ValueError as exc:
                    self.log(f"[warn] 跳过物量异常的谱面 {song_id}/{level_index}：{exc}")
                    continue
                info = ChartInfo(
                    song_id=song_id,
                    title=str(entry.get("title", song_id)),
                    type=str(entry.get("type", "SD")),
                    level_index=level_index,
                    level=str(level_list[level_index]) if level_index < len(level_list) else "",
                    ds=float(ds_list[level_index]) if level_index < len(ds_list) else 0.0,
                    notes=notes,
                )
                index[info.key] = info
        return index

    # ----------------------------------------------------------------------- 成绩

    def _filter_utage(self, records: Iterable[dict], include_utage: bool) -> list[dict]:
        if include_utage:
            return list(records)
        return [r for r in records if not is_utage(r.get("id", r.get("song_id", 0)))]

    def records(self, *, refresh: bool = False, include_utage: bool = False) -> list[dict]:
        """全量成绩（``/player/records``，需 OAuth）。"""
        cached = None if refresh else self._load_cache("records.json")
        if cached is None:
            cached = self._request_player_data(RECORDS_URL)
            self._save_cache("records.json", cached)
        records = cached.get("records", []) if isinstance(cached, dict) else cached
        return self._filter_utage(records, include_utage)

    def b50(
        self,
        *,
        username: str | None = None,
        qq: str | None = None,
        refresh: bool = False,
        include_utage: bool = False,
    ) -> list[dict]:
        """B50（``/query/player``，公开接口，按用户名或 QQ 查询）。"""
        if not username and not qq:
            raise DataSourceError("B50 查询需要 username 或 qq")
        name = "b50.json" if username else "b50-qq.json"
        cached = None if refresh else self._load_cache(name)
        if cached is None:
            payload = {"username": username} if username else {"qq": qq}
            resp = self._request(
                "POST",
                QUERY_PLAYER_URL,
                json=payload,
                headers={"Content-Type": "application/json", "accept": "*/*"},
            )
            if resp.status_code == 400:
                raise DataSourceError("400 查询对象不存在或未绑定水鱼账号")
            if resp.status_code == 403:
                raise DataSourceError("403 未同意用户协议或查询被拒绝")
            resp.raise_for_status()
            cached = resp.json()
            self._save_cache(name, cached)
        return self._filter_utage(flatten_b50(cached), include_utage)

    # ----------------------------------------------------------------------- OAuth

    def access_token(self) -> str:
        """取得可用的 access token（优先复用进程内缓存，凭据缺失时走设备码登录）。"""
        if self._access_token and time.monotonic() < self._token_expire:
            return self._access_token
        if self.credentials.confidential:
            return self._exchange_on_behalf_of()
        return self._refresh_access_token()

    def login(self) -> str:
        """设备码流程：打印用户码与授权链接，轮询到令牌后落盘凭据。"""
        label = socket.gethostname() or "local"
        resp = self._post_form(
            DEVICE_AUTHORIZATION_URL,
            {
                "client_id": self.credentials.client_id,
                "scope": RECORDS_SCOPE,
                "binding_label": label,
            },
        )
        error = resp.get("error")
        if error:
            raise OAuthError(f"设备码申请失败：{error} {resp.get('error_description', '')}".strip())
        device_code = resp.get("device_code")
        user_code = resp.get("user_code")
        if not device_code or not user_code:
            raise OAuthError(f"设备码申请返回异常：{resp!r}")
        try:
            expires_in = int(resp.get("expires_in", 600))
        except (TypeError, ValueError):
            expires_in = 600
        try:
            interval = max(1, int(resp.get("interval", 5)))
        except (TypeError, ValueError):
            interval = 5
        verify_url = resp.get("verification_uri_complete") or resp.get("verification_uri") or DEVICE_VERIFY_URL
        self.log(
            f"请在 {max(1, expires_in // 60)} 分钟内打开下面的链接完成授权（用户码 {user_code}）：\n{verify_url}"
        )

        deadline = time.monotonic() + expires_in
        while time.monotonic() < deadline:
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
            body = self._post_form(
                TOKEN_URL,
                {
                    "grant_type": DEVICE_CODE_GRANT,
                    "device_code": device_code,
                    "client_id": self.credentials.client_id,
                },
            )
            error = body.get("error")
            if not error:
                return self._store_token(body, from_login=True)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += SLOW_DOWN_STEP
                continue
            if error == "access_denied":
                raise AuthorizationDenied("用户拒绝了本次授权")
            if error == "expired_token":
                raise DeviceCodeExpired("设备码已过期")
            raise OAuthError(f"设备码换取令牌失败：{error}")
        raise DeviceCodeExpired("用户未在有效期内完成授权")

    def _store_token(self, body: dict, *, from_login: bool = False) -> str:
        """缓存 access token；设备码流程还需落盘 refresh token（公开）或用户标识（机密）。"""
        token = body.get("access_token")
        if not token:
            raise OAuthError(f"令牌响应缺少 access_token：{body!r}")
        try:
            expires = float(body.get("expires_in") or ON_BEHALF_OF_TTL)
        except (TypeError, ValueError):
            expires = ON_BEHALF_OF_TTL
        self._access_token = str(token)
        self._token_expire = time.monotonic() + max(0.0, expires - TOKEN_MARGIN)
        if self.credentials.confidential:
            subject = body.get("sub")
            if from_login:
                if subject is None:
                    raise OAuthError(f"令牌响应缺少用户标识 sub：{body!r}")
                self.credentials.subject = str(subject)
                save_credentials(self.credentials, self.config_path)
        elif from_login:
            refresh = body.get("refresh_token")
            if not refresh:
                raise OAuthError(f"令牌响应缺少 refresh_token：{body!r}")
            self.credentials.refresh_token = str(refresh)
            save_credentials(self.credentials, self.config_path)
        return self._access_token

    def _refresh_access_token(self) -> str:
        """公开客户端：用 refresh token 续期（刷新会轮换令牌，新令牌必须先落盘）。"""
        refresh_token = self.credentials.refresh_token
        if not refresh_token:
            return self.login()
        body = self._post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.credentials.client_id,
            },
        )
        error = body.get("error")
        if error:
            if error == "invalid_grant":
                self.credentials.refresh_token = None
                save_credentials(self.credentials, self.config_path)
                raise TokenRevoked("refresh token 已失效，请重新登录授权")
            raise OAuthError(f"刷新令牌失败：{error}")
        rotated = body.get("refresh_token")
        if rotated:
            self.credentials.refresh_token = str(rotated)
            save_credentials(self.credentials, self.config_path)
        return self._store_token(body)

    def _exchange_on_behalf_of(self) -> str:
        """机密客户端：以应用凭据 + 用户标识换票（令牌仅 5 分钟，必须复用缓存）。"""
        subject = self.credentials.subject
        if not subject:
            return self.login()
        body = self._post_form(
            TOKEN_URL,
            {
                "grant_type": ON_BEHALF_OF_GRANT,
                "client_id": self.credentials.client_id,
                "subject": f"sub:{subject}",
            },
        )
        error = body.get("error")
        if error:
            description = body.get("error_description", "")
            if error == "consent_required":
                raise ConsentRequired(f"该用户未授权本应用：{description}".strip())
            if error == "slow_down":
                raise RateLimited(f"429 换票过于频繁：{description}".strip())
            raise OAuthError(f"换票失败：{error} {description}".strip())
        return self._store_token(body)

    def revoke(self, token: str, token_type_hint: str = "refresh_token") -> None:
        """撤销令牌（解绑时使用）。"""
        self._post_form(
            REVOKE_URL,
            {
                "client_id": self.credentials.client_id,
                "token": token,
                "token_type_hint": token_type_hint,
            },
        )

    def _request_player_data(
        self, url: str, method: str = "GET", **kwargs: Any
    ) -> Any:
        """带令牌请求玩家数据；遇 401 丢弃缓存令牌重取一次。"""
        # 两轮：第一轮用当前令牌，401 时清掉缓存再走一次
        for attempt in range(2):
            token = self.access_token()
            resp = self._request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                **kwargs,
            )
            if resp.status_code == 401 and attempt == 0:
                self._access_token = None
                self._token_expire = 0.0
                continue
            if resp.status_code == 401:
                raise OAuthError("401 令牌被拒绝，且重新获取后仍然无效")
            if resp.status_code == 403:
                raise DataSourceError("403 未同意用户协议或无权访问该玩家数据")
            if resp.status_code == 429:
                raise RateLimited("429 请求过于频繁，请稍后再试")
            resp.raise_for_status()
            return resp.json()
        raise OAuthError("未能取得玩家数据")
