import ipaddress
import os as _os
from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Skill Agent Loop Service"
    database_url: str = "sqlite:///./skill_agent_loop.db"
    app_secret: str = "change-me-in-development"
    demo_model_base_url: str = "http://localhost:52010/v1"
    demo_model_name: str = "qwen3.6-27b"
    demo_model_api_key: str = ""
    model_api_timeout_seconds: float = 600.0
    model_thinking_mode: str = ""
    model_thinking_models: str = ""
    tool_timeout_seconds: float = 8.0
    external_task_poll_seconds: float = 2.0
    external_task_callback_base_url: str = ""
    a2a_task_timeout_seconds: float = 600.0
    a2a_poll_interval_seconds: float = 0.5
    codex_a2a_enabled: bool = False
    codex_a2a_command: str = "codex"
    codex_a2a_workspace_root: str = ""
    codex_a2a_timeout_seconds: float = 1800.0
    codex_a2a_token: str = ""
    # 飞书官方 lark-cli 集成（app/lark_cli/）。开启即进入能力清单；应用凭据
    # 与用户登录都在对话中完成（config init / auth login 设备码），无需预置
    # 配置或飞书渠道绑定。
    lark_cli_enabled: bool = True
    # 二进制供给见 lark_cli/provision.py：懒加载（首次调用时装，非启动时），
    # 装到用户数据目录。关掉自动安装则必须自备二进制并指定 binary_path。
    lark_cli_auto_install: bool = True
    lark_cli_binary_path: str = ""
    # 企微 OA 审批代发（app/wecom_oa/）。开启后进入能力清单；凭证与 bot 绑定分离。
    wecom_oa_enabled: bool = True
    tool_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    general_skill_runtime_python: str = ""
    general_skill_runtime_venv: str = ""
    general_skill_runtime_packages: str = "requests,httpx"
    # Keep runtime dependency installation enabled so published skills can
    # provision their declared baseline libraries on first use. Deployments
    # can still disable it explicitly for locked-down environments.
    general_skill_runtime_auto_install: bool = True
    general_skill_pip_index_url: str = ""
    general_skill_pip_timeout_seconds: int = 180
    general_skill_network_install: bool = True
    channel_secret: str = ""
    staffdeck_role: str = "all"
    wechat_ilink_base_url: str = "https://ilinkai.weixin.qq.com"
    channel_delivery_poll_seconds: float = 1.0
    channel_delivery_max_attempts: int = 8
    # 渠道文件下载链接必须使用显式公网基地址，不再回退 tool_base_url。
    public_base_url: str = ""
    # 仅本地开发允许 http://localhost 的下载链接；生产默认关闭。
    allow_local_artifact_links: bool = False
    # 聊天附件暂存目录；为空时使用用户数据目录下的 harness_uploads。
    harness_uploads_path: str = ""
    # 第一期开放写入的渠道白名单，逗号分隔；空字符串表示关闭。
    knowledge_open_write_channels: str = ""
    public_api_enabled: bool = True
    public_api_key_pepper: str = ""
    public_api_idempotency_ttl_seconds: int = 60 * 60 * 24
    public_api_retention_days: int = 30
    public_api_webhook_timeout_seconds: float = 10.0
    public_api_webhook_max_attempts: int = 6
    # 钉钉 emotion 接口的表情常量与所需权限尚未真机验证，验证通过前默认关闭：
    # 否则常量失效或权限未开时，每条入站消息都会留下一条失败的 reaction 投递。
    channel_dingtalk_reaction_enabled: bool = False
    # 出站富文本渲染开关：开启时飞书走 post 富文本、钉钉走 markdown 消息；
    # 关闭时两者回退为纯 text 消息，用于快速回退。
    channel_rich_render_enabled: bool = True
    # 飞书渠道实时执行步骤卡片开关：开启后飞书对话在执行过程中创建并实时更新
    # 一张独立卡片展示智能体每一步（SOP/工具/知识检索），与正文回复互不影响。
    # 仅影响飞书渠道；关闭时退化为仅发最终回复。
    channel_feishu_trace_enabled: bool = True
    # 飞书 trace 卡片 SOP 紧凑展示开关：开启后匹配 SOP（判断意图/进入流程）之后的
    # 中间步骤不再逐行展示，仅显示"翻书动画 + 正在推进SOP"，等待用户补充信息时
    # 定格为"📖 流程已暂停"，SOP 结束时定格为"✅ 流程已结束"。设为 False 可整体
    # 回滚为逐行展示的旧样式；binding 的 config_json.compact_trace=false 可对单个
    # 绑定回滚。
    channel_feishu_trace_compact_sop: bool = True

    model_config = SettingsConfigDict(
        env_file=_os.environ.get("ULTRARAG_DOTENV", ".env"),
        env_file_encoding="utf-8", extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def normalized_tool_base_url(self) -> str:
        return self.tool_base_url.rstrip("/")

    @property
    def knowledge_open_write_channel_set(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.knowledge_open_write_channels.split(",")
            if item.strip()
        }

    @property
    def normalized_public_base_url(self) -> str:
        raw = self.public_base_url.strip().rstrip("/")
        if not raw:
            return ""
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.strip().lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            return raw if self.allow_local_artifact_links else ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return raw
        if address.is_loopback or address.is_private or address.is_link_local:
            return raw if self.allow_local_artifact_links else ""
        return raw

    @property
    def general_skill_runtime_package_list(self) -> list[str]:
        return [item.strip() for item in self.general_skill_runtime_packages.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
