import asyncio
import re
from pathlib import Path

from aiohttp import ClientConnectionError
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .card import CARD_TEMPLATE, build_card, fallback_text
from .pagination import CardTooLargeError, paginate_card
from .receiver import (
    ERROR_MESSAGES,
    PlatformNotFoundError,
    WebhookReceiver,
)
from .scope import filter_today_event

RENDER_PAGE_TIMEOUT = 30
RENDER_TOTAL_TIMEOUT = 60


class InvalidRenderedImageError(ValueError):
    """The renderer returned an error page or an unusable image path."""


def _rendered_image_path(value):
    # Some AstrBot releases save HTTP error bodies as .jpg without checking the
    # response status. Do not let those files reach the messaging platform.
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise InvalidRenderedImageError
    if re.match(r"^[a-z][a-z0-9+.-]*://", str(value), re.IGNORECASE):
        raise InvalidRenderedImageError
    path = Path(value)
    with path.open("rb") as image:
        header = image.read(16)
        image.seek(0, 2)
        size = image.tell()
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR"
    is_jpeg = header.startswith(b"\xff\xd8\xff")
    is_gif = header[:6] in (b"GIF87a", b"GIF89a")
    is_webp = header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if size <= 16 or not (is_png or is_jpeg or is_gif or is_webp):
        raise InvalidRenderedImageError
    return str(path)


def _render_failure(exc):
    """Describe known failures without exposing URLs, account data or tokens."""
    if isinstance(exc, asyncio.TimeoutError):
        return "渲染超时（单页 30 秒、整组 60 秒），请检查 AstrBot 的 t2i 服务"
    if isinstance(exc, CardTooLargeError):
        return "结果超过图片分页大小限制，保留完整文字结果"
    if isinstance(exc, InvalidRenderedImageError):
        return "渲染服务未返回有效图片，请检查 AstrBot 的 t2i 服务"
    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return "渲染图片文件不存在或无法读取，请检查 AstrBot 临时目录"
    if isinstance(exc, ClientConnectionError):
        return "无法连接渲染服务，请检查 AstrBot 的 t2i 配置和网络"
    status = re.search(r"\bHTTP\s*([45]\d{2})\b", str(exc), re.IGNORECASE)
    if status:
        return f"渲染服务返回 HTTP {status[1]}，请检查 AstrBot 的 t2i 服务"
    if isinstance(exc, (TypeError, AttributeError)):
        return f"渲染接口调用失败（{type(exc).__name__}），请检查 AstrBot 版本兼容性"
    return f"渲染失败（{type(exc).__name__}），请检查 AstrBot 渲染日志"


class AsobiWebhookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.receiver = None
        self.render_status = "尚未渲染"

    async def initialize(self):
        data_dir = StarTools.get_data_dir("astrbot_plugin_asobi")
        receiver = WebhookReceiver(
            self.config,
            data_dir / "deliveries.sqlite3",
            self._send_message,
            logger,
            prepare_callback=self._prepare_message,
        )
        await receiver.start()
        self.receiver = receiver
        settings = receiver.settings
        logger.info(
            f"ASOBI Webhook 已监听 {settings['host']}:{settings['port']}{settings['path']}，"
            f"收件人 {len(settings['recipients'])} 个"
        )

    async def _prepare_message(self, event):
        event = filter_today_event(event)
        if event is None:
            logger.info("ASOBI 通知没有今天已开奖的结果，已跳过")
            return None
        view = build_card(event)
        if view is not None:
            try:
                pages = paginate_card(view)
                message = await asyncio.wait_for(
                    self._render_pages(pages), timeout=RENDER_TOTAL_TIMEOUT
                )
                self.render_status = (
                    "最近一次渲染成功"
                    if len(pages) == 1
                    else f"最近一次渲染成功，共 {len(pages)} 张"
                )
                return message
            except Exception as exc:  # noqa: BLE001 - Rendering must not block text delivery.
                self.render_status = _render_failure(exc) + "；已改为打码文字通知"
                logger.warning(f"ASOBI 图片渲染状态：{self.render_status}")
        return MessageChain().message(fallback_text(event))

    async def _render_pages(self, pages):
        paths = []
        for page in pages:
            path = await asyncio.wait_for(
                self.html_render(
                    CARD_TEMPLATE,
                    page,
                    return_url=False,
                    options={
                        "type": "png",
                        "quality": None,
                        "full_page": True,
                        "viewport_width": 840,
                        "viewport_height": 1,
                        "scale": "css",
                    },
                ),
                timeout=RENDER_PAGE_TIMEOUT,
            )
            paths.append(_rendered_image_path(path))
        # Prepare every page before any recipient is sent a message. A failure
        # on a later page must not send half the result followed by a full text.
        message = MessageChain()
        for path in paths:
            message.file_image(path)
        return message

    async def _send_message(self, recipient, message):
        if isinstance(message, str):
            message = MessageChain().message(message)
        found = await self.context.send_message(recipient, message)
        if not found:
            raise PlatformNotFoundError("找不到收件人对应的平台")

    @filter.command("asobi会话")
    async def asobi_session(self, event: AstrMessageEvent):
        """获取当前会话的通知目标 ID，供管理员填写插件收件人。"""
        yield event.plain_result(
            f"当前会话 UMO：\n{event.unified_msg_origin}\n"
            "将完整 UMO 添加到插件配置的 recipients 并重载插件后生效。"
            "此指令不会自动订阅。"
        )

    @filter.command("asobi状态")
    async def asobi_status(self, event: AstrMessageEvent):
        """查看 Webhook 监听状态和收件人数。"""
        if self.receiver is None:
            yield event.plain_result("ASOBI Webhook 未监听，请检查插件日志并重新加载。")
            return
        settings = self.receiver.settings
        queue = self.receiver.queue_status()
        error = ERROR_MESSAGES.get(queue["last_error"], "无")
        warnings = "\n".join(self.receiver.config_warnings) or "无"
        yield event.plain_result(
            f"ASOBI Webhook 正在监听：\n"
            f"{settings['host']}:{settings['port']}{settings['path']}\n"
            f"收件人：{len(settings['recipients'])} 个\n"
            f"配置提醒：{warnings}\n"
            f"鉴权：{'已启用' if settings['token'] else '未启用'}\n"
            f"待发通知：{queue['pending']} 条\n"
            f"队列状态：{error}\n"
            f"图片状态：{self.render_status}\n"
            "开奖时间与检测由 Asb7k-tools 后台监控负责。"
        )

    async def terminate(self):
        if self.receiver is not None:
            await self.receiver.close()
            self.receiver = None
