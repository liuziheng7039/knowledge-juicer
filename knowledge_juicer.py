import streamlit as st
import fitz  # PyMuPDF
import dashscope
from dashscope.api_entities.dashscope_response import Role
import base64

# =========================================================
# [配置层] 全局设置与状态初始化
# =========================================================
st.set_page_config(page_title="榨知机 V1.2", page_icon="🍋", layout="wide")

# 初始化 Session State (会话状态)
# knowledge_base: 存储 AI 生成的最终复习讲义
# messages: 存储与 AI 助教的对话历史
if "knowledge_base" not in st.session_state:
    st.session_state.knowledge_base = ""
if "messages" not in st.session_state:
    st.session_state.messages = []

# =========================================================
# [逻辑层] 核心技术函数库
# =========================================================

def extract_text_from_pdf(file_bytes):
    """
    [基础功能] 从 PDF 文件流中提取纯文本。
    适用场景：普通的文字版 PDF 教材/课件。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text

def pdf_pages_to_base64_images(file_bytes, max_pages=5):
    """
    [多模态预处理] 将 PDF 页面转换为图片流 (Base64)。
    
    参数:
        max_pages (int): 为了平衡 Token 消耗与响应速度，默认仅截取前 5 页进行视觉分析。
        (通常前几页包含了目录或核心大纲)
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images_base64 = []
    
    target_pages = min(len(doc), max_pages)
    
    for i in range(target_pages):
        page = doc.load_page(i)
        # dpi=72 是为了降低图片分辨率，减少 Token 消耗，同时足够 AI 识别文字
        pix = page.get_pixmap(dpi=72) 
        img_data = pix.tobytes("png")
        base64_str = base64.b64encode(img_data).decode("utf-8")
        images_base64.append(f"data:image/png;base64,{base64_str}")
        
    doc.close()
    return images_base64

def call_qwen_vl_vision(images, api_key):
    """
    [视觉模型] 调用 Qwen-VL-Max 进行图像理解。
    功能：当检测到 PDF 文字过少（疑似扫描件/PPT）时触发，提取图片中的知识点。
    """
    # 容错处理：清洗 Key 中的中文符号
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    # 构造 Qwen-VL 专用的多模态消息格式
    content_list = [{"text": "你是大学助教。请详细分析这些 PPT/课件截图，提取里面的所有核心知识点、文字内容和图表含义，输出为详细的文本笔记。"}]
    for img in images:
        content_list.append({"image": img})
        
    messages = [
        {
            "role": "user",
            "content": content_list
        }
    ]

    try:
        response = dashscope.MultiModalConversation.call(
            model='qwen-vl-max', 
            messages=messages,
            result_format='message',
        )
        if response.status_code == 200:
            return response.output.choices[0]['message']['content'][0]['text']
        else:
            return f"[视觉识别失败] Code: {response.code}"
    except Exception as e:
        return f"[视觉系统错误] {str(e)}"

