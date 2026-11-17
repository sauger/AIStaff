#!/usr/bin/env python3
"""Build two StaffDeck decks from real local screenshots.

Deck A: customer pitch (10 slides)
Deck B: technical introduction (12 slides)

All facts come from the repository at origin/main f86d49c and from the local
instance running on 127.0.0.1:5273 (seeded demo data, admin/admin).
"""
from __future__ import annotations

import copy
import pathlib

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = pathlib.Path("/Users/haroldw/ai-agent/AIStaff/presentation_output")
SHOTS = ROOT / "screenshots"
TMP = pathlib.Path("/tmp/pw")

FONT = "PingFang SC"
INK = RGBColor(0x18, 0x22, 0x30)
BODY = RGBColor(0x33, 0x3B, 0x47)
MUTED = RGBColor(0x6E, 0x76, 0x83)
ACCENT = RGBColor(0x0E, 0x6E, 0x5C)
ACCENT_WARM = RGBColor(0xB5, 0x7A, 0x2E)
RULE = RGBColor(0xDD, 0xE2, 0xE8)
LIGHT = RGBColor(0xF4, 0xF6, 0xF8)
PAPER = RGBColor(0xFB, 0xFB, 0xFC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARK_BG = RGBColor(0x11, 0x19, 0x24)

SW = Inches(13.333)
SH = Inches(7.5)
MARGIN = Inches(0.78)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def set_font(run, size=None, bold=None, color=None, italic=None, spacing=None):
    run.font.name = FONT
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", FONT)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color
    if spacing is not None:
        run.font._rPr.set("spc", str(int(spacing * 100)))


def textbox(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    return box


def para(tf, first=False, space_before=0, space_after=0, line=1.3, align=PP_ALIGN.LEFT):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.line_spacing = line
    p.space_before = Pt(space_before)
    p.space_after = Pt(space_after)
    return p


def write(tf, lines, first=True):
    """lines: list of dicts(text,size,bold,color,space_before,space_after,line,align,italic)"""
    p = None
    for i, spec in enumerate(lines):
        if i == 0 and first:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.alignment = spec.get("align", PP_ALIGN.LEFT)
        p.line_spacing = spec.get("line", 1.32)
        p.space_before = Pt(spec.get("space_before", 0))
        p.space_after = Pt(spec.get("space_after", 0))
        run = p.add_run()
        run.text = spec["text"]
        set_font(
            run,
            size=spec.get("size", 14),
            bold=spec.get("bold", False),
            color=spec.get("color", BODY),
            italic=spec.get("italic", False),
            spacing=spec.get("spacing"),
        )


def rect(slide, x, y, w, h, fill=None, line=None, radius=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, line_w=1.0):
    shp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shp.adjustments[0] = radius
        except Exception:
            pass
    if shp.has_text_frame:
        shp.text_frame.word_wrap = True
    return shp


def hline(slide, x, y, w, color=RULE, weight=1.0):
    ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x + w), Inches(y))
    ln.line.color.rgb = color
    ln.line.width = Pt(weight)


def vline(slide, x, y, h, color=RULE, weight=1.0):
    ln = slide.shapes.add_connector(1, Inches(x), Inches(y), Inches(x), Inches(y + h))
    ln.line.color.rgb = color
    ln.line.width = Pt(weight)


def arrow_down(slide, x, y, w=0.2, h=0.22, color=ACCENT):
    shp = slide.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()


def arrow_right(slide, x, y, w=0.26, h=0.16, color=ACCENT):
    shp = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()


def new_deck():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    return prs


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def page_header(prs, kicker, title, sub=None, page=None, total=None, deck="客户方案"):
    s = blank(prs)
    rect(s, 0, 0, 13.333, 7.5, fill=WHITE, shape=MSO_SHAPE.RECTANGLE)
    textbox(s, 0.78, 0.46, 9.6, 0.3)
    tf = s.shapes[-1].text_frame
    write(tf, [{"text": kicker, "size": 10.5, "bold": True, "color": ACCENT, "spacing": 1.6}])
    textbox(s, 0.78, 0.8, 11.9, 0.78)
    write(s.shapes[-1].text_frame, [{"text": title, "size": 27, "bold": True, "color": INK, "line": 1.15}])
    if sub:
        textbox(s, 0.78, 1.5, 11.6, 0.42)
        write(s.shapes[-1].text_frame, [{"text": sub, "size": 12.5, "color": MUTED, "line": 1.3}])
    # footer
    hline(s, 0.78, 6.95, 11.77, color=RULE, weight=0.75)
    textbox(s, 0.78, 7.04, 7.0, 0.24)
    write(s.shapes[-1].text_frame,
          [{"text": f"StaffDeck 数字员工平台 · {deck}", "size": 8.5, "color": MUTED, "spacing": 0.4}])
    if page is not None:
        textbox(s, 11.3, 7.02, 1.25, 0.26)
        write(s.shapes[-1].text_frame,
              [{"text": f"{page:02d} / {total:02d}", "size": 8.5, "color": MUTED, "align": PP_ALIGN.RIGHT}])
    return s


def cover(prs, kicker, title, subtitle, meta_lines, footer):
    s = blank(prs)
    rect(s, 0, 0, 13.333, 7.5, fill=DARK_BG, shape=MSO_SHAPE.RECTANGLE)
    # subtle band
    rect(s, 0, 0, 0.26, 7.5, fill=ACCENT, shape=MSO_SHAPE.RECTANGLE)
    textbox(s, 1.15, 1.55, 9.5, 0.36)
    write(s.shapes[-1].text_frame,
          [{"text": kicker, "size": 11, "bold": True, "color": RGBColor(0x7F, 0xD3, 0xBE), "spacing": 2.2}])
    textbox(s, 1.15, 2.0, 10.4, 1.2)
    write(s.shapes[-1].text_frame, [{"text": title, "size": 46, "bold": True, "color": WHITE, "line": 1.1}])
    textbox(s, 1.15, 3.45, 10.2, 0.6)
    write(s.shapes[-1].text_frame, [{"text": subtitle, "size": 20, "color": RGBColor(0xC9, 0xD3, 0xDE)}])
    hline(s, 1.15, 4.35, 2.2, color=RGBColor(0x7F, 0xD3, 0xBE), weight=1.5)
    textbox(s, 1.15, 4.7, 10.4, 0.9)
    write(s.shapes[-1].text_frame,
          [{"text": t, "size": 12.5, "color": RGBColor(0x9F, 0xAC, 0xBB), "space_after": 5, "line": 1.45}
           for t in meta_lines])
    textbox(s, 1.15, 6.55, 10.4, 0.4)
    write(s.shapes[-1].text_frame,
          [{"text": footer, "size": 10, "color": RGBColor(0x6E, 0x7C, 0x8B), "spacing": 0.4}])
    return s


# --------------------------------------------------------------------------
# image helpers
# --------------------------------------------------------------------------
def prepared_image(src: pathlib.Path, box, name: str, target_ratio: float) -> pathlib.Path:
    """Crop `box` (left, top, right, bottom in source px), then fit to target ratio."""
    dst = SHOTS / name
    img = Image.open(src).convert("RGB")
    if box:
        img = img.crop(box)
    w, h = img.size
    ratio = w / h
    if ratio > target_ratio:  # too wide -> trim sides
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif ratio < target_ratio:  # too tall -> trim bottom
        new_h = int(w / target_ratio)
        img = img.crop((0, 0, w, new_h))
    img.save(dst, quality=92)
    return dst


def picture(slide, path: pathlib.Path, x, y, w, h, border=True, caption=None):
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    if border:
        pic.line.color.rgb = RGBColor(0xCF, 0xD6, 0xDE)
        pic.line.width = Pt(0.75)
    if caption:
        textbox(slide, x, y + h + 0.08, w, 0.26)
        write(slide.shapes[-1].text_frame,
              [{"text": caption, "size": 8.5, "color": MUTED, "italic": False, "line": 1.2}])
    return pic


