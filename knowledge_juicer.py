import streamlit as st
import fitz  # PyMuPDF
import dashscope
from dashscope.api_entities.dashscope_response import Role
import base64
import streamlit.components.v1 as components
from docx import Document
import io
import random # 用于概率控制

# =========================================================
# [配置层]
# =========================================================
st.set_page_config(page_title="榨知机 V1.4", page_icon="🍋", layout="wide")

# 初始化状态
if "result_content" not in st.session_state:
    st.session_state.result_content = ""      # 讲义内容
if "mindmap_code" not in st.session_state:
    st.session_state.mindmap_code = ""        # 导图代码
if "messages" not in st.session_state:
    st.session_state.messages = []            # 聊天记录
if "last_socratic_question" not in st.session_state:
    st.session_state.last_socratic_question = None # 记录题目状态
if "course_name" not in st.session_state:
    st.session_state.course_name = "通用学科" # 存储提取的课程名
if "input_disabled" not in st.session_state:
    st.session_state.input_disabled = False   # 控制输入框显隐

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
    doc = Document()
    doc.add_heading('复习资料 (由榨知机 AI 生成)', 0)
    for line in content.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('# '): doc.add_heading(line[2:], level=1)
        elif line.startswith('## '): doc.add_heading(line[3:], level=2)
        elif line.startswith('### '): doc.add_heading(line[4:], level=3)
        elif line.startswith('- ') or line.startswith('* '): doc.add_paragraph(line[2:], style='List Bullet')
        else: doc.add_paragraph(line.replace('**', '').replace('__', ''))
    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio

