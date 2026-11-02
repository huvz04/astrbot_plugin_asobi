import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .card import CARD_TEMPLATE, build_card, fallback_text
from .receiver import (
    ERROR_MESSAGES,
    PlatformNotFoundError,
    WebhookReceiver,
)


class AsobiWebhookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.receiver = None

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
        view = build_card(event)
        if view is not None:
            try:
                path = await asyncio.wait_for(
                    self.html_render(
                        CARD_TEMPLATE,
                        view,
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
                    timeout=30,
                )
                if not isinstance(path, str) or not path.strip():
                    raise ValueError("渲染结果缺少图片路径")
                return MessageChain().file_image(path)
            except Exception as exc:  # noqa: BLE001 - Rendering must not block text delivery.
                logger.warning(
                    f"ASOBI 图片渲染失败（{type(exc).__name__}），改为发送打码文字通知"
                )
        return MessageChain().message(fallback_text(event))

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
            "开奖时间与检测由 Asb7k-tools 后台监控负责。"
        )

    async def terminate(self):
        if self.receiver is not None:
            await self.receiver.close()
            self.receiver = None
