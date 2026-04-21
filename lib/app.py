import os 
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_google_genai import GoogleGenerativeAiEmbeddings, ChatGoogleGenerativeAI

# 環境変数の読み取り
load_dotenv()

DOCS_DIR ="docs"
PERSIST_DIR = "chroma_db"

#------------------------------------------------
# 1. ベクトルDBの構築
#------------------------------------------------
# ---------------------------------------------------------------
# 1. ベクトルDBの構築（初回のみ）
#    @st.cache_resource により、再実行されても1回しか走らない
# ---------------------------------------------------------------
@st.cache_resource(show_spinner="ドキュメントをインデックス化中...")
def build_vectorstore():
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    # すでに永続化済みなら読み込むだけ
    if Path(PERSIST_DIR).exists() and any(Path(PERSIST_DIR).iterdir()):
        return Chroma(
            persist_directory=PERSIST_DIR,
            embedding_function=embeddings,
        )

    # 1-1. ドキュメント読み込み (.md と .txt を対象)
    loader = DirectoryLoader(
        DOCS_DIR,
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    docs = loader.load()

    # 1-2. チャンク分割
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
    )
    chunks = splitter.split_documents(docs)

    # 1-3. ChromaへEmbedding & 保存
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=PERSIST_DIR,
    )
    return vectorstore


# ---------------------------------------------------------------
# 2. RAGチェーンの構築
# ---------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def build_rag_chain():
    vectorstore = build_vectorstore()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

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

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)

    def format_docs(docs):
        return "\n\n".join(d.page_content for d in docs)

    # LCEL (LangChain Expression Language) でパイプラインを宣言的に組む
    chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain, retriever

# ---------------------------------------------------------------
# 3. Streamlit UI
# ---------------------------------------------------------------
st.set_page_config(page_title="シンプルRAG", page_icon="📚")
st.title("📚 RAGトレーニング Langchain")
st.caption("docs/内のMarkdownを根拠に回答します")

#APキー未設定チェック
if not os.getenv("GOOGLE_API_KEY"):
    st.error(".envにGOOGLE_API_KEYを設定してください。")
    st.stop()

chain, retriever = build_rag_chain()

question = st.text_input("質問を入力", placeholder="例: コアタイムは何時から？")

if st.button("送信", type="primary") and question:
    with st.spinner("考え中..."):
        answer = chain.invoke(question)
    
    st.subheader