def render_markmap(markdown_content):
    markmap_html = f"""
    <!DOCTYPE html>
    <style>svg {{ width: 100%; height: 500px; background: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}</style>
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
    components.html(markmap_html, height=500)

# =========================================================
# [模型层]
# =========================================================

def extract_course_name(text, api_key):
    """
    [V1.4 新增] “考考我”
    """
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    system_prompt = "你是一个信息提取助手。请阅读下面的文本（课件片段），提取出这门课程的名称（例如：‘宏观经济学’、‘数据结构’）。\n要求：只输出课程名称，不要任何标点符号或废话。"
    try:
        # 只读取前1000字，节省Token且速度快
        resp = dashscope.Generation.call(
            model='qwen-turbo', # 使用轻量级模型
            messages=[{'role':Role.SYSTEM,'content':system_prompt},{'role':Role.USER,'content':text[:1000]}],
            result_format='message'
        )
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "通用课程"
    except: return "通用课程"

def call_qwen_vl_vision(images, api_key):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    content = [{"text": "分析PPT截图，提取核心知识点。"}] + [{"image": img} for img in images]
    try:
        resp = dashscope.MultiModalConversation.call(model='qwen-vl-max', messages=[{"role":"user","content":content}])
        return resp.output.choices[0]['message']['content'][0]['text'] if resp.status_code==200 else ""
    except: return ""

def call_qwen_speedrun(main_text, exam_text, api_key):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    exam_instruction = "【⚠️真题锚定】：请用“⭐”标记真题考点。" if exam_text else ""
    system_prompt = f"""
    你是一位金牌大学助教。请为“0基础”学生生成一份全能复习包。
    {exam_instruction}
    请严格按以下格式输出：
    ## part1_mindmap
    (Markdown格式思维导图代码，例如 # 主题 ## 分支)
    ## part2_concepts
    # 📘 核心知识清单
    (提取所有知识点，每个点联想1个实际案例)
    ## part3_memory
    # 🧠 必背清单
    (筛选必须背诵的年份、定义、公式)
    ## part4_exam
    # 📝 模拟真题
    (10单选+5多选+5判断+2简答+2案例。含答案，答案放在试卷最末端)
    """
    user_content = f"课件：\n{main_text[:12000]}\n\n真题：\n{exam_text[:3000]}"
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=[{'role':Role.SYSTEM,'content':system_prompt},{'role':Role.USER,'content':user_content}], result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else f"Error: {resp.message}"
    except Exception as e: return str(e)

def call_qwen_advanced(main_text, exam_text, api_key):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    anchor_instruction = "70%题目考察真题同类考点，30%考察冷门重点。" if exam_text else "基于课件出题。"
    system_prompt = f"""
    你是一位魔鬼出题人。用户是高分选手。
    {anchor_instruction}
    请输出【全真模拟试卷】：
    1. 包含：单选(10)、多选(5)、判断(5)、简答(5)、论述(2)。
    2. 难度：Hard。
    3. 答案统一放在试卷最后，不要跟在题后。
    """
    user_content = f"课件：\n{main_text[:12000]}\n\n真题：\n{exam_text[:5000]}"
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=[{'role':Role.SYSTEM,'content':system_prompt},{'role':Role.USER,'content':user_content}], result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else f"Error: {resp.message}"
    except Exception as e: return str(e)

def call_qwen_pure_chat(messages, api_key):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    system_prompt = "你是一位全能助教。请直接解答题目。如果用户只说'第几题'，请引导他复制题干。"
    api_msgs = [{'role': Role.SYSTEM, 'content': system_prompt}]
    for msg in messages: api_msgs.append({'role': msg['role'], 'content': msg['content']})
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=api_msgs, result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "Error"
    except: return "Error"

def generate_socratic_question(course_name, api_key):
    """
    [V1.4 升级] 基于课程出题
    """
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    # 概率逻辑
    seed = random.random()
    if seed < 0.5:
        q_type = "单选题 (Single Choice Question)"
    elif seed < 0.85:
        q_type = "简答题 (Short Answer Question)"
    else:
        q_type = "综合论述题 (Essay Question)"
        
    system_prompt = f"""
    你是一位严肃的考官。现在的考试科目是《{course_name}》。
    请利用你的知识储备，出一道 {q_type}。
    要求：直接出题，不要输出答案，不要废话。
    """
    
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=[{'role':Role.SYSTEM,'content':system_prompt}], result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "Error"
    except: return "Error"

def call_socratic_feedback(previous_question, user_answer, api_key):
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    system_prompt = f"""
    你是一位阅卷老师。
    题目：【{previous_question}】
    学生回答：【{user_answer}】
    请点评：
    1. 判断对错。
    2. 如果是选择题，给出正确选项和解析。
    3. 如果是简答/论述，指出得分点和失分点。
    """
    try:
        resp = dashscope.Generation.call(model='qwen-plus', messages=[{'role':Role.SYSTEM,'content':system_prompt}], result_format='message')
        return resp.output.choices[0]['message']['content'] if resp.status_code==200 else "Error"
    except: return "Error"

# =========================================================
# [UI层]
# =========================================================

with st.sidebar:
    st.title("🍋 榨知机 V1.4 ")
    
    if "DASHSCOPE_API_KEY" in st.secrets:
        api_key = st.secrets["DASHSCOPE_API_KEY"]
        st.success("✅ 开发者演示模式")
    else:
        api_key = st.text_input("API Key", type="password")

    st.markdown("---")
    
    # --- 工具箱 (仅当生成内容后显示) ---
    if st.session_state.result_content:
        st.markdown("🛠️ 互动工具箱")
        
        # 1. 考考我 (基于课程名)
        if st.button("🙋‍♂️ 考考我 ", use_container_width=True):
            if not api_key:
                st.error("请先配置 API Key")
            else:
                with st.spinner(f"正在生成《{st.session_state.course_name}》试题..."):
                    question = generate_socratic_question(st.session_state.course_name, api_key)
                    if question:
                        # 存入历史
                        st.session_state.messages.append({"role": "assistant", "content": f"【考考我】{question}"})
                        # 标记状态：等待回答，并解锁输入框
                        st.session_state.last_socratic_question = question
                        st.session_state.input_disabled = False 
                        st.rerun() # 强制刷新显示输入框

        # 2. 下载按钮
        docx_file = generate_word_file(st.session_state.result_content)
        st.download_button(
            label="📄 下载 Word 讲义",
            data=docx_file,
            file_name=f"{st.session_state.course_name}_复习资料.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True
        )
        st.markdown("---")
    
    # --- 设置区 ---
    st.markdown("### ⚙️ 复习设置")
    mode = st.radio("目标：", ("帮我速通！(全流程)", "我是高玩！(仅刷题)"), index=0)
    uploaded_files = st.file_uploader("上传课件 (必需)", type=["pdf"], accept_multiple_files=True)
    uploaded_exams = st.file_uploader("上传真题 (可选)", type=["pdf"], key="exam")
    process_btn = st.button("🚀 开始学习", type="primary", use_container_width=True)

# 主界面
st.title("🍋 榨知机 V1.4 ：你的期末救星")

# 核心处理逻辑
if process_btn and uploaded_files and api_key:
    with st.spinner('榨知机正在全速运转...'):
        # 1. 文本提取
        exam_text = extract_text_from_pdf(uploaded_exams) if uploaded_exams else ""
        main_text = ""
        for file in uploaded_files:
            bytes_data = file.read()
            text = extract_text_from_pdf(bytes_data)
            if len(text) < 50: 
                st.warning(f"视觉分析: {file.name}")
                imgs = pdf_pages_to_base64_images(bytes_data)
                text = call_qwen_vl_vision(imgs, api_key)
            main_text += text

        # 2. [新增] 提取课程名
        st.session_state.course_name = extract_course_name(main_text, api_key)

        # 3. 模式执行
        if "速通" in mode:
            raw_result = call_qwen_speedrun(main_text, exam_text, api_key)
            if "## part1_mindmap" in raw_result:
                parts = raw_result.split("## part2_concepts")
                st.session_state.mindmap_code = parts[0].replace("## part1_mindmap", "").strip()
                st.session_state.result_content = "## 核心知识清单" + parts[1] if len(parts)>1 else raw_result
            else:
                st.session_state.result_content = raw_result
        else:
            raw_result = call_qwen_advanced(main_text, exam_text, api_key)
            st.session_state.result_content = raw_result
            st.session_state.mindmap_code = ""
            
        # 4. [新增] 强制刷新，让侧边栏按钮瞬间显示
        st.rerun()

# 结果展示与动态布局
if st.session_state.result_content:
    # --- 布局 B：生成后 (左侧讲义 + 右侧聊天) ---
    st.success(f"✅ 《{st.session_state.course_name}》复习资料已生成！请在左侧下载 Word。")
    
    if st.session_state.mindmap_code:
        st.subheader("🗺️ 知识点思维导图")
        render_markmap(st.session_state.mindmap_code)
        st.markdown("---")

    col1, col2 = st.columns([6, 4])
    with col1:
        st.subheader("📄 复习资料")
        st.markdown(st.session_state.result_content)

    with col2:
        st.subheader("🤖 助教答疑")
        # 渲染历史
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.write(msg["content"])
        
        # 核心逻辑：输入框显隐控制 (Hardcore Mode)
        if not st.session_state.input_disabled:
            if prompt := st.chat_input("输入答案 或 提问..."):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"): st.write(prompt)
                
                with st.spinner("AI正在批改..."):
                    # 如果正在进行考考我
                    if st.session_state.last_socratic_question:
                        response = call_socratic_feedback(st.session_state.last_socratic_question, prompt, api_key)
                        # 闭环动作：点评完毕 -> 隐藏输入框
                        st.session_state.last_socratic_question = None
                        st.session_state.input_disabled = True # 🔒 锁住！
                    else:
                        # 普通聊天 (带记忆)
                        response = call_qwen_pure_chat(st.session_state.messages, api_key)
                
                st.session_state.messages.append({"role": "assistant", "content": response})
                # 强制刷新以更新输入框状态
                st.rerun()
        else:
            st.info("🔒 本轮测试已结束。请点击侧边栏“考考我”开启下一题，或重置页面。")

else:
    # --- 布局 A：初始状态 (全屏聊天) ---
    st.info("👈 请先在左侧上传课件，生成讲义后解锁完整功能。")
    
    # 初始状态下的聊天框 (纯伴读)
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.write(msg["content"])
        
    if prompt := st.chat_input("我是你的 AI 伴读，有什么可以帮你的？"):
         if not api_key:
             st.error("请先配置 API Key")
         else:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.write(prompt)
            with st.spinner("Thinking..."):
                response = call_qwen_pure_chat(st.session_state.messages, api_key)
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant"): st.write(response)