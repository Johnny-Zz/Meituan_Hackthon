"""LLM model initialization for the agent graph.

Default backend: DeepSeek V4 via OpenAI-compatible API.
"""
import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    model_provider="openai",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
