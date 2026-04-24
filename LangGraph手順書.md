# Self-RAG相当の RAGアプリを LangGraph で組み上げる 手順書

> **対象読者**: [RAG手順書.md](./RAG手順書.md) を完了し、シンプルRAGアプリを動かせる人
> **前提**: 既存の `app.py`（Streamlit × LangChain × Chroma）が動く状態
> **LLM**: Google Gemini (`gemini-2.5-flash-lite`)
> **追加するもの**: LangGraph による「検索判定 → 回答検証 → 再検索ループ」
> **完成イメージ**: 一問一答の直線的なRAGから、**状態遷移グラフ（ステートマシン）で動くRAG** に進化させる

---

## 0. なぜ LangGraph か？（5分で理解）

既存の `app.py` は、検索 → 生成の**一方通行**。
質問に対して的外れな回答が返っても、**再検索も軌道修正もしない**。

```
[質問] → [検索] → [生成] → [回答]
                              ↑
                 「検索が外した」に気付けない
```

Self-RAG（Self-Reflective RAG）の考え方は、このパイプラインに **自己評価のループ** を入れる：

```
[質問]
  ↓
[検索] → [検索結果の採点]
              ↓
      ┌───「関連なし」→ [質問の書き換え] → [検索] にループ
      │
      └───「関連あり」→ [生成] → [回答の採点]
                                      ↓
                             ┌──「根拠薄い」→ [生成] やり直し
                             │
                             └──「根拠あり」→ [最終回答]
```

**判定・分岐・再試行**を扱うには、LangChain の `|` パイプラインだけでは表現が難しい。
ここで登場するのが **LangGraph** — 状態（State）と処理（Node）と遷移（Edge）を**グラフとして宣言的に書ける**ライブラリ。

| 概念 | 意味 |
|---|---|
| **State** | 全ノードで共有される「作業メモ」。質問・検索結果・回答などを保持 |
| **Node** | 状態を受け取って更新する Python 関数（検索する・採点する等） |
| **Edge** | ノード間の遷移（矢印） |
| **Conditional Edge** | 状態を見て次ノードを選ぶ**分岐の矢印** |
| **START / END** | グラフの入口と出口 |

👉 LangGraph は **LangChain の上位互換ではなく補完**。
- LangChain：個別のチェーン（Embedding、Retriever、LLM）を提供
- LangGraph：それらを**ループと分岐付きのワークフロー**として束ねる

---

## 1. 事前準備

### 1-1. 前提チェック

- [ ] `RAG手順書.md` を完了している
- [ ] `~/rag-streamlit-app/` に既存の `app.py` がある（または本リポジトリの `lib/app.py`）
- [ ] `streamlit run app.py` が正常に動く
- [ ] `.venv` が有効化できる（`source .venv/bin/activate`）

### 1-2. 新しいファイルの置き場

既存の `app.py` を**壊さずに**進めるため、別ファイルに書く。

```bash
cd ~/rag-streamlit-app   # 既存プロジェクトに入る
source .venv/bin/activate
```

最終的には以下の構成になる：

```
~/rag-streamlit-app/
├── app.py              # 既存（Vanilla RAG、保存）
├── app_graph.py        # 新規（LangGraph版、本手順書で作成）
├── rag/                # 新規（LangGraph のノードを分割管理）
│   ├── __init__.py
│   ├── state.py        # 状態定義
│   ├── nodes.py        # ノード群
│   └── graph.py        # グラフ構築
├── docs/
├── chroma_db/
└── requirements.txt
```

> 💡 1ファイルに詰め込まず、**役割ごとにモジュール分割**する。
> 本番プロダクト相当の書き方を身につけるための意図的な構成。

---

## 2. 追加ライブラリのインストール

`requirements.txt` に LangGraph を追加：

```bash
cat >> requirements.txt << 'EOF'
langgraph==0.2.60
EOF
```

インストール：

```bash
pip install -r requirements.txt
```

動作確認：

