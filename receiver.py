"""Receive the monitor's JSON events and deliver them to configured sessions."""

import asyncio
import hmac
import json
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

from aiohttp import web

ERROR_MESSAGES = {
    "receiver_unavailable": "插件未就绪，请等待重载完成后重试。",
    "platform_not_found": "找不到收件会话对应的平台，请复制完整 UMO 并检查平台连接。",
    "delivery_timeout": "消息发送超时，请检查平台连接及 AstrBot 日志。",
    "storage_failed": "发送记录保存失败，请检查插件数据库及 AstrBot 日志。",
    "delivery_failed": "消息发送失败，请查看 AstrBot 日志。",
}


class PlatformNotFoundError(RuntimeError):
    pass


def _failure_response(code):
    return web.json_response(
        {"ok": False, "error": code, "message": ERROR_MESSAGES[code]}, status=503
    )


def validate_settings(config: dict, *, warnings: list | None = None) -> dict:
    if not isinstance(config, dict):
        raise ValueError("插件配置必须是 JSON 对象")  # noqa: TRY004
    settings = {
        "host": "127.0.0.1",
        "port": 8766,
        "path": "/asobi/webhook",
        "token": "",
        "recipients": [],
    }
    settings.update({key: config[key] for key in settings if key in config})
    host, port, path, token, recipients = (
        settings[key] for key in ("host", "port", "path", "token", "recipients")
    )
    if not isinstance(host, str) or not host or any(c.isspace() for c in host):
        raise ValueError("host 必须是非空监听地址")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port 必须是 1 到 65535 的整数")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path == "/"
        or any(c.isspace() or c in "?#{}" for c in path)
    ):
        raise ValueError("path 必须是以 / 开头的固定路径，不能包含空白、?#{}")
    if not isinstance(token, str) or (
        token and (len(token) < 16 or any(not 33 <= ord(c) <= 126 for c in token))
    ):
        raise ValueError("token 必须至少 16 位，仅包含无空白的可见 ASCII 字符")
    if warnings is None:
        warnings = []
    if not isinstance(recipients, list):
        warnings.append(
            "recipients 必须是完整 UMO 的列表，已忽略此配置；"
            "请在目标会话发送 /asobi会话 获取 UMO，逐项添加后保存并重载。"
        )
        recipients = []
    valid_recipients = []
    for index, recipient in enumerate(recipients, start=1):
        if isinstance(recipient, str):
            recipient = recipient.strip()
        parts = recipient.split(":", 2) if isinstance(recipient, str) else []
        if (
            len(parts) != 3
            or not parts[0]
            or parts[1] not in {"FriendMessage", "GroupMessage"}
            or not parts[2]
            or any(c.isspace() for c in recipient)
        ):
            warnings.append(
                f"recipients 第 {index} 项不是有效的完整 UMO，已跳过；"
                "请在目标会话发送 /asobi会话 获取 UMO，替换后保存并重载。"
            )
            continue
        valid_recipients.append(recipient)
    settings["recipients"] = list(dict.fromkeys(valid_recipients))
    return settings


def _validate_event(event):
    if not isinstance(event, dict):
        raise TypeError
    for key in ("id", "title", "message"):
        if not isinstance(event.get(key), str) or not event[key].strip():
            raise ValueError
    if len(event["id"]) > 256 or event.get("type") not in (
        "won",
        "result",
        "error",
        "test",
    ):
        raise ValueError
    if not isinstance(event.get("account"), str):
        raise TypeError
    if event["type"] in ("won", "error") and not event["account"].strip():
        raise ValueError


