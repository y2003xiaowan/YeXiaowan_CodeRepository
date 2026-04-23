import json
import requests
from bs4 import BeautifulSoup
import re
from collections import deque
import asyncio
import sys
import os
from mcp.server import Server
from mcp.types import Tool
from mcp.server.stdio import stdio_server

# ====================== 火山豆包引擎 API 配置 ======================
API_KEY = "ark-1eeb20bf-09f3-43c3-9dac-0e4f2a2907fc-8bc12"
DOUBAO_API = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_NAME = "doubao-seed-2-0-lite-260215"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ====================== 编码配置 ======================
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
os.environ["PYTHONUTF8"] = "1"
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ====================== 短期记忆 ======================
class ShortTermMemory:
    def __init__(self, max_len=10):
        self.memory = deque(maxlen=max_len)
    def add(self, role, content):
        self.memory.append({"role": role, "content": content})
    def get_history(self):
        return list(self.memory)
    def clear(self):
        self.memory.clear()

# ====================== 中英语言判断 ======================
def detect_language(text):
    if not text:
        return "zh"
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\uffff')
    return "zh" if chinese > 0 else "en"

# ====================== 豆包大模型调用（无超时） ======================
def doubao_chat(messages):
    try:
        data = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.7
        }
        r = requests.post(DOUBAO_API, json=data, headers=HEADERS, timeout=None)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ 调用错误：{str(e)}"

async def doubao_async(prompt):
    try:
        messages = [{"role":"user","content":prompt}]
        data = {"model":MODEL_NAME, "messages":messages, "temperature":0.7}
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, lambda: requests.post(
            DOUBAO_API, json=data, headers=HEADERS, timeout=None
        ))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM 调用失败：{str(e)}"

# ====================== 工具1：PDF 总结 ======================
class MCPPdfSummaryTool:
    name = "mcp_pdf_summary"
    description = "读取本地PDF文件并自动生成总结"
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string", "description": "本地PDF文件路径"}},
        "required": ["file_path"]
    }

    @staticmethod
    async def execute(arguments):
        try:
            from PyPDF2 import PdfReader
            path = arguments["file_path"]
            reader = PdfReader(path)
            text = ""
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text += t
                if len(text) >= 3000:
                    break

            lang = detect_language(text)
            prompt = f"请清晰总结这段内容：\n{text[:3000]}" if lang == "zh" else f"Summarize this clearly:\n{text[:3000]}"
            summary = await doubao_async(prompt)
            return {"content": [{"type": "text", "text": f"✅ PDF 总结完成：\n{summary}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"❌ 错误：{str(e)}"}]}

# ====================== 工具2：网页总结（无超时 + 无反爬固定提示） ======================
class MCPWebSummaryTool:
    name = "mcp_web_summary"
    description = "抓取网页内容并自动总结"
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "网页URL地址"}},
        "required": ["url"]
    }

    @staticmethod
    async def execute(arguments):
        try:
            url = arguments["url"]
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                "Connection": "close"
            }

            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: requests.get(
                url, headers=headers, allow_redirects=True, timeout=None
            ))
            r.raise_for_status()
            
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.string.strip() if soup.title else "无标题"
            text = soup.get_text(strip=True)[:3000]
            lang = detect_language(text)

            prompt = f"请总结这个网页：\n{text[:3000]}" if lang == "zh" else f"Summarize this page:\n{text[:3000]}"
            summary = await doubao_async(prompt)
            return {"content":[{"type":"text","text":f"✅ {title}\n{summary}"}]}

        except Exception as e:
            return {"content":[{"type":"text","text":f"❌ 无法访问：{str(e)}"}]}

