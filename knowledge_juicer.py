import streamlit as st
import os
import fitz  # PyMuPDF，用于提取PDF文本
import dashscope # 阿里云百炼大模型SDK
from dashscope.api_entities.dashscope_response import Role

# =========================================================
# 1. 全局配置与状态初始化 (Config & Init)
# =========================================================
st.set_page_config(
    page_title="榨知机 - 期末复习神器", 
    page_icon="🍋", 
    layout="wide"
)

# 初始化 Session State (类似于网站的“短期记忆”)
if "knowledge_base" not in st.session_state:
    st.session_state.knowledge_base = ""  # 存放 AI 生成的复习讲义
if "messages" not in st.session_state:
    st.session_state.messages = []        # 存放聊天记录

# =========================================================
# 2. 核心功能函数层 (Core Logic)
#    这里是你的“技术护城河”，负责处理数据和调用 AI
# =========================================================

def extract_text_from_pdf(uploaded_file):
    """
    [功能]: 从 PDF 文件流中提取纯文本
    [技术点]: 使用 PyMuPDF 直接处理内存流，无需保存文件到硬盘，保护隐私且速度快。
    """
    try:
        file_bytes = uploaded_file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_content = ""
        for page in doc:
            text_content += page.get_text()
        doc.close()
        return text_content
    except Exception as e:
        return f"[Error] PDF解析失败: {str(e)}"

def process_ppt_placeholder(uploaded_file):
    """
    [功能]: PPT 处理占位函数 (MVP V1.0)
    [说明]: 目前 V1.0 版本主要支持 PDF。如果用户上传 PPT，提示暂时不支持。
    [迭代计划]: V2.0 将引入 Qwen-VL 视觉模型直接进行截图识别。
    """
    return f"\n[提示] 文件 {uploaded_file.name} 是 PPT 格式。由于 MVP 版本限制，暂跳过 PPT 处理。请导出为 PDF 后上传以获得最佳效果。\n"

