import streamlit as st
import fitz  # PyMuPDF（读 PDF 的“瑞士军刀”）
import dashscope
from dashscope.api_entities.dashscope_response import Role
import base64
from docx import Document
import io
import random
import json
import re
import threading
import time
import os

# =========================================================
# 🧩 这份代码的“总导演”说明（给入门同学看的）
# =========================================================
# 你可以把整个 App 想成一家“榨汁店”：
# - 课件/PDF：原材料（水果）
# - 知识清单：榨出的“果汁”（核心营养）
# - 考考我：用果汁做“试饮挑战”（题目）
# - 答疑：店员只根据果汁回答，不瞎编新水果
# - 真题：像“顾客口味偏好”，会把高频考点权重拉高
#
# 关键产品设计点：
# 1) 点击开始学习后：显示一行等待提示，并禁用按钮，避免用户狂点。
# 2) 真·流式：模型生成时我们实时更新“进度行”，但不把半截 JSON 展示给用户（避免乱码）。
# 3) 0基础 vs 高玩：流程一样，但“榨汁配方”（prompt）不同 => 知识清单条数/风格、出题难度不同。

# =========================================================
# [UI 文案集中管理]
# =========================================================
UI = {
    # 页面/标题
    "PAGE_TITLE": "榨知机 V1.5",
    "SIDEBAR_TITLE": "🍋 榨知机 V1.5",
    "MAIN_TITLE": "🍋 榨知机 V1.5：你的期末救星",

    # Secrets 状态
    "DEV_MODE": "✅ 开发者演示模式",

    # 工具箱区
    "TOOLBOX": "🛠️ 工具箱",
    "QUIZ_BTN": "🙋‍♂️ 考考我（来一组题练练手）",
    "DOWNLOAD_BTN": "📄 下载 知识清单",

    # 设置区
    "SETTINGS": "### ⚙️ 复习设置",
    "GOAL": "目标：",
    "MODE_BEGINNER": "俺0基础 ！（召唤所有知识）",
    "MODE_PRO": "我是高玩！（直接上最难的）",
    "UPLOAD_MAIN": "上传课件 (必需)",
    "UPLOAD_EXAM": "上传真题 (可选，用于标记考点)",
    "START_BTN": "🚀 开始学习",

    # 主区等待提示（你指定）
    "WAIT_LINE": "榨汁机正在运转，大概三分钟就可以出餐！",

    # 生成结果提示
    "GEN_FAIL": "知识清单生成失败：",
    "GEN_SUCCESS": "✅ 《{course_name}》知识清单已生成！侧边栏可下载/考考我。",

    # 左侧展示
    "LEFT_HEADER": "📄 核心知识清单",
    "MD_TITLE": "# 📘 核心知识清单",
    "MD_EXPLAIN": "- 解释：",
    "MD_EXAMPLE": "- 例子：",
    "MD_SCORE": "（重点指数：{score}）",

    # 右侧
    "RIGHT_HEADER": "🤖 右侧：答疑 / 刷题与批改",
    "MODE_SWITCH": "模式",
    "MODE_QA": "答疑",
    "MODE_QUIZ": "刷题与批改",

    # 答疑
    "QA_INPUT": "基于知识清单提问…（不会回答知识清单未覆盖的新考点）",
    "QA_SPINNER": "AI 正在基于知识清单作答...",

    # 刷题
    "QUIZ_HINT": "点击侧边栏「考考我」开始刷题",
    "QUIZ_TITLE": "### 📝 高频卷（10题）",
    "QUIZ_PROGRESS": "进度：第 {cur}/{total} 题｜题组：{set_id}",
    "QUIZ_CONCEPT": "考点：{title}",
    "QUIZ_STEM": "**题目：**",

    # 错题循环
    "REMEDIAL_TITLE": "### 🔁 错题循环（直到掌握）",
    "REMEDIAL_META": "考点：{title} ｜ 已错次数：{n}/4",
    "REMEDIAL_STUCK": "该考点已连续错误达到上限（4次）。建议回看该概念后再挑战。",
    "REMEDIAL_REVIEW": "📌 建议回看：",
    "REMEDIAL_DONE": "🎉 错题已全部掌握！本轮结束。",
    "REMEDIAL_END": "🎉 错题循环已结束（部分考点需回看后再挑战）。",

    # 批改反馈
    "GRADE_FAIL": "⚠️ 批改失败：",
    "CORRECT": "✅ 正确",
    "WRONG": "❌ 错误",
    "RIGHT_ANSWER": "正确答案：",
    "SUGGEST_REVIEW": "建议回看该概念。",

    # 下一题
    "NEXT_BTN": "➡️ 下一题 / 继续",

    # 简答/论述
    "TEXTAREA_LABEL": "请输入你的答案：",
    "TEXTAREA_PLACEHOLDER": "简答/论述：写要点即可，越结构化越好。",
    "SUBMIT_BTN": "✅ 提交答案",
    "NEED_INPUT": "请先输入答案。",

    # 未生成提示
    "EMPTY_HINT": "👈 请先在左侧上传课件并点击「开始学习」。生成知识清单后才会解锁：下载 / 高频刷题 / 答疑。",

    # 上传中文引导（覆盖组件内部英文体验）
    "UPLOAD_TIP": "把 PDF 拖到下面区域，或点击浏览按钮上传。",
}

