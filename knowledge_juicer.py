import streamlit as st
import fitz  # PyMuPDF
import dashscope
from dashscope.api_entities.dashscope_response import Role
import base64
import streamlit.components.v1 as components
from docx import Document
import io
import random
import json
import re
import threading
import time

# =========================================================
# [配置层]
# =========================================================
st.set_page_config(page_title="榨知机 V1.5", page_icon="🍋", layout="wide")

# -------------------------
# Session State Init
# -------------------------
def init_state():
    if "course_name" not in st.session_state:
        st.session_state.course_name = "通用课程"

    # 复习包（结构化）
    if "review_pack" not in st.session_state:
        st.session_state.review_pack = None  # dict

    # 展示用讲义（markdown）
    if "result_content" not in st.session_state:
        st.session_state.result_content = ""  # 仅 concepts 的 markdown

    # 导图
    if "mindmap_code" not in st.session_state:
        st.session_state.mindmap_code = ""

    # 右侧答疑聊天记录（只在生成后可用）
    if "qa_messages" not in st.session_state:
        st.session_state.qa_messages = []

    # 题组缓存（预生成 + 下一组后台生成）
    if "question_sets" not in st.session_state:
        st.session_state.question_sets = []   # list[dict]，每个 dict 是一组 10 题（JSON）
    if "next_set_generating" not in st.session_state:
        st.session_state.next_set_generating = False
    if "next_set_error" not in st.session_state:
        st.session_state.next_set_error = ""

    # 刷题状态机
    if "quiz" not in st.session_state:
        st.session_state.quiz = {
            "active": False,
            "phase": "main",           # main / remedial
            "questions": [],           # 当前做的题列表
            "idx": 0,
            "last_feedback": None,     # str
            "await_next": False,       # 是否等待用户点“下一题”
            "wrong_concepts": {},      # concept_key -> {"attempts": int, "stuck": bool, "preferred_type": str}
            "concept_mastery": {},     # concept_key -> bool
            "remedial_queue": [],      # list[concept_key]
            "current_set_id": None,    # 当前题组id（可选）
        }

init_state()

# =========================================================
# [工具层]
# =========================================================

def extract_text_from_pdf(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def pdf_pages_to_base64_images(file_bytes, max_pages=5):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images = []
    for i in range(min(len(doc), max_pages)):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=72)
        b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        images.append(f"data:image/png;base64,{b64}")
    doc.close()
    return images

def render_markmap(markdown_content):
    markmap_html = f"""
    <!DOCTYPE html>
    <style>
      svg {{
        width: 100%;
        height: 500px;
        background: white;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/d3@6"></script>
    <script src="https://cdn.jsdelivr.net/npm/markmap-view@0.14.4"></script>
    <div id="markmap"></div>
    <script>
        const markdown = `{markdown_content.replace('`', '\\`')}`;
        const transformer = new markmap.Transformer();
        const {{ root }} = transformer.transform(markdown);
        markmap.Markmap.create('#markmap', null, root);
    </script>
    """
    components.html(markmap_html, height=520)

