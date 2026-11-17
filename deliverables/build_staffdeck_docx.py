# -*- coding: utf-8 -*-
"""Build the StaffDeck project introduction DOCX (customer + technical)."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parent / "StaffDeck_数字员工平台项目介绍.docx"

BODY_FONT = "宋体"
HEAD_FONT = "黑体"
LATIN_FONT = "Times New Roman"
INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x5A, 0x5A, 0x5F)
ACCENT = RGBColor(0x1F, 0x3A, 0x5F)

CUSTOMER: list[tuple[str, str]] = [
    ("h2", "一、项目简介"),
    ("p", "StaffDeck 是面向企业的数字员工构建与管理平台。平台把岗位职责、业务知识和处理流程整理成可配置、可复用的数字员工：设定岗位与工作方式，绑定知识资料、标准流程和业务接口，再通过网页工作台或企业微信等渠道对外服务。工作过程会被记录，流程中需要人判断的环节可转交指定人员。平台以 AGPL 3.0 开源，支持本地部署与桌面安装。"),

    ("h2", "二、企业常见的业务问题"),
    ("p", "企业引入数字员工，通常不是为了多一个聊天入口，而是几类问题反复出现：经验跟着人走，判断方法和例外处理留在老员工手里，人员变动后接替者要重新摸索；服务质量随人波动，同类请求的口径不一致，制度写了却没有进入日常；知识存得下、用不上，一线仍要翻找，也难判断答案出自哪份文件；自动化不敢放手，缺少“谁做了什么、依据是什么”的记录，业务部门不愿交出真实操作。"),

    ("h2", "三、StaffDeck 的解决思路"),
    ("h3", "（一）把岗位写成数字员工"),
    ("p", "岗位名称、职责边界、服务风格与访问范围都作为配置保存，数字员工因此有明确身份和权限。"),
    ("h3", "（二）把流程写成可执行的 SOP"),
    ("p", "SOP 由节点和连线组成，每个节点说明收集什么信息、能查哪些知识、可调用哪些工具、满足什么条件进入下一步。它可由自然语言生成草稿，再经编辑、校验与发布；发布后才参与接待，并保留版本记录。"),
    ("h3", "（三）把知识按文档结构管理"),
    ("p", "文档入库时被解析、分桶并建立索引；回答时先定位章节再取原文，回复可附来源，业务人员能核对答案出自哪份文件。"),
    ("h3", "（四）在授权范围内执行并留痕"),
    ("p", "数字员工只能使用被授予的知识、流程和工具，每次检索、调用与流程推进都记录在会话中。"),

    ("h2", "四、一个典型场景：售后换货"),
    ("p", "以下用仓库自带的演示流程说明，属演示数据，不是真实客户案例。"),
    ("p", "客户在企业微信里说：“上周买的鞋尺码不对，想换一双。”数字员工识别出换货场景，进入已发布的换货 SOP：先请客户提供订单号，再调用订单查询接口核对商品与购买时间；符合规则时，告知可以换货及下一步。"),
    ("p", "如果订单异常或客户提出规则外要求，流程走到预设的转人工节点：系统生成待处理事项，附上会话摘要与待答问题，交给指定处理人；处理人在工作台或飞书回复后，流程带着答复继续。"),
    ("p", "每一步都有记录，事后可以查清判断依据，以及哪一步由系统完成、哪一步由人完成。"),

    ("h2", "五、能力如何支撑业务价值"),
    ("h3", "（一）流程可以复用"),
    ("p", "打磨好的 SOP 可复制给其他数字员工，或发布到广场供其他部门取用；修改留在私有分支，不影响原件。"),
    ("h3", "（二）知识可以沉淀"),
    ("p", "知识与岗位绑定，而不是留在个人电脑里；反馈会被归类，“知识缺口”可指向需补充的资料。"),
    ("h3", "（三）过程可以追踪"),
    ("p", "会话、运行记录与事件日志构成完整记录，出现争议时可还原请求、检索内容、调用接口与答复。"),

    ("h2", "六、适用场景与试点建议"),
    ("p", "较适合：售后与客户服务、内部制度与 IT/HR 服务台、销售与渠道支持、按固定节奏执行的运营任务，以及需跨系统汇总后再回复的岗位。"),
    ("p", "试点可按以下顺序推进。"),
    ("p", "1. 选一条边界清晰、结果容易核对的流程，由负责人整理现有文档与例外情况作为输入。"),
    ("p", "2. 明确谁在什么情况下接手，把人工节点写进 SOP。"),
    ("p", "3. 先在工作台内部试用，确认后再接入一个即时通信渠道。"),
    ("p", "4. 用真实请求回看记录，按反馈调整流程与知识，再考虑扩大范围。"),

    ("h2", "七、小结"),
    ("p", "StaffDeck 的价值不在于让模型多回答几个问题，而在于把“岗位怎么干”变成可配置、可发布、可追溯的组织资产。它承接可标准化的部分，在需要判断和承担责任的地方把人请回来。"),
]

TECHNICAL: list[tuple[str, str]] = [
    ("h2", "一、项目定位与系统边界"),
    ("p", "StaffDeck 是单体架构的企业数字员工平台：一个 FastAPI 进程同时提供 Web 控制台、REST API 与 Swagger（默认 5173 端口），前端为 React 单页应用，另有桌面安装包。对话运行时为 Harness v2。"),
    ("p", "边界先说清楚：平台不训练模型，通过 OpenAI 兼容接口调用外部模型；不是微服务，仓库没有容器化配置，部署以源码单端口运行或桌面安装包为主；数据默认存于本地 SQLite，多租户通过 tenant_id 贯穿业务表。"),
    ("p", "版本与命名不一致，需项目方确认：代码版本 0.1.0，git tag 0.1.17，英文 README 为 v0.5.0、中文为 v0.4.0；运行时代码中仍有 RapidStaff 品牌残留。"),

    ("h2", "二、技术栈与总体架构"),
    ("p", "后端使用 Python 3.11+、FastAPI、SQLModel、SQLite 与 uvicorn；文档解析依赖 PyMuPDF、pypdf、python-docx 与 BeautifulSoup；渠道侧依赖 lark-channel-sdk、wecom-aibot-python-sdk 与 dingtalk-stream；加密使用 cryptography 的 Fernet。前端为 React 18、TypeScript、Vite、Tailwind 4 与 ECharts。"),
    ("p", "系统可分四层：接入层处理 Web 请求、开放 API、IM 渠道回调与定时任务触发；运行时层由 HarnessV2Engine 编排单次 turn，TurnPlanner 判断意图与 SOP，AgentLoop 维护步骤状态，TaskFrame 保存跨轮次任务；能力层包括知识检索、SOP、通用技能、HTTP/A2A/MCP 工具、记忆与大屏渲染；数据层保存会话、消息、事件、运行记录与配置。"),

    ("h2", "三、一次典型请求的执行链路"),
    ("p", "1. 入口与幂等。请求先认领 turn，重复请求直接回放；写用户消息、记事件，并取会话锁与租约，保证同会话串行。"),
    ("p", "2. 上下文。注入用户记忆、上下文投影与附件材料，必要时注入 honcho 上下文。"),
    ("p", "3. 意图选择。TurnPlanner 结合消息、已发布 SOP、会话状态与记忆，判断直接回答还是进入某条 SOP。"),
    ("p", "4. 能力装配。按节点构建完整能力清单，全量留在服务端鉴权，只把裁剪投影交给模型；未声明的能力不可见。"),
    ("p", "5. 任务编译。编译器生成需求，含槽位、当前步骤、历史结果、附件、时区与说话人，写入 TaskFrame。"),
    ("p", "6. 执行循环。模型在最大动作数（默认 32）内选择查知识、调工具、追问、推进或回复；工具结果经反射决定重试、换工具或停止。"),
    ("p", "7. 图推进。GraphRules 按条件与边优先级计算下一步并更新当前步骤；结束或转人工时写入状态。"),
    ("p", "8. 输出。回复经组装并附引用，写入消息表，经 SSE 推送；运行记录、轮次记录与事件日志留下轨迹。"),

    ("h2", "四、核心模块及职责"),
    ("h3", "（一）数字员工配置"),
    ("p", "AgentProfile 保存名称、描述、人格设定、负责人、状态与最大动作数；身份提示从 metadata 读取岗位、部门、团队与工作方式。员工拥有知识、SOP、工具的私有分支，广场资源以复制方式引用。"),
    ("h3", "（二）SOP"),
    ("p", "Skill 以节点与连线保存流程图，节点类型包括信息收集、判断、工具调用、知识查询、回复、转人工与子流程；节点可声明可用动作、知识范围、能力引用、重试策略与处理人。SOP 支持生成草稿、校验、发布、版本对比与回滚，未发布不参与意图匹配。"),
    ("h3", "（三）知识检索"),
    ("p", "入库阶段解析文档、分桶、切块并生成概念页；检索阶段先做概念匹配，再由模型选择候选文档（失败时回退词法打分），最后在桶内取原文块，返回引用与路由轨迹。"),
    ("h3", "（四）工具调用"),
    ("p", "支持 HTTP、A2A 与 MCP。HTTP 工具做占位符替换，密钥从员工密钥注入；MCP 支持 stdio 子进程与 streamable HTTP，可发现并同步工具清单；调用受超时策略约束。"),
    ("h3", "（五）会话与运行记录"),
    ("p", "ChatSession 记录当前 SOP 与步骤；AgentEvent 记录各阶段事件；运行与轮次记录保存执行细节；Trace 按会话查询，开放 API 的轨迹含意图、能力选择、工具结果与引用，不含原始思维链。"),
    ("h3", "（六）反馈与记忆"),
    ("p", "反馈按模型、SOP 触发、槽位、流转、知识缺口、工具等类别归因；记忆只有画像、偏好、事实三类，按用户与员工注入，不是向量检索。"),

    ("h2", "五、对接与扩展方式"),
    ("p", "（一）开放 API v1 使用 Bearer 密钥，租户与员工边界由凭证推导；覆盖会话、SSE 流式运行、异步 Job，以及 SOP、知识、工具、MCP 与定时任务管理。通用协议包括幂等键、ETag 与 If-Match、problem+json 错误与 Webhook 签名。"),
    ("p", "（二）MCP 按服务器配置 stdio 或 HTTP，发现后同步工具清单；响应中的认证头与环境变量会被掩码。"),
    ("p", "（三）模型服务通过 OpenAI 兼容接口配置地址、模型与密钥，超时默认 600 秒；依赖中另有 Anthropic SDK，实际路由需确认。"),
    ("p", "（四）微信、企业微信智能机器人、微信客服、飞书、钉钉均有适配器，统一入站、出站与身份绑定。"),
    ("p", "（五）定时任务支持单次、每日、每周、每月与 RRULE，时区默认 Asia/Shanghai，含并发与错过补偿策略，由常驻进程或独立 worker 轮询。"),

    ("h2", "六、权限、安全、隔离与部署"),
    ("p", "认证采用本地账号，密码以 pbkdf2_sha256 加盐存储，令牌为 HMAC 签名、有效期 14 天；租户隔离由 tenant_id 与请求校验保证；角色分管理员与普通成员，员工另有负责人与可见范围校验。"),
    ("p", "密钥使用 Fernet 加密，密钥由 APP_SECRET 派生；渠道凭证与员工密钥加密落库，接口只回掩码；开放 API 密钥只存摘要与前缀。更换 APP_SECRET 会导致既有密文无法解密。"),
    ("p", "Harness 暴露的命令经操作系统沙盒执行，优先 Anthropic Sandbox Runtime，Linux 可回退 Bubblewrap，仅当前任务工作区可写；网络策略分不受限、白名单与断网三档。"),
    ("p", "部署为源码单端口运行或桌面安装包，没有容器编排；SQLite 是唯一受支持的生产迁移路径，后端说明明确不支持其他数据库。定时任务依赖常驻进程或独立 worker。"),

    ("h2", "七、当前支持范围与已知限制"),
    ("p", "已实现的能力包括 SOP 全生命周期、结构感知的知识检索、HTTP/A2A/MCP 工具、多渠道接入、定时任务、运行轨迹、反馈归因、开放 API 与对话内数据大屏。"),
    ("p", "限制：数据大屏只做对话内可视化，不取数、不聚合 SQL；记忆不是语义检索；部分开放 API 的分页游标固定为空，幂等与 ETag 未覆盖全部接口；删除默认为归档。"),
    ("p", "另需确认：README 提到 OIDC，但后端没有实现；后端说明称沙盒没有降级路径，而 sandbox.py 在 Windows 缺少 SRT 时会降级为无沙盒执行；团队协作已实现，但路线图未勾选。"),
]

DIFF_ROWS = [
    ("读者对象", "企业负责人、业务部门", "技术负责人、开发者、IT 运维"),
    ("核心问题", "值不值得引入，从哪条流程开始", "能否集成，如何部署、扩展和排障"),
    ("内容重心", "业务问题、典型场景、价值与推进方式", "系统边界、执行链路、模块职责、接口与安全"),
    ("表述方式", "业务语言，技术名词只保留必要的", "术语加文件位置，解释机制而非结论"),
    ("证据密度", "只用可核实的能力与演示流程", "逐项标注对应文档或代码，区分已实现与未确认"),
    ("刻意不写", "成效数字、客户案例、周期与回报承诺", "未验证的性能、容量与稳定性结论"),
    ("共同底线", "不夸大能力，不把设想写成现状", "同左，并明确指出文档与实现的差异"),
]

CONFIRM_GROUPS = [
    ("产品与版本", [
        "对外版本号以哪个为准：代码为 0.1.0，git tag 为 0.1.17，README 分别为 v0.4.0 和 v0.5.0。",
        "对外产品名用 StaffDeck 还是 RapidStaff：文档统一为前者，运行时代码仍出现后者及 rpdnex.com 域名。",
        "多员工协作（teams）是否对外发布：代码与界面已具备，README 路线图未勾选。",
        "数据大屏的对外定位：当前是对话内可视化输出，是否计划扩展为独立报表或看板产品。",
    ]),
    ("部署与集成", [
        "生产数据库：SQLite 是否为交付基线，是否有 PostgreSQL 支持计划及时间表。",
        "部署与运维：是否提供容器镜像、反向代理与 HTTPS 参考配置、备份恢复方案、日志与监控接入方式。",
        "身份认证：是否支持 OIDC、LDAP 或企业微信扫码登录；当前代码只有本地账号密码。",
        "许可与商业使用：AGPL 3.0 对闭源集成和商业分发的影响，是否提供商业授权。",
    ]),
    ("能力与合规", [
        "权限模型：员工可见范围的具体档位，以及管理员与普通成员的能力差异清单。",
        "人工介入范围：是否仅限 SOP 节点声明的转人工，能否随时接管进行中的会话，是否有审批流与超时升级。",
        "高风险工具审批：路线图尚未完成细粒度审批，当前对写操作的实际控制手段是什么。",
        "知识能力：是否使用向量检索或重排；支持的文件类型、单文件与知识库容量上限；解析失败如何处理。",
        "模型接入：支持哪些供应商与私有化模型；是否有内容安全与备案要求；模型故障时的降级策略。",
        "沙盒与隔离：Windows 缺少 SRT 时降级为无沙盒执行的实际策略，生产环境是否强制沙盒。",
        "渠道合规：微信个人号方案的使用边界与合规风险；企微、飞书、钉钉的能力差异。",
        "容量与成效：并发会话、单租户员工数、消息吞吐的实测数据；是否有可公开的试点成效。",
        "行业适配：目标行业与已有落地行业，定制开发的工作量估算方式。",
    ]),
]

SOURCE_ROWS = [
    ("README.zh.md", "仓库根目录", "产品定位、四大能力、快速开始、渠道、路线图、风险与限制"),
    ("README.md", "仓库根目录", "英文版说明与版本更新记录（v0.2.0 至 v0.5.0）"),
    ("backend/README.md", "backend/", "后端运行方式、SQLite 迁移基线、沙盒与通用技能运行时说明"),
    ("docs/open-api-v1.md", "docs/", "开放 API 密钥模型、调用链、资源接口、通用协议与版本边界"),
    ("docs/design/dashboard-capability.md", "docs/design/", "数据大屏能力边界、渲染输入约束与未实现项"),
    ("docs/design/multi-agent-team-decisions.md", "docs/design/", "多员工协作的决策与阶段划分"),
    ("core/harness_v2_engine.py", "backend/app/", "单次 turn 编排、会话锁、能力清单装配与执行循环"),
    ("core/turn_planner.py", "backend/app/", "意图与 SOP 选择"),
    ("core/capability_manifest.py", "backend/app/", "能力描述、权限投影与按节点授权"),
    ("core/graph_rules.py", "backend/app/", "SOP 图规则与步骤推进"),
    ("core/human_handoff_service.py", "backend/app/", "转人工请求的创建、去重与恢复"),
    ("knowledge/service.py", "backend/app/", "知识入库阶段与分层检索实现"),
    ("skills/skill_schema.py", "backend/app/", "SOP 节点、连线与能力引用结构"),
    ("tools/tool_executor.py、tools/mcp_client.py", "backend/app/", "HTTP/A2A/MCP 工具执行、密钥占位符与传输方式"),
    ("scheduled_tasks/worker.py、schema.py", "backend/app/", "定时任务调度、时区、并发与补偿策略"),
    ("channels/adapters/", "backend/app/", "微信、企微、飞书、钉钉适配器与统一收发"),
    ("security/auth.py、encryption.py、permissions.py", "backend/app/", "登录、令牌、密钥加密与角色校验"),
    ("harness/sandbox.py、command.py", "backend/app/", "沙盒后端选择、网络策略与降级行为"),
    ("db/models.py、db/database.py、config.py", "backend/app/", "数据模型、SQLite 迁移与配置项默认值"),
    ("packaging/", "仓库根目录", "桌面安装包与各平台构建脚本，无容器化配置"),
]


def set_run_font(run, name: str, size: float, *, bold=False, color=INK, italic=False):
    run.font.name = LATIN_FONT if name == BODY_FONT else name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), LATIN_FONT if name == BODY_FONT else name)
    rfonts.set(qn("w:hAnsi"), LATIN_FONT if name == BODY_FONT else name)


def add_bottom_border(paragraph, color="1F3A5F", size="8"):
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    ppr.append(borders)


def shade_cell(cell, hex_fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), hex_fill)
    tc_pr.append(shading)


def prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant = OxmlElement("w:cantSplit")
    tr_pr.append(cant)


def add_field(paragraph, instr: str):
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = f" {instr} "
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    fld_text = OxmlElement("w:t")
    fld_text.text = "1"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr_el)
    run._r.append(fld_sep)
    run._r.append(fld_text)
    run._r.append(fld_end)
    set_run_font(run, BODY_FONT, 8.5, color=MUTED)


def setup_page(document: Document):
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.4)
    section.left_margin = Cm(2.8)
    section.right_margin = Cm(2.7)
    section.header_distance = Cm(1.4)
    section.footer_distance = Cm(1.3)


def setup_styles(document: Document):
    normal = document.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    fmt = normal.paragraph_format
    fmt.line_spacing = 1.5
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(3)
    fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def header_footer(document: Document):
    section = document.sections[0]
    header = section.header
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hp.paragraph_format.space_after = Pt(2)
    run = hp.add_run("StaffDeck 数字员工平台项目介绍")
    set_run_font(run, BODY_FONT, 8.5, color=MUTED)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = fp.add_run("第 ")
    set_run_font(r1, BODY_FONT, 8.5, color=MUTED)
    add_field(fp, "PAGE")
    r2 = fp.add_run(" 页，共 ")
    set_run_font(r2, BODY_FONT, 8.5, color=MUTED)
    add_field(fp, "NUMPAGES")
    r3 = fp.add_run(" 页")
    set_run_font(r3, BODY_FONT, 8.5, color=MUTED)


def para(document, text="", *, size=10.5, bold=False, color=INK, align=None,
         first_indent=True, space_before=0, space_after=3, line=1.5, font=BODY_FONT):
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    fmt.line_spacing = line
    fmt.first_line_indent = Pt(size * 2) if first_indent else Pt(0)
    if align is not None:
        fmt.alignment = align
    if text:
        run = p.add_run(text)
        set_run_font(run, font, size, bold=bold, color=color)
    return p


def h1(document, text, *, page_break=False):
    p = document.add_paragraph()
    if page_break:
        p.paragraph_format.page_break_before = True
    fmt = p.paragraph_format
    fmt.space_before = Pt(6 if page_break else 18)
    fmt.space_after = Pt(14)
    fmt.line_spacing = 1.2
    fmt.keep_with_next = True
    run = p.add_run(text)
    set_run_font(run, HEAD_FONT, 17, color=ACCENT)
    add_bottom_border(p)
    return p


def h2(document, text):
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(15)
    fmt.space_after = Pt(6)
    fmt.line_spacing = 1.3
    fmt.keep_with_next = True
    fmt.first_line_indent = Pt(0)
    run = p.add_run(text)
    set_run_font(run, HEAD_FONT, 13, color=ACCENT)
    return p


def h3(document, text):
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = Pt(8)
    fmt.space_after = Pt(2)
    fmt.line_spacing = 1.4
    fmt.keep_with_next = True
    fmt.first_line_indent = Pt(21)
    run = p.add_run(text)
    set_run_font(run, HEAD_FONT, 10.5, bold=False, color=INK)
    return p


def note(document, text, *, indent=Cm(0.6)):
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.left_indent = indent
    fmt.space_before = Pt(4)
    fmt.space_after = Pt(4)
    fmt.line_spacing = 1.35
    fmt.first_line_indent = Pt(0)
    run = p.add_run(text)
    set_run_font(run, BODY_FONT, 9, color=MUTED)
    return p


def grouped_list(document, groups):
    n = 0
    for label, items in groups:
        p = document.add_paragraph()
        fmt = p.paragraph_format
        fmt.space_before = Pt(8)
        fmt.space_after = Pt(2)
        fmt.line_spacing = 1.4
        fmt.first_line_indent = Pt(0)
        fmt.left_indent = Cm(0.55)
        run = p.add_run(label)
        set_run_font(run, HEAD_FONT, 10.5, color=ACCENT)
        for item in items:
            n += 1
            pp = document.add_paragraph()
            f = pp.paragraph_format
            f.left_indent = Cm(1.35)
            f.first_line_indent = Cm(-0.85)
            f.space_before = Pt(0)
            f.space_after = Pt(2)
            f.line_spacing = 1.45
            r1 = pp.add_run(f"{n}. ")
            set_run_font(r1, BODY_FONT, 10, color=ACCENT)
            r2 = pp.add_run(item)
            set_run_font(r2, BODY_FONT, 10)


def table(document, header, rows, widths, *, font_size=9, header_fill="E8EDF5"):
    table_obj = document.add_table(rows=1, cols=len(header))
    table_obj.style = "Table Grid"
    table_obj.alignment = WD_TABLE_ALIGNMENT.CENTER
    table_obj.autofit = False
    hdr = table_obj.rows[0]
    for i, text in enumerate(header):
        cell = hdr.cells[i]
        cell.width = widths[i]
        shade_cell(cell, header_fill)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell.paragraphs[0].paragraph_format.space_before = Pt(3)
        cell.paragraphs[0].paragraph_format.space_after = Pt(3)
        run = cell.paragraphs[0].add_run(text)
        set_run_font(run, HEAD_FONT, font_size + 0.5, color=ACCENT)
    for row_data in rows:
        row = table_obj.add_row()
        prevent_row_split(row)
        for i, text in enumerate(row_data):
            cell = row.cells[i]
            cell.width = widths[i]
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2.5)
            p.paragraph_format.space_after = Pt(2.5)
            p.paragraph_format.line_spacing = 1.25
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(text)
            set_run_font(run, BODY_FONT, font_size)
    document.add_paragraph().paragraph_format.space_after = Pt(4)
    return table_obj


def count_cjk(blocks):
    return sum(len(re.findall(r"[\u4e00-\u9fff]", t)) for _, t in blocks)


def count_words(blocks):
    text = "".join(t for _, t in blocks)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-/:]*", text))
    return cjk, latin, len(re.sub(r"\s", "", text))


def build():
    document = Document()
    setup_page(document)
    setup_styles(document)
    header_footer(document)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(58)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run("StaffDeck 数字员工平台")
    set_run_font(run, HEAD_FONT, 26, color=ACCENT)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run("项目介绍")
    set_run_font(run, HEAD_FONT, 22, color=ACCENT)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(26)
    run = p.add_run("客户版 · 技术版")
    set_run_font(run, HEAD_FONT, 13, color=MUTED)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(30)
    add_bottom_border(p, color="B9C4D4", size="6")
    run = p.add_run("")
    set_run_font(run, BODY_FONT, 2)

    para(document, "编制日期：2026 年 11 月 9 日", first_indent=False,
         align=WD_ALIGN_PARAGRAPH.CENTER, size=10, color=MUTED)
    para(document, "资料依据：StaffDeck 项目仓库的说明文档、开放 API 文档、设计文档与实现代码（清单见附录 C）",
         first_indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=10, color=MUTED,
         space_after=30)

    note(document, "阅读说明", indent=Cm(0))
    note(document, "本文分为两部分。客户版面向企业负责人与业务部门，说明项目解决什么问题、如何融入日常工作、"
                   "以何种方式试点；技术版面向技术负责人、开发者与运维人员，说明系统边界、执行链路、模块职责、"
                   "对接方式与安全部署。两部分的问题意识相同，但内容取舍不同。", indent=Cm(0))
    note(document, "文中区分三类表述：项目资料可以直接证实的能力、需要通过配置才能实现的能力、以及资料不足或"
                   "彼此矛盾的事项。第三类统一标注“需项目方确认”，并在附录 B 中汇总。本文不引用未经项目资料"
                   "支撑的成效数据、客户案例与实施周期。", indent=Cm(0))
    note(document, "文中引用代码时只写文件位置，不写具体行号，因为仓库当前工作区存在未提交改动，行号会变化。",
         indent=Cm(0))

    note(document, "文档结构", indent=Cm(0))
    note(document, "第一部分　客户版：一、项目简介　二、企业常见的业务问题　三、解决思路　四、典型场景　"
                   "五、业务价值　六、适用场景与试点建议　七、小结", indent=Cm(0))
    note(document, "第二部分　技术版：一、定位与边界　二、技术栈与架构　三、执行链路　四、核心模块　"
                   "五、对接与扩展　六、安全与部署　七、支持范围与限制", indent=Cm(0))
    note(document, "附录　A. 两版定位差异　B. 需要补充或确认的信息　C. 引用的项目资料清单", indent=Cm(0))

    h1(document, "第一部分　客户版", page_break=True)
    note(document, "读者：企业负责人、业务部门负责人、流程与知识管理者。目的：判断是否值得引入，以及如何开始试点。",
         indent=Cm(0))
    for kind, text in CUSTOMER:
        if kind == "h2":
            h2(document, text)
        elif kind == "h3":
            h3(document, text)
        else:
            para(document, text)

    h1(document, "第二部分　技术版", page_break=True)
    note(document, "读者：技术负责人、开发者、IT 运维。目的：判断能否集成，以及部署、扩展与排障时需要知道什么。",
         indent=Cm(0))
    for kind, text in TECHNICAL:
        if kind == "h2":
            h2(document, text)
        elif kind == "h3":
            h3(document, text)
        else:
            para(document, text)

    h1(document, "附录", page_break=True)

    h2(document, "A. 两版的定位差异")
    para(document, "两版不是同一篇文章换一套术语。客户版回答“为什么需要、从哪里开始”，技术版回答“怎么运转、"
                   "边界在哪”。具体差异如下。")
    table(document, ["维度", "客户版", "技术版"], DIFF_ROWS,
          [Cm(3.0), Cm(6.1), Cm(6.1)])

    h2(document, "B. 需要补充或确认的信息")
    para(document, "下列事项在现有资料中无法确认，或文档与代码之间存在差异。请在对外发布前逐项确认；"
                   "未确认前，本文相关表述保持保守。")
    grouped_list(document, CONFIRM_GROUPS)

    h2(document, "C. 引用的项目资料清单")
    para(document, "以下资料均来自项目仓库当前分支（feat/dashboard-phase2，2026 年 11 月 9 日）。"
                   "文中结论可直接对照这些文件核对。")
    table(document, ["资料", "位置", "本文用途"], SOURCE_ROWS,
          [Cm(4.6), Cm(3.2), Cm(7.4)], font_size=8.5)

    document.core_properties.title = "StaffDeck 数字员工平台项目介绍（客户版 · 技术版）"
    document.core_properties.subject = "企业数字员工平台的项目介绍文档"
    document.core_properties.author = "项目方案整理"
    document.core_properties.comments = "依据项目仓库资料整理，含待确认事项清单。"

    document.save(OUT)
    return OUT


if __name__ == "__main__":
    print("客户版:", count_words(CUSTOMER))
    print("技术版:", count_words(TECHNICAL))
    path = build()
    print("saved:", path)