def call_qwen_summary(raw_text, api_key):
    """
    [功能]: 调用通义千问 (Qwen-Plus) 生成复习讲义
    [输入]: 杂乱的课件文本
    [输出]: 结构化的 Markdown 复习资料
    """
    if not raw_text: return None
    dashscope.api_key = api_key 

    # 精心设计的 Prompt (提示词工程)
    system_prompt = (
        "你是一个严厉的大学助教，请根据学生提供的课件内容，整理出Markdown格式的期末复习讲义。\n"
        "要求：\n"
        "1. 【概念解析】：首先根据题目中所提到的所有概念性名词、公式、原理给予清晰的解释，并附带通俗易懂的实际例子。\n"
        "2. 【重点标注】：作为经常出题的助教，判断哪些是重点，在输出内容中对重点内容加粗或高亮。\n"
        "3. 【模拟试题】：根据提到的知识点（尤其是高频考点），输出三道模拟试题（含答案），风格符合中国本科大学期末考试。\n"
    )

    messages = [
        {'role': Role.SYSTEM, 'content': system_prompt},
        {'role': Role.USER, 'content': f"这是我的课件内容摘要：\n\n{raw_text[:12000]}"} # 扩容到1.2万字
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
            return f"❌ 大模型调用失败: {response.code} - {response.message}"
    except Exception as e:
        return f"❌ 系统错误: {str(e)}"

def call_qwen_tutor(messages, context, api_key):
    """
    [功能]: 苏格拉底式助教对话
    [原理]: RAG (检索增强生成) 的简化版 -> Context Injection (上下文注入)
    """
    dashscope.api_key = api_key
    
    # 注入“记忆”
    system_prompt = f"""
    你现在是一位苏格拉底式的大学助教。你的任务是帮助学生巩固复习。
    
    【背景知识】：
    {context}
    
    【教学规则】：
    1. 不要直接把答案灌输给学生。
    2. 采用“反问”和“引导”的方式，检查学生是否掌握了【背景知识】里的重点。
    3. 如果学生问“考考我”，请从【背景知识】里挑一个核心考点出一个任意类型的题。
    4. 只有当学生回答错误时，才给出正确解析。
    """
    
    # 构建历史对话链
    api_messages = [{'role': Role.SYSTEM, 'content': system_prompt}]
    for msg in messages:
        api_messages.append({'role': msg['role'], 'content': msg['content']})

    try:
        response = dashscope.Generation.call(
            model='qwen-plus',
            messages=api_messages,
            result_format='message',
        )
        if response.status_code == 200:
            return response.output.choices[0]['message']['content']
        else:
            return "❌ 助教掉线了，请检查网络或Key。"
    except Exception as e:
        return f"❌ 发生错误: {str(e)}"

# =========================================================
# 3. 界面交互层 (UI Layout)
# =========================================================

# --- 侧边栏 (Sidebar) ---
with st.sidebar:
    st.title("🍋 榨知机")
    st.caption("v1.0 | 期末生存指南")
    st.markdown("---")
    
    # 敏感信息输入
    api_key = st.text_input("请输入通义千问 API Key", type="password", help="去阿里云百炼平台申请")
    
    # 文件上传
    st.markdown("### 1. 投喂课件")
    uploaded_files = st.file_uploader(
        "上传PDF课件", 
        type=["pdf", "ppt", "pptx"], 
        accept_multiple_files=True
    )
    
    process_btn = st.button("开始榨取知识 🚀", use_container_width=True)
    
    # 底部说明
    st.markdown("---")
    st.info("💡 提示：支持多文件上传，上传越多，知识点越全。")

# --- 主展示区 (Main Area) ---
st.title("🎓 榨知机 Knowledge Juicer")

# 逻辑分支 1: 开始处理文件
if process_btn and uploaded_files and api_key:
    with st.spinner('正在疯狂阅读你的课件 (让 GPU 飞一会儿)...'):
        all_content = ""
        progress_bar = st.progress(0)
        
        for i, file in enumerate(uploaded_files):
            # 根据文件类型分流处理
            if file.type == "application/pdf":
                all_content += extract_text_from_pdf(file)
            else:
                # 处理 PPT 或其他格式的占位符
                all_content += process_ppt_placeholder(file)
            
            progress_bar.progress((i + 1) / len(uploaded_files))
        
        if len(all_content) > 50:
            # 调用大模型
            review_guide = call_qwen_summary(all_content, api_key)
            st.session_state.knowledge_base = review_guide
            
            # (可选) 开发者调试信息
            with st.expander("🕵️‍♂️ 开发者模式：查看提取原文"):
                st.text(all_content[:2000])
            
            st.success("✅ 知识榨取完成！请查看右侧讲义。")
        else:
            st.error("❌ 未提取到有效文字。请检查PDF是否为纯图片/扫描件。")

# 逻辑分支 2: 展示结果与问答区
if st.session_state.knowledge_base:
    # 使用两列布局
    col1, col2 = st.columns([6, 4]) # 左侧讲义宽(6)，右侧对话窄(4)
    
    # 左侧：复习讲义展示
    with col1:
        st.subheader("📄 复习讲义")
        st.markdown(st.session_state.knowledge_base)
        st.download_button(
            label="📥 下载讲义 (.md)", 
            data=st.session_state.knowledge_base, 
            file_name="期末复习重点.md"
        )
    
    # 右侧：苏格拉底助教
    with col2:
        st.subheader("🤖 AI 助教")
        st.markdown("---")
        
        # 渲染聊天记录
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        # 处理用户输入
        if prompt := st.chat_input("不懂就问，或者输入'考考我'"):
            # 1. 显示用户消息
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.write(prompt)
            
            # 2. 生成并显示 AI 回复
            with st.spinner("🤔 助教思考中..."):
                response = call_qwen_tutor(
                    st.session_state.messages, 
                    st.session_state.knowledge_base, 
                    api_key
                )
            
            # 3. 保存并显示 AI 消息 (修复了原来的双重显示Bug)
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant"):
                st.write(response)

# 逻辑分支 3: 初始欢迎页
elif not uploaded_files:
    st.info("👈 请在左侧侧边栏输入 Key 并上传课件，开始你的复习之旅。")