def generate_word_file_from_concepts(course_name, concepts_markdown):
    doc = Document()
    doc.add_heading(f'复习资料（{course_name}）- 核心知识清单', 0)
    for line in concepts_markdown.split('\n'):
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
    尽量从模型输出中提取 JSON（允许前后夹杂少量文本）。
    """
    if not text:
        return None
    # 优先找第一个 {...} 或 [...]
    m = re.search(r'(\{.*\}|\[.*\])', text, re.S)
    if not m:
        return None
    raw = m.group(1)
    try:
        return json.loads(raw)
    except:
        # 常见容错：去掉尾逗号
        raw2 = re.sub(r',\s*([\]}])', r'\1', raw)
        try:
            return json.loads(raw2)
        except:
            return None

def concepts_to_markdown(review_pack: dict):
    """
    把结构化 concepts 转成可读 markdown（用于左侧展示+Word导出）
    """
    lines = ["# 📘 核心知识清单（Concepts）"]
    if not review_pack or "concepts" not in review_pack:
        return "\n".join(lines)

    for i, c in enumerate(review_pack["concepts"], start=1):
        star = "⭐ " if c.get("is_star") else ""
        title = c.get("title", f"概念{i}")
        explain = c.get("explain", "")
        example = c.get("example", "")
        score = c.get("final_score", c.get("model_score", 0))
        lines.append(f"## {star}{title}（高频指数：{score}）")
        if explain:
            lines.append(f"- 解释：{explain}")
        if example:
            lines.append(f"- 例子：{example}")
        lines.append("")  # 空行
    return "\n".join(lines)

# =========================================================
# [模型层]
# =========================================================

def _set_api_key(api_key: str):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")

def call_qwen_vl_vision(images, api_key):
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

def call_qwen_generate_concepts_json(main_text, exam_text, api_key):
    """
    生成 concepts-only 的复习包：课程名 + concepts（含 is_star + model_score）
    你选择的策略：
      - ⭐ 仅在用户上传真题时存在
      - 最终排序分 = A+B混合：is_star 60% + model_score 40%
    """
    _set_api_key(api_key)

    has_exam = bool(exam_text and exam_text.strip())

    system_prompt = f"""
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
1) concepts 输出 15-25 条（宁可少而精，覆盖全课）。
2) 如果提供了真题，请将与真题高度一致/同类高频考点标记 is_star=true；若无真题，全部 is_star=false。
3) model_score 表示“你判断的可能考到程度”，0-100。
4) 不要输出任何多余字段。
"""

    exam_hint = "真题（有）" if has_exam else "真题（无）"
    user_content = f"""
{exam_hint}
【课件正文】：
{main_text[:14000]}

【真题正文】：
{exam_text[:6000] if has_exam else ""}
"""

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
        if not data or "concepts" not in data:
            return None, "Error: JSON 解析失败"

        # 计算 final_score（A+B混合）
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
                final = ms  # 无真题就全靠 model_score
                c["is_star"] = False

            c["model_score"] = ms
            c["final_score"] = int(final)

        # 课程名兜底
        course_name = data.get("course_name") or "通用课程"
        data["course_name"] = course_name

        # 按 final_score 排序（高->低）
        data["concepts"] = sorted(concepts, key=lambda x: x.get("final_score", 0), reverse=True)

        return data, None
    except Exception as e:
        return None, str(e)

def call_qwen_generate_question_set(review_pack, api_key):
    """
    预生成一组题：10题（7单选+2简答+1论述），按 concepts 高频到低频锚定。
    输出严格 JSON：
    {
      "set_id": "...",
      "questions":[...]
    }
    """
    _set_api_key(api_key)

    concepts = review_pack.get("concepts", []) if review_pack else []
    # 取前 12 个高频概念做锚定（允许少量扩展，但考点来自这些）
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

    system_prompt = """
你是一个“高频考点出题器”。请严格输出 JSON（不要 markdown，不要解释）。
目标：生成一组 10 道题，按“最可能考”到“较不可能考”排序。

输入会提供 12 条高频概念（已按 final_score 排序）。
出题规则：
1) 必须严格生成：7道单选题 + 2道简答题 + 1道综合论述题。
2) 每道题必须绑定一个概念 concept_id，并尽量覆盖不同概念（允许重复但不要全重复）。
3) 单选题提供 A/B/C/D 四个选项；answer 必须是 "A"|"B"|"C"|"D"。
4) 简答/论述题 answer 用“要点列表”的形式给评分要点（不是一大段）。
5) 解析 explanation 要简洁，且允许少量扩展解释，但考点必须来自给定概念。
6) questions 必须按考点高频到低频排序（优先 concept final_score 高的概念）。
7) 不要输出任何额外字段。