```bash
python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

`LangGraph OK` と表示されればOK。

---

## 3. 設計する Self-RAG 相当グラフの全体像

これから作るグラフを、図で把握しておく。

```
             ┌─────────┐
             │  START  │
             └────┬────┘
                  ↓
           ┌──────────────┐
           │  retrieve    │ ← Chroma から関連チャンク取得
           └──────┬───────┘
                  ↓
          ┌──────────────────┐
          │ grade_documents  │ ← 取得チャンクの関連性を採点
          └────────┬─────────┘
                   ↓
             【分岐1】
      ┌────────────┴────────────┐
 「関連あり」                「関連なし」
      ↓                          ↓
┌──────────┐           ┌─────────────────┐
│ generate │           │ rewrite_query   │ ← 質問を書き換え
└────┬─────┘           └────────┬────────┘
     ↓                          │
┌──────────────┐                │
│ grade_answer │                │
└─────┬────────┘                │
      ↓                         │
   【分岐2】                     │
┌─────┴──────┐                  │
↓            ↓                  │
「OK」  「根拠が薄い」             │
 ↓            ↓                  │
END     (generate に戻る)         │
                                 │
         ┌───────────────────────┘
         ↓
    (retrieve に戻る)
```

**ノードは5つ**：
1. `retrieve`：Chroma から関連チャンクを取得
2. `grade_documents`：取得したチャンクの関連性を LLM で採点
3. `rewrite_query`：質問を書き換えて再検索
4. `generate`：コンテキストを使って回答生成
5. `grade_answer`：回答がコンテキストに根拠を持つか採点

**条件分岐（Conditional Edge）は2つ**：
- 分岐1：`grade_documents` の結果で `generate` か `rewrite_query` に分岐
- 分岐2：`grade_answer` の結果で `END` か `generate` に戻す

---

## 4. 状態（State）の定義 — `rag/state.py`

LangGraph のグラフは **1つの「State」** を各ノードが更新しながら進む。
まずはこの State の構造を定義する。

```bash
mkdir -p rag
touch rag/__init__.py
```

`rag/state.py` を作成：

```python
"""
LangGraph で扱う状態（State）の定義
"""
from typing import TypedDict, List
from langchain_core.documents import Document


class GraphState(TypedDict):
    """
    グラフ全体で共有される状態。
    各ノードはこのオブジェクトを受け取り、更新を返す。

    Attributes:
        question:          ユーザーの質問（書き換えで変わりうる）
        original_question: 最初の質問（ログ用に保持）
        documents:         検索で得られたチャンク
        generation:        LLM が生成した回答
        rewrite_count:     再検索した回数（無限ループ防止）
        answer_grade:      回答の採点結果 ("yes" / "no")
    """
    question: str
    original_question: str
    documents: List[Document]
    generation: str
    rewrite_count: int
    answer_grade: str
```

👉 **ポイント**
- `TypedDict` を使うのは LangGraph の慣例（型チェック & IDE 補完が効く）
- 状態には「現在のスナップショット」を入れる — 履歴は持たない
- `rewrite_count` で無限ループを防ぐ**ガード**を用意しておく

---

## 5. ノードの実装 — `rag/nodes.py`

`rag/nodes.py` を作成。長いので**役割ごとに section を切って貼る**。

### 5-1. 共通のセットアップ

```python
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
```

### 5-2. `retrieve` ノード — 検索する

```python
def retrieve(state: GraphState) -> dict:
    """Chroma から関連チャンクを取得する"""
    logger.info(f"[retrieve] question = {state['question']}")
    docs = _retriever.invoke(state["question"])
    logger.info(f"[retrieve] got {len(docs)} docs")
    return {"documents": docs}
```

### 5-3. `grade_documents` ノード — 検索結果を採点する

```python
_grade_docs_prompt = ChatPromptTemplate.from_template(
    """あなたは検索結果の関連性を判定する採点者です。
ユーザーの質問に対して、以下のドキュメントが回答に役立つかを判定してください。

判定基準:
- 回答に使える情報が含まれていれば "yes"
- 関係がない / 情報が不足していれば "no"

# 質問
{question}

# ドキュメント
{document}

# 判定（"yes" または "no" の1語のみ）
"""
)

