"""Cookie validation, loader lifecycle, and shared RateController."""

from __future__ import annotations

import os
import pickle
import re
import time

from instaloader import (
    Instaloader,
    RateController,
)
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    LoginRequiredException,
)

import inscrawler.config as config
import inscrawler.diagnostics.classification as diagnostics_classification
import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.hotfixes as instagram_hotfixes
import inscrawler.paths as app_paths


def _get_user_agent_override():
    """Return custom user_agent or None for instaloader to use its default."""
    raw = (
        os.environ.get("user_agent")
        or config._read_dotenv_file().get("user_agent")
        or ""
    ).strip()
    return raw if raw else None


class _CooperativeRateController(RateController):
    """Keep Instaloader's own waits responsive to the app's Pause and Stop."""

    def sleep(self, secs: float):
        end = time.monotonic() + max(0.0, secs)
        while True:
            downloads_control.wait_if_paused()
            downloads_control._raise_if_stop_requested()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))


def init_base_loader() -> Instaloader:
    L = Instaloader(
        sleep=True,
        quiet=True,
        user_agent=_get_user_agent_override(),
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        storyitem_metadata_txt_pattern="",
        max_connection_attempts=3,
        request_timeout=300.0,
        rate_controller=_CooperativeRateController,
        # API 429 is an account-safety boundary.  Abort the first JSON 429
        # instead of letting Instaloader retry it; raw CDN downloads do not use
        # this setting.
        fatal_status_codes=[429],
        iphone_support=downloads_runtime.runtime.iphone_support_enabled,
        sanitize_paths=True,
    )
    instagram_hotfixes.install_login_check(L)
    return L


def _context_errors_after(context, error_log_start):
    try:
        return tuple(list(context.error_log)[error_log_start:])
    except Exception:
        return ()


def _context_reported_api_rate_limit(context, error_log_start):
    """Whether test_login() swallowed an explicit temporary API limit."""
    return any(
        diagnostics_classification._looks_like_instagram_api_rate_limit(error)
        for error in _context_errors_after(context, error_log_start)
    )


def _context_reported_authorization_failure(context, error_log_start):
    markers = (
        "checkpoint_required",
        "challenge_required",
        "feedback_required",
        "login page",
        "logged out",
    )
    for error in _context_errors_after(context, error_log_start):
        text = str(error).lower()
        if any(marker in text for marker in markers):
            return True
        if diagnostics_classification._looks_like_instagram_api_status(text, 401) and (
            "unauthorized" in text or "login_required" in text
        ):
            return True
    return False


def _missing_test_login_user_status(context, error_log_start):
    if _context_reported_api_rate_limit(context, error_log_start):
        return downloads_models.status_code.RATE_LIMITED
    if _context_reported_authorization_failure(context, error_log_start):
        return downloads_models.status_code.AUTHORIZATION_REQUIRED
    if _context_errors_after(context, error_log_start):
        return downloads_models.status_code.FAILED
    return downloads_models.status_code.AUTHORIZATION_REQUIRED


def _risk_note_context_errors(context, error_log_start):
    for error in _context_errors_after(context, error_log_start):
        diagnostics_risk._risk_note_incident(error)


def _api_status_for_failure(status):
    if status == downloads_models.status_code.AUTHORIZATION_REQUIRED:
        return "auth_required"
    if status == downloads_models.status_code.RATE_LIMITED:
        return "rate_limited"
    return "error"


def _validate_session(L: Instaloader) -> str:
    """显式区分网络错误与 cookie 失效。返回 status_code 字符串。

    通过公开的 test_login() 调用 T3 当前用户验证；真实响应必须与 Cookie
    用户 ID 一致，再核对配置用户名。T3 会保留异常分类，不把请求失败当成功。
    同时保留上游吞异常后的 error_log 分类，方便移除临时修复时维持行为。
    """
    try:
        error_log_start = len(getattr(L.context, "error_log", ()))
        diagnostics_risk._risk_api("session")
        actual_user = L.test_login()
    except LoginRequiredException:
        return downloads_models.status_code.AUTHORIZATION_REQUIRED
    except Exception as e:
        return downloads_errors.handle_error(e)
    _risk_note_context_errors(L.context, error_log_start)
    if actual_user is None:
        return _missing_test_login_user_status(L.context, error_log_start)
    if (
        downloads_runtime.runtime.login_username
        and actual_user.lower() != downloads_runtime.runtime.login_username.lower()
    ):
        return downloads_models.status_code.AUTHORIZATION_REQUIRED
    return downloads_models.status_code.SUCCESS


_REQUIRED_COOKIE_NAMES = ("sessionid", "ds_user_id", "csrftoken")


