import streamlit as st
import fitz  # PyMuPDF
import dashscope
from dashscope.api_entities.dashscope_response import Role
import base64
import streamlit.components.v1 as components
from docx import Document # 新增：用于生成Word
import io # 新增：用于处理文件流

# =========================================================
# [配置层] 全局设置
# =========================================================
st.set_page_config(page_title="榨知机 V1.3", page_icon="🍋", layout="wide")

# 初始化状态
if "result_content" not in st.session_state:
    st.session_state.result_content = ""    # 存储生成的讲义/试卷
if "mindmap_code" not in st.session_state:
    st.session_state.mindmap_code = ""      # 存储思维导图代码
if "messages" not in st.session_state:
    st.session_state.messages = []          # 聊天记录
if "last_socratic_question" not in st.session_state:
    st.session_state.last_socratic_question = None # 记录上一道苏格拉底题目

# =========================================================
# [工具层] 
# =========================================================

def extract_text_from_pdf(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc: text += page.get_text()
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

def generate_word_file(content):
    """
    [V1.3 新增] 将 Markdown 内容转换为 Word 文档对象
    解析简单的 Markdown 语法：标题 (#)、列表 (-)
    """
    doc = Document()
    doc.add_heading('复习资料 (由榨知机 V1.3 生成)', 0)

    # 按行处理
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # 识别标题
        if line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        elif line.startswith('#### '):
            doc.add_heading(line[5:], level=4)
        # 识别列表
        elif line.startswith('- ') or line.startswith('* '):
            doc.add_paragraph(line[2:], style='List Bullet')
        # 正文
        else:
            # 简单清理一下 markdown 的加粗符号 **，让 Word 看起来更干净
            clean_text = line.replace('**', '').replace('__', '')
            doc.add_paragraph(clean_text)
            
    # 保存到内存流
    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio

def render_markmap(markdown_content):
    """渲染思维导图"""
    markmap_html = f"""
    <!DOCTYPE html>
    <style>svg {{ width: 100%; height: 600px; background: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}</style>
    <script src="https://cdn.jsdelivr.net/npm/d3@6"></script>
    <script src="https://cdn.jsdelivr.net/npm/markmap-view@0.14.4"></script>
    <div id="markmap"></div>
    <script>
        const markdown = `{markdown_content.replace('`', '\`')}`;
        const transformer = new markmap.Transformer();
        const {{ root, features }} = transformer.transform(markdown);
        markmap.Markmap.create('#markmap', null, root);
    </script>
    """
    components.html(markmap_html, height=600)

# =========================================================
# [模型层 - 核心生成]
# =========================================================

def call_qwen_vl_vision(images, api_key):
    """视觉模型"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    content = [{"text": "你是大学助教。请详细分析这些 PPT/课件截图，提取里面的所有核心知识点、文字内容和图表含义，输出为详细的文本笔记。"}] + [{"image": img} for img in images]
    try:
        resp = dashscope.MultiModalConversation.call(model='qwen-vl-max', messages=[{"role":"user","content":content}])
        if resp.status_code == 200:
            return resp.output.choices[0]['message']['content'][0]['text']
        else:
            return f"视觉分析失败: {resp.code} - {resp.message}"
    except Exception as e: return f"视觉错误: {str(e)}"

def call_qwen_speedrun(main_text, exam_text, api_key):
    """【模式A：0基础】Prompt 2 定制版"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    exam_instruction = "【⚠️ 真题锚定】：请用“⭐”标记真题考点。" if exam_text else ""
    
    system_prompt = f"""
    你是一位金牌大学助教。请为“0基础”学生生成一份全能复习包。
    {exam_instruction}

    【输出结构要求】：
    请严格按照以下4个部分输出，不要废话：

    ## part1_mindmap
    （请生成一段 Markdown 格式的思维导图代码，不要用代码块包裹，直接写层级，例如：# 核心主题 ## 分支1 ### 叶子节点）

    ## part2_concepts
    # 📘 核心知识清单
    （解释名词、原理。关键步骤：提取讲义中所有的具体知识点，确保不重不漏，并根据每一个知识点联想1个经典或最新的实际案例/应用场景来辅助解释这些概念，以弥补课件的枯燥。）

    ## part3_memory
    # 🧠 必背清单
    （筛选出你觉得应该必须记住的必背知识，例如历史课上的大事件发生的年份，政治课上的某个思想的具体含义。身为大学助教的你非常清楚什么是应该背的。）

    ## part4_exam
    # 📝 模拟真题
    （一套模拟题，包含：10道单项选择 + 5个多选 + 5个判断 + 2道简答 + 2个案例分析/综合/论述/计算题。含答案，但答案要放在试卷的最末端。）
    """
    
    user_content = f"课件：\n{main_text[:12000]}\n\n真题：\n{exam_text[:3000]}"
    try:
        resp = dashscope.Generation.call(
            model='qwen-plus', 
            messages=[{'role':Role.SYSTEM,'content':system_prompt},{'role':Role.USER,'content':user_content}],
            result_format='message'
        )
        if resp.status_code == 200:
            return resp.output.choices[0]['message']['content']
        else:
            return f"生成失败 (Code: {resp.code}): {resp.message}"
    except Exception as e: return f"系统错误: {str(e)}"

def call_qwen_advanced(main_text, exam_text, api_key):
    """【模式B：高分拔尖】"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    anchor_instruction = "检测到用户上传了真题。请执行“70% 相似度锚定”策略：70%题目考察真题同类考点(变种)，30%考察冷门重点。" if exam_text else "请基于课件的整体范围出题。"
    
    system_prompt = f"""
    你是一位魔鬼出题人。用户已经掌握了基础知识，现在需要进行“高分冲刺”。
    {anchor_instruction}
    
    请输出一份【全真模拟试卷】：
    1. 包含：单选(10题)、多选(5题)、判断（5题）、简答(5题)、深度论述(2题)。
    2. 难度：Hard。
    3. 最后一道题目后面展示所有答案，不要把答案放在每一道题后。
    """
    
    user_content = f"课件：\n{main_text[:12000]}\n\n真题：\n{exam_text[:5000]}"
    try:
        resp = dashscope.Generation.call(
            model='qwen-plus', 
            messages=[{'role':Role.SYSTEM,'content':system_prompt},{'role':Role.USER,'content':user_content}],
            result_format='message'
        )
        if resp.status_code == 200:
            return resp.output.choices[0]['message']['content']
        else:
            return f"生成失败 (Code: {resp.code}): {resp.message}"
    except Exception as e: return f"系统错误: {str(e)}"

# =========================================================
# [模型层 - 交互与答疑]
# =========================================================

def call_qwen_pure_chat(messages, api_key):
    """纯净版答疑"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    system_prompt = """
    你是一个全能助教。请直接解答用户输入的具体题目或概念。
    1. 如果用户输入了具体的题目内容，请直接给出解析。
    2. 如果用户只输入了“第3题选什么”这种无上下文的问题，请礼貌地引导他：“为了节省你的大脑带宽（其实是 Token），请直接把题目复制发给我哦~”
    """
    
    api_msgs = [{'role': Role.SYSTEM, 'content': system_prompt}]
    for msg in messages: api_msgs.append({'role': msg['role'], 'content': msg['content']})
    
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=api_msgs, result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else f"API Error: {resp.code}"
    except Exception as e: return f"Error: {str(e)}"

def generate_socratic_question(context, api_key):
    """苏格拉底出题"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    system_prompt = f"""
    你是一位苏格拉底式老师。请阅读下方的【复习资料】，从中挑选一个核心知识点，向学生提出一个具有启发性的问题（不要选择题，要简答或思考题）。
    要求：只输出问题本身，简短有力，语气自然。
    【复习资料片段】：
    {context[:3000]} 
    """
    
    try:
        resp = dashscope.Generation.call(
            model='qwen-plus', 
            messages=[{'role':Role.SYSTEM,'content':system_prompt}],
            result_format='message'
        )
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "出题失败"
    except: return "Error"

def call_socratic_feedback(previous_question, user_answer, api_key):
    """苏格拉底点评"""
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    system_prompt = f"""
    你是一位苏格拉底式老师。
    刚才你问了学生这个问题：【{previous_question}】
    学生的回答是：【{user_answer}】
    请点评学生的回答：答对了给予鼓励并拓展；答错了给出提示引导。语气幽默鼓励。
    """
    
    try:
        resp = dashscope.Generation.call(
            model='qwen-plus', 
            messages=[{'role':Role.SYSTEM,'content':system_prompt}],
            result_format='message'
        )
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "API Error"
    except: return "Error"

# =========================================================
# [UI层]
# =========================================================

with st.sidebar:
    st.title("🍋 榨知机 V1.3")
    with st.expander("🔔 V1.3.更新说明", expanded=True):
        st.markdown("""
        1. 深度定制：0基础模式知识点覆盖更全，案例更丰富。
        2. 交互升级：新增“考考我”按钮。
        3. 极致省流：答疑模式不再重复读取课件。
        """)
    
    # 鉴权
    if "DASHSCOPE_API_KEY" in st.secrets:
        api_key = st.secrets["DASHSCOPE_API_KEY"]
        st.success("✅开发者演示模式")
    else:
        api_key = st.text_input("请输入通义千问 API Key", type="password")

    st.markdown("---")
    st.subheader("1. 选择复习策略")
    mode = st.radio("你的目标是？", ("帮我速通！(思维导图+知识清单+模拟试题+问题答疑)", "我是高玩！(仅刷题)"), index=0)
    
    st.subheader("2. 上传课程文件")
    uploaded_files = st.file_uploader("上传课件 (必需)", type=["pdf"], accept_multiple_files=True)
    uploaded_exams = st.file_uploader("上传真题 (可选)", type=["pdf"], key="exam")
    
    process_btn = st.button("开始学习！", type="primary")
    
    # --- 苏格拉底按钮 ---
    if st.session_state.result_content:
        st.markdown("---")
        st.markdown("### 互动练习")
        if st.button("考考我"):
            with st.spinner("助教正在出题..."):
                question = generate_socratic_question(st.session_state.result_content, api_key)
                if question:
                    st.session_state.messages.append({"role": "assistant", "content": f"【苏格拉底提问】{question}"})
                    st.session_state.last_socratic_question = question
                    st.rerun()

# 主界面
st.title("🍋 榨知机 V1.3 ：你的期末救星")

# --- 核心处理逻辑 ---
if process_btn and uploaded_files and api_key:
    with st.spinner('榨知机正在全速运转...大概需要三五分钟（我知道你很急但你先别急）'):
        # 1. 预处理
        exam_text = extract_text_from_pdf(uploaded_exams) if uploaded_exams else ""
        main_text = ""
        for file in uploaded_files:
            bytes_data = file.read()
            text = extract_text_from_pdf(bytes_data)
            if len(text) < 50: 
                st.warning(f"正在视觉分析: {file.name}")
                imgs = pdf_pages_to_base64_images(bytes_data)
                text = call_qwen_vl_vision(imgs, api_key)
            main_text += text

        if "速通" in mode:
            # 模式A
            raw_result = call_qwen_speedrun(main_text, exam_text, api_key)
            if "Code:" in raw_result or "失败" in raw_result:
                st.error(raw_result)
                st.session_state.result_content = ""
            elif "## part1_mindmap" in raw_result:
                parts = raw_result.split("## part2_concepts")
                st.session_state.mindmap_code = parts[0].replace("## part1_mindmap", "").strip()
                st.session_state.result_content = "## 核心知识清单" + parts[1] if len(parts)>1 else raw_result
            else:
                st.session_state.result_content = raw_result
        else:
            # 模式B
            raw_result = call_qwen_advanced(main_text, exam_text, api_key)
            if "Code:" in raw_result or "失败" in raw_result:
                st.error(raw_result)
                st.session_state.result_content = ""
            else:
                st.session_state.result_content = raw_result
            st.session_state.mindmap_code = "" 
            
        if st.session_state.result_content:
            st.success("✅ 生成完毕！")

# --- 结果展示 ---
if st.session_state.result_content:
    # 板块①：思维导图
    if st.session_state.mindmap_code:
        st.subheader("🗺️ 知识点思维导图")
        render_markmap(st.session_state.mindmap_code)
        st.caption("💡 提示：可使用微信/QQ截图保存导图")
        st.markdown("---")

    col1, col2 = st.columns([6, 4])
    
    # 板块②③：内容展示
    with col1:
        title = "全真模拟卷" if "模拟试卷" in st.session_state.result_content else "复习讲义"
        st.subheader(f"{title}")
        st.markdown(st.session_state.result_content)
        
        # Word 下载按钮
        docx_file = generate_word_file(st.session_state.result_content)
        st.download_button(
            label="📄 下载 Word 讲义/试卷 (.docx)",
            data=docx_file,
            file_name="复习资料.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    # 板块④：交互答疑
    with col2:
        st.subheader("助教答疑")
        st.caption("💡提示：AI 此时不记得课件内容。问具体题目请完整复制题干。")
        
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.write(msg["content"])
        
        if prompt := st.chat_input("复制题目 / 回答助教提问..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.write(prompt)
            
            with st.spinner("Thinking..."):
                if st.session_state.last_socratic_question:
                    response = call_socratic_feedback(st.session_state.last_socratic_question, prompt, api_key)
                    st.session_state.last_socratic_question = None
                else:
                    response = call_qwen_pure_chat(st.session_state.messages, api_key)
            
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant"): st.write(response)

elif not uploaded_files:
    st.info("👈 请在左侧选择模式并上传课件(必需)。")