# =========================================================
# [配置层]
# =========================================================
st.set_page_config(page_title=UI["PAGE_TITLE"], page_icon="🍋", layout="wide")


# =========================================================
# [State Init]
# =========================================================
def init_state():
    # course_name：课程名（像“饮品名”）
    if "course_name" not in st.session_state:
        st.session_state.course_name = "通用课程"

    if "run_generation" not in st.session_state:
        st.session_state.run_generation = False

    # review_pack：结构化知识清单（像“榨出来的果汁配方”）
    if "review_pack" not in st.session_state:
        st.session_state.review_pack = None  # dict

    # result_content：把 review_pack 转成 Markdown 给前端显示
    if "result_content" not in st.session_state:
        st.session_state.result_content = ""  # markdown（知识清单）

    # 答疑对话（右侧答疑 tab）
    if "qa_messages" not in st.session_state:
        st.session_state.qa_messages = []

    # 缓存题组（至少保证本地永远有 1 套题，做到“秒开”）
    if "question_sets" not in st.session_state:
        st.session_state.question_sets = []  # list[dict]

    # 后台生成标记（前端不展示）
    if "next_set_generating" not in st.session_state:
        st.session_state.next_set_generating = False
    if "next_set_error" not in st.session_state:
        st.session_state.next_set_error = ""

    # 生成锁：点击开始学习后，禁用按钮防止重复点击
    if "is_generating" not in st.session_state:
        st.session_state.is_generating = False

    # 记录学习模式（0基础 / 高玩），后台线程也要用
    if "study_mode" not in st.session_state:
        st.session_state.study_mode = UI["MODE_BEGINNER"]

    # 刷题状态机（像“闯关系统”）
    if "quiz" not in st.session_state:
        st.session_state.quiz = {
            "active": False,
            "phase": "main",           # main / remedial
            "questions": [],
            "idx": 0,
            "last_feedback": None,     # 上一次批改结果展示内容
            "await_next": False,       # 是否等待“下一题 / 继续”
            "wrong_concepts": {},      # cid -> {"attempts": int, "stuck": bool, "preferred_type": str}
            "concept_mastery": {},     # cid -> bool（这个知识点是否掌握）
            "remedial_queue": [],      # 错题循环队列 list[cid]
            "current_set_id": None,
        }


init_state()


# =========================================================
# [工具层]
# =========================================================
def extract_text_from_pdf(file_bytes: bytes) -> str:
    """从 PDF 直接抠文字。像“把水果皮剥了，看里面的果肉”"""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def pdf_pages_to_base64_images(file_bytes: bytes, max_pages: int = 5):
    """
    当 PDF 里没多少可复制文字（比如全是扫描图），就把前几页渲成图片喂给视觉模型。
    像“果肉看不清，只能拍照让店员识别”。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for i in range(min(len(doc), max_pages)):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=72)
        b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        images.append(f"data:image/png;base64,{b64}")
    doc.close()
    return images


def generate_word_file_from_markdown(course_name: str, md: str):
    """把 Markdown 讲义转成 Word，像“把果汁装瓶贴标签”"""
    doc = Document()
    doc.add_heading(f'知识清单（{course_name}）', 0)
    for line in md.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        elif line.startswith('- ') or line.startswith('* '):
            doc.add_paragraph(line[2:], style='List Bullet')
        else:
            doc.add_paragraph(line.replace('**', '').replace('__', ''))
    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


def safe_extract_json(text: str):
    """
    模型可能会在 JSON 外面夹带一些废话。
    我们像“滤网”一样，从中间抓出第一个 {...} 结构，然后尝试 JSON 解析。
    """
    if not text:
        return None
    m = re.search(r'(\{.*\})', text, re.S)
    if not m:
        return None
    raw = m.group(1)
    try:
        return json.loads(raw)
    except:
        # 兜底：去掉末尾多余逗号（常见模型小错误）
        raw2 = re.sub(r',\s*([\]}])', r'\1', raw)
        try:
            return json.loads(raw2)
        except:
            return None


def concepts_to_markdown(pack: dict) -> str:
    """把结构化知识清单变成能在前端展示的 Markdown"""
    lines = [UI["MD_TITLE"]]
    if not pack or "concepts" not in pack:
        return "\n".join(lines)

    for c in pack["concepts"]:
        title = c.get("title", "未命名考点")
        explain = c.get("explain", "")
        example = c.get("example", "")
        score = c.get("final_score", c.get("model_score", 0))
        star = "⭐ " if c.get("is_star") else ""
        lines.append(f"## {star}{title}{UI['MD_SCORE'].format(score=score)}")
        if explain:
            lines.append(f"{UI['MD_EXPLAIN']}{explain}")
        if example:
            lines.append(f"{UI['MD_EXAMPLE']}{example}")
        lines.append("")
    return "\n".join(lines)


def is_beginner_mode(mode_str: str) -> bool:
    """判断模式：像“看看顾客选的是儿童杯还是加强版浓缩”"""
    return mode_str.strip().startswith("俺0基础")


# =========================================================
# [模型层]
# =========================================================
def _set_api_key(api_key: str):
    # 把用户输入/Secrets 的 Key 清洗一下
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")


def call_qwen_vl_vision(images, api_key: str) -> str:
    """视觉模型：当 PDF 是图片时，用它提取文字/要点"""
    _set_api_key(api_key)
    content = [{"text": "分析PPT截图，提取核心知识点（尽量结构化输出）。"}] + [{"image": img} for img in images]
    try:
        resp = dashscope.MultiModalConversation.call(
            model='qwen-vl-max',
            messages=[{"role": "user", "content": content}]
        )
        if resp.status_code == 200:
            return resp.output.choices[0]['message']['content'][0]['text']
        return ""
    except:
        return ""


def _concepts_system_prompt(mode_str: str) -> str:
    """
    0基础 vs 高玩 的“配方差异”在这里体现：
    - 0基础：清单更全、解释更白话、更像教学
    - 高玩：清单更精炼、解释更考点化、更像押题笔记
    """
    beginner = is_beginner_mode(mode_str)

    n_req = "20-30" if beginner else "12-18"
    style_req = (
        "解释要非常白话、分步骤、像给0基础同学讲；每条尽量提供可类比的生活/直观例子。"
        if beginner else
        "解释要短、考点化、像高分笔记；例子可以更抽象但必须直击考点。"
    )

    return f"""
你是一个“复习包结构化生成器”。请基于用户课件与（可选）真题，输出严格 JSON（不要 markdown，不要解释）。

JSON schema（必须严格遵守）：
{{
  "course_name": "课程名称",
  "concepts": [
    {{
      "id": "c1",
      "title": "概念/考点标题（短）",
      "explain": "一句话到三句话解释（清晰、可背）",
      "example": "一个实际例子（短）",
      "is_star": true/false,
      "model_score": 0-100
    }}
  ]
}}

要求：
1) concepts 输出 {n_req} 条（宁可少而精，覆盖全课）。
2) 如果提供了真题，请将与真题高度一致/同类高频考点标记 is_star=true；若无真题，全部 is_star=false。
3) model_score 表示“你判断的可能考到程度”，0-100。
4) 风格要求：{style_req}
5) 只输出 JSON，不要任何多余字段。
""".strip()


def call_qwen_generate_concepts_json_stream(main_text: str, exam_text: str, api_key: str, mode_str: str, on_progress=None):
    """
    真·流式：像“榨汁机边榨边出汁”，我们边接收边更新进度条（这里只显示一行进度）
    注意：我们不把半截 JSON 展示出来，等榨完了再一次性解析。
    """
    _set_api_key(api_key)
    has_exam = bool(exam_text and exam_text.strip())

    user_content = f"""
