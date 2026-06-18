"""
LangGraph の各ノード実装
"""
import logging
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_google_genai.chat_models import ChatGoogleGenerativeAI
from langchain_google_genai.embeddings import GoogleGenerativeAIEmbeddings

from rag.state import GraphState

logger = logging.getLogger(__name__)

PERSIST_DIR = "chroma_db"

# LLM と Embeddings は使い回すので、モジュールロード時に1回だけ作る
_embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2-preview")
_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)
_vectorstore = Chroma(persist_directory=PERSIST_DIR, embedding_function=_embeddings)
_retriever = _vectorstore.as_retriever(search_kwargs={"k": 3})