_IGNORED_COOKIE_NAMES = frozenset({"dpr", "wd", "ps_l", "ps_n"})


# One Netscape cookies.txt record, the default export of browser extensions
# such as Get cookies.txt LOCALLY: domain, subdomain flag, path, secure,
# expiry, name and value, separated by tabs. Saving the GUI field joins its
# lines with spaces, so records are matched one by one rather than per line.
_NETSCAPE_COOKIE_RECORD = re.compile(
    r"(\S+)\t(?:TRUE|FALSE)\t\S*\t(?:TRUE|FALSE)\t\d+\t(\S+)\t(\S*)"
)


def _parse_cookie_header(text: str) -> dict:
    """Parse 'name=val; name=val; ...' while preserving opaque values.

    Tolerates surrounding quotes on values, leading/trailing whitespace,
    and missing pairs. Skips browser-UI cookies that aren't IG auth state.
    Netscape cookies.txt text is accepted too, keeping only Instagram's
    records.
    """
    cookies = {}
    if not text:
        return cookies
    records = _NETSCAPE_COOKIE_RECORD.findall(text)
    if records:
        text = ";".join(
            f"{name}={value}"
            for domain, name, value in records
            if ("." + domain.removeprefix("#HttpOnly_").lstrip(".")).endswith(
                ".instagram.com"
            )
        )
    for pair in text.replace("\n", ";").split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        if not name or name in _IGNORED_COOKIE_NAMES:
            continue
        value = value.strip().strip('"').strip("'")
        cookies[name] = value
    return cookies


def _normalize_cookie_dict(cookies: dict | None) -> dict:
    """Normalize cookie dictionaries for local comparison only."""
    normalized = {}
    for raw_name, raw_value in (cookies or {}).items():
        name = str(raw_name).strip()
        if not name or name in _IGNORED_COOKIE_NAMES or raw_value is None:
            continue
        normalized[name] = str(raw_value).strip().strip('"').strip("'")
    return normalized


def _missing_required_cookies(cookies: dict | None) -> list[str]:
    return [name for name in _REQUIRED_COOKIE_NAMES if not (cookies or {}).get(name)]


def _load_saved_session_cookies(session_path: str):
    """Read the saved Instaloader session pickle without making network calls."""
    try:
        with open(session_path, "rb") as session_file:
            session_data = pickle.load(session_file)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    if not isinstance(session_data, dict):
        return None, f"unexpected session data type: {type(session_data).__name__}"
    return _normalize_cookie_dict(session_data), None


def _should_reseed_from_cookie_before_pickle_validation(session_path: str) -> bool:
    """Return True when current GUI cookie should supersede the saved pickle.

    This check is intentionally local-only. If the user pasted a different
    complete Cookie header, we avoid validating the stale saved session against
    Instagram and go straight to cookie seeding.
    """
    if (
        not downloads_runtime.runtime.session_cookie_text
        or not downloads_runtime.runtime.session_cookie_text.strip()
    ):
        return False

    current_cookies = _parse_cookie_header(
        downloads_runtime.runtime.session_cookie_text
    )
    missing = _missing_required_cookies(current_cookies)
    if missing:
        diagnostics_console.print(
            "Cookie field is incomplete; validating saved session first"
        )
        return False

    saved_cookies, error = _load_saved_session_cookies(session_path)
    if saved_cookies is None:
        diagnostics_console.print(
            f"Saved session could not be read locally ({error}); reseeding from cookie field"
        )
        return True

    if all(
        current_cookies.get(name) == saved_cookies.get(name)
        for name in _REQUIRED_COOKIE_NAMES
    ):
        return False

    diagnostics_console.print("Cookie authentication fields differ from saved session")
    diagnostics_console.print("Abandon old session")
    return True


