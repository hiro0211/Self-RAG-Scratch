# ---------- ベースイメージ ----------
# Python 3.11 の slim 版を使う
# slim = Debian ベースで最小限 (alpine は chromadb のビルドで詰まるので避ける)
FROM python:3.11-slim

# ---------- OS レベルの依存 ----------
# build-essential: C拡張ビルド用 (chromadb / onnxruntime で必要)
# curl: ヘルスチェック用
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------- 作業ディレクトリ ----------
WORKDIR /app

# ---------- Python 依存のインストール ----------
# requirements.txt を先にコピーして pip install する (レイヤキャッシュ最適化)
# → コード変更しても pip install は再実行されない
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---------- アプリコードのコピー ----------
# これは頻繁に変わるので、pip install の後に置く
COPY . .

# ---------- ポート公開 ----------
EXPOSE 8501

# ---------- 起動コマンド ----------
# --server.address=0.0.0.0 でコンテナ外からアクセス可能に
# --server.headless=true でブラウザ自動起動を抑制
CMD ["streamlit", "run", "lib/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]