def numbered_badge(slide, x, y, n, d=0.34):
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    c.shadow.inherit = False
    c.fill.solid()
    c.fill.fore_color.rgb = ACCENT_WARM
    c.line.color.rgb = WHITE
    c.line.width = Pt(1.5)
    tf = c.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    write(tf, [{"text": str(n), "size": 11, "bold": True, "color": WHITE, "align": PP_ALIGN.CENTER}])


def bullets_block(slide, x, y, w, items, size=13.5, gap=0.62, color=BODY, marker=True):
    """items: list of (lead, rest) tuples; lead is bold accent text."""
    cur = y
    for lead, rest in items:
        if marker:
            dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(cur + 0.09), Inches(0.09), Inches(0.09))
            dot.shadow.inherit = False
            dot.fill.solid()
            dot.fill.fore_color.rgb = ACCENT
            dot.line.fill.background()
        textbox(slide, x + 0.24, cur, w - 0.24, 0.9)
        tf = slide.shapes[-1].text_frame
        p = tf.paragraphs[0]
        p.line_spacing = 1.32
        r1 = p.add_run()
        r1.text = lead
        set_font(r1, size=size, bold=True, color=INK)
        r2 = p.add_run()
        r2.text = rest
        set_font(r2, size=size, color=color)
        cur += gap
    return cur


def chip(slide, x, y, w, h, text, fill=LIGHT, color=INK, size=11, bold=False, line=None):
    shp = rect(slide, x, y, w, h, fill=fill, line=line, radius=0.5)
    tf = shp.text_frame
    tf.margin_left = Inches(0.08)
    tf.margin_right = Inches(0.08)
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    write(tf, [{"text": text, "size": size, "color": color, "bold": bold, "align": PP_ALIGN.CENTER, "line": 1.0}])
    return shp


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------
def build_assets() -> dict[str, pathlib.Path]:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = {}
    out["gallery"] = prepared_image(TMP / "tour3/platform.png", (225, 45, 2995, 1330), "p-gallery.png", 2.36)
    out["agents"] = prepared_image(TMP / "tour3/agents.png", (225, 40, 2995, 1400), "p-agents.png", 2.36)
    out["workbench"] = prepared_image(SHOTS / "02-workbench.png", (225, 20, 2995, 1330), "p-workbench.png", 2.36)
    out["sop"] = prepared_image(TMP / "sop2/editor.png", (905, 300, 3160, 1830), "p-sop.png", 1.86)
    out["knowledge"] = prepared_image(SHOTS / "03-knowledge.png", (225, 120, 2995, 1560), "p-knowledge.png", 2.2)
    out["runtime"] = prepared_image(SHOTS / "06-runtime-settings.png", (280, 130, 2995, 1560), "p-runtime.png", 2.2)
    out["skills"] = prepared_image(SHOTS / "07-general-skills.png", (225, 60, 2995, 1440), "p-skills.png", 2.2)
    out["chat"] = prepared_image(SHOTS / "10-chat-top.png", (700, 110, 2900, 1480), "p-chat.png", 1.62)
    out["chat_live"] = prepared_image(TMP / "chat4/chat_reply.png", (760, 130, 2820, 1730), "p-chat-live.png", 1.62)
    return out


# --------------------------------------------------------------------------
# deck A - customer
# --------------------------------------------------------------------------
def evidence_badge(slide, x, y, text, strong=True):
    w = 0.42 + len(text) * 0.135
    fill = ACCENT if strong else LIGHT
    color = WHITE if strong else MUTED
    chip(slide, x - w, y, w, 0.3, text, fill=fill, color=color, size=9, bold=strong)
    return x - w


def case_flow(slide, x, y, w, steps, branch_after=None, branch=None, h=0.64, gap=0.14):
    """Vertical case flow; branch draws two side-by-side chips after a step."""
    cur = y
    for i, (title, desc, tag) in enumerate(steps):
        rect(slide, x, cur, w, h, fill=PAPER, line=RULE, radius=0.1)
        textbox(slide, x + 0.16, cur + 0.19, 0.42, 0.26)
        write(slide.shapes[-1].text_frame,
              [{"text": f"{i + 1:02d}", "size": 9.5, "bold": True, "color": RGBColor(0xA9, 0xB3, 0xBF)}])
        textbox(slide, x + 0.58, cur + 0.07, w - 1.5, 0.28)
        write(slide.shapes[-1].text_frame, [{"text": title, "size": 11.5, "bold": True, "color": INK}])
        if tag:
            textbox(slide, x + w - 1.42, cur + 0.09, 1.3, 0.24)
            write(slide.shapes[-1].text_frame,
                  [{"text": tag, "size": 7.8, "color": MUTED, "align": PP_ALIGN.RIGHT}])
        textbox(slide, x + 0.58, cur + 0.33, w - 0.75, 0.26)
        write(slide.shapes[-1].text_frame, [{"text": desc, "size": 8.8, "color": BODY, "line": 1.15}])
        cur += h
        if branch_after == i and branch:
            arrow_down(slide, x + w / 2 - 0.09, cur + 0.02, w=0.18, h=0.12)
            cur += 0.2
            gap_w = 0.14
            cw = (w - gap_w) / 2
            for j, (bt, bd) in enumerate(branch):
                bx = x + j * (cw + gap_w)
                rect(slide, bx, cur, cw, 0.56, fill=LIGHT, line=None, radius=0.14)
                textbox(slide, bx + 0.16, cur + 0.07, cw - 0.3, 0.22)
                write(slide.shapes[-1].text_frame, [{"text": bt, "size": 8.6, "bold": True, "color": ACCENT}])
                textbox(slide, bx + 0.16, cur + 0.28, cw - 0.3, 0.22)
                write(slide.shapes[-1].text_frame, [{"text": bd, "size": 8.0, "color": BODY, "line": 1.1}])
            cur += 0.56
            if i < len(steps) - 1:
                arrow_down(slide, x + w / 2 - 0.09, cur + 0.02, w=0.18, h=0.12)
                cur += 0.18
        elif i < len(steps) - 1:
            arrow_down(slide, x + w / 2 - 0.09, cur + 0.02, w=0.18, h=0.12)
            cur += gap
    return cur


def case_side(slide, x, y, w, scene, outputs, human, note=None):
    textbox(slide, x, y, w, 0.24)
    write(slide.shapes[-1].text_frame, [{"text": "场景", "size": 9.5, "bold": True, "color": ACCENT, "spacing": 1.2}])
    textbox(slide, x, y + 0.3, w, 0.9)
    write(slide.shapes[-1].text_frame, [{"text": scene, "size": 10.2, "color": BODY, "line": 1.5}])
    textbox(slide, x, y + 1.42, w, 0.24)
    write(slide.shapes[-1].text_frame, [{"text": "交付物", "size": 9.5, "bold": True, "color": ACCENT, "spacing": 1.2}])
    textbox(slide, x, y + 1.72, w, 1.1)
    write(slide.shapes[-1].text_frame,
          [{"text": "· " + t, "size": 9.8, "color": BODY, "line": 1.4, "space_after": 4} for t in outputs])
    textbox(slide, x, y + 3.02, w, 0.24)
    write(slide.shapes[-1].text_frame, [{"text": "人在哪里接手", "size": 9.5, "bold": True, "color": ACCENT, "spacing": 1.2}])
    textbox(slide, x, y + 3.32, w, 1.1)
    write(slide.shapes[-1].text_frame, [{"text": human, "size": 9.8, "color": BODY, "line": 1.45}])
    if note:
        textbox(slide, x, y + 4.55, w, 0.7)
        write(slide.shapes[-1].text_frame,
              [{"text": note, "size": 8.3, "color": MUTED, "italic": True, "line": 1.35}])