def _seed_session_from_cookie_text(L: Instaloader, expected_username: str):
    """Inject parsed cookies, verify the authenticated user, save pickle.

    Returns (ok: bool, status: str, message: str). status is a status_code value.
    """
    if (
        not downloads_runtime.runtime.session_cookie_text
        or not downloads_runtime.runtime.session_cookie_text.strip()
    ):
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            "Cookie field is empty.",
        )

    cookies = _parse_cookie_header(downloads_runtime.runtime.session_cookie_text)
    missing = _missing_required_cookies(cookies)
    if missing:
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            f"Cookie is missing required values: {', '.join(missing)}",
        )

    session_username = (expected_username or "").strip()
    if not session_username:
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            "Username is required for cookie session.",
        )

    try:
        L.load_session(session_username, cookies)
    except Exception as e:
        return (
            False,
            downloads_models.status_code.FAILED,
            f"Failed to initialize session from cookies: {e}",
        )

    try:
        error_log_start = len(getattr(L.context, "error_log", ()))
        diagnostics_risk._risk_api("session")
        actual = L.test_login()
    except LoginRequiredException:
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            "Instagram could not verify this Cookie session.",
        )
    except (ConnectionException, AbortDownloadException) as e:
        status = downloads_errors.handle_error(e)
        reason = (
            "Cookie validation failed"
            if status == downloads_models.status_code.AUTHORIZATION_REQUIRED
            else "Cookie validation network error"
        )
        return False, status, f"{reason}: {e}"
    except Exception as e:
        return False, downloads_errors.handle_error(e), f"Cookie validation failed: {e}"

    _risk_note_context_errors(L.context, error_log_start)
    if not actual:
        status = _missing_test_login_user_status(L.context, error_log_start)
        if status == downloads_models.status_code.RATE_LIMITED:
            return False, status, "Instagram temporarily limited Cookie validation."
        if status == downloads_models.status_code.FAILED:
            return (
                False,
                status,
                "Cookie validation could not complete because Instagram or the network returned an error.",
            )
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            "Instagram could not verify this Cookie session.",
        )
    if actual.lower() != session_username.lower():
        return (
            False,
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            f"Cookie belongs to @{actual}, but the configured username is @{session_username}.",
        )

    os.makedirs(app_paths.SESSION_DIR, exist_ok=True)
    session_path = os.path.join(app_paths.SESSION_DIR, actual.lower())
    try:
        L.save_session_to_file(session_path)
    except Exception as e:
        return (
            False,
            downloads_models.status_code.FAILED,
            f"Failed to save session pickle: {e}",
        )
    return True, downloads_models.status_code.SUCCESS, f"Signed in as @{actual}"


