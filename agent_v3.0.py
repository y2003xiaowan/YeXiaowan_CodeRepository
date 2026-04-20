import json
import requests
from bs4 import BeautifulSoup
import re
from collections import deque
import asyncio
import sys
from mcp.server import Server
from mcp.types import Tool
from mcp.server.stdio import stdio_server

# ====================== 强制 UTF-8 编码（解决乱码）======================
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import os
os.environ["PYTHONUTF8"] = "1"

# ====================== Ollama 本地配置 ======================
OLLAMA_API = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3:4b"

# ====================== 短期记忆 ======================
class ShortTermMemory:
    def __init__(self, max_len=10):
        self.memory = deque(maxlen=max_len)

    def add(self, role, content):
        self.memory.append({"role": role, "content": content})

    def get_history(self):
        return list(self.memory)

# ====================== MCP 工具基类 ======================
class MCPTool:
    def __init__(self, name, description, input_schema):
        self.name = name
        self.description = description
        self.input_schema = input_schema

# ====================== MCP 工具 1：PDF 总结 ======================
class MCPPdfSummaryTool(MCPTool):
    def __init__(self):
        super().__init__(
            name="mcp_pdf_summary",
            description="读取本地PDF文件并生成双语总结",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "PDF文件本地路径"}
                },
                "required": ["file_path"]
            }
        )

    def execute(self, arguments):
        try:
            from PyPDF2 import PdfReader
            path = arguments["file_path"]
            reader = PdfReader(path)
            text = ""
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text += t
                    if len(text) > 3000:
                        break

            prompt = f"请用清晰简洁的语言总结以下内容：\n{text[:3000]}"
            summary = self._llm_call(prompt)
            return {"content": [{"type": "text", "text": f"✅ PDF 总结完成：\n{summary}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"❌ 错误：{str(e)}"}]}

    def _llm_call(self, prompt):
        try:
            data = {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            }
            res = requests.post(OLLAMA_API, json=data, timeout=30)
            res.raise_for_status()
            return res.json()["message"]["content"]
        except Exception as e:
            return f"LLM 请求失败：{str(e)}"

# ====================== MCP 工具 2：网页爬取与总结 ======================
class MCPWebSummaryTool(MCPTool):
    def __init__(self):
        super().__init__(
            name="mcp_web_summary",
            description="抓取网页内容并生成AI总结",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type":"string", "description": "网页URL"}
                },
                "required": ["url"]
            }
        )
        self.headers = {"User-Agent": "Mozilla/5.0"}

    def execute(self, arguments):
        try:
            url = arguments["url"]
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.title.string.strip() if soup.title else "无标题"
            text = soup.get_text(strip=True)[:4000]

            prompt = f"请用清晰的中文总结这个网页：\n标题：{title}\n内容：{text}"
            summary = self._llm_call(prompt)
            return {"content": [{"type": "text", "text": f"✅ 网页：{title}\n📝 总结：{summary}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"❌ 错误：{str(e)}"}]}

    def _llm_call(self, prompt):
        try:
            data = {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            }
            res = requests.post(OLLAMA_API, json=data, timeout=30)
            res.raise_for_status()
            return res.json()["message"]["content"]
        except Exception as e:
            return f"LLM 请求失败：{str(e)}"

# ====================== MCP 工具 3：Moodle 页面新增文件检测 ======================
class MCPMoodleFileDetectorTool(MCPTool):
    def __init__(self):
        super().__init__(
            name="mcp_moodle_file_detector",
            description="检测Moodle页面上的所有文件",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Moodle课程URL"}
                },
                "required": ["url"]
            }
        )
        self.headers = {"User-Agent": "Mozilla/5.0"}

    def execute(self, arguments):
        try:
            url = arguments["url"]
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.title.string.strip() if soup.title else "无标题"

            file_links = []
            for a in soup.find_all("a", href=True):
                if re.search(r"\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip)", a["href"], re.I):
                    file_links.append(a)

            unique_files = []
            seen = set()
            for a in file_links:
                href = a["href"]
                if href not in seen:
                    seen.add(href)
                    name = a.get_text(strip=True) or "未命名文件"
                    unique_files.append(f"- {name}")

            result = f"✅ 课程：{title}\n文件总数：{len(unique_files)}\n" + "\n".join(unique_files[:15])
            return {"content": [{"type": "text", "text": result}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"❌ 错误：{str(e)}"}]}

# ====================== AI Agent 智能助手 ======================
class StudyAssistantAgent:
    def __init__(self):
        self.memory = ShortTermMemory()
        self.mcp_tools = [
            MCPPdfSummaryTool(),
            MCPWebSummaryTool(),
            MCPMoodleFileDetectorTool()
        ]

    def chat(self, user_input):
        self.memory.add("user", user_input)
        user = user_input.lower()

        # 完全移除 input()，避免 MCP 卡死
        reply = "我是AI学习助手，支持：PDF总结、网页总结、Moodle文件检测"
        self.memory.add("assistant", reply)
        return reply

# ====================== MCP sever MCP服务======================
def run_mcp_server():
    server = Server("study-assistant-mcp")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=MCPPdfSummaryTool().name,
                description=MCPPdfSummaryTool().description,
                inputSchema=MCPPdfSummaryTool().input_schema
            ),
            Tool(
                name=MCPWebSummaryTool().name,
                description=MCPWebSummaryTool().description,
                inputSchema=MCPWebSummaryTool().input_schema
            ),
            Tool(
                name=MCPMoodleFileDetectorTool().name,
                description=MCPMoodleFileDetectorTool().description,
                inputSchema=MCPMoodleFileDetectorTool().input_schema
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name == "mcp_pdf_summary":
                return MCPPdfSummaryTool().execute(arguments)
            elif name == "mcp_web_summary":
                return MCPWebSummaryTool().execute(arguments)
            elif name == "mcp_moodle_file_detector":
                return MCPMoodleFileDetectorTool().execute(arguments)
            return {"content": [{"type": "text", "text": "未知工具"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"执行失败：{str(e)}"}]}

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )

    asyncio.run(main())

# ====================== 运行入口 ======================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        run_mcp_server()
    else:
        agent = StudyAssistantAgent()
        print("=== AI Agent智能体 (Ollma qwen 3:4b) ===")
        print("Type 'exit' to quit/输入 'exit' 退出\n\n")
        while True:
            msg = input("You你：")
            if msg.lower() == "exit":
                break
            print("\nAI智能体：", agent.chat(msg), "\n")