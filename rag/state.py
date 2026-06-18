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