def grade_documents(state: GraphState) -> dict:
    """
    取得したチャンクをそれぞれ LLM で採点し、
    "yes" のチャンクだけを残す。
    """
    logger.info(f"[grade_documents] grading {len(state['documents'])} docs")
    grader = _grade_docs_prompt | _llm | StrOutputParser()

    filtered = []
    for doc in state["documents"]:
        score = grader.invoke({
            "question": state["question"],
            "document": doc.page_content,
        }).strip().lower()
        if "yes" in score:
            filtered.append(doc)
        logger.info(f"  score={score[:20]} kept={len(filtered)}")

    return {"documents": filtered}
```

👉 **ポイント**
- チャンクごとに LLM を呼ぶ（コスト増の代わりに精度向上）
- 採点結果を `filtered` に絞ることで、後段 `generate` に渡すコンテキストが**ノイズ少なめ**になる

### 5-4. `rewrite_query` ノード — 質問を書き換える

```python
_rewrite_prompt = ChatPromptTemplate.from_template(
    """あなたは質問を書き換えて検索精度を上げるアシスタントです。
元の質問を、検索に適した**具体的で検索キーワードが豊富**な形に書き換えてください。

# 元の質問
{question}

# 書き換えた質問（1文のみ、説明不要）
"""
)

def rewrite_query(state: GraphState) -> dict:
    """検索で引っかからなかった質問を書き換える"""
    logger.info(f"[rewrite_query] before = {state['question']}")
    rewriter = _rewrite_prompt | _llm | StrOutputParser()
    new_q = rewriter.invoke({"question": state["question"]}).strip()
    logger.info(f"[rewrite_query] after  = {new_q}")
    return {
        "question": new_q,
        "rewrite_count": state.get("rewrite_count", 0) + 1,
    }
