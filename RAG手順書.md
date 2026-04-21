# Streamlit × LangChain × Chroma で作る シンプルRAGアプリ 手順書

> **対象読者**: Pythonの基本構文がわかる初学者
> **想定OS**: macOS（Apple Silicon / Intel どちらもOK）
> **LLM**: OpenAI (gpt-4o-mini)
> **ベクトルDB**: Chroma（ローカル）
> **題材**: テキスト/Markdownファイル（自分のメモや社内ドキュメントなど）
> **完成イメージ**: ブラウザでStreamlitアプリを開き、テキスト入力欄に質問 → ローカルに置いたMarkdownの内容を踏まえてLLMが回答する

---

## 0. RAGとは？（5分で理解）

**RAG (Retrieval-Augmented Generation)** = 「検索して」「文章生成する」仕組み。

LLMは学習データに含まれない最新情報や社内ドキュメントを知らない。そこで、

1. 自分のドキュメントを **小さく分割（チャンク化）** して
2. 各チャンクを **ベクトル化（Embedding）** し、ベクトルDBに保存
3. ユーザーの質問もベクトル化し、 **意味的に近いチャンク** をDBから取り出す（Retrieval）
4. 取り出したチャンクを **プロンプトに埋め込んでLLMに渡す**（Augmented Generation）

これでLLMは「自分のドキュメントに基づいた」回答を返せる。

```
[ユーザー質問] → [Embedding] → [Chroma検索] → [関連チャンク取得]
                                                          ↓
[プロンプト構築: 質問 + 関連チャンク] → [LLM (gpt-4o-mini)] → [回答]
```

LangChainはこれらの「ローダー / スプリッター / Embedding / VectorStore / LLM / プロンプト / チェーン」をパーツ化した便利ライブラリ。Streamlitは数行でWeb UIが書けるPythonライブラリ。

---

## 1. 事前準備

### 1-1. 必要なもの

- macOS
- Python 3.10 以上（3.11 推奨）
- ターミナル（標準のTerminal.appでOK）
- エディタ（VS Code推奨）
- OpenAI APIキー … [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys) で発行（クレカ登録 & 少額チャージが必要）

### 1-2. Pythonバージョン確認

```bash
python3 --version
```

`Python 3.10.x` 以上が表示されればOK。古い場合はHomebrewで入れる：

```bash
# Homebrewが未導入なら
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python@3.11
```

---

## 2. プロジェクトディレクトリの作成

```bash
mkdir ~/rag-streamlit-app
cd ~/rag-streamlit-app
```

以降、すべての作業はこのディレクトリで行う。

---

## 3. 仮想環境の構築

プロジェクトごとにライブラリを隔離するため、Pythonの仮想環境（venv）を作る。

```bash
python3 -m venv .venv
source .venv/bin/activate
```

プロンプト先頭に `(.venv)` が付けばOK。
抜けるときは `deactivate`。

> 💡 これ以降の `pip install` は**必ず仮想環境を有効化した状態**で行うこと。

---

## 4. 必要ライブラリのインストール

`requirements.txt` を作成：

```bash
cat > requirements.txt << 'EOF'
streamlit==1.39.0
langchain==0.3.7
langchain-openai==0.2.8
langchain-chroma==0.1.4
langchain-community==0.3.7
langchain-text-splitters==0.3.2
chromadb==0.5.18
python-dotenv==1.0.1
EOF
```

インストール：

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

各ライブラリの役割：

| ライブラリ | 役割 |
|---|---|
| `streamlit` | WebUIフレームワーク |
| `langchain` | RAGの土台 |
| `langchain-openai` | OpenAI LLM / Embeddingのラッパー |
| `langchain-chroma` | Chroma連携 |
| `langchain-community` | テキストローダー等の共通機能 |
| `langchain-text-splitters` | チャンク分割 |
| `chromadb` | ベクトルDB本体 |
| `python-dotenv` | `.env` ファイルから環境変数を読む |

---

## 5. APIキーを `.env` に保存

```bash
cat > .env << 'EOF'
OPENAI_API_KEY=sk-ここに自分のキーを貼る
EOF
```

そして `.gitignore` に `.env` を追加（誤コミット防止）：

```bash
cat > .gitignore << 'EOF'
.venv/
.env
__pycache__/
chroma_db/
EOF
```

---

## 6. 題材となるドキュメントを用意

```bash
mkdir docs
```

`docs/` 配下に、自分のメモやMarkdownを入れる。練習用にサンプルを作る：

```bash
cat > docs/sample.md << 'EOF'
# 社内FAQ サンプル

## 勤務時間について
当社のコアタイムは10時から15時です。フレックスタイム制を採用しており、
始業は7時から10時の間、終業は15時から20時の間で自由に選べます。

## 有給休暇
入社半年経過後、10日付与されます。1年ごとに1日ずつ増えます。
取得は前日までの申請でOKです。

## リモートワーク
週3日までリモート可。事前にチームリーダーへSlackで連絡すること。
出社日は火曜と木曜が推奨されています。
EOF
```

ここに**自分の好きなドキュメント**を追加していけば、それに答えるBotになる。

---

## 7. RAG処理の中身を理解する（ファイルを作る前にイメージ）

これから `app.py` に書くロジックを、ステップで把握しておく。