def call_qwen_summary_v1_2(main_content, exam_content, api_key):
    """
    [核心大脑] 调用 Qwen-Plus 生成结构化讲义 (V1.2版)。
    特性：
    1. 提示词工程：强制 Markdown 排版。
    2. 上下文学习：结合真题 (exam_content) 进行加权分析。
    """
    # 容错处理
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")

    # 动态构建指令：如果存在真题，插入最高优先级指令
    exam_instruction = ""
    if exam_content:
        exam_instruction = f"""
        【⚠️ 真题锚定模式已开启】：
        下方提供了【真题参考】。请仔细分析其出题风格。
        在生成讲义时，如果遇到真题里的知识点，必须使用“⭐”图标高亮，并引用真题原题作为例证。
        """

    # System Prompt：定义 AI 的人设与输出规范
    system_prompt = f"""
    你是一位**追求极致排版**的大学金牌助教。请整理一份结构清晰、阅读体验极佳的期末复习讲义。
    {exam_instruction}
    
    【排版严格要求】：
    1. **层级分明**：主标题用 # (H1)，模块标题用 ## (H2)，子知识点用 ### (H3)。
    2. **视觉优化**：
       - 核心定义必须**加粗**。
       - 使用 Emoji 图标（如 🎯, 💡, ⚡, 📝）区分不同板块。
       - 关键公式请使用 LaTeX 格式（如 $E=mc^2$）。
    3. **列表规范**：使用无序列表 (-) 展示细节，段落之间保留空行。
    4. **分隔线**：不同大板块之间使用 `---` 分隔线。

    【目标输出结构】：
    # 📘 [课程名称] 期末突击讲义
    
    ## 🎯 核心概念深度解析
    （此处解释名词、原理，配合通俗例子）
    
    ## ⭐ 真题/高频考点映射
    （此处列出真题里考过的点，标注出题年份或类型）
    
    ## 🧠 必背记忆清单
    （此处列出死记硬背的公式、年代，建议用表格形式展示）
    
    ## 📝 模拟押题 (含解析)
    （基于真题风格，出3套模拟题：单选/多选/简答/论述。每套题100分，符合中国大学期末风格，题目与答案分离展示。）
    """

    # 拼接用户输入：课件摘要 + 真题参考
    user_content = f"【课件内容摘要】：\n{main_content[:15000]}\n\n"
    if exam_content:
        user_content += f"【真题参考】：\n{exam_content[:5000]}"

    messages = [
        {'role': Role.SYSTEM, 'content': system_prompt},
        {'role': Role.USER, 'content': user_content}
    ]

    try:
        response = dashscope.Generation.call(
            model='qwen-plus', 
            messages=messages,
            result_format='message',
        )
        if response.status_code == 200:
            return response.output.choices[0]['message']['content']
        else:
            return f"❌ 讲义生成失败: {response.code}"
    except Exception as e:
        return f"❌ 系统错误: {str(e)}"

def call_qwen_tutor(messages, context, api_key):
    """
    [交互层] 苏格拉底导学助手。
    基于生成的讲义 (context) 进行 RAG 对话，而不依赖外部知识。
    """
    dashscope.api_key = api_key.strip().replace("：", "").replace(":", "")
    
    system_prompt = f"你是一位苏格拉底式助教。\n【背景知识】\n{context}\n请基于背景知识进行反问式教学，不要直接给答案，而是引导学生思考。"
    
    api_messages = [{'role': Role.SYSTEM, 'content': system_prompt}]
    for msg in messages:
        api_messages.append({'role': msg['role'], 'content': msg['content']})
        
    try:
        response = dashscope.Generation.call(
            model='qwen-plus', messages=api_messages, result_format='message'
        )
        if response.status_code == 200: 
            return response.output.choices[0]['message']['content']
        else:
            return "❌ 助教掉线了 (API Error)"
    except Exception as e: 
        return f"❌ 连接错误: {str(e)}"

# =========================================================
# [界面层] Streamlit UI 布局
# =========================================================