```

### 5-5. `generate` ノード — 回答を生成する

```python
_generate_prompt = ChatPromptTemplate.from_template(
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

def generate(state: GraphState) -> dict:
    """コンテキストを使って回答を生成する"""
    logger.info(f"[generate] context docs = {len(state['documents'])}")
    chain = _generate_prompt | _llm | StrOutputParser()
    context = "\n\n".join(d.page_content for d in state["documents"])
    answer = chain.invoke({
        "context": context,
        "question": state["question"],
    })
    logger.info(f"[generate] answer head = {answer[:50]}...")
    return {"generation": answer}
```

### 5-6. `grade_answer` ノード — 回答を採点する

```python
_grade_answer_prompt = ChatPromptTemplate.from_template(
    """あなたは回答がコンテキストに根拠を持つかを判定する採点者です。

判定基準:
- 回答の内容がコンテキストから読み取れる → "yes"
- コンテキストにない情報が含まれている（ハルシネーション） → "no"

# コンテキスト
{context}

# 回答
{generation}

# 判定（"yes" または "no" の1語のみ）
"""
)

def grade_answer(state: GraphState) -> dict:
    """生成された回答が、コンテキストに根拠を持つかを採点"""
    logger.info(f"[grade_answer] grading generation")
    grader = _grade_answer_prompt | _llm | StrOutputParser()
    context = "\n\n".join(d.page_content for d in state["documents"])
    score = grader.invoke({
        "context": context,
        "generation": state["generation"],
    }).strip().lower()
    grade = "yes" if "yes" in score else "no"
    logger.info(f"[grade_answer] grade = {grade}")
    return {"answer_grade": grade}
```

---

## 6. 条件分岐関数の定義

ノード実装の続きに、**分岐を決める関数** を足す。
これは「ノード」ではなく、**Conditional Edge が呼ぶ判定関数**。

`rag/nodes.py` の末尾に追加：

```python
# ---------------------------------------------------------------
# 条件分岐の判定関数
# ---------------------------------------------------------------

MAX_REWRITE = 2  # 無限ループ防止: 書き換えは最大2回まで

def decide_after_grade_documents(state: GraphState) -> str:
    """
    grade_documents の後の分岐:
    - 関連ドキュメントが残っている → "generate"
    - 空 かつ まだ書き換え余地あり → "rewrite_query"
    - 空 かつ リトライ上限 → "generate"（諦めて生成させる）
    """
    count = state.get("rewrite_count", 0)
    if len(state["documents"]) == 0 and count < MAX_REWRITE:
        logger.info(f"[decide] no relevant docs → rewrite_query (count={count})")
        return "rewrite_query"
    if len(state["documents"]) == 0:
        logger.info(f"[decide] no relevant docs, but MAX_REWRITE reached → generate")
    else:
        logger.info(f"[decide] {len(state['documents'])} docs relevant → generate")
    return "generate"


def decide_after_grade_answer(state: GraphState) -> str:
    """
    grade_answer の後の分岐:
    - 根拠あり ("yes") → END
    - 根拠なし ("no") かつ 再生成上限に達していない → "generate"
    - 根拠なし ("no") かつ 再生成上限 → END（諦めてその回答を返す）
    """
    grade = state.get("answer_grade", "no")
    if grade == "yes":
        logger.info("[decide] answer grounded → END")
        return "end"
    # シンプルに1回だけ再生成
    if state.get("rewrite_count", 0) >= MAX_REWRITE:
        logger.info("[decide] answer not grounded but retries exhausted → END")
        return "end"
    logger.info("[decide] answer not grounded → regenerate")
    return "generate"
```

> 💡 **分岐関数は `str` を返す。** その文字列が Conditional Edge の「行き先マップ」のキーになる。

---

## 7. グラフの構築 — `rag/graph.py`

ノードと分岐関数ができたので、それらを**グラフに組み立てる**。

`rag/graph.py` を作成：

```python
"""
LangGraph のグラフ定義
"""
import logging
from langgraph.graph import StateGraph, START, END

from rag.state import GraphState
from rag.nodes import (
    retrieve,
    grade_documents,
    rewrite_query,
    generate,
    grade_answer,
    decide_after_grade_documents,
    decide_after_grade_answer,
)

logger = logging.getLogger(__name__)


def build_graph():
    """Self-RAG 相当のグラフを構築してコンパイルする"""
    workflow = StateGraph(GraphState)

    # --- ノード登録 ---
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("grade_documents", grade_documents)
    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("generate", generate)
    workflow.add_node("grade_answer", grade_answer)

    # --- エッジ（普通の矢印）---
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "grade_documents")
    workflow.add_edge("rewrite_query", "retrieve")     # 書き換え後は再検索
    workflow.add_edge("generate", "grade_answer")

    # --- 条件分岐 ---
    workflow.add_conditional_edges(
        "grade_documents",
        decide_after_grade_documents,
        {
            "generate": "generate",
            "rewrite_query": "rewrite_query",
        },
    )
    workflow.add_conditional_edges(
        "grade_answer",
        decide_after_grade_answer,
        {
            "generate": "generate",   # 回答やり直し
            "end": END,
        },
    )

    graph = workflow.compile()
    logger.info("Graph compiled successfully")
    return graph
```

👉 **ポイント**
- `add_edge(A, B)` は無条件の矢印
- `add_conditional_edges(A, fn, map)` は `fn(state)` の戻り値を `map` のキーで見て行き先を決める
- `START` と `END` は `langgraph.graph` からインポートする特別な定数

---

## 8. Streamlit UI — `app_graph.py`

既存の `app.py` と並列に、LangGraph 版の UI を作る。

`app_graph.py` をプロジェクトルートに作成：

```python
"""
Self-RAG 相当のグラフを Streamlit から叩くアプリ
"""
import os
import logging

import streamlit as st
from dotenv import load_dotenv

from rag.graph import build_graph

# ログ設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()


# ---------------------------------------------------------------
# グラフの構築（初回1回だけ）
# ---------------------------------------------------------------
@st.cache_resource(show_spinner="Self-RAG グラフを構築中...")
def get_graph():
    return build_graph()


# ---------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------
st.set_page_config(page_title="Self-RAG", page_icon="🔁")
st.title("🔁 Self-RAG with LangGraph")
st.caption("検索判定・回答採点・再検索ループ付き RAG")

if not os.getenv("GOOGLE_API_KEY"):
    st.error(".env に GOOGLE_API_KEY を設定してください。")
    st.stop()