class WebhookReceiver:
    def __init__(
        self, settings, db_path, send_callback, logger, *, prepare_callback=None
    ):
        self.config_warnings = []
        self.settings = validate_settings(settings, warnings=self.config_warnings)
        self.send_callback = send_callback
        self.prepare_callback = prepare_callback
        self.logger = logger
        for warning in self.config_warnings:
            self.logger.warning(f"ASOBI 配置提醒：{warning}")
        self.delivery_timeout = 10.0
        self.retry_interval = 60.0
        self._lock = asyncio.Lock()
        self._wakeup = asyncio.Event()
        self._worker_task = None
        self._worker_error = None
        self._stopping = False
        self._runner = None
        self._db = None
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS delivered ("
                    "event_id TEXT NOT NULL, recipient TEXT NOT NULL, "
                    "PRIMARY KEY (event_id, recipient))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS inbox ("
                    "event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, recipients TEXT, "
                    "completed INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT, "
                    "accepted_at REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS inbox_due ON inbox (completed, next_attempt)"
                )
                # Older versions deferred recipient assignment. Do not replay
                # those notifications to users who bind after receipt.
                connection.execute(
                    "UPDATE inbox SET recipients = '[]', completed = 1, "
                    "next_attempt = 0, last_error = NULL WHERE recipients IS NULL"
                )
        except Exception:
            connection.close()
            raise
        self._db = connection
        self.app = web.Application(client_max_size=1024 * 1024)
        self.app.router.add_get(self.settings["path"], self.connection_status)
        self.app.router.add_post(self.settings["path"], self.handle)
        self.app.on_startup.append(self._start_worker)
        self.app.on_shutdown.append(self._stop_worker)
        self.app.on_cleanup.append(self._cleanup_database)

    async def start(self):
        runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=12)
        self._runner = runner
        try:
            await runner.setup()
            await web.TCPSite(
                runner, self.settings["host"], self.settings["port"]
            ).start()
        except BaseException:
            await self.close()
            raise

    async def _cleanup_database(self, app):
        await self._stop_worker(app)
        if self._db is not None:
            self._db.close()
            self._db = None

    async def close(self):
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            finally:
                self._runner = None
        await self._cleanup_database(self.app)

    async def connection_status(self, request):
        return web.Response(
            text=(
                "ASOBI Webhook HTTP 入口可访问。\n"
                "接收通知需使用 POST 请求和 JSON 正文。\n"
                "Asb7k-tools 的测试通知用于验证接收；QQ 投递状态请查看 /asobi状态。\n"
            ),
            content_type="text/plain",
        )

    async def handle(self, request):
        if self.settings["token"]:
            expected = f"Bearer {self.settings['token']}".encode("ascii")
            received = request.headers.get("Authorization", "").encode(
                "utf-8", errors="replace"
            )
            if not hmac.compare_digest(received, expected):
                return web.json_response(
                    {"ok": False, "error": "unauthorized"}, status=401
                )
        try:
            event = await request.json()
            _validate_event(event)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return web.json_response(
                {"ok": False, "error": "invalid_event"}, status=400
            )
        if self._db is None or self._stopping:
            return _failure_response("receiver_unavailable")
        recipients = self.settings["recipients"]
        try:
            # Acknowledgement means durable receipt, independent of IM delivery.
            with self._db:
                self._db.execute(
                    "INSERT OR IGNORE INTO inbox "
                    "(event_id, payload, recipients, completed, accepted_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event["id"],
                        json.dumps(event, ensure_ascii=False),
                        json.dumps(recipients),
                        int(not recipients),
                        time.time(),
                    ),
                )
        except (sqlite3.Error, UnicodeError):
            self.logger.error("ASOBI 通知未能写入接收队列，请稍后重新投递")
            return _failure_response("storage_failed")
        self._wakeup.set()
        return web.json_response(
            {"ok": True, "status": "accepted", "id": event["id"]}, status=202
        )

    def queue_status(self):
        if self._db is None:
            return {"pending": 0, "last_error": "receiver_unavailable"}
        try:
            pending = self._db.execute(
                "SELECT COUNT(*) FROM inbox WHERE completed = 0"
            ).fetchone()[0]
            error = self._db.execute(
                "SELECT last_error FROM inbox "
                "WHERE completed = 0 AND last_error IS NOT NULL "
                "ORDER BY next_attempt DESC, accepted_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            return {"pending": 0, "last_error": "storage_failed"}
        return {
            "pending": pending,
            "last_error": self._worker_error or (error[0] if error else None),
        }

    async def _start_worker(self, app):
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="asobi-webhook-delivery"
        )

    async def _stop_worker(self, app):
        self._stopping = True
        if self._worker_task is not None:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

    def _next_delay(self):
        due = self._db.execute(
            "SELECT MIN(next_attempt) FROM inbox WHERE completed = 0"
        ).fetchone()[0]
        return max(0, due - time.time()) if due is not None else None

    async def _worker_loop(self):
        while not self._stopping:
            self._wakeup.clear()
            try:
                await self.process_pending()
                delay = self._next_delay()
                self._worker_error = None
            except Exception as exc:  # noqa: BLE001 - Keep the durable queue alive after worker errors.
                self._worker_error = (
                    "storage_failed"
                    if isinstance(exc, sqlite3.Error)
                    else "delivery_failed"
                )
                self.logger.error(
                    f"ASOBI 队列处理失败（{type(exc).__name__}），稍后自动重试"
                )
                delay = self.retry_interval
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def process_pending(self):
        """Attempt due deliveries; failures remain in AstrBot's persistent queue."""
        async with self._lock:
            rows = self._db.execute(
                "SELECT event_id, payload, recipients FROM inbox "
                "WHERE completed = 0 AND next_attempt <= ? "
                "ORDER BY accepted_at, event_id LIMIT 100",
                (time.time(),),
            ).fetchall()
            for event_id, payload, targets in rows:
                failure = await self._deliver(json.loads(payload), json.loads(targets))
                with self._db:
                    self._db.execute(
                        "UPDATE inbox SET completed = ?, next_attempt = ?, last_error = ? "
                        "WHERE event_id = ?",
                        (
                            int(failure is None),
                            time.time() + self.retry_interval if failure else 0,
                            failure,
                            event_id,
                        ),
                    )

    async def _deliver(self, event, recipients):
        pending = [
            (index, recipient)
            for index, recipient in enumerate(recipients)
            if not self._db.execute(
                "SELECT 1 FROM delivered WHERE event_id = ? AND recipient = ?",
                (event["id"], recipient),
            ).fetchone()
        ]
        if not pending:
            return None
        # Prepare once per attempt, outside each recipient's send timeout.
        message = (
            await self.prepare_callback(event)
            if self.prepare_callback is not None
            else f"{event['title']}\n\n{event['message']}"
        )
        if message is None:
            # Filtering may expire a queued result. Complete it without sending
            # or replaying it if recipients are added later.
            return None
        failure = None
        for index, recipient in pending:
            try:
                await asyncio.wait_for(
                    self.send_callback(recipient, message),
                    timeout=self.delivery_timeout,
                )
                # Commit each successful target before attempting the next one.
                with self._db:
                    self._db.execute(
                        "INSERT INTO delivered (event_id, recipient) VALUES (?, ?)",
                        (event["id"], recipient),
                    )
            except Exception as exc:  # noqa: BLE001 - Platform adapters may raise arbitrary exceptions.
                self.logger.error(
                    f"ASOBI 第 {index + 1} 个收件人发送失败（{type(exc).__name__}）"
                )
                if isinstance(exc, asyncio.TimeoutError):
                    code = "delivery_timeout"
                elif isinstance(exc, PlatformNotFoundError):
                    code = "platform_not_found"
                elif isinstance(exc, sqlite3.Error):
                    code = "storage_failed"
                else:
                    code = "delivery_failed"
                failure = failure or code
        return failure