class LoaderManager:
    """管理单一 Instaloader 实例的完整生命周期。

    设计原则（遵循 instaloader 官方 best practice）：
    - 整个程序生命周期只维护一个 Instaloader 实例
    - Session 只在首次使用或配置变更时验证
    - RateController 状态跨任务保留
    """

    def __init__(self):
        self._loader: Instaloader | None = None
        self._config_snapshot: dict | None = None
        self._is_validated: bool = False
        self._config_check_pending: bool = True

    @property
    def is_ready(self) -> bool:
        return self._loader is not None and self._is_validated

    def _take_config_snapshot(self) -> dict:
        """捕获影响 Instaloader 实例的配置项，用于 diff 检测。"""
        return {
            "username": (downloads_runtime.runtime.login_username or "")
            .strip()
            .lower(),
            "cookie": (downloads_runtime.runtime.session_cookie_text or "").strip(),
            "user_agent": _get_user_agent_override() or "",
            "iphone_support": downloads_runtime.runtime.iphone_support_enabled,
        }

    def _config_changed(self) -> bool:
        """检查配置是否与上次初始化时不同。"""
        if self._config_snapshot is None:
            return True
        return self._take_config_snapshot() != self._config_snapshot

    def get_loader(self) -> Instaloader | None:
        """获取可用的 Instaloader 实例。

        首次调用时创建并验证；后续调用直接返回缓存实例。
        配置变更时自动重新初始化。
        """
        if self.is_ready:
            if self._config_check_pending:
                if self._config_changed():
                    self._teardown()
                else:
                    self._config_check_pending = False
            if self.is_ready:
                diagnostics_risk._risk_session("loader_reused")
                return self._loader

        if self._loader is not None:
            self._teardown()

        return self._initialize()

    def _mark_ready(self, L: Instaloader):
        self._loader = L
        self._is_validated = True
        self._config_snapshot = self._take_config_snapshot()
        self._config_check_pending = False

    def _initialize(self) -> Instaloader | None:
        """创建、加载 session、验证。失败返回 None。

        持有 _api_lock 全程：内部 _validate_session / _seed_session_from_cookie_text
        都会发真实 HTTP（test_login → current_user），按文件头契约必须串行化。
        当前调用模式（GUI 串行 + 单 DownloadThread）下没有并发竞争，但保留锁
        防止未来调整时违反契约。
        """
        with downloads_runtime.runtime.api_lock:
            return self._initialize_locked()

    def _initialize_locked(self) -> Instaloader | None:

        if not downloads_runtime.runtime.login_username:
            diagnostics_risk._risk_session("not_configured")
            downloads_runtime.runtime.private_client_status = (
                downloads_models.status_code.AUTHORIZATION_REQUIRED
            )
            downloads_events.emit_api_status("auth_required")
            diagnostics_console.print("Login username not configured")
            return None

        downloads_control.wait_if_paused()
        session_path = os.path.join(
            app_paths.SESSION_DIR, downloads_runtime.runtime.login_username.lower()
        )
        downloads_runtime.runtime.private_client_status = (
            downloads_models.status_code.SUCCESS
        )
        validation_status = None

        # --- Path 1: existing pickle ---
        # If a complete Cookie header is present and differs from the saved
        # session file, skip old-session validation so no stale cookie is sent.
        skip_pickle_validation = os.path.exists(
            session_path
        ) and _should_reseed_from_cookie_before_pickle_validation(session_path)
        if os.path.exists(session_path) and not skip_pickle_validation:
            downloads_events.emit_api_status("client_init")
            L = init_base_loader()
            try:
                downloads_events.emit_api_status("login")
                L.load_session_from_file(
                    downloads_runtime.runtime.login_username, session_path
                )
                downloads_events.emit_api_status("session_validating")
                diagnostics_risk._risk_session("pickle_validating")
                result = _validate_session(L)
                if result == downloads_models.status_code.SUCCESS:
                    diagnostics_risk._risk_session("pickle_reused")
                    diagnostics_console.print("Session validated from pickle")
                    self._mark_ready(L)
                    return L
                diagnostics_risk._risk_session("pickle_rejected")
                validation_status = result
                diagnostics_console.print(f"Saved session validation failed ({result})")
            except Exception as e:
                diagnostics_risk._risk_session("pickle_rejected")
                diagnostics_console.print(
                    f"Failed to load pickle ({type(e).__name__}: {e}); will try cookie field"
                )
            try:
                L.close()
            except Exception:
                pass
            if validation_status is not None:
                downloads_runtime.runtime.private_client_status = validation_status
                downloads_events.emit_api_status(
                    _api_status_for_failure(validation_status)
                )
                if validation_status == downloads_models.status_code.RATE_LIMITED:
                    diagnostics_console.print(
                        "Instagram temporarily limited login validation; stopping this queue run"
                    )
                elif (
                    validation_status
                    == downloads_models.status_code.AUTHORIZATION_REQUIRED
                ):
                    diagnostics_console.print(
                        f"Existing session for @{downloads_runtime.runtime.login_username} is no longer "
                        "authorized; paste a fresh Cookie header."
                    )
                else:
                    diagnostics_console.print(
                        f"Existing session for @{downloads_runtime.runtime.login_username} could not be "
                        f"validated because Instagram or the network returned an "
                        f"error ({validation_status}); the saved session was kept, "
                        "so retry later."
                    )
                return None

        # --- Path 2: seed from cookie text ---
        if (
            not downloads_runtime.runtime.session_cookie_text
            or not downloads_runtime.runtime.session_cookie_text.strip()
        ):
            diagnostics_risk._risk_session("not_configured")
            downloads_runtime.runtime.private_client_status = (
                downloads_models.status_code.AUTHORIZATION_REQUIRED
            )
            downloads_events.emit_api_status("auth_required")
            diagnostics_console.print(
                f"No usable session for @{downloads_runtime.runtime.login_username}. "
                f"Paste the IG Cookie header into the Cookie field, save config, and retry."
            )
            return None

        downloads_events.emit_api_status("client_init")
        L = init_base_loader()
        downloads_events.emit_api_status("login")
        diagnostics_risk._risk_session("cookie_reseeding")
        ok, status, message = _seed_session_from_cookie_text(
            L, downloads_runtime.runtime.login_username
        )
        if not ok:
            diagnostics_risk._risk_session("cookie_reseed_failed")
            downloads_runtime.runtime.private_client_status = status
            downloads_events.emit_api_status(_api_status_for_failure(status))
            diagnostics_console.print(f"Cookie seeding failed: {message}")
            try:
                L.close()
            except Exception:
                pass
            return None

        diagnostics_risk._risk_session("cookie_reseeded")
        diagnostics_console.print("Cookie seeding succeeded")
        diagnostics_console.print(message)
        self._mark_ready(L)
        return L

    def invalidate(self):
        """标记当前实例需要检查配置是否已变更。"""
        self._config_check_pending = True

    def _teardown(self):
        """安全关闭旧实例。"""
        if self._loader is not None:
            try:
                self._loader.close()
            except Exception:
                pass
        self._loader = None
        self._is_validated = False
        self._config_snapshot = None
        self._config_check_pending = True

    def shutdown(self):
        """程序退出时调用。"""
        self._teardown()


_loader_manager = LoaderManager()


def get_loader() -> Instaloader | None:
    """获取可用的 Instaloader 实例（全局入口）。"""
    return _loader_manager.get_loader()


def shutdown_loader():
    """程序退出时调用，安全关闭 Instaloader 实例。"""
    _loader_manager.shutdown()