# ====================== 工具3：Moodle 文件检测（无超时） ======================
class MCPMoodleFileDetectorTool:
    name = "mcp_moodle_file_detector"
    description = "检测Moodle课程页面中的可下载文件"
    input_schema = {
        "type":"object",
        "properties":{
            "url":{"type":"string","description":"Moodle课程页面URL"},
            "cookie":{"type":"string","description":"可选：登录Cookie"}
        },
        "required":["url"]
    }

    @staticmethod
    async def execute(arguments):
        try:
            url = arguments["url"]
            cookie = arguments.get("cookie","")
            headers = {
                "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie":cookie
            }
            
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: requests.get(
                url, headers=headers, allow_redirects=True, timeout=None
            ))

            lang = detect_language(r.text[:2000])
            soup = BeautifulSoup(r.text,"html.parser")
            title = soup.title.string.strip() if soup.title else ("课程页面" if lang=="zh" else "Course Page")

            files = []
            for a in soup.find_all("a",href=True):
                href = a["href"]
                if "mod/resource/view.php" in href or re.search(r"\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z|txt)",href,re.I):
                    fname = a.get_text(strip=True) or href
                    files.append({"name":fname,"link":href})

            total = len(files)
            if lang=="zh":
                if total>0:
                    res=f"✅ {title}\n📊 发现 {total} 个文件\n\n"
                    for i,f in enumerate(files[:15],1):
                        res += f"{i}. {f['name']}\n"
                else:
                    res=f"✅ {title}\n❌ 未发现文件"
            else:
                if total>0:
                    res=f"✅ {title}\n📊 Found {total} files\n\n"
                    for i,f in enumerate(files[:15],1):
                        res += f"{i}. {f['name']}\n"
                else:
                    res=f"✅ {title}\n❌ No files found"
            return {"content":[{"type":"text","text":res}]}
        except Exception as e:
            return {"content":[{"type":"text","text":f"❌ 失败：{str(e)}"}]}

# ====================== Agent 智能体 ======================
class StudyAssistantAgent:
    def __init__(self):
        self.memory = ShortTermMemory()
        self.pdf = MCPPdfSummaryTool()
        self.web = MCPWebSummaryTool()
        self.moodle = MCPMoodleFileDetectorTool()

    def chat(self, user_input):
        self.memory.add("user", user_input)
        user = user_input.strip().lower()
        lang = detect_language(user_input)

        # 触发PDF总结
        if any(k in user for k in ["pdf","总结pdf","总结 PDF"]):
            print("请输入PDF路径：" if lang=="zh" else "Enter PDF path:")
            p = input().strip()
            return asyncio.run(self.pdf.execute({"file_path":p}))["content"][0]["text"]

        # 触发网页总结
        if any(k in user for k in ["网页","url","web","总结网页","总结页面"]):
            print("请输入URL：" if lang=="zh" else "Enter URL:")
            u = input().strip()
            return asyncio.run(self.web.execute({"url":u}))["content"][0]["text"]

        # 触发Moodle文件检测
        if any(k in user for k in ["moodle","文件","课程","download","下载"]):
            print("请输入URL：" if lang=="zh" else "Enter URL:")
            u = input().strip()
            print("需要Cookie吗？[y/n]")
            c = input().strip().lower()
            args = {"url":u}
            if c in ["y","yes"]:
                print("请输入Cookie：")
                args["cookie"] = input().strip()
            return asyncio.run(self.moodle.execute(args))["content"][0]["text"]

        # 普通对话
        sys_prompt = "你是智能学习助手，简洁、准确、友好。" if lang=="zh" else "You are a helpful study assistant."
        messages = [{"role":"system","content":sys_prompt}] + self.memory.get_history()
        reply = doubao_chat(messages)
        self.memory.add("assistant", reply)
        return reply

# ====================== MCP 服务 ======================
def run_mcp():
    server=Server("study-agent")
    
    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=MCPPdfSummaryTool.name,
                description=MCPPdfSummaryTool.description,
                inputSchema=MCPPdfSummaryTool.input_schema
            ),
            Tool(
                name=MCPWebSummaryTool.name,
                description=MCPWebSummaryTool.description,
                inputSchema=MCPWebSummaryTool.input_schema
            ),
            Tool(
                name=MCPMoodleFileDetectorTool.name,
                description=MCPMoodleFileDetectorTool.description,
                inputSchema=MCPMoodleFileDetectorTool.input_schema
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name == MCPPdfSummaryTool.name:
            return await MCPPdfSummaryTool.execute(arguments)
        if name == MCPWebSummaryTool.name:
            return await MCPWebSummaryTool.execute(arguments)
        if name == MCPMoodleFileDetectorTool.name:
            return await MCPMoodleFileDetectorTool.execute(arguments)
        return {"content":[{"type":"text","text":"未知工具"}]}

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    
    asyncio.run(main())

# ====================== 运行入口 ======================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        run_mcp()
    else:
        agent = StudyAssistantAgent()
        print("=== 中英双语智能学习助手（豆包Doubao-Seed-2.0）===")
        print("=== 输入 exit 退出 ===")
        while True:
            msg = input("你/You：")
            if msg.lower() in ["exit","quit"]:
                print("再见！")
                break
            if not msg:
                continue
            print("\nAI 助手：", agent.chat(msg), "\n")