def deck_a(assets, out_path):
    prs = new_deck()
    TOTAL = 10

    s = cover(
        prs,
        "企业数字员工平台 · 客户方案",
        "StaffDeck 数字员工",
        "四个可核对的 agent 案例：从岗位流程，到一次真实运行",
        [
            "案例一 采购需求核对与比价（本地已实跑）· 案例二 合同条款风险审查",
            "案例三 用章申请审批 · 案例四 IT 权限开通工单分流",
            "案例来自平台内置演示场景与本地真实运行，用于说明能力与交付方式，不代表真实客户项目",
        ],
        "2026 年 11 月",
    )
    notes(s, "开场先说明材料性质：这是“案例集”，不是功能清单，也不是客户成功故事。\n"
             "案例一二来自仓库内置演示场景；其中案例一在本地实例上完整跑过一次，其余三个是按内置 SOP 配置整理的流程样例，页面标注了证据等级。\n"
             "截图全部来自本地运行（127.0.0.1:5273，admin/admin，tenant_demo 演示数据）。\n"
             "界面左上角当前显示 RapidStaff 品牌，与仓库名 StaffDeck 不一致，对外前需统一。")

    # 2 why cases
    s = page_header(prs, "01 · 为什么用案例讲", "客户要的不是功能清单，是能跑通的案例",
                    "一个 agent 项目能不能成，通常卡在三件事上；这三件事也都必须有可核对的证据。",
                    page=2, total=TOTAL)
    cards = [
        ("流程说得清", "这条流程有几步、在哪里判断、什么情况交给人，都能在 SOP 里看到，而不是只在演示话术里。",
         "证据：流程图、节点配置、版本记录"),
        ("知识找得到", "回答依据哪份文档、哪一段，能点开核对；找不到时会说找不到，而不是编一个。",
         "证据：引用来源、检索记录"),
        ("人能接手", "哪一步转人工、转给谁、带什么上下文，是流程里的配置项，不是口头约定。",
         "证据：转人工节点、待办与回答"),
    ]
    for i, (h, body, ev) in enumerate(cards):
        cx = 0.78 + i * 4.02
        rect(s, cx, 1.98, 3.78, 3.5, fill=PAPER, line=RULE, radius=0.05)
        textbox(s, cx + 0.32, 2.22, 3.1, 0.34)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 14.5, "bold": True, "color": INK}])
        textbox(s, cx + 0.32, 2.68, 3.12, 1.6)
        write(s.shapes[-1].text_frame, [{"text": body, "size": 10.3, "color": BODY, "line": 1.55}])
        textbox(s, cx + 0.32, 4.5, 3.12, 0.7)
        write(s.shapes[-1].text_frame, [{"text": ev, "size": 8.8, "color": ACCENT, "line": 1.35}])
    textbox(s, 0.78, 5.72, 11.7, 1.0)
    write(s.shapes[-1].text_frame, [
        {"text": "本材料的证据分三级，页面上逐条标注：", "size": 9.8, "bold": True, "color": INK, "space_after": 5},
        {"text": "界面截图 = 本地真实运行的产品页面；流程配置 = 仓库内置的 SOP 与知识库；运行记录 = 一次完整会话的执行轨迹。",
         "size": 9.6, "color": MUTED, "line": 1.4},
    ])
    notes(s, "这一页建立判断标准，后面四个案例都按这三条来讲。\n"
             "强调：不引用任何客户案例、行业数据或效果指标；所有数字要么是界面里能看到的，要么是这次运行的实际耗时。")

    # 3 common structure
    s = page_header(prs, "02 · 案例结构", "每个案例都按同一套结构展开",
                    "这样客户可以逐段对照自己的业务：谁触发、按什么流程、查什么知识、留下什么记录、在哪里交回人。",
                    page=3, total=TOTAL)
    steps = [("场景与触发", "谁在什么情况下提出问题"), ("岗位与 SOP", "由哪位数字员工、按哪条流程处理"),
             ("知识与工具", "查哪些资料、调哪些系统"), ("运行与记录", "每一步做了什么、耗时多少"),
             ("交接与边界", "哪一步交回人、哪些事不做")]
    x0, y0, cw, gap = 0.78, 2.15, 2.24, 0.17
    for i, (h, t) in enumerate(steps):
        cx = x0 + i * (cw + gap)
        rect(s, cx, y0, cw, 1.28, fill=PAPER, line=RULE, radius=0.08)
        textbox(s, cx + 0.2, y0 + 0.18, cw - 0.4, 0.3)
        write(s.shapes[-1].text_frame,
              [{"text": f"0{i + 1}", "size": 10, "bold": True, "color": ACCENT}])
        textbox(s, cx + 0.2, y0 + 0.5, cw - 0.4, 0.3)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 11.5, "bold": True, "color": INK}])
        textbox(s, cx + 0.2, y0 + 0.84, cw - 0.36, 0.4)
        write(s.shapes[-1].text_frame, [{"text": t, "size": 8.6, "color": MUTED, "line": 1.25}])
        if i < len(steps) - 1:
            arrow_right(s, cx + cw + 0.01, y0 + 0.55, w=0.15, h=0.14)
    # lower: what each case page shows
    textbox(s, 0.78, 3.95, 5.4, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "每个案例页面给出四样东西", "size": 10.5, "bold": True, "color": INK}])
    left_items = [
        ("场景　", "一段业务原话，说明触发条件与提问方式。"),
        ("流程　", "按真实节点顺序展开，标出判断与分支。"),
        ("交付物　", "这件事做完，业务手上多出什么。"),
        ("人的位置　", "哪一步转人工、转给谁、带什么信息。"),
    ]
    bullets_block(s, 0.78, 4.4, 5.6, left_items, size=9.8, gap=0.55)
    rect(s, 6.9, 3.95, 5.62, 2.35, fill=LIGHT, line=None, radius=0.06)
    textbox(s, 7.2, 4.15, 5.05, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "页面右上角的标签", "size": 9.5, "bold": True, "color": MUTED}])
    chip(s, 9.7, 4.62, 1.55, 0.32, "已本地实跑", fill=ACCENT, color=WHITE, size=9, bold=True)
    textbox(s, 7.2, 4.6, 2.4, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "至少完整跑通过一次", "size": 9, "color": BODY}])
    chip(s, 9.7, 5.12, 1.55, 0.32, "流程样例", fill=WHITE, color=MUTED, size=9, line=RULE)
    textbox(s, 7.2, 5.1, 2.4, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "按内置 SOP 整理，未实跑", "size": 9, "color": BODY}])
    textbox(s, 7.2, 5.62, 5.0, 0.5)
    write(s.shapes[-1].text_frame,
          [{"text": "标注不是免责声明，而是告诉客户：哪些现在就能演示，哪些需要在项目里配置后验证。",
            "size": 8.8, "color": MUTED, "italic": True, "line": 1.35}])
    notes(s, "先讲结构，再讲案例，客户更容易把自己的流程套进来。\n"
             "标签制度要主动说明：实跑过的只有案例一；其余三个是按仓库内置 SOP 配置整理的流程样例。")

    # 4 case 1
    s = page_header(prs, "03 · 案例一", "采购需求核对与比价：从一句话到可核对的清单",
                    "提问只有一句，数字员工要补齐要素、查依据、给出可执行的清单，同时明确自己不能代替谁做决定。",
                    page=4, total=TOTAL)
    evidence_badge(s, 12.55, 0.62, "已本地实跑")
    case_side(s, 0.78, 1.95, 3.75,
              "“采购 20 台办公笔记本，预算 12 万。请直接给出采购需求清单和比价建议，不用再问我。”",
              ["需求清单：对象、数量、预算、规格待补项", "比价表头：口径、总成本、交付、质量、服务", "风险提示：职责分离、授权前不得对外承诺"],
              "SOP 写明不得代替采购或审批人确认订单、付款或合作承诺；信息不足时向提问者追问，而不是编造。",
              note="本页数字来自这次运行：判断意图 15.5s、SOP 任务 203.6s、4 个动作、检索 70.7s、读文件 13.3s。")
    picture(s, assets["chat"], 4.85, 1.95, 7.7, 7.7 / 1.62,
            caption="真实运行记录：判断意图 → 匹配 SOP《采购需求准备与比较》→ 知识检索 → 读原文 → 推进到验收清单 → 完成，回答末尾附知识来源")
    notes(s, "这是四个案例里唯一完整实跑的：本机实例、演示租户、员工“采购”、模型 gml（演示环境配置）。\n"
             "截图里能看到的耗时是真实调用耗时，不是估算，也不作为性能承诺。\n"
             "讲解重点：回答带了 1 条知识来源引用（采购-协同流程通用指南 / 采购需求与供应商比较 / evidence 1），"
             "并且把“规格、到位时间、验收标准”列为待补项，没有替客户拍板。\n"
             "这次运行没有走到转人工节点，所以没有待办截图；转人工机制见案例二、三。")

    # 5 case 2
    s = page_header(prs, "04 · 案例二", "合同条款风险审查：先查标准，再决定要不要人看",
                    "法务场景的价值不在“自动通过”，而在把常规条款审掉、把高风险条款准确交到人手上。",
                    page=5, total=TOTAL)
    evidence_badge(s, 12.55, 0.62, "流程样例", strong=False)
    case_side(s, 0.78, 1.95, 3.75,
              "业务把一份采购合同发过来：“这份能不能签？风险在哪几条？”",
              ["审查意见：条款级问题与依据", "风险等级与建议修改点", "需业务补充的信息清单"],
              "高风险或无先例的合同转人工待办，带上合同摘要与待确认问题，由流程配置的负责人接手；常规条款才由数字员工给意见。",
              note="知识来源：法务 4 个内置知识库（高风险条款识别指南、合同审查标准清单、企业合规制度汇编、历史条款判例库）。")
    case_flow(s, 5.0, 1.9, 7.55, [
        ("收集合同要素", "类型、对方主体、金额、期限、特殊条款", "collect_info"),
        ("查审查标准", "从法务知识库取条款级审查要点", "knowledge_query"),
        ("检索历史判例", "调用合同判例检索工具比对先例", "tool_call"),
        ("判定风险等级", "按命中条款与先例情况分级", "decision"),
        ("出意见或转人工", "意见附条款依据；转人工附上下文", "response / handoff"),
    ], branch_after=3, branch=[("低 / 中风险", "输出审查意见与修改建议"), ("高风险 / 无先例", "升级法务专员")])
    notes(s, "流程来自仓库内置 SOP contract_risk_review（6 个节点），本页为便于阅读合并为 5 步，分支与终态保持一致。\n"
             "本次尝试在本地实跑：9.2s 匹配到该 SOP，执行了知识检索与读文件，并真实调用了合同判例检索工具"
             "（首次 8 秒超时后重试成功）；流程仍在推进、未产出最终意见，因此仍标注为流程样例；"
             "页面按配置如实绘制，未声称已上线。\n"
             "讲解重点：判断节点不是让模型自由发挥，而是按命中条款、先例情况分级；转人工是两个终态之一，不是异常处理。")

    # 6 case 3
    s = page_header(prs, "05 · 案例三", "用章申请审批：把“找谁批”变成流程里的分支",
                    "员工问的是“这个章能不能盖”，真正要解决的是审批路径与依据；两条转人工分支都在流程里写明。",
                    page=6, total=TOTAL)
    evidence_badge(s, 12.55, 0.62, "流程样例", strong=False)
    case_side(s, 0.78, 1.95, 3.75,
              "“这份合同要盖公章，一共三份，对方催得急，走谁批？”",
              ["用章申请单：文件类型、份数、用途、主体", "合规结论与依据条款", "审批去向与当前状态"],
              "普通用章转行政登记办理，重要合同用章转负责人审批；材料不合规时直接驳回并说明依据，不进入审批。",
              note="对应内置 SOP：seal_application_approval（7 个节点，含两个转人工终态与一个驳回终态）。")
    case_flow(s, 5.0, 1.9, 7.55, [
        ("收集用章信息", "文件类型、份数、用途、用章主体", "collect_info"),
        ("查用章规定", "行政用章管理规定与审批权限", "knowledge_query"),
        ("合规判定", "材料是否齐全、是否属禁用情形", "decision"),
        ("与申请人确认", "确认信息无误后生成申请单", "confirm"),
        ("分派或驳回", "按类型分派审批；不合规直接驳回", "handoff / response"),
    ], branch_after=2, branch=[("普通用章", "转行政登记办理"), ("重要合同用章", "转负责人审批")])
    notes(s, "流程来自内置 SOP seal_application_approval，节点顺序与分支按配置绘制。\n"
             "这是“流程样例”，未在本地跑通；不要对客户说这条流程已经在生产使用。\n"
             "讲解重点：人工审批不是兜底，而是流程设计的一部分——先判定，再决定走哪条审批路径。")

    # 7 case 4
    s = page_header(prs, "06 · 案例四", "IT 权限开通：普通权限自动办，高权限走审批",
                    "同一个入口，按权限等级分流：能自动办的不打扰人，该批的必须有人签字，失败的说清原因。",
                    page=7, total=TOTAL)
    evidence_badge(s, 12.55, 0.62, "流程样例", strong=False)
    case_side(s, 0.78, 1.95, 3.75,
              "“新同事下周入职，需要开通 CRM 和报表系统的账号，走什么流程？”",
              ["开通回执（授权单号与生效时间）或审批去向", "审批状态与所需材料", "失败原因与后续动作"],
              "高权限申请转 IT 主管审批；开通失败时说明原因与后续处理，不静默失败。",
              note="对应内置 SOP：skill_perm_grant_routing_001（7 个节点，普通权限调用 it.grant_permission，高权限与失败各走一个分支）。")
    case_flow(s, 5.0, 1.9, 7.55, [
        ("收集权限申请", "系统、账号、权限范围、申请理由", "collect_info"),
        ("判定权限等级", "普通权限 / 高权限（管理员或敏感系统）", "decision"),
        ("申请人确认", "确认范围与生效时间", "confirm"),
        ("执行或转审批", "调用开通接口；失败说明原因", "tool_call / handoff"),
        ("回执与留痕", "结果写回会话，可追溯", "response"),
    ], branch_after=2, branch=[("普通权限", "自动开通并回执"), ("高权限", "转 IT 主管审批")])
    notes(s, "流程来自内置 SOP skill_perm_grant_routing_001，工具为 it.grant_permission（演示环境指向 mock 服务）。\n"
             "这是“流程样例”，未在本地跑通；工具依赖外部 mock 地址，生产接入需替换为客户系统。\n"
             "讲解重点：工具调用不是“让模型随便点按钮”，而是节点声明可用能力、服务端再做鉴权。")

    # 8 base
    s = page_header(prs, "07 · 共用底座", "四个案例，共用同一套可复用资产",
                    "案例之间不是四套系统，而是同一个平台上的四份配置：员工、SOP、知识库、工具，都可以复制与版本化。",
                    page=8, total=TOTAL)
    picture(s, assets["gallery"], 0.78, 2.1, 8.4, 8.4 / 2.36,
            caption="开放广场平台：数字员工 10 · 知识库 19 · 技能 9 · SOP 26 · 工具 17（本地演示数据）")
    pts = [
        ("复制再改", "广场资产复制到员工后独立演进，不会改一处影响所有人。"),
        ("版本与回滚", "SOP 有草稿、发布、版本与回滚；员工侧还能保留自己的分支。"),
        ("按员工装配", "知识库、技能、SOP、工具按员工挂载，能力范围可见。"),
    ]
    y = 2.15
    for h, b in pts:
        textbox(s, 9.6, y, 3.0, 0.3)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 11.5, "bold": True, "color": ACCENT}])
        textbox(s, 9.6, y + 0.32, 3.0, 0.8)
        write(s.shapes[-1].text_frame, [{"text": b, "size": 9.8, "color": BODY, "line": 1.45}])
        y += 1.32
    notes(s, "截图来源：本地实例 /enterprise/platform，tenant_demo 演示数据。\n"
             "数字只说明演示规模，不是客户现场数据，也不代表性能。\n"
             "讲解重点：项目交付的不是四个孤立机器人，而是一套可复用的资产库与装配方式。")

    # 9 pilot
    s = page_header(prs, "08 · 怎么开始", "把客户的一条流程，做成第一个案例",
                    "不建议一上来铺多个岗位；先用一条流程跑通“配置—运行—复盘”的闭环，再复制到第二条。",
                    page=9, total=TOTAL)
    steps = [("选一条流程", "高频、规则相对清楚、结果能被业务核对；先避开跨部门长流程。"),
             ("配四样资产", "一位员工、一条 SOP、一组知识文档、必要的工具与审批人。"),
             ("小范围跑", "限定可见范围，保留人工确认，记录哪些问题必须找人。"),
             ("按记录迭代", "看执行记录与反馈：是知识缺口、流程缺口，还是工具没接通。")]
    for i, (h, body) in enumerate(steps):
        cx = 0.78 + (i % 2) * 5.98
        cy = 1.92 + (i // 2) * 1.62
        textbox(s, cx, cy, 0.6, 0.5)
        write(s.shapes[-1].text_frame,
              [{"text": f"0{i + 1}", "size": 22, "bold": True, "color": RGBColor(0xC5, 0xCE, 0xD8)}])
        textbox(s, cx + 0.62, cy + 0.02, 5.0, 0.32)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 13, "bold": True, "color": INK}])
        textbox(s, cx + 0.62, cy + 0.38, 4.9, 0.8)
        write(s.shapes[-1].text_frame, [{"text": body, "size": 10.2, "color": BODY, "line": 1.45}])
    textbox(s, 0.78, 5.28, 11.7, 0.28)
    write(s.shapes[-1].text_frame, [{"text": "第一个案例应当交付的五样东西", "size": 10, "bold": True, "color": ACCENT}])
    dels = ["一条可发布的 SOP", "一组带引用的知识库", "一份工具与审批人清单", "一份运行记录样例", "一份转人工点清单"]
    x = 0.78
    for t in dels:
        w = 0.34 + len(t) * 0.152
        chip(s, x, 5.66, w, 0.4, t, fill=LIGHT, size=9.5)
        x += w + 0.14
    textbox(s, 0.78, 6.3, 11.7, 0.5)
    write(s.shapes[-1].text_frame, [{
        "text": "边界：数字员工不替代人做最终判断；超出流程、信息不足或高风险时，按节点设置交回人工，并保留记录。",
        "size": 9.6, "color": MUTED, "line": 1.35}])
    notes(s, "四步是通用方法，不含周期承诺，也不含收益数字。\n"
             "五样交付物可以直接作为试点验收清单；客户如果已经在做 RPA 或知识库，也可以把已有资产接进来。\n"
             "下一步建议：拿客户一条真实流程做半天工作坊，现场产出 SOP 草图与知识清单。")

    # 10 close
    s = page_header(prs, "09 · 边界与下一步", "能做什么，不能做什么，先说清楚",
                    "把边界讲在前面，比事后解释更省成本；这些限制在项目文档里也是公开写明的。",
                    page=10, total=TOTAL)
    left = [
        ("模型会出错　", "回答可能不准确、不完整；执行记录提高可审计性，但不保证结论正确。"),
        ("检索依赖文档　", "知识库质量、解析效果、权限范围都会影响检索结果，需要持续维护。"),
        ("工具是真实动作　", "外部工具与生成的 Runner 可能产生真实副作用，应按最小权限接入并保留人工审批。"),
        ("定时任务依赖运行环境　", "需要常驻进程或独立 worker，且时区配置正确才会按预期触发。"),
    ]
    bullets_block(s, 0.78, 1.98, 6.1, left, size=10.2, gap=0.95)
    rect(s, 7.15, 1.95, 5.38, 3.9, fill=PAPER, line=RULE, radius=0.05)
    textbox(s, 7.45, 2.18, 4.8, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "下一步可以这样推进", "size": 11.5, "bold": True, "color": INK}])
    textbox(s, 7.45, 2.62, 4.8, 3.0)
    write(s.shapes[-1].text_frame, [
        {"text": "1. 选一条客户自己的流程，现场画出 SOP 草图。", "size": 9.9, "color": BODY, "line": 1.4, "space_after": 8},
        {"text": "2. 列出这条流程需要的文档、系统与审批人。", "size": 9.9, "color": BODY, "line": 1.4, "space_after": 8},
        {"text": "3. 在演示环境里配一版，跑三次真实提问。", "size": 9.9, "color": BODY, "line": 1.4, "space_after": 8},
        {"text": "4. 用执行记录复盘：哪些能自动、哪些必须人看。", "size": 9.9, "color": BODY, "line": 1.4},
    ])
    textbox(s, 0.78, 6.1, 11.7, 0.6)
    write(s.shapes[-1].text_frame, [{
        "text": "许可证 AGPL 3.0：以 SaaS 对外提供服务或需要商业授权时，请在方案阶段与项目方确认授权与合规安排。",
        "size": 9.2, "color": MUTED, "line": 1.35}])
    notes(s, "边界一页不要跳过：模型能力、知识质量、工具副作用、定时任务依赖，都是客户上线后最容易踩的坑。\n"
             "结尾落到行动：用客户自己的流程做一次工作坊，比继续看演示更有说服力。\n"
             "AGPL 3.0 的商用与分发约束，报价前必须由项目方确认。")

    prs.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# deck B - technical
