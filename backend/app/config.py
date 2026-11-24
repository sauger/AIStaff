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
    model_max_retries: int = 0
    # Agent 执行层预算（见 docs/design/agent-execution-redesign-plan.md）。
    # react 为默认执行模式；compiled 尚未启用，切换前必须保持 react。
    agent_execution_mode: str = "react"
    agent_turn_budget_seconds: float = 120.0
    agent_llm_call_timeout_seconds: float = 45.0
    agent_plan_max_nodes: int = 12
    agent_plan_parallel: int = 1
    agent_plan_node_timeout_seconds: float = 30.0
    agent_max_replans: int = 2
    agent_tools_max_description_chars: int = 300
    # 动作协议：tool_calls=原生工具调用（供应商不支持时自动回落 json），json=旧协议。
    agent_action_protocol: str = "json"
    agent_native_tool_surface: str = "full"
    agent_tools_schema_budget_chars: int = 2000
    agent_shadow_sample_rate: float = 0.1
    # 岗位运行时（workflow_v1）总开关。默认关闭：只有同时满足本开关与
    # SkillCard.metadata.workflow.runtime=="workflow_v1" 的 SOP 才走声明式节点
    # 执行，排障时可一键回退到旧执行路径。
    workflow_runtime_enabled: bool = False
    # 部署档位：dev=本地开发，保留默认种子口令与旧执行路径回退；
    # demo=客户演示，启动时校验演示所依赖的配置，样例数据只允许写入演示空间；
    # prod=生产，关键配置缺失时启动直接失败，不允许静默降级。
    deployment_profile: str = "dev"
    # 非 dev 档的种子口令。留空时 demo/prod 会生成一次性随机口令并写入
    # user_data_dir()/demo_credentials.txt，避免把 admin/admin 带到演示环境。
    demo_admin_password: str = ""
    demo_user_password: str = ""
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
    # Honcho sidecar bootstrap. These env values apply until an admin saves
    # 运行设置 for the tenant. ui_configs.honcho_enabled, honcho_base_url, and
    # honcho_timeout_ms then override this process. HONCHO_API_KEY and
    # HONCHO_WORKSPACE_PREFIX stay env-only (no secret field on that page;
    # the prefix is the Honcho identity namespace).
    # HONCHO_SIDECAR_ENV_FILE is an absolute path to the Honcho process .env.
    # When set, saving deriver/embedding settings rewrites that file. The
    # deriver and embedding API keys are Honcho's own keys, not this app's.
    honcho_enabled: bool = False
    honcho_base_url: str = ""
    honcho_api_key: str = ""
    honcho_timeout_ms: int = 200
    honcho_workspace_prefix: str = "tenant:"
    honcho_sidecar_env_file: str = ""

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

    @property
    def normalized_deployment_profile(self) -> str:
        return (self.deployment_profile or "dev").strip().lower()


DEPLOYMENT_PROFILES = frozenset({"dev", "demo", "prod"})


def runtime_profile_problems(settings: Settings) -> list[str]:
    """Return human-readable reasons the current profile cannot run safely.

    ``dev`` only reports an unknown profile value: local development keeps the
    historical defaults (seed accounts, legacy execution fallback) on purpose.
    ``demo`` and ``prod`` must fail fast instead of silently degrading the SOP
    runtime, because a degraded demo still answers — just not through the
    workflow the customer is being shown.
    """

    profile = settings.normalized_deployment_profile
    if profile not in DEPLOYMENT_PROFILES:
        return [
            f"DEPLOYMENT_PROFILE={settings.deployment_profile!r} 无效，"
            f"只接受 {'/'.join(sorted(DEPLOYMENT_PROFILES))}"
        ]

    problems: list[str] = []
    if profile in {"demo", "prod"} and not settings.workflow_runtime_enabled:
        problems.append(
            "必须设置 WORKFLOW_RUNTIME_ENABLED=true："
            "关闭时声明式 SOP 会静默回退到自由规划，演示/生产看到的不是同一条执行路径"
        )
    if profile == "prod":
        if settings.app_secret == "change-me-in-development":
            problems.append("必须设置非默认 APP_SECRET")
        elif len(settings.app_secret) < 16:
            problems.append("APP_SECRET 长度必须 >= 16")
        if settings.allow_local_artifact_links:
            problems.append("必须关闭 ALLOW_LOCAL_ARTIFACT_LINKS")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