# --- 侧边栏配置 ---
with st.sidebar:
    st.title("🍋 榨知机 Pro")
    st.caption("🚀 版本：V1.2 (演示版)")
    
    # --- 📢 更新公告 (Updated for V1.2) ---
    with st.expander("🔔 V1.2 更新日志", expanded=True):
        st.markdown("""
        **最新特性 (New Features):**
        1. 🔑 **免 Key 速通**：内置开发者演示 Key，无需输入即可直接体验！
        2. 👁️ **视觉觉醒**：AI 读图能力上线，搞定 PPT 和扫描件。
        3. ⚓ **真题锚定**：上传真题，押题更精准。
        4. 🎨 **排版重构**：讲义格式深度优化。
        """)
    # ------------------------------------
    
    st.markdown("---")
    
    # --- 🔐 智能 Key 管理 (Secrets 逻辑) ---
    # 1. 优先检查云端 Secrets
    if "DASHSCOPE_API_KEY" in st.secrets:
        api_key = st.secrets["DASHSCOPE_API_KEY"]
        st.success("✅ 已加载开发者演示 Key，可直接使用！")
    
    # 2. 如果无 Secrets，则要求用户输入
    else:
        api_key = st.text_input("请输入通义千问 API Key", type="password")
        if not api_key:
            st.warning("⚠️ 个人部署请先输入 API Key")
    # -------------------------------------

    # 分区 1: 课件上传
    st.markdown("### 1. 投喂原材料 (课件)")
    uploaded_files = st.file_uploader(
        "支持 PDF (含扫描件/PPT转PDF)", 
        type=["pdf"], 
        accept_multiple_files=True
    )
    
    # 分区 2: 真题上传
    st.markdown("### 2. 投喂催化剂 (往年)")
    st.info("💡 选填。上传真题，让 AI 懂老师的出题套路。")
    uploaded_exams = st.file_uploader(
        "上传真题/提纲 (PDF)", 
        type=["pdf"], 
        key="exam_uploader"
    )
    
    process_btn = st.button("开始深度榨取 🚀", type="primary")

# --- 主内容区域 ---
st.title("🎓 榨知机 V1.2：全能备考助手")

# 逻辑分支：开始处理
if process_btn and uploaded_files and api_key:
    with st.spinner('AI 正在看课件、读真题，大脑飞速运转中...'):
        
        # 1. 处理真题
        exam_text = ""
        if uploaded_exams:
            exam_text = extract_text_from_pdf(uploaded_exams)
            st.toast(f"✅ 已加载真题，长度：{len(exam_text)}字")

        # 2. 处理课件 (智能路由：文本 vs 视觉)
        main_text = ""
        vision_triggered = False
        
        progress_bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            file_bytes = file.read()
            
            # 尝试提取文本
            text = extract_text_from_pdf(file_bytes)
            
            # 触发视觉模型的条件：字数过少
            if len(text) < 50: 
                st.warning(f"⚠️ 文件 {file.name} 看起来像图片/PPT，正在切换【视觉模型】...(速度稍慢)")
                vision_triggered = True
                
                # 转换图片 -> 调用 Qwen-VL
                images = pdf_pages_to_base64_images(file_bytes, max_pages=5)
                vision_desc = call_qwen_vl_vision(images, api_key)
                text = f"\n[视觉模型描述 - {file.name}]:\n{vision_desc}\n"
            
            main_text += text
            progress_bar.progress((i + 1) / len(uploaded_files))
        
        # 3. 综合生成
        if len(main_text) > 50:
            result = call_qwen_summary_v1_2(main_text, exam_text, api_key)
            st.session_state.knowledge_base = result
            st.success("🎉 榨取成功！")
            if vision_triggered:
                st.info("💡 刚才启用了视觉模型，已成功提取图片中的知识点。")
        else:
            st.error("❌ 无法提取有效内容，请检查文件是否损坏。")

# 逻辑分支：结果展示与互动
if st.session_state.knowledge_base:
    col1, col2 = st.columns([6, 4])
    
    # 左侧：讲义区
    with col1:
        st.markdown("### 📄 结构化讲义")
        st.markdown(st.session_state.knowledge_base)
        st.download_button("下载讲义 (.md)", st.session_state.knowledge_base, "复习资料.md")
    
    # 右侧：对话区
    with col2:
        st.markdown("### 🤖 助教答疑")
        # 渲染历史消息
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
        
        # 处理新消息
        if prompt := st.chat_input("不懂就问，或者输入'考考我'..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): 
                st.write(prompt)
            
            with st.spinner("助教思考中..."):
                response = call_qwen_tutor(st.session_state.messages, st.session_state.knowledge_base, api_key)
            
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant"): 
                st.write(response)

# 初始引导页
elif not uploaded_files:
    st.info("👈 请在左侧上传课件。V1.2 已支持免 Key 试用！")