```
[起動時に1回だけ]
1. docs/ 内のテキストを読み込む           ← TextLoader / DirectoryLoader
2. テキストをチャンクに分割（例: 500文字）  ← RecursiveCharacterTextSplitter
3. 各チャンクをEmbedding化               ← OpenAIEmbeddings
4. Chromaに保存                          ← Chroma.from_documents

[質問が来るたびに]
5. 質問文をEmbedding化して類似チャンクを取得 ← Retriever
6. プロンプトテンプレに埋め込む             ← ChatPromptTemplate
7. LLMで回答生成                          ← ChatOpenAI
8. 結果を画面に表示                       ← st.write
```

---

## 8. メイン処理 `app.py` を作成

`app.py` を作成し、以下を貼り付ける。
**全部一気に書かず、セクションごとに何をしているか読みながら写経する**のがおすすめ。

```python
"""
シンプルRAGアプリ (Streamlit + LangChain + Chroma + OpenAI)
"""
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough


# ---------------------------------------------------------------
# 0. 環境変数の読み込み
# ---------------------------------------------------------------
load_dotenv()

DOCS_DIR = "docs"
PERSIST_DIR = "chroma_db"   # ChromaのデータをローカルにためるFolder


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

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

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
st.title("📚 シンプルRAG with LangChain")
st.caption("docs/ 内のMarkdownを根拠に回答します")

# APIキー未設定チェック
if not os.getenv("OPENAI_API_KEY"):
    st.error(".env に OPENAI_API_KEY を設定してください。")
    st.stop()

chain, retriever = build_rag_chain()

question = st.text_input("質問を入力", placeholder="例: コアタイムは何時から？")

if st.button("送信", type="primary") and question:
    with st.spinner("考え中..."):
        answer = chain.invoke(question)

    st.subheader("💬 回答")
    st.write(answer)

    with st.expander("🔎 参照したチャンクを見る"):
        related_docs = retriever.invoke(question)
        for i, d in enumerate(related_docs, 1):
            st.markdown(f"**[{i}] source: `{d.metadata.get('source', '?')}`**")
            st.code(d.page_content)
```

---

## 9. アプリを起動

```bash
streamlit run app.py
```

ブラウザで `http://localhost:8501` が自動で開く。
質問を入力 → 「送信」ボタン → 数秒で回答が出ればOK。

初回起動時は「ドキュメントをインデックス化中...」と表示され、`chroma_db/` ディレクトリが作られる。
2回目以降はキャッシュ＆永続化されているので即起動する。

---

## 10. 動作確認のチェックリスト

- [ ] `streamlit run app.py` でブラウザが開く
- [ ] 「コアタイムは？」と聞くと「10時から15時」と答える
- [ ] 「資料にないこと」を聞くと「資料には記載がありません」と返ってくる
- [ ] エクスパンダーを開くと検索された関連チャンクが表示される

---

## 11. よくあるトラブルと対処

| エラー | 原因と対処 |
|---|---|
| `OPENAI_API_KEY が見つからない` | `.env` の中身を確認。アプリを再起動 |
| `RateLimitError / 401` | APIキーが無効 or 課金未設定。Platform画面で確認 |
| `chromadb` 関連のビルドエラー | `pip install --upgrade pip setuptools wheel` してから再インストール |
| 何度動かしても同じ古い回答 | `chroma_db/` を削除して作り直す（インデックスをリフレッシュ） |
| Streamlitが起動しない | 仮想環境が有効か確認 (`source .venv/bin/activate`) |
| Apple SiliconでChromaが落ちる | Python 3.11 を使う / `pip install --upgrade chromadb` |

---

## 12. 次のステップ（発展課題）

- **PDF対応** … `pip install pypdf` して `PyPDFLoader` に差し替える
- **チャット履歴** … `st.session_state` でメッセージ履歴を保存し、`st.chat_message` でチャット風UIに
- **会話履歴を考慮した検索** … `create_history_aware_retriever` を使う
- **ドキュメントアップロード機能** … `st.file_uploader` で動的にRAG対象を追加
- **Embeddingモデル変更** … `text-embedding-3-large` に変えると精度向上（コスト増）
- **チャンクサイズの最適化** … `chunk_size=300〜1000` の範囲でAB検証
- **LangSmith導入** … チェーン内部の挙動を可視化・デバッグ

---

## 13. ディレクトリ最終構成

```
~/rag-streamlit-app/
├── .venv/                  # 仮想環境（Git管理しない）
├── .env                    # APIキー（Git管理しない）
├── .gitignore
├── requirements.txt
├── app.py                  # メインアプリ
├── docs/                   # RAG対象のMarkdown
│   └── sample.md
└── chroma_db/              # 自動生成されるベクトルDB
```

---

## 14. 用語ミニ辞典

- **Embedding** … テキストを意味を保ったまま数値ベクトルに変換すること
- **Chunk** … ドキュメントを小分けにした断片。長すぎるとLLMに入らないため必須
- **Retriever** … VectorStoreから関連チャンクを取り出すインターフェース
- **LCEL** … LangChainの宣言的パイプライン記法（`|` でつなぐ）
- **`@st.cache_resource`** … 重い処理（DB構築等）をセッション間で再利用するStreamlitのデコレータ
- **persist_directory** … Chromaがベクトルを保存するローカルディレクトリ

---

これで「自分のドキュメントに答えるシンプルなRAGアプリ」の完成です。
まずはサンプルで動作させ、徐々に自分の使いたい資料に置き換えていきましょう 🚀