graph = get_graph()

question = st.text_input("質問を入力", placeholder="例: コアタイムは何時から？")

if st.button("送信", type="primary") and question:
    logger.info(f"=== ユーザー質問: {question} ===")

    # 初期状態を作って invoke
    initial_state = {
        "question": question,
        "original_question": question,
        "documents": [],
        "generation": "",
        "rewrite_count": 0,
        "answer_grade": "",
    }

    with st.spinner("考え中（検索 → 採点 → 生成 → 採点...）"):
        # ステップごとの進捗を表示したいので stream を使う
        status_area = st.empty()
        final_state = None
        for step in graph.stream(initial_state):
            # step は {"node_name": partial_state} の辞書
            node_name = list(step.keys())[0]
            status_area.info(f"🔧 実行中: `{node_name}`")
            final_state = step[node_name]
            logger.info(f"node={node_name} state_keys={list(final_state.keys())}")

    # 最後のノード出力から最終的な回答と参照を組み立てる
    # ※ stream の最終 state は部分更新なので、invoke で完全な state を取り直すのが確実
    result = graph.invoke(initial_state)

    st.subheader("💬 回答")
    st.write(result["generation"])

    with st.expander("🔎 最終的に参照したチャンク"):
        for i, d in enumerate(result["documents"], 1):
            st.markdown(f"**[{i}] source: `{d.metadata.get('source', '?')}`**")
            st.code(d.page_content)

    with st.expander("🧪 デバッグ: 実行トレース"):
        st.json({
            "original_question": result["original_question"],
            "final_question": result["question"],
            "rewrite_count": result.get("rewrite_count", 0),
            "answer_grade": result.get("answer_grade", ""),
            "num_documents": len(result["documents"]),
        })
```

---

## 9. アプリを起動

```bash
streamlit run app_graph.py
```

ブラウザで `http://localhost:8501` が自動で開く。
初回は `chroma_db/` が既に存在する前提（`app.py` で作成済み）。

---

## 10. 動作確認のチェックリスト

### 10-1. 既存の質問が従来どおり動くこと

- [ ] 「コアタイムは？」と聞くと「10時から15時」と答える
- [ ] 「🔧 実行中」の表示が `retrieve` → `grade_documents` → `generate` → `grade_answer` と流れる
- [ ] 「🧪 実行トレース」を開くと `rewrite_count: 0` `answer_grade: yes` になる

### 10-2. 再検索ループが発動する質問

docs に書いてないことを聞いて、**書き換えが走る**ことを確認：

- [ ] 「役員の自家用車は何？」→ `rewrite_query` が1〜2回発動 → 最終的に「資料には記載がありません」
- [ ] トレースの `rewrite_count` が 1 以上になっている

### 10-3. ログで流れが追える

ターミナルに以下のようなログが出ていること：

```
[retrieve] question = コアタイムは？
[retrieve] got 3 docs
[grade_documents] grading 3 docs
[decide] 2 docs relevant → generate
[generate] context docs = 2
[grade_answer] grade = yes
[decide] answer grounded → END
```

---

## 11. よくあるトラブルと対処

| エラー | 原因と対処 |
|---|---|
| `ModuleNotFoundError: No module named 'langgraph'` | `pip install -r requirements.txt` をやり直す。`.venv` が有効か確認 |
| `KeyError: 'question'` | 初期状態に必須キーを入れ忘れ。`app_graph.py` の `initial_state` を確認 |
| 無限ループして止まらない | `MAX_REWRITE` のガードが効いていない。`decide_after_grade_documents` の条件を見直し |
| `grade_documents` が常に "no" を返す | プロンプトが厳しすぎる。評価基準を緩める or `temperature=0` 確認 |
| `chroma_db` が空でエラー | 先に `streamlit run app.py` でインデックスを作る |
| ノードの出力が反映されない | ノードは `dict` を返すこと（`return state` ではなく `return {"question": new_q}` のように**更新分だけ**返す） |
| Gemini が JSON で返してきて採点が失敗 | プロンプトを「1語のみ」と強調。`.strip().lower()` で後処理 |

