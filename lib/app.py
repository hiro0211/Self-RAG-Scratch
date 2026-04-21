import os 
import logging
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_google_genai.chat_models import ChatGoogleGenerativeAI
from langchain_google_genai.embeddings import GoogleGenerativeAIEmbeddings
from langchain_google_genai.llms import GoogleGenerativeAI

# ログ設定
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 環境変数の読み取り
load_dotenv()
logger.debug("環境変数を読み込みました")
logger.debug(f"GOOGLE_API_KEY の存在: {bool(os.getenv('GOOGLE_API_KEY'))}")

DOCS_DIR ="docs"
PERSIST_DIR = "chroma_db"

# ---------------------------------------------------------------
# 1. ベクトルDBの構築
# ---------------------------------------------------------------
@st.cache_resource(show_spinner="ドキュメントをインデックス化中...")
def build_vectorstore():
    logger.debug("=== build_vectorstore() 開始 ===")
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2-preview")
    logger.debug("Embeddingsモデル作成完了: models/gemini-embedding-2-preview")

    # すでに永続化済みなら読み込むだけ
    if Path(PERSIST_DIR).exists() and any(Path(PERSIST_DIR).iterdir()):
        logger.debug(f"永続化済みDB検出: {PERSIST_DIR} → 既存DBを読み込みます")
        return Chroma(
            persist_directory=PERSIST_DIR,
            embedding_function=embeddings,
        )

    logger.debug(f"永続化済みDBなし → 新規構築を開始します")

    # 1-1. ドキュメント読み込み (.md と .txt を対象)
    loader = DirectoryLoader(
        DOCS_DIR,
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    docs = loader.load()
    logger.debug(f"ドキュメント読み込み完了: {len(docs)}件")
    for i, doc in enumerate(docs):
        logger.debug(f"  doc[{i}] source: {doc.metadata.get('source', '?')}, 文字数: {len(doc.page_content)}")

    # 1-2. チャンク分割
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
    )
    chunks = splitter.split_documents(docs)
    logger.debug(f"チャンク分割完了: {len(chunks)}件 (chunk_size=500, overlap=50)")
    for i, chunk in enumerate(chunks[:3]):
        logger.debug(f"  chunk[{i}] 先頭100文字: {chunk.page_content[:100]}...")

    # 1-3. ChromaへEmbedding & 保存
    logger.debug("ChromaへEmbedding開始...")
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=PERSIST_DIR,
    )
    logger.debug(f"ChromaへEmbedding完了 → {PERSIST_DIR} に永続化しました")
    return vectorstore


# ---------------------------------------------------------------
# 2. RAGチェーンの構築
# ---------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def build_rag_chain():
    logger.debug("=== build_rag_chain() 開始 ===")
    vectorstore = build_vectorstore()
    retriever = vectorstore.as_retriever()
    logger.debug("Retriever作成完了 (デフォルト k=4)")

    prompt = ChatPromptTemplate.from_template(
        """あなたは社内ドキュメントに基づいて回答するアシスタントです。
以下の「コンテキスト」だけを根拠に、日本語で簡潔に答えてください。
コンテキストに答えがない場合は「資料には記載がありません」と答えてください。

# コンテキスト
{context}

# 質問
{question}

# 回答
"""
    )
    logger.debug("プロンプトテンプレート設定完了")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)
    logger.debug("LLMモデル設定完了: gemini-2.5-flash-lite (temperature=0)")

    def format_docs(docs):
        formatted = "\n\n".join(d.page_content for d in docs)
        logger.debug(f"format_docs: {len(docs)}件のドキュメントをテキストに整形 (合計{len(formatted)}文字)")
        return formatted

    # LCEL (LangChain Expression Language) でパイプラインを宣言的に組む
    chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    logger.debug("RAGチェーン構築完了")
    logger.debug("=== build_rag_chain() 完了 ===")
    return chain, retriever

# ---------------------------------------------------------------
# 3. Streamlit UI
# ---------------------------------------------------------------
st.set_page_config(page_title="シンプルRAG", page_icon="📚")
st.title("📚 RAGトレーニング Langchain")
st.caption("docs/内のMarkdownを根拠に回答します")

#APキー未設定チェック
if not os.getenv("GOOGLE_API_KEY"):
    logger.error("GOOGLE_API_KEY が未設定です")
    st.error(".envにGOOGLE_API_KEYを設定してください。")
    st.stop()

logger.debug("APIキー確認OK → RAGチェーン構築開始")
chain, retriever = build_rag_chain()

question = st.text_input("質問を入力", placeholder="例: コアタイムは何時から？")

if st.button("送信", type="primary") and question:
    logger.debug(f"=== ユーザー質問受信: {question} ===")
    with st.spinner("考え中..."):
        st.subheader("💬 回答")
        st.write_stream(chain.stream(question))
    logger.debug("LLM回答のストリーミング完了")

    with st.expander("🔎 参照したチャンクを見る"):
        related_docs = retriever.invoke(question)
        logger.debug(f"Retriever取得結果: {len(related_docs)}件")
        for i, d in enumerate(related_docs, 1):
            logger.debug(f"  [{i}] source: {d.metadata.get('source', '?')}, 先頭50文字: {d.page_content[:50]}...")
            st.markdown(f"**[{i}] source: `{d.metadata.get('source', '?')}`**")
            st.code(d.page_content)