真题：{"有" if has_exam else "无"}
【课件正文】：
{main_text[:14000]}
【真题正文】：
{exam_text[:6000] if has_exam else ""}
""".strip()

    system_prompt = _concepts_system_prompt(mode_str)

    full_text = ""
    last_len = 0
    try:
        stream = dashscope.Generation.call(
            model='qwen-plus',
            messages=[
                {'role': Role.SYSTEM, 'content': system_prompt},
                {'role': Role.USER, 'content': user_content},
            ],
            result_format='message',
            stream=True,
            incremental_output=True
        )

        for chunk in stream:
            piece = ""
            # 兼容不同 SDK chunk 形态
            try:
                piece = chunk.output.choices[0]['message']['content']
            except Exception:
                try:
                    piece = chunk["output"]["choices"][0]["message"]["content"]
                except Exception:
                    piece = ""

            if isinstance(piece, str) and piece:
                full_text += piece
            elif isinstance(piece, list) and piece:
                try:
                    full_text += "".join([p for p in piece if isinstance(p, str)])
                except Exception:
                    pass

            if on_progress and len(full_text) != last_len:
                last_len = len(full_text)
                on_progress(last_len)

        return full_text, None
    except Exception as e:
        return "", str(e)


def postprocess_concepts_json(data: dict, exam_text: str):
    """
    评分规则（你定的）：
    - 若有真题：A1=60%（是否真题同类） + 其他=40%（模型判断的可能考到程度）
    - 若无真题：只用模型判断分数
    """
    if not data or "concepts" not in data:
        return None, "JSON 缺少 concepts"

    has_exam = bool(exam_text and exam_text.strip())
    concepts = data["concepts"]

    for c in concepts:
        is_star = bool(c.get("is_star")) if has_exam else False
        ms = c.get("model_score", 0)
        try:
            ms = int(ms)
        except:
            ms = 0
        ms = max(0, min(100, ms))

        if has_exam:
            final = round(0.6 * (100 if is_star else 0) + 0.4 * ms)
        else:
            final = ms
            c["is_star"] = False

        c["model_score"] = ms
        c["final_score"] = int(final)

    data["course_name"] = data.get("course_name") or "通用课程"
    data["concepts"] = sorted(concepts, key=lambda x: x.get("final_score", 0), reverse=True)
    return data, None


def call_qwen_generate_question_set(review_pack: dict, api_key: str, mode_str: str):
    """
    生成 10 道题（7单选+2简答+1论述），按高频->低频排序。
    0基础 vs 高玩：题目难度和干扰项强度不同，但题型比例不变。
    """
    _set_api_key(api_key)
    concepts = review_pack.get("concepts", []) if review_pack else []

    # 取高频概念，作为出题锚点（像“从最甜的水果先榨”）
    top = concepts[:12]
    top_payload = [
        {
            "id": c.get("id"),
            "title": c.get("title"),
            "explain": c.get("explain"),
            "example": c.get("example"),
            "is_star": c.get("is_star", False),
            "final_score": c.get("final_score", 0),
        } for c in top
    ]

    beginner = is_beginner_mode(mode_str)
    difficulty_req = (
        "难度偏基础：以定义、辨析、理解为主，干扰项不要太阴间。"
        if beginner else
        "难度偏高：增加易混淆选项与边界条件，考察概念辨析与应用陷阱。"
    )
    style_req = (
        "题干更清晰直接，避免过长；简答/论述给出明确作答方向。"
        if beginner else
        "题干更像真题，设置反直觉陷阱但不允许超纲；简答/论述更看结构与推理。"
    )

    system_prompt = f"""
你是一个“高频考点出题器”。请严格输出 JSON（不要 markdown，不要解释）。
目标：生成一组 10 道题，按“最可能考”到“较不可能考”排序。

规则：
1) 必须严格生成：7道单选题 + 2道简答题 + 1道综合论述题。
2) 每道题必须绑定一个概念 concept_id，并尽量覆盖不同概念。
3) 单选题提供 A/B/C/D 四个选项；answer 必须是 "A"|"B"|"C"|"D"。
4) 简答/论述题 answer 用“要点列表”的形式给评分要点。
5) 解析 explanation 要简洁，允许少量扩展解释，但考点必须来自给定概念。
6) questions 必须按概念 final_score 高->低排序优先绑定。
7) {difficulty_req}
8) {style_req}

JSON schema：
{{
  "set_id": "string",
  "questions": [
    {{
      "qid": 1,
      "type": "single"|"short"|"essay",
      "concept_id": "c1",
      "concept_title": "概念标题",
      "stem": "题干",
      "options": {{"A":"...", "B":"...", "C":"...", "D":"..."}},
      "answer": "A" 或 ["要点1","要点2"],
      "explanation": "解析（用于答错时）"
    }}
  ]
}}
""".strip()

    user_content = json.dumps({"top_concepts": top_payload}, ensure_ascii=False)

    try:
        resp = dashscope.Generation.call(
            model='qwen-plus',
            messages=[
                {'role': Role.SYSTEM, 'content': system_prompt},
                {'role': Role.USER, 'content': user_content},
            ],
            result_format='message'
        )
        if resp.status_code != 200:
            return None, f"Error: {resp.message}"

        raw = resp.output.choices[0]['message']['content']
        data = safe_extract_json(raw)
        if not data or "questions" not in data:
            return None, "Error: 题组 JSON 解析失败"

        if not data.get("set_id"):
            data["set_id"] = f"set_{int(time.time())}_{random.randint(1000,9999)}"

        return data, None
    except Exception as e:
        return None, str(e)


def call_qwen_grade_answer(question: dict, user_answer: str, review_pack: dict, api_key: str):
    """
    批改：你要求的策略
    - 对：只说“你为什么做对了”
    - 错：给正确答案 + 解析，并作为错题进入循环
    """
    _set_api_key(api_key)
    concept_id = question.get("concept_id")
    concept_ctx = None
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            concept_ctx = c
            break
    concept_ctx = concept_ctx or {}

    system_prompt = """