# --------------------------------------------------------------------------
def deck_b(assets, out_path):
    prs = new_deck()
    TOTAL = 12

    s = cover(
        prs,
        "技术项目介绍 · 架构与运行边界",
        "StaffDeck 技术项目介绍",
        "FastAPI + React 的企业数字员工平台，对话运行时为 Harness v2",
        [
            "本文档依据仓库 origin/main（f86d49c）的源码、README 与文档，并在本地实例上实际运行核实",
            "凡资料与实现不一致之处，均在对应页与末页标注“需项目方确认”",
        ],
        "2026 年 11 月 · 面向技术负责人、开发者与 IT 运维",
    )
    notes(s, "技术版与客户版不共用同一套话术：这里关注边界、链路、数据模型与限制。\n"
             "核实环境：本地 worktree（detached at origin/main f86d49c），Python 3.11.15，Chrome 157，SQLite 空库 + seed。")

    # 2 positioning
    s = page_header(prs, "01 · 定位与边界", "它是什么，以及它刻意不是什么",
                    "一句话：把“数字员工”当作有岗位、有流程、有知识、有运行记录的一等对象来管理。",
                    page=2, total=TOTAL, deck="技术介绍")
    left = [
        ("平台层　", "负责员工、SOP、知识、工具、渠道、定时任务、运行记录与反馈的配置和管理。"),
        ("运行时　", "Harness v2 负责一次请求的意图判断、任务编译、能力装配、循环执行与回复生成。"),
        ("资源层　", "知识库、通用技能、HTTP/MCP 工具、模型配置、密钥，均按租户与员工授权。"),
    ]
    bullets_block(s, 0.78, 1.98, 5.65, left, size=11.5, gap=1.0, marker=False)
    rect(s, 6.85, 1.95, 5.7, 4.5, fill=PAPER, line=RULE, radius=0.05)
    textbox(s, 7.15, 2.18, 5.1, 0.34)
    write(s.shapes[-1].text_frame, [{"text": "需要明确的边界", "size": 11, "bold": True, "color": ACCENT_WARM}])
    textbox(s, 7.15, 2.62, 5.15, 3.6)
    write(s.shapes[-1].text_frame, [
        {"text": "· 不是 BI 平台：大屏能力只在对话内渲染，不负责取数、聚合与定时刷新。", "size": 10.2, "color": BODY, "line": 1.45, "space_after": 7},
        {"text": "· 不是任意时刻人工接管：只有 SOP 节点声明 handoff 才会生成待办。", "size": 10.2, "color": BODY, "line": 1.45, "space_after": 7},
        {"text": "· 不是“接上模型就能用”：岗位、流程、知识仍需要人配置与维护。", "size": 10.2, "color": BODY, "line": 1.45, "space_after": 7},
        {"text": "· 不保证模型结论正确：平台提供依据与记录，不替代业务判断。", "size": 10.2, "color": BODY, "line": 1.45},
    ])
    notes(s, "先划边界，能避免后面把“可配置”误解成“开箱即用”。\n"
             "依据：README.zh.md 的能力说明；大屏不取数见 docs/design/dashboard-capability.md；"
             "转人工仅由节点触发见 backend/app/core/agent_loop.py 的 _maybe_route_to_handoff_node。")

    # 3 stack
    s = page_header(prs, "02 · 技术栈", "四层技术栈，没有隐藏的中间件",
                    "后端单体 + 前端 SPA + 文件与 SQLite；外部只依赖模型服务与 IM 平台。",
                    page=3, total=TOTAL, deck="技术介绍")
    stacks = [
        ("后端", ["Python 3.11+ / FastAPI", "SQLModel + SQLite", "uvicorn 单端口", "cryptography（Fernet）"]),
        ("前端", ["React 18 + TypeScript", "Vite + Tailwind 4", "ECharts / Cytoscape", "单页应用，随 FastAPI 托管"]),
        ("运行时", ["Harness v2（自有引擎）", "OpenAI Chat Completions 兼容", "SSE 流式输出", "子进程沙盒执行"]),
        ("集成", ["HTTP / A2A / MCP 工具", "企业微信 / 微信 / 飞书 / 钉钉", "文档解析 PDF/DOCX/HTML", "PyInstaller 桌面包"]),
    ]
    for i, (h, items) in enumerate(stacks):
        cx = 0.78 + i * 3.05
        textbox(s, cx, 1.95, 2.8, 0.34)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 12, "bold": True, "color": ACCENT}])
        hline(s, cx, 2.33, 2.72, color=RULE, weight=0.75)
        textbox(s, cx, 2.45, 2.72, 2.6)
        write(s.shapes[-1].text_frame,
              [{"text": t, "size": 9.8, "color": BODY, "line": 1.4, "space_after": 8} for t in items])
    textbox(s, 0.78, 5.05, 11.7, 1.4)
    write(s.shapes[-1].text_frame, [
        {"text": "版本与构建", "size": 10, "bold": True, "color": MUTED, "space_after": 5},
        {"text": "backend/pyproject.toml: skill-agent-loop-backend 0.1.0；frontend-enterprise/package.json: 0.1.0；"
                 "backend/app/version.py 默认 0.1.0，可由 STAFFDECK_VERSION 覆盖；仓库最新 tag 为 0.1.17。",
         "size": 9.2, "color": MUTED, "line": 1.5},
    ])
    notes(s, "技术栈来自 pyproject.toml、package.json 与实际目录结构，没有 Dockerfile / docker-compose。\n"
             "版本号四个来源不一致（代码 0.1.0、tag 0.1.17、英文 README v0.5.0、中文 README v0.4.0），统一口径需项目方确认。")

    # 4 architecture
    s = page_header(prs, "03 · 总体架构", "四层结构：接入、应用、运行时、数据",
                    "同一进程内按职责分层；运行时是唯一执行入口，所有能力调用都要经过它。",
                    page=4, total=TOTAL, deck="技术介绍")
    bands = [
        ("接入层", ["Web 工作台", "对话端（SSE）", "开放 API v1", "IM 渠道", "定时任务"]),
        ("应用层", ["数字员工与权限", "SOP 图与版本", "知识库与检索", "工具 / MCP", "反馈与记忆"]),
        ("运行时", ["HarnessV2Engine", "TurnPlanner", "TaskFrame", "AgentLoop", "CapabilityInvoker"]),
        ("数据层", ["SQLite", "上传与工作区文件", "模型服务（外部）", "IM 平台（外部）"]),
    ]
    y = 1.9
    for i, (label, items) in enumerate(bands):
        fill = RGBColor(0xEC, 0xF3, 0xF1) if i == 2 else PAPER
        rect(s, 0.78, y, 11.78, 0.92, fill=fill, line=RULE, radius=0.06)
        textbox(s, 1.02, y + 0.3, 1.0, 0.3)
        write(s.shapes[-1].text_frame, [{"text": label, "size": 10.5, "bold": True, "color": ACCENT if i == 2 else INK}])
        x = 2.15
        for t in items:
            w = 0.3 + len(t) * 0.135
            chip(s, x, y + 0.24, w, 0.44, t, fill=WHITE, color=BODY, size=8.8, line=RULE)
            x += w + 0.16
        if i < 3:
            arrow_down(s, 6.6, y + 0.94, w=0.2, h=0.2, color=RGBColor(0xB9, 0xC4, 0xCF))
        y += 1.18
    textbox(s, 0.78, 6.62, 11.7, 0.3)
    write(s.shapes[-1].text_frame, [{
        "text": "说明：运行时同时被 Web、API 与渠道复用；渠道消息先落库再进入运行时，保证幂等与断点恢复。",
        "size": 9.2, "color": MUTED}])
    notes(s, "架构按目录与调用关系整理：api/ 与 channels/ 为入口，core/ 为运行时，skills/ knowledge/ tools/ 等为应用能力。\n"
             "注意：这里是逻辑分层，物理上仍是单个 Python 进程（可选独立 worker 跑定时任务）。\n"
             "不要画成微服务，也不要暗示有消息队列或 Redis——代码里没有。")

    # 5 request chain
    s = page_header(prs, "04 · 执行链路", "一次请求的八个步骤",
                    "从 HTTP 进入运行时开始，每一步都有持久化记录；重复请求由 turn claim 去重。",
                    page=5, total=TOTAL, deck="技术介绍")
    steps = [
        ("会话与幂等", "session lock / lease；turn claim 命中则直接回放"),
        ("消息落库", "写入用户消息与事件，SSE 订阅从此开始"),
        ("意图与 SOP", "TurnPlanner 在已发布 SOP 中匹配，未命中转通用任务"),
        ("任务编译", "TaskRequestCompiler 生成 TaskRequirement：槽位、记忆、附件、时区"),
        ("能力装配", "服务端构建完整 manifest 并鉴权，只把投影交给模型"),
        ("循环执行", "AgentLoop 按图推进，调用知识/工具，默认最多 32 个动作"),
        ("反思与步进", "pass / retry / ask_user / advance / handoff"),
        ("输出与留痕", "ResponseGenerator 流式返回；RunRecord、TurnRecord、Event 落库"),
    ]
    for i, (h, body) in enumerate(steps):
        col, row = i % 2, i // 2
        cx = 0.78 + col * 6.05
        cy = 1.92 + row * 1.2
        textbox(s, cx, cy, 0.5, 0.4)
        write(s.shapes[-1].text_frame, [{"text": f"{i+1:02d}", "size": 16, "bold": True, "color": RGBColor(0xC5, 0xCE, 0xD8)}])
        textbox(s, cx + 0.55, cy - 0.02, 5.2, 0.3)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 11.5, "bold": True, "color": INK}])
        textbox(s, cx + 0.55, cy + 0.3, 5.2, 0.62)
        write(s.shapes[-1].text_frame, [{"text": body, "size": 9.2, "color": MUTED, "line": 1.35}])
    notes(s, "对应 backend/app/core/harness_v2_engine.py 的 run()/​_run_frame()，以及 agent_loop.py 的 handle_turn。\n"
             "强调两点：一是能力授权在服务端完成，模型只看到裁剪后的清单；二是每轮都会重建 manifest，SOP 节点可以收窄可用能力。\n"
             "默认上限 32 来自 AgentProfile.harness_max_actions，运行设置页面也可见。")

    # 6 sop model
    s = page_header(prs, "05 · SOP 模型", "流程是数据，不是提示词",
                    "SOP 存为图结构：节点、边、起点与终点；发布后参与意图匹配，编辑中的草稿不生效。",
                    page=6, total=TOTAL, deck="技术介绍")
    picture(s, assets["sop"], 6.55, 1.95, 5.95, 5.95 / 1.86, caption="编辑器中的节点配置（演示 SOP：procurement_request_checklist）")
    items = [
        ("结构　", "content_json 保存 nodes / edges / start_node_id / terminal_node_ids。"),
        ("节点　", "七类：collect_info、decision、tool_call、knowledge_query、response、handoff、subflow。"),
        ("约束　", "allowed_actions、knowledge_scope、capability_refs、retry_policy、assignee。"),
        ("版本　", "draft → published → archived；回滚产生新版本，不覆盖历史。"),
        ("分支　", "员工可复制为私有分支，sync_state 标记 synced / diverged。"),
    ]
    y = 1.98
    for lead, rest in items:
        textbox(s, 0.78, y, 5.55, 0.95)
        tf = s.shapes[-1].text_frame
        p = tf.paragraphs[0]
        p.line_spacing = 1.45
        r1 = p.add_run(); r1.text = lead
        set_font(r1, size=10.5, bold=True, color=ACCENT)
        r2 = p.add_run(); r2.text = rest
        set_font(r2, size=10.5, color=BODY)
        y += 0.9
    textbox(s, 0.78, 6.45, 5.6, 0.4)
    write(s.shapes[-1].text_frame,
          [{"text": "数据模型：Skill / SkillVersion / AgentSkillBranch（backend/app/db/models.py）", "size": 8.6, "color": MUTED}])
    notes(s, "依据 skills/skill_schema.py 与 core/graph_rules.py。\n"
             "注意命名坑：数据库里的 Skill 指的是 SOP（历史命名），通用技能另有 GeneralSkill 模型，二者不是一回事。\n"
             "节点类型 handoff 才会触发转人工，assignee 决定待办指派给谁。")

    # 7 knowledge
    s = page_header(prs, "06 · 知识检索", "先定位文档，再回到原文",
                    "入库阶段建立结构，检索阶段分层收敛；模型只负责选文档，不负责编造原文。",
                    page=7, total=TOTAL, deck="技术介绍")
    textbox(s, 0.78, 1.9, 5.6, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "入库", "size": 10, "bold": True, "color": ACCENT}])
    inb = ["解析：PDF / DOCX / HTML / Markdown", "分桶：按主题归并文档", "切分：章节、页面、chunk", "建页：OKF 概念页与引用"]
    y = 2.25
    for t in inb:
        chip(s, 0.78, y, 5.4, 0.42, t, fill=PAPER, color=BODY, size=9.2, line=RULE)
        y += 0.52
    textbox(s, 0.78, 4.5, 5.6, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "检索", "size": 10, "bold": True, "color": ACCENT}])
    ret = ["概念匹配：从查询找到候选概念页", "文档路由：模型选文档，失败回退词法 top-5", "桶内取原文：返回 chunk 与 citations", "route_trace：记录每一跳，便于调试"]
    y = 4.85
    for t in ret:
        chip(s, 0.78, y, 5.4, 0.42, t, fill=RGBColor(0xEC, 0xF3, 0xF1), color=BODY, size=9.2, line=None)
        y += 0.52
    picture(s, assets["knowledge"], 6.55, 1.98, 5.95, 5.95 / 2.2, caption="知识库列表：文档数、目录索引数、引用来源数")
    notes(s, "依据 knowledge/service.py 的 search()/_search()，链路为 concept → document → bucket → chunk。\n"
             "关键设计：LLM 只做“选文档”，原文由检索层取出，降低编造空间；失败时有词法回退，链路不会直接断。\n"
             "route_trace 是排查召回问题的入口，界面上对应“检索调试”。")

    # 8 capability manifest
    s = page_header(prs, "07 · 能力与鉴权", "模型看到的，只是它能用的",
                    "能力清单在服务端按租户、员工、SOP 节点与资源绑定计算，投影后才进入模型上下文。",
                    page=8, total=TOTAL, deck="技术介绍")
    rect(s, 0.78, 2.0, 3.5, 2.2, fill=PAPER, line=RULE, radius=0.05)
    textbox(s, 0.98, 2.18, 3.1, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "全量 manifest（服务端）", "size": 10.5, "bold": True, "color": INK}])
    textbox(s, 0.98, 2.58, 3.15, 1.5)
    write(s.shapes[-1].text_frame, [
        {"text": "· 文件、通用技能、工具、知识", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· SOP 节点允许的 sop_specific 能力", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 用于调用时的二次校验", "size": 9.4, "color": BODY, "line": 1.4},
    ])
    arrow_right(s, 4.42, 2.95, w=0.4, h=0.2)
    rect(s, 4.98, 2.0, 3.5, 2.2, fill=RGBColor(0xEC, 0xF3, 0xF1), line=None, radius=0.05)
    textbox(s, 5.18, 2.18, 3.1, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "投影（进入模型）", "size": 10.5, "bold": True, "color": INK}])
    textbox(s, 5.18, 2.58, 3.15, 1.5)
    write(s.shapes[-1].text_frame, [
        {"text": "· 名称、用途、参数摘要", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 不含鉴权与凭证细节", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 结果过大时落为 sandbox_json_file", "size": 9.4, "color": BODY, "line": 1.4},
    ])
    arrow_right(s, 8.62, 2.95, w=0.4, h=0.2)
    rect(s, 9.18, 2.0, 3.4, 2.2, fill=PAPER, line=RULE, radius=0.05)
    textbox(s, 9.38, 2.18, 3.0, 0.3)
    write(s.shapes[-1].text_frame, [{"text": "调用时", "size": 10.5, "bold": True, "color": INK}])
    textbox(s, 9.38, 2.58, 3.0, 1.5)
    write(s.shapes[-1].text_frame, [
        {"text": "· 校验租户 / 员工 / 绑定", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 密钥占位符在服务端替换", "size": 9.4, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 记录调用与耗时", "size": 9.4, "color": BODY, "line": 1.4},
    ])
    textbox(s, 0.78, 4.55, 11.8, 1.9)
    write(s.shapes[-1].text_frame, [
        {"text": "为什么这样做", "size": 10.5, "bold": True, "color": ACCENT, "space_after": 6},
        {"text": "把权限判断放在模型之外：即使模型被诱导，也只能申请投影里出现过的能力；"
                 "真正的越权判断由服务端在调用时再做一次。SOP 节点还能进一步收窄范围，"
                 "例如“收集需求”这一步只允许知识检索，不允许下单。", "size": 10.2, "color": BODY, "line": 1.55, "space_after": 6},
        {"text": "对应实现：core/capability_manifest.py、core/harness_capability_invoker.py、core/context_projection.py。",
         "size": 8.8, "color": MUTED},
    ])
    notes(s, "这是整套设计里最值得讲的一点：授权与执行分离。\n"
             "另外注意大结果落文件（sandbox_json_file），避免把整页数据塞回上下文——对话里看到的知识检索结果就是这种形态。")

    # 9 runtime records
    s = page_header(prs, "08 · 运行与可观测", "每一步动作、耗时与结果都留在记录里",
                    "执行记录面向排障；运行设置控制循环上限与上下文预算；两者共同决定一次运行的可解释程度。",
                    page=9, total=TOTAL, deck="技术介绍")
    picture(s, assets["chat"], 0.78, 1.95, 7.55, 7.55 / 1.62,
            caption="真实运行：判断意图 15.5s、SOP 任务 203.6s、知识检索 70.7s、读文件 13.3s，4 个动作后完成")
    items = [
        ("上限", "反思轮数与单轮最大动作数（默认 32），防止循环失控。"),
        ("上下文", "按 token 预算压缩，保留最近 6 轮，长期与近期摘要分开预算。"),
        ("记录", "RunRecord / TurnRecord / InvocationRecord / Event，SSE 断线可续传。"),
        ("反馈", "按 model / SOP / knowledge / tool 等维度归因，回到流程与知识改进。"),
    ]
    y = 2.0
    for lead, rest in items:
        textbox(s, 8.62, y, 3.98, 1.2)
        tf = s.shapes[-1].text_frame
        p = tf.paragraphs[0]
        p.line_spacing = 1.4
        r1 = p.add_run(); r1.text = lead + "　"
        set_font(r1, size=9.6, bold=True, color=ACCENT)
        r2 = p.add_run(); r2.text = rest
        set_font(r2, size=9.6, color=BODY)
        y += 1.2
    notes(s, "截图来源：本地实例 /workspace/chat，员工“采购”，模型 gml，演示数据。\n"
             "运行设置中的默认值（单轮 32 个动作、上下文预算 32000、压缩触发 0.7、保留 6 轮）来自 /enterprise/runtime-settings。\n"
             "这里的数值是可配置的默认值，不是性能承诺；不同部署可以调低以控制成本，或调高以支持长流程。\n"
             "公开 Trace 不含原始思维链（Open API 文档明确说明），排障依赖事件与动作记录。")

    # 10 integration
    s = page_header(prs, "09 · 接入面", "模型、API、MCP、渠道与定时任务",
                    "六个接入方向，各自有明确的鉴权与边界；未实现的部分在末页列出。",
                    page=10, total=TOTAL, deck="技术介绍")
    grid = [
        ("模型", ["OpenAI Chat Completions 兼容", "多份模型配置，一份默认", "接口超时默认 600s"]),
        ("开放 API", ["Bearer 员工级密钥", "Idempotency-Key / ETag", "SSE 流式 + 事件续传"]),
        ("MCP", ["stdio 与 streamable HTTP", "可发现、同步、测试", "凭证与请求头掩码返回"]),
        ("渠道", ["企微 / 微信 / 微信客服 / 飞书 / 钉钉", "统一入站、出站与身份绑定", "指令：切换、绑定、解绑"]),
        ("定时任务", ["once / daily / weekly / monthly", "支持 RRULE，默认 Asia/Shanghai", "需常驻进程或独立 worker"]),
        ("工具", ["HTTP 工具，支持占位符替换", "密钥 ${secret.NAME} 注入", "默认超时 8s"]),
    ]
    for i, (h, items) in enumerate(grid):
        col, row = i % 3, i // 3
        cx = 0.78 + col * 3.99
        cy = 1.92 + row * 2.3
        rect(s, cx, cy, 3.72, 2.05, fill=PAPER, line=RULE, radius=0.05)
        textbox(s, cx + 0.26, cy + 0.2, 3.2, 0.3)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 10.5, "bold": True, "color": ACCENT}])
        textbox(s, cx + 0.26, cy + 0.58, 3.24, 1.35)
        write(s.shapes[-1].text_frame,
              [{"text": "· " + t, "size": 8.4, "color": BODY, "line": 1.35, "space_after": 4} for t in items])
    notes(s, "依据 docs/open-api-v1.md、tools/mcp_client.py、channels/adapters/、scheduled_tasks/schema.py 与 config.py。\n"
             "渠道适配器在代码中存在，但本地演示库没有绑定任何渠道账号，因此没有真实渠道截图。\n"
             "定时任务在本地演示库同样为空，页面截图未使用，避免展示空白页。")

    # 11 security
    s = page_header(prs, "10 · 权限、安全与隔离", "四道边界：租户、角色、密钥、沙盒",
                    "默认是本机单租户部署；多租户字段齐全，但对外多租户运营仍需评估。",
                    page=11, total=TOTAL, deck="技术介绍")
    rows = [
        ("身份与租户", "本地账号 PBKDF2-SHA256（12 万次迭代）；HMAC 令牌 14 天；所有查询带 tenant_id，跨租户返回 403。"),
        ("角色与可见性", "管理员 / 成员两级；员工有负责人、可见范围与资源绑定；开放 API 按密钥主体每次重算权限。"),
        ("密钥与凭证", "渠道凭证与工具密钥用 Fernet（密钥由 APP_SECRET 派生）加密落库，接口只返回掩码；API 密钥仅存摘要与前缀。"),
        ("工具执行", "命令走 OS 进程沙盒（优先 SRT，Linux 可回退 bwrap），仅任务工作区可写；网络策略 all / allowlist / deny，无法表达时拒绝执行。"),
    ]
    y = 1.95
    for h, body in rows:
        textbox(s, 0.78, y, 2.1, 0.4)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 10.5, "bold": True, "color": INK}])
        textbox(s, 3.0, y, 9.5, 0.95)
        write(s.shapes[-1].text_frame, [{"text": body, "size": 10, "color": BODY, "line": 1.5}])
        hline(s, 0.78, y + 1.02, 11.75, color=RGBColor(0xEC, 0xEF, 0xF3), weight=0.6)
        y += 1.2
    rect(s, 0.78, 6.25, 11.75, 0.62, fill=RGBColor(0xFD, 0xF4, 0xE6), line=None, radius=0.08)
    textbox(s, 0.98, 6.38, 11.35, 0.4)
    write(s.shapes[-1].text_frame, [{
        "text": "需确认：backend/README.md 称“没有无沙盒降级”，但 harness/sandbox.py 在 Windows 缺 SRT 时会降级为无沙盒执行；README 提到的 OIDC 在代码中未实现。",
        "size": 8.6, "color": RGBColor(0x8A, 0x5A, 0x18), "line": 1.3}])
    notes(s, "安全部分只写代码能核实的内容：security/auth.py（14 天令牌、PBKDF2 12 万次）、security/encryption.py（Fernet）、"
             "permissions.py（角色与员工范围）、harness/sandbox.py（网络策略与诊断）。\n"
             "两处矛盾必须在方案阶段确认：沙盒降级行为、OIDC 是否支持。")

    # 12 limits
    s = page_header(prs, "11 · 现状与限制", "能做什么、还差什么、需要谁确认",
                    "以下按“已实现 / 需配置 / 待确认”三类整理，便于评估工作量与风险。",
                    page=12, total=TOTAL, deck="技术介绍")
    data = [
        ("已实现", "员工、SOP、知识、工具、渠道、定时任务、运行记录、反馈、团队协作模块"),
        ("需配置", "模型服务与密钥、渠道账号、工具与 MCP、知识文档、SOP 发布"),
        ("待确认", "对外版本号；品牌（RapidStaff / StaffDeck）；OIDC；沙盒降级；非 SQLite 迁移"),
    ]
    y = 1.95
    for h, body in data:
        color = ACCENT if h == "已实现" else (ACCENT_WARM if h == "需配置" else RGBColor(0x9A, 0x3B, 0x3B))
        textbox(s, 0.78, y, 1.1, 0.36)
        write(s.shapes[-1].text_frame, [{"text": h, "size": 10.5, "bold": True, "color": color}])
        textbox(s, 2.0, y, 10.5, 0.6)
        write(s.shapes[-1].text_frame, [{"text": body, "size": 9.8, "color": BODY, "line": 1.45}])
        y += 0.78
    hline(s, 0.78, 4.4, 11.75, color=RULE, weight=0.75)
    textbox(s, 0.78, 4.6, 5.7, 0.32)
    write(s.shapes[-1].text_frame, [{"text": "已知限制", "size": 10, "bold": True, "color": INK}])
    textbox(s, 0.78, 4.98, 5.6, 1.8)
    write(s.shapes[-1].text_frame, [
        {"text": "· 群聊多员工仍在路线图（未勾选），但 teams 模块代码已存在。", "size": 9.2, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 大屏只在对话内渲染，不取数、不做 SQL 聚合。", "size": 9.2, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· 删除为归档，不做物理删除。", "size": 9.2, "color": BODY, "line": 1.4},
    ])
    textbox(s, 6.75, 4.6, 5.8, 0.32)
    write(s.shapes[-1].text_frame, [{"text": "部署事实", "size": 10, "bold": True, "color": INK}])
    textbox(s, 6.75, 4.98, 5.75, 1.8)
    write(s.shapes[-1].text_frame, [
        {"text": "· 无 Dockerfile / compose，单进程 + 可选 worker。", "size": 9.2, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· SQLite 为唯一受支持的迁移路径，切库需重写迁移层。", "size": 9.2, "color": BODY, "line": 1.4, "space_after": 4},
        {"text": "· AGPL 3.0，对外提供服务前确认授权方式。", "size": 9.2, "color": BODY, "line": 1.4},
    ])
    notes(s, "收尾页用于对齐预期：能力已经可跑，但版本、品牌、OIDC、沙盒降级、数据库迁移都还没有定论。\n"
             "建议在技术评审会上逐条确认，并把结论写回 README 与部署文档，避免口头承诺。\n"
             "本页不含任何未经验证的性能或容量数字。")

    prs.save(out_path)
    return out_path


def main():
    assets = build_assets()
    a = deck_a(assets, ROOT / "StaffDeck 客户方案与产品宣讲.pptx")
    b = deck_b(assets, ROOT / "StaffDeck 技术项目介绍.pptx")
    print("saved", a)
    print("saved", b)


if __name__ == "__main__":
    main()