JSON schema：
{
  "set_id": "string",
  "questions": [
    {
      "qid": 1,
      "type": "single"|"short"|"essay",
      "concept_id": "c1",
      "concept_title": "概念标题",
      "stem": "题干",
      "options": {"A":"...", "B":"...", "C":"...", "D":"..."} ,   // 仅 single
      "answer": "A" 或 ["要点1","要点2"] ,
      "explanation": "解析（用于答错时）"
    }
  ]
}
"""

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

        # 简单校验并兜底 set_id
        if not data.get("set_id"):
            data["set_id"] = f"set_{int(time.time())}_{random.randint(1000,9999)}"

        return data, None
    except Exception as e:
        return None, str(e)

def call_qwen_grade_answer(question, user_answer, review_pack, api_key):
    """
    批改：严格锚定 concepts（允许少量扩展解释）。
    规则：
      - 若答对：只输出 why_correct（告诉你为什么做对了）
      - 若答错：输出 correct_answer + explanation（并记录错题）
    输出严格 JSON：
    {
      "is_correct": true/false,
      "why_correct": "...",              // only if correct
      "correct_answer": "...",           // only if incorrect
      "explanation": "..."               // only if incorrect
    }
    """
    _set_api_key(api_key)

    # 找到该题对应 concept 的内容作上下文
    concept_id = question.get("concept_id")
    concept_ctx = None
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            concept_ctx = c
            break
    concept_ctx = concept_ctx or {}

    system_prompt = """
你是一个“严格锚定复习包的阅卷老师”。请只输出 JSON（不要 markdown，不要解释）。
你将收到：一个题目 + 用户答案 + 对应概念上下文。
要求：
1) 判断用户答案正确与否。
2) 若正确：只输出 why_correct（告诉用户他为什么做对了：抓住了哪些关键点/概念）。
3) 若错误：输出 correct_answer + explanation（简洁讲清为什么）。
4) 允许少量扩展解释，但考点必须来自 concept_context。
JSON schema：
{
  "is_correct": true/false,
  "why_correct": "string",
  "correct_answer": "string",
  "explanation": "string"
}
注意：如果 is_correct=true，correct_answer/explanation 可为空字符串；反之 why_correct 为空字符串。
"""

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

def call_qwen_generate_similar_question(concept_id, preferred_type, review_pack, api_key):
    """
    为某个错题概念生成“类似题”，用于错题循环。
    """
    _set_api_key(api_key)

    concept_ctx = None
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            concept_ctx = c
            break
    concept_ctx = concept_ctx or {}

    # 错题循环：优先 single（更快反馈），但尊重 preferred_type
    qtype = preferred_type if preferred_type in ["single", "short", "essay"] else "single"

    system_prompt = """
你是一个“错题再训练出题器”。请严格输出 JSON（不要 markdown，不要解释）。
输入：一个概念上下文 + 题型。
目标：出一道“同考点、不同问法”的类似题，用于纠错训练。
要求：
- 如果 type=single：必须提供 A/B/C/D 和答案字母。
- 如果 type=short/essay：answer 用要点列表。
- explanation 给出简洁解析（用于答错时）。
JSON schema：
{
  "qid": 999,
  "type": "single"|"short"|"essay",
  "concept_id": "c1",
  "concept_title": "概念标题",
  "stem": "题干",
  "options": {"A":"...", "B":"...", "C":"...", "D":"..."} ,   // only single
  "answer": "A" 或 ["要点1","要点2"],
  "explanation": "解析"
}
"""

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

        # 强制绑定概念
        data["concept_id"] = concept_id
        data["concept_title"] = concept_ctx.get("title", data.get("concept_title", ""))
        return data, None
    except Exception as e:
        return None, str(e)

def call_qwen_qa(user_question, review_pack, api_key):
    """
    右侧答疑：严格以 concepts 为知识源
    """
    _set_api_key(api_key)

    # 只取前 25 条概念防止太长
    concepts = (review_pack.get("concepts") or [])[:25]
    ctx = [{"title": c.get("title",""), "explain": c.get("explain",""), "example": c.get("example","")} for c in concepts]

    system_prompt = """