你是一个“严格锚定知识清单的阅卷老师”。请只输出 JSON（不要 markdown，不要解释）。
要求：
1) 判断用户答案正确与否。
2) 若正确：只输出 why_correct（告诉用户他为什么做对了）。
3) 若错误：输出 correct_answer + explanation。
4) 允许少量扩展解释，但考点必须来自 concept_context。
JSON schema：
{
  "is_correct": true/false,
  "why_correct": "string",
  "correct_answer": "string",
  "explanation": "string"
}
注意：正确时 correct_answer/explanation 允许为空；错误时 why_correct 允许为空。
""".strip()

    payload = {
        "question": question,
        "user_answer": user_answer,
        "concept_context": {
            "title": concept_ctx.get("title", ""),
            "explain": concept_ctx.get("explain", ""),
            "example": concept_ctx.get("example", ""),
            "is_star": concept_ctx.get("is_star", False),
        }
    }

    try:
        resp = dashscope.Generation.call(
            model='qwen-plus',
            messages=[
                {'role': Role.SYSTEM, 'content': system_prompt},
                {'role': Role.USER, 'content': json.dumps(payload, ensure_ascii=False)},
            ],
            result_format='message'
        )
        if resp.status_code != 200:
            return None, f"Error: {resp.message}"

        raw = resp.output.choices[0]['message']['content']
        data = safe_extract_json(raw)
        if not data or "is_correct" not in data:
            return None, "Error: 批改 JSON 解析失败"
        return data, None
    except Exception as e:
        return None, str(e)


def call_qwen_generate_similar_question(concept_id: str, preferred_type: str, review_pack: dict, api_key: str):
    """
    错题再练：同考点换问法。
    像“同一道菜换个摆盘”，让你确认不是蒙对的。
    """
    _set_api_key(api_key)
    concept_ctx = None
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            concept_ctx = c
            break
    concept_ctx = concept_ctx or {}

    qtype = preferred_type if preferred_type in ["single", "short", "essay"] else "single"

    system_prompt = """
你是一个“错题再训练出题器”。请严格输出 JSON（不要 markdown，不要解释）。
目标：出一道“同考点、不同问法”的类似题，用于纠错训练。
JSON schema：
{
  "qid": 999,
  "type": "single"|"short"|"essay",
  "concept_id": "c1",
  "concept_title": "概念标题",
  "stem": "题干",
  "options": {"A":"...", "B":"...", "C":"...", "D":"..."},
  "answer": "A" 或 ["要点1","要点2"],
  "explanation": "解析"
}
""".strip()

    payload = {
        "type": qtype,
        "concept_context": {
            "id": concept_id,
            "title": concept_ctx.get("title", ""),
            "explain": concept_ctx.get("explain", ""),
            "example": concept_ctx.get("example", ""),
        }
    }

    try:
        resp = dashscope.Generation.call(
            model='qwen-plus',
            messages=[
                {'role': Role.SYSTEM, 'content': system_prompt},
                {'role': Role.USER, 'content': json.dumps(payload, ensure_ascii=False)},
            ],
            result_format='message'
        )
        if resp.status_code != 200:
            return None, f"Error: {resp.message}"

        raw = resp.output.choices[0]['message']['content']
        data = safe_extract_json(raw)
        if not data or "stem" not in data:
            return None, "Error: 类似题 JSON 解析失败"

        data["concept_id"] = concept_id
        data["concept_title"] = concept_ctx.get("title", data.get("concept_title", ""))
        return data, None
    except Exception as e:
        return None, str(e)


def call_qwen_qa(user_question: str, review_pack: dict, api_key: str):
    """
    答疑：只能基于知识清单回答。
    像“店员只用现有果汁配方回答，不会凭空发明新水果”。
    """
    _set_api_key(api_key)
    concepts = (review_pack.get("concepts") or [])[:25]
    ctx = [{"title": c.get("title", ""), "explain": c.get("explain", ""), "example": c.get("example", "")} for c in concepts]

    system_prompt = """