---

## 12. 次のステップ（発展課題）

- **グラフの可視化** … `graph.get_graph().draw_mermaid()` で Mermaid 形式を出力 → README に貼る
- **ループ回数の状態化** … `MAX_REWRITE` を State に持たせて設定可能にする
- **Web 検索フォールバック** … 書き換え後も検索失敗なら Tavily / DuckDuckGo で Web 検索
- **Agentic RAG 化** … `retrieve` を Tool として LLM に呼ばせる（LangGraph の `ToolNode` / `tools_condition`）
- **Pydantic での採点出力** … `with_structured_output()` で LLM 出力を型固定し、`.strip().lower()` の泥臭いパースをなくす
- **Langfuse 連携** … 各ノードのレイテンシとトークン消費を可視化
- **Human-in-the-loop** … 書き換え前にユーザーに確認を取るノード追加（LangGraph の checkpointer + interrupt）
- **並列実行** … チャンクごとの採点を並列化してレイテンシ削減

---

## 13. ディレクトリ最終構成

```
~/rag-streamlit-app/
├── .venv/
├── .env
├── .gitignore
├── requirements.txt         # langgraph==0.2.60 が追加済み
├── app.py                   # 既存（Vanilla RAG）
├── app_graph.py             # ★新規（LangGraph 版）
├── rag/                     # ★新規
│   ├── __init__.py
│   ├── state.py             # GraphState 定義
│   ├── nodes.py             # ノード群 + 分岐関数
│   └── graph.py             # グラフ構築
├── docs/
│   └── RagDB.md
└── chroma_db/               # 既存（app.py で作成済み）
```

---

## 14. 用語ミニ辞典

- **State（状態）** … グラフ全体で共有される作業メモ。各ノードが読み書きする
- **Node（ノード）** … 状態を受け取り、更新分の辞書を返す Python 関数
- **Edge（エッジ）** … ノード間の無条件な遷移（矢印）
- **Conditional Edge（条件分岐）** … 判定関数の戻り値で行き先を選ぶ矢印
- **START / END** … グラフの入口と出口を表す LangGraph の特殊な定数
- **TypedDict** … Python の型付き辞書。LangGraph で State の構造を定義する標準
- **Self-RAG** … 「検索 → 採点 → 書き換え / 生成 → 採点 → やり直し」のループで精度を高める RAG 手法
- **ハルシネーション** … LLM がコンテキストにない情報を作り出してしまう現象
- **LCEL** … LangChain Expression Language（`|` で繋ぐパイプライン記法）
- **`stream()` vs `invoke()`** … `stream` はノード単位で逐次 state を返す（進捗表示に便利）、`invoke` は最終 state を1回で返す

---

## 15. 資料のアーキとの対応

参考までに、本手順書で作ったものが**業務用 RAG システム**のどの部分に対応するかを整理しておく：

| 本手順書 | 業務用 RAG システム |
|---|---|
| `rag/state.py` の `GraphState` | LangGraph で扱う StateSchema |
| `retrieve` ノード | Azure AI Search へのクエリ |
| `grade_documents` ノード | クエリ種別判定 |
| `rewrite_query` ノード | 再検索ワークフローの一部 |
| `generate` ノード | Azure OpenAI (gpt-4o-mini) 呼び出し |
| `grade_answer` ノード | 再生成ループの判定 |
| グラフ全体 | LangChain + LangGraph での RAG ワークフロー |

👉 ここまで作れれば、**「単純な検索 → 生成ではなく、判定・分岐・再試行を含む LLM フロー」** を自分で書けるレベルに到達している。

---

## 16. まとめ

この手順書で身についたこと：

- LangGraph の基本概念（State / Node / Edge / Conditional Edge）
- Self-RAG の設計思想（自己採点 → 書き換え → 再試行）
- 一方通行のチェーンから、**ループと分岐のあるワークフロー**への進化
- モジュール分割（`state.py` / `nodes.py` / `graph.py`）による保守性

次は **Docker化手順書** に進み、この LangGraph 版アプリをコンテナに載せていく 🐳