你是一个“复习包内答疑助教”。请基于给定 concepts 内容回答问题。
要求：
1) 优先引用 concepts 的解释与例子进行回答（可以重述但不要胡编考点）。
2) 允许少量扩展解释，但不要引入与 concepts 无关的新考点。
3) 如果用户问到 concepts 未覆盖的内容，坦率说明“复习包未包含”，并建议他补充课件或生成更完整概念。
"""

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
# [后台预生成：下一组题]
# =========================================================

def background_generate_next_question_set(api_key: str):
    """
    后台线程：生成下一组题并塞进 question_sets。
    注意：Streamlit 对线程写 session_state 有一定风险，但通常在单用户会话可用。
    """
    try:
        st.session_state.next_set_generating = True
        st.session_state.next_set_error = ""

        pack = st.session_state.review_pack
        if not pack:
            st.session_state.next_set_error = "复习包不存在，无法生成题组"
            return

        data, err = call_qwen_generate_question_set(pack, api_key)
        if err:
            st.session_state.next_set_error = err
            return

        st.session_state.question_sets.append(data)
    finally:
        st.session_state.next_set_generating = False

def ensure_next_set_async(api_key: str):
    """
    如果当前题组缓存少于 1 组，就后台补一组。
    """
    if st.session_state.next_set_generating:
        return
    # 目标：始终保证至少 1 组“待用题组”
    if len(st.session_state.question_sets) >= 1:
        return

    t = threading.Thread(target=background_generate_next_question_set, args=(api_key,), daemon=True)
    t.start()

# =========================================================
# [刷题状态机逻辑]
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

def register_wrong_concept(question):
    cid = question.get("concept_id", "unknown")
    qtype = question.get("type", "single")
    wrong = st.session_state.quiz["wrong_concepts"].get(cid)
    if not wrong:
        st.session_state.quiz["wrong_concepts"][cid] = {"attempts": 1, "stuck": False, "preferred_type": qtype}
    else:
        wrong["attempts"] += 1
    st.session_state.quiz["concept_mastery"][cid] = False

def register_correct_concept(question):
    cid = question.get("concept_id", "unknown")
    st.session_state.quiz["concept_mastery"][cid] = True

def build_remedial_queue():
    wrong = st.session_state.quiz["wrong_concepts"]
    queue = []
    for cid, meta in wrong.items():
        if meta.get("stuck"):
            continue
        # 只有未掌握的才需要再考
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
            # 主卷结束 -> 进入错题循环
            build_remedial_queue()
            if qz["remedial_queue"]:
                qz["phase"] = "remedial"
                qz["idx"] = 0
                qz["questions"] = []  # remedial 每次动态生成题
            else:
                qz["active"] = False
    else:
        # remedial：每次答完会根据 mastery / attempts 决定是否继续
        build_remedial_queue()
        if not qz["remedial_queue"]:
            qz["active"] = False

def get_concept_snippet(concept_id, review_pack):
    for c in (review_pack.get("concepts") or []):
        if c.get("id") == concept_id:
            return f"**{c.get('title','')}**：{c.get('explain','')}"
    return ""

# =========================================================
# [UI层]
# =========================================================

with st.sidebar:
    st.title("🍋 榨知机 V1.5")

    if "DASHSCOPE_API_KEY" in st.secrets:
        api_key = st.secrets["DASHSCOPE_API_KEY"]
        st.success("✅ 开发者演示模式")
    else:
        api_key = st.text_input("API Key", type="password")

    st.markdown("---")

    # 工具箱：只在生成后显示
    if st.session_state.review_pack:
        st.markdown("🛠️ 工具箱")

        # 考考我：只负责“开始刷题”，题组秒开来自预生成缓存
        if st.button("🙋‍♂️ 考考我（10题高频卷）", use_container_width=True):
            if not api_key:
                st.error("请先配置 API Key")
            elif not st.session_state.question_sets:
                st.warning("题组尚未预生成完成，稍等或重新点击。")
            else:
                # 取出一组题并开始刷题
                qs = st.session_state.question_sets.pop(0)
                quiz_start_with_set(qs)
                # 开始刷题后，后台补下一组（保持“永远有一组在本地”）
                ensure_next_set_async(api_key)
                st.rerun()

        # Word 下载（仅 concepts）
        docx_file = generate_word_file_from_concepts(
            st.session_state.course_name,
            st.session_state.result_content
        )
        st.download_button(
            label="📄 下载 Word（Concepts）",
            data=docx_file,
            file_name=f"{st.session_state.course_name}_核心知识清单.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )

        # 预生成状态提示
        if st.session_state.next_set_generating:
            st.info("⏳ 正在后台预生成下一组题...")
        elif st.session_state.next_set_error:
            st.warning(f"⚠️ 下一组题生成失败：{st.session_state.next_set_error}")

        st.markdown("---")

    # 设置区
    st.markdown("### ⚙️ 复习设置")
    mode = st.radio("目标：", ("帮我速通！（生成Concepts）", "我是高玩！（也生成Concepts）"), index=0)
    uploaded_files = st.file_uploader("上传课件 (必需)", type=["pdf"], accept_multiple_files=True)
    uploaded_exams = st.file_uploader("上传真题 (可选，用于⭐锚点)", type=["pdf"], key="exam")
    process_btn = st.button("🚀 开始学习", type="primary", use_container_width=True)

# -------------------------
# 主界面
# -------------------------
st.title("🍋 榨知机 V1.5：Concepts 高频刷题机")

# 生成流程
if process_btn and uploaded_files and api_key:
    with st.spinner("榨知机正在生成 Concepts（并预生成一组高频卷）..."):
        # 1) 文本提取
        exam_text = ""
        if uploaded_exams:
            try:
                exam_text = extract_text_from_pdf(uploaded_exams.read())
            except:
                exam_text = ""

        main_text = ""
        for file in uploaded_files:
            bytes_data = file.read()
            text = extract_text_from_pdf(bytes_data)
            if len(text) < 50:
                st.warning(f"检测到 {file.name} 文本过少，启用视觉分析")
                imgs = pdf_pages_to_base64_images(bytes_data)
                text = call_qwen_vl_vision(imgs, api_key)
            main_text += "\n" + text

        # 2) 生成 concepts JSON
        pack, err = call_qwen_generate_concepts_json(main_text, exam_text, api_key)
        if err or not pack:
            st.error(f"Concepts 生成失败：{err}")
        else:
            st.session_state.review_pack = pack
            st.session_state.course_name = pack.get("course_name", "通用课程")
            st.session_state.result_content = concepts_to_markdown(pack)

            # 3) 预生成一组题（备选1：保证“秒开”）
            qset, qerr = call_qwen_generate_question_set(pack, api_key)
            if qerr or not qset:
                st.warning(f"题组预生成失败（仍可答疑/下载）：{qerr}")
            else:
                st.session_state.question_sets = [qset]  # 先放 1 组
                # （可选）立即再后台补一组，让“永远有一组在本地”
                ensure_next_set_async(api_key)

            # 清理答疑记录与刷题状态（生成新包视为新会话）
            st.session_state.qa_messages = []
            quiz_reset()

            st.rerun()

# -------------------------
# 展示区：有复习包才解锁
# -------------------------
if st.session_state.review_pack:
    st.success(f"✅ 《{st.session_state.course_name}》Concepts 已生成！侧边栏可下载/考考我。")

    col1, col2 = st.columns([6, 4])

    with col1:
        st.subheader("📄 核心知识清单（Concepts）")
        st.markdown(st.session_state.result_content)

    with col2:
        st.subheader("🤖 右侧：答疑 / 刷题与批改")
        tab = st.radio("模式", ("答疑", "刷题与批改"), horizontal=True)

        # -------------------------
        # 答疑模式
        # -------------------------
        if tab == "答疑":
            for msg in st.session_state.qa_messages:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            q = st.chat_input("基于 Concepts 提问…（不会回答 Concepts 未覆盖的新考点）")
            if q:
                st.session_state.qa_messages.append({"role": "user", "content": q})
                with st.chat_message("user"):
                    st.write(q)

                with st.spinner("AI 正在基于 Concepts 作答..."):
                    a = call_qwen_qa(q, st.session_state.review_pack, api_key)

                st.session_state.qa_messages.append({"role": "assistant", "content": a})
                with st.chat_message("assistant"):
                    st.write(a)

        # -------------------------
        # 刷题与批改模式
        # -------------------------
        else:
            qz = st.session_state.quiz

            if not qz["active"]:
                st.info("点击侧边栏「考考我」开始一组 10 题高频卷。")
                if not st.session_state.question_sets and st.session_state.next_set_generating:
                    st.caption("（题组正在后台生成，稍等即可秒开）")
            else:
                # 如果处于 remedial，需要动态生成当前概念的类似题
                if qz["phase"] == "remedial":
                    # 取队列头概念
                    cid = qz["remedial_queue"][0] if qz["remedial_queue"] else None
                    if not cid:
                        st.success("🎉 错题已全部掌握！本轮结束。")
                        qz["active"] = False
                        st.rerun()

                    meta = qz["wrong_concepts"].get(cid, {"attempts": 0, "preferred_type": "single", "stuck": False})
                    attempts = meta.get("attempts", 0)

                    if attempts >= 4:
                        # 到达上限：标记 stuck，并移出队列
                        meta["stuck"] = True
                        qz["wrong_concepts"][cid] = meta
                        snippet = get_concept_snippet(cid, st.session_state.review_pack)
                        st.warning("该考点已连续错误达到上限（4次）。建议回看该概念后再挑战。")
                        if snippet:
                            st.markdown("📌 建议回看： " + snippet)

                        # 移出队列并继续检查
                        qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid]
                        build_remedial_queue()
                        if not qz["remedial_queue"]:
                            st.success("🎉 错题循环已结束（部分考点需回看后再挑战）。")
                            qz["active"] = False
                            st.rerun()
                        else:
                            st.rerun()

                    # 生成类似题（每次 rerun 只生成一次：存在 session 中）
                    if "remedial_current_q" not in st.session_state or st.session_state.get("remedial_current_cid") != cid:
                        with st.spinner("正在为错题考点生成类似题..."):
                            simq, serr = call_qwen_generate_similar_question(
                                concept_id=cid,
                                preferred_type=meta.get("preferred_type", "single"),
                                review_pack=st.session_state.review_pack,
                                api_key=api_key
                            )
                        if serr or not simq:
                            st.error(f"类似题生成失败：{serr}")
                            # 防止卡死：移出该概念
                            meta["stuck"] = True
                            qz["wrong_concepts"][cid] = meta
                            qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid]
                            st.rerun()
                        st.session_state.remedial_current_q = simq
                        st.session_state.remedial_current_cid = cid

                    current_q = st.session_state.remedial_current_q
                    st.markdown("### 🔁 错题循环（直到掌握）")
                    st.caption(f"考点：{current_q.get('concept_title','')} ｜ 已错次数：{meta.get('attempts',0)}/4")
                else:
                    current_q = quiz_current_question()
                    st.markdown("### 📝 高频卷（10题）")
                    st.caption(f"进度：第 {qz['idx']+1} / {len(qz['questions'])} 题｜题组：{qz.get('current_set_id')}")
                    st.caption(f"考点：{current_q.get('concept_title','')}")

                # 展示题干
                st.markdown(f"**题目：** {current_q.get('stem','')}")
                qtype = current_q.get("type", "single")

                # 展示上次反馈
                if qz["last_feedback"]:
                    st.markdown("---")
                    st.markdown(qz["last_feedback"])
                    st.markdown("---")

                # 等待用户点下一题
                if qz["await_next"]:
                    if st.button("➡️ 下一题 / 继续", use_container_width=True):
                        # remedial：答完后清除 current_q 缓存
                        if qz["phase"] == "remedial":
                            if "remedial_current_q" in st.session_state:
                                del st.session_state.remedial_current_q
                            if "remedial_current_cid" in st.session_state:
                                del st.session_state.remedial_current_cid
                        move_to_next_question_or_phase()
                        st.rerun()
                    st.stop()

                # ---- 作答区域（单选按钮 / 文本提交） ----
                def submit_answer(answer_text: str):
                    with st.spinner("AI 正在批改..."):
                        grade, gerr = call_qwen_grade_answer(
                            question=current_q,
                            user_answer=answer_text,
                            review_pack=st.session_state.review_pack,
                            api_key=api_key
                        )

                    if gerr or not grade:
                        qz["last_feedback"] = f"⚠️ 批改失败：{gerr}"
                        qz["await_next"] = True
                        return

                    is_correct = bool(grade.get("is_correct"))
                    if is_correct:
                        # 只给 why_correct
                        why = grade.get("why_correct", "").strip()
                        qz["last_feedback"] = "✅ **正确**\n\n" + (why if why else "你抓住了关键考点。")
                        register_correct_concept(current_q)

                        # remedial：如果该考点答对，则标记 mastery=true 并从队列移除
                        if qz["phase"] == "remedial":
                            cid = current_q.get("concept_id", "unknown")
                            qz["concept_mastery"][cid] = True
                            qz["remedial_queue"] = [x for x in qz["remedial_queue"] if x != cid]

                    else:
                        ca = grade.get("correct_answer", "").strip()
                        ex = grade.get("explanation", "").strip()
                        qz["last_feedback"] = "❌ **错误**\n\n" + (f"**正确答案：** {ca}\n\n" if ca else "") + (ex if ex else "建议回看该概念。")
                        register_wrong_concept(current_q)

                        # remedial：错误会保留在队列头，attempts++ 已在 register_wrong_concept 里做
                        # 但我们要确保 attempts 不会被主卷/错题卷混淆：register_wrong_concept 对 concept 聚合计数

                    qz["await_next"] = True

                if qtype == "single":
                    opts = current_q.get("options", {}) or {}
                    # 四个按钮：ABCD
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(f"A. {opts.get('A','')}", use_container_width=True):
                            submit_answer("A")
                            st.rerun()
                        if st.button(f"C. {opts.get('C','')}", use_container_width=True):
                            submit_answer("C")
                            st.rerun()
                    with c2:
                        if st.button(f"B. {opts.get('B','')}", use_container_width=True):
                            submit_answer("B")
                            st.rerun()
                        if st.button(f"D. {opts.get('D','')}", use_container_width=True):
                            submit_answer("D")
                            st.rerun()

                else:
                    user_text = st.text_area("请输入你的答案：", height=120, placeholder="简答/论述：写要点即可，越结构化越好。")
                    if st.button("✅ 提交答案", use_container_width=True):
                        if not user_text.strip():
                            st.warning("请先输入答案。")
                        else:
                            submit_answer(user_text.strip())
                            st.rerun()

# -------------------------
# 未生成时：只提示（砍掉伴读）
# -------------------------
else:
    st.info("👈 请先在左侧上传课件并点击「开始学习」。生成 Concepts 后才会解锁：下载 / 高频刷题 / 答疑。")