你是一个“知识清单内答疑助教”。请基于给定知识清单内容回答问题。
要求：
1) 优先引用知识清单的解释与例子进行回答。
2) 允许少量扩展解释，但不要引入与知识清单无关的新考点。
3) 如果用户问到清单未覆盖内容，说明“知识清单未包含”，并建议补充课件或生成更完整清单。
""".strip()

    payload = {"concepts": ctx, "question": user_question}
    try:
        resp = dashscope.Generation.call(
            model='qwen-plus',
            messages=[
                {'role': Role.SYSTEM, 'content': system_prompt},
                {'role': Role.USER, 'content': json.dumps(payload, ensure_ascii=False)},
            ],
            result_format='message'
        )
        if resp.status_code == 200:
            return resp.output.choices[0]['message']['content']
        return "Error"
    except:
        return "Error"


# =========================================================
# [后台预生成：下一组题（前端静默）]
# =========================================================
def background_generate_next_question_set(api_key: str):
    """
    后台线程：偷偷再榨一杯“下一套题”，保证用户点“考考我”秒开。
    注意：这是“静默动作”，前端不显示“正在生成...”
    """
    try:
        st.session_state.next_set_generating = True
        st.session_state.next_set_error = ""

        pack = st.session_state.review_pack
        if not pack:
            st.session_state.next_set_error = "知识清单不存在"
            return

        mode_str = st.session_state.get("study_mode", UI["MODE_BEGINNER"])
        data, err = call_qwen_generate_question_set(pack, api_key, mode_str)
        if err:
            st.session_state.next_set_error = err
            return

        st.session_state.question_sets.append(data)
    finally:
        st.session_state.next_set_generating = False


def ensure_next_set_async(api_key: str):
    """保证题库里至少有 1 套题。如果没有，就开个后台线程生成。"""
    if st.session_state.next_set_generating:
        return
    if len(st.session_state.question_sets) >= 1:
        return
    t = threading.Thread(target=background_generate_next_question_set, args=(api_key,), daemon=True)
    t.start()


# =========================================================
# [刷题状态机]
# =========================================================
def quiz_reset():
    st.session_state.quiz = {
        "active": False,
        "phase": "main",
        "questions": [],
        "idx": 0,
        "last_feedback": None,
        "await_next": False,
        "wrong_concepts": {},
        "concept_mastery": {},
        "remedial_queue": [],
        "current_set_id": None,
    }


def quiz_start_with_set(question_set: dict):
    quiz_reset()
    st.session_state.quiz["active"] = True
    st.session_state.quiz["phase"] = "main"
    st.session_state.quiz["questions"] = question_set.get("questions", [])
    st.session_state.quiz["idx"] = 0
    st.session_state.quiz["current_set_id"] = question_set.get("set_id")


def quiz_current_question():
    qz = st.session_state.quiz
    if not qz["active"]:
        return None
    if qz["idx"] < 0 or qz["idx"] >= len(qz["questions"]):
        return None
    return qz["questions"][qz["idx"]]


def register_wrong_concept(question: dict):
    cid = question.get("concept_id", "unknown")
    qtype = question.get("type", "single")
    wrong = st.session_state.quiz["wrong_concepts"].get(cid)
    if not wrong:
        st.session_state.quiz["wrong_concepts"][cid] = {"attempts": 1, "stuck": False, "preferred_type": qtype}
    else:
        wrong["attempts"] += 1
    st.session_state.quiz["concept_mastery"][cid] = False


def register_correct_concept(question: dict):
    cid = question.get("concept_id", "unknown")
    st.session_state.quiz["concept_mastery"][cid] = True


def build_remedial_queue():
    wrong = st.session_state.quiz["wrong_concepts"]
    queue = []
    for cid, meta in wrong.items():
        if meta.get("stuck"):
            continue
        if st.session_state.quiz["concept_mastery"].get(cid) is False:
            queue.append(cid)
    st.session_state.quiz["remedial_queue"] = queue


def move_to_next_question_or_phase():
    qz = st.session_state.quiz
    qz["last_feedback"] = None
    qz["await_next"] = False

    if qz["phase"] == "main":
        qz["idx"] += 1
        if qz["idx"] >= len(qz["questions"]):
            build_remedial_queue()
            if qz["remedial_queue"]:
                qz["phase"] = "remedial"
                qz["idx"] = 0
                qz["questions"] = []
            else:
                qz["active"] = False
    else:
        build_remedial_queue()
        if not qz["remedial_queue"]:
            qz["active"] = False


def get_concept_snippet(concept_id: str, review_pack: dict) -> str:
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            return f"**{c.get('title','')}**：{c.get('explain','')}"
    return ""

def request_generation():
    #只改状态，不主动 rerun。因为点击按钮后 Streamlit 本身就会触发一次 rerun。
    st.session_state.is_generating = True
    st.session_state.run_generation = True


# =========================================================
# [UI层]
# =========================================================
with st.sidebar:
    st.title(UI["SIDEBAR_TITLE"])

    # API Key 来源：secrets / env / 用户输入
    api_key = st.secrets.get("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    if api_key:
        st.success(UI["DEV_MODE"])
    else:
        api_key = st.text_input("API Key", type="password")

    st.markdown("---")

    # 工具箱：仅生成后显示
    if st.session_state.review_pack:
        st.markdown(UI["TOOLBOX"])

        if st.button(UI["QUIZ_BTN"], use_container_width=True):
            if not api_key:
                st.error("请先配置 API Key")
            elif not st.session_state.question_sets:
                # 你允许“点考考我时再提示题组未准备好”
                st.warning("题组还没准备好，稍等几秒再点一次。")
            else:
                qs = st.session_state.question_sets.pop(0)
                quiz_start_with_set(qs)
                # 点开后就后台再生成一套，保证永远有库存
                ensure_next_set_async(api_key)
                st.rerun()

        docx_file = generate_word_file_from_markdown(
            st.session_state.course_name,
            st.session_state.result_content
        )
        st.download_button(
            label=UI["DOWNLOAD_BTN"],
            data=docx_file,
            file_name=f"{st.session_state.course_name}_知识清单.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )

        st.markdown("---")

    st.markdown(UI["SETTINGS"])
    mode = st.radio(UI["GOAL"], (UI["MODE_BEGINNER"], UI["MODE_PRO"]), index=0)
    # 保存模式：后台线程生成题也要知道你选的是哪种
    st.session_state.study_mode = mode

    st.caption(UI["UPLOAD_TIP"])
    uploaded_files = st.file_uploader(UI["UPLOAD_MAIN"], type=["pdf"], accept_multiple_files=True)

    st.caption(UI["UPLOAD_TIP"])
    uploaded_exams = st.file_uploader(UI["UPLOAD_EXAM"], type=["pdf"], key="exam")

    
    st.button(
    UI["START_BTN"],
    type="primary",
    use_container_width=True,
    disabled=st.session_state.is_generating or (st.session_state.review_pack is not None),
    on_click=request_generation
)

# 主界面
st.title(UI["MAIN_TITLE"])

# 一行状态占位（你选 2.1：只显示一行进度）
status_line = st.empty()

st.caption(f"debug: run_generation={st.session_state.run_generation}, is_generating={st.session_state.is_generating}, has_pack={st.session_state.review_pack is not None}")

# =========================================================
# 生成流程：一行等待提示 + 禁用按钮 + 真流式（仅更新进度行）
# =========================================================
if st.session_state.run_generation:
    # 进来就先把等待提示亮出来（此时 UI 已经更新过一次了）
    status_line.info(UI["WAIT_LINE"])

    try:
        # ====== 这里放你原来的重活（PDF提取 + 流式生成 + 解析 + 写入session） ======
        # 重要：生成完成后 st.session_state.review_pack 会被赋值
        # ================================================
        ...
        st.session_state.run_generation = False
        st.session_state.is_generating = False
        status_line.empty()
        st.rerun()

    except Exception as e:
    # 失败：解锁按钮，让用户能重试，同时把错误留在页面上
        st.session_state.run_generation = False
        st.session_state.is_generating = False
        status_line.empty()
        st.error(f"{UI['GEN_FAIL']}{e}")
        st.stop()   # ✅ 不要 rerun，避免把错误刷没


    finally:
        st.session_state.is_generating = False

# =========================================================
# 展示区：有知识清单才解锁
# =========================================================
if st.session_state.review_pack:
    st.success(UI["GEN_SUCCESS"].format(course_name=st.session_state.course_name))

    col1, col2 = st.columns([6, 4])

    with col1:
        st.subheader(UI["LEFT_HEADER"])
        st.markdown(st.session_state.result_content)

    with col2:
        st.subheader(UI["RIGHT_HEADER"])
        tab = st.radio(UI["MODE_SWITCH"], (UI["MODE_QA"], UI["MODE_QUIZ"]), horizontal=True)

        # -------------------------
        # 答疑（只依据知识清单）
        # -------------------------
        if tab == UI["MODE_QA"]:
            for msg in st.session_state.qa_messages:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            q = st.chat_input(UI["QA_INPUT"])
            if q:
                st.session_state.qa_messages.append({"role": "user", "content": q})
                with st.chat_message("user"):
                    st.write(q)

                with st.spinner(UI["QA_SPINNER"]):
                    a = call_qwen_qa(q, st.session_state.review_pack, api_key)

                st.session_state.qa_messages.append({"role": "assistant", "content": a})
                with st.chat_message("assistant"):
                    st.write(a)

        # -------------------------
        # 刷题与批改（ABCD按钮/文本作答）
        # -------------------------
        else:
            qz = st.session_state.quiz

            if not qz["active"]:
                st.info(UI["QUIZ_HINT"])
            else:
                # remedial：动态生成类似题
                if qz["phase"] == "remedial":
                    cid = qz["remedial_queue"][0] if qz["remedial_queue"] else None
                    if not cid:
                        st.success(UI["REMEDIAL_DONE"])
                        qz["active"] = False
                        st.rerun()

                    meta = qz["wrong_concepts"].get(cid, {"attempts": 0, "preferred_type": "single", "stuck": False})
                    attempts = meta.get("attempts", 0)

                    if attempts >= 4:
                        meta["stuck"] = True
                        qz["wrong_concepts"][cid] = meta
                        snippet = get_concept_snippet(cid, st.session_state.review_pack)
                        st.warning(UI["REMEDIAL_STUCK"])
                        if snippet:
                            st.markdown(f"{UI['REMEDIAL_REVIEW']} {snippet}")

                        qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid]
                        build_remedial_queue()
                        if not qz["remedial_queue"]:
                            st.success(UI["REMEDIAL_END"])
                            qz["active"] = False
                            st.rerun()
                        else:
                            st.rerun()

                    if "remedial_current_q" not in st.session_state or st.session_state.get("remedial_current_cid") != cid:
                        simq, serr = call_qwen_generate_similar_question(
                            concept_id=cid,
                            preferred_type=meta.get("preferred_type", "single"),
                            review_pack=st.session_state.review_pack,
                            api_key=api_key
                        )
                        if serr or not simq:
                            # 失败静默：标记 stuck 并跳过
                            meta["stuck"] = True
                            qz["wrong_concepts"][cid] = meta
                            qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid]
                            st.rerun()

                        st.session_state.remedial_current_q = simq
                        st.session_state.remedial_current_cid = cid

                    current_q = st.session_state.remedial_current_q
                    st.markdown(UI["REMEDIAL_TITLE"])
                    st.caption(UI["REMEDIAL_META"].format(
                        title=current_q.get("concept_title", ""),
                        n=meta.get("attempts", 0)
                    ))

                else:
                    current_q = quiz_current_question()
                    st.markdown(UI["QUIZ_TITLE"])
                    st.caption(UI["QUIZ_PROGRESS"].format(
                        cur=qz["idx"] + 1,
                        total=len(qz["questions"]),
                        set_id=qz.get("current_set_id")
                    ))
                    st.caption(UI["QUIZ_CONCEPT"].format(title=current_q.get("concept_title", "")))

                st.markdown(f"{UI['QUIZ_STEM']} {current_q.get('stem', '')}")

                if qz["last_feedback"]:
                    st.markdown("---")
                    st.markdown(qz["last_feedback"])
                    st.markdown("---")

                if qz["await_next"]:
                    if st.button(UI["NEXT_BTN"], use_container_width=True):
                        if qz["phase"] == "remedial":
                            if "remedial_current_q" in st.session_state:
                                del st.session_state.remedial_current_q
                            if "remedial_current_cid" in st.session_state:
                                del st.session_state.remedial_current_cid
                        move_to_next_question_or_phase()
                        st.rerun()
                    st.stop()

                # -------- 批改入口（统一函数）--------
                def submit_answer(answer_text: str):
                    grade, gerr = call_qwen_grade_answer(
                        question=current_q,
                        user_answer=answer_text,
                        review_pack=st.session_state.review_pack,
                        api_key=api_key
                    )
                    if gerr or not grade:
                        qz["last_feedback"] = f"{UI['GRADE_FAIL']}{gerr}"
                        qz["await_next"] = True
                        return

                    is_correct = bool(grade.get("is_correct"))
                    if is_correct:
                        why = (grade.get("why_correct") or "").strip()
                        qz["last_feedback"] = f"{UI['CORRECT']}\n\n" + (why if why else "你抓住了关键考点。")
                        register_correct_concept(current_q)

                        if qz["phase"] == "remedial":
                            cid2 = current_q.get("concept_id", "unknown")
                            qz["concept_mastery"][cid2] = True
                            qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid2]
                    else:
                        ca = (grade.get("correct_answer") or "").strip()
                        ex = (grade.get("explanation") or "").strip()
                        block = f"{UI['WRONG']}\n\n"
                        if ca:
                            block += f"{UI['RIGHT_ANSWER']} {ca}\n\n"
                        block += ex if ex else UI["SUGGEST_REVIEW"]
                        qz["last_feedback"] = block
                        register_wrong_concept(current_q)

                    qz["await_next"] = True

                # -------- 题型渲染：单选=ABCD按钮；简答/论述=文本输入 --------
                qtype = current_q.get("type", "single")
                if qtype == "single":
                    opts = current_q.get("options", {}) or {}
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(f"A. {opts.get('A','')}", use_container_width=True):
                            submit_answer("A"); st.rerun()
                        if st.button(f"C. {opts.get('C','')}", use_container_width=True):
                            submit_answer("C"); st.rerun()
                    with c2:
                        if st.button(f"B. {opts.get('B','')}", use_container_width=True):
                            submit_answer("B"); st.rerun()
                        if st.button(f"D. {opts.get('D','')}", use_container_width=True):
                            submit_answer("D"); st.rerun()
                else:
                    user_text = st.text_area(
                        UI["TEXTAREA_LABEL"],
                        height=120,
                        placeholder=UI["TEXTAREA_PLACEHOLDER"]
                    )
                    if st.button(UI["SUBMIT_BTN"], use_container_width=True):
                        if not user_text.strip():
                            st.warning(UI["NEED_INPUT"])
                        else:
                            submit_answer(user_text.strip())
                            st.rerun()


else:
    # 未生成 && 未在生成中 才显示
    if (not st.session_state.is_generating) and (not st.session_state.run_generation):
        st.info(UI["EMPTY_HINT"])
