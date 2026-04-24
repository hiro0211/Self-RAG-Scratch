# Mac で CLI のみで RAGアプリをコンテナ化する 手順書

> **対象読者**: [RAG手順書.md](./RAG手順書.md) と [LangGraph手順書.md](./LangGraph手順書.md) を完了した人
> **想定OS**: macOS (Apple Silicon 前提、M4 で検証)
> **コンテナランタイム**: **colima** + **Docker CLI**（GUI 一切使わない）
> **題材**: 既存の `app_graph.py`（Streamlit × LangGraph × Chroma）をコンテナ化
> **完成イメージ**: `docker compose up` 一発で RAG アプリが起動し、Nginx 経由で `http://localhost:8080` からアクセスできる。**すべての操作がターミナルで完結**する

---

## 0. なぜ CLI だけで Docker を扱うのか？（5分で理解）

Docker には代表的な2つの導入方法がある：

| 方法 | GUI | 業務ライセンス | 学習価値 |
|---|---|---|---|
| Docker Desktop | あり（🐳アイコン、ダッシュボード） | 従業員250人 or 売上1000万ドル超で有償 | ★☆☆ GUI 頼み |
| **colima + docker CLI** | **なし** | **無料（MIT）** | ★★★ 本番運用と同じ |

本手順書は **colima + CLI** で統一する。理由：

- 業務用 AI アプリ基盤は Linux サーバー上の CLI で動く。GUI 前提の学習は本番で役に立たない
- 自動化・CI/CD・SSH 越しのサーバー操作など、**CLI スキルは全方位で応用が効く**
- Docker Desktop のライセンス問題を回避できる
- VM のリソース（CPU/RAM/ディスク）を明示的に制御できる

👉 用語を先に整理：

| 用語 | 意味 |
|---|---|
| **Image（イメージ）** | アプリ + 依存 + OS を固めた読み取り専用のテンプレート |
| **Container（コンテナ）** | Image を元に動いているプロセス |
| **Dockerfile** | Image を作るレシピ（テキストファイル） |
| **Volume** | コンテナの外に保存されるデータ置き場 |
| **docker compose** | 複数コンテナを yml でまとめて管理するツール |
| **colima** | macOS 上で Linux VM を立ち上げて Docker を動かす軽量ランタイム |
| **Lima** | colima の土台になっている macOS 向け Linux VM 基盤 |

---

## 1. 事前準備

### 1-1. 必要なもの

- macOS（Apple Silicon 推奨、Intel も可）
- ターミナル（標準の Terminal.app、iTerm2、Warp 等なんでも）
- Homebrew
- 既存の `~/rag-streamlit-app/` プロジェクト（`app_graph.py` が動く状態）
- 最低 8GB の空きディスク容量

### 1-2. Homebrew の確認

```bash
brew --version
```

バージョンが出ればOK。未導入なら：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 1-3. Docker Desktop が入っていないことを確認

もし過去に Docker Desktop を入れていた場合は、**完全に停止**しておく（プロセスが競合する）：

```bash
# Docker Desktop が動いていないか確認
pgrep -l Docker

# 動いていたら停止
osascript -e 'quit app "Docker"'

# 完全にアンインストールする場合（任意）
brew uninstall --cask docker
```

---

## 2. colima と Docker CLI のインストール

### 2-1. Homebrew で一括インストール

```bash
brew install colima docker docker-compose
```

- `colima` … Linux VM ランタイム本体
- `docker` … Docker CLI（`docker` コマンド）
- `docker-compose` … Compose プラグイン（`docker compose` コマンド）

### 2-2. インストール確認

```bash
colima version
docker --version
docker compose version
```

全部バージョン表示が出ればOK。

### 2-3. docker-compose プラグインのパス設定（必要な場合のみ）

`docker compose version` でエラーが出た場合、Homebrew のプラグインパスを Docker に教える：

```bash
mkdir -p ~/.docker/cli-plugins
ln -sfn $(brew --prefix)/opt/docker-compose/bin/docker-compose ~/.docker/cli-plugins/docker-compose
```

再度 `docker compose version` で確認。

---

## 3. colima の起動（Apple Silicon 最適設定）

Apple Silicon 向けの最適な VM 起動オプションがある。
`--vm-type=vz`（macOS 13+ の仮想化フレームワーク）+ `--vz-rosetta`（x86_64 イメージの高速エミュレーション）+ `--mount-type=virtiofs`（高速ファイル共有）の3点セットが鉄板。

### 3-1. 初回起動

```bash
colima start \
  --cpu 4 \
  --memory 8 \
  --disk 60 \
  --vm-type vz \
  --vz-rosetta \
  --mount-type virtiofs
```

オプションの意味：

| オプション | 意味 |
|---|---|
| `--cpu 4` | VM に 4 コア割当（M4 なら余裕、ホストの半分目安） |
| `--memory 8` | VM に 8GB 割当（chromadb + Gemini 呼び出しには十分） |
| `--disk 60` | VM に 60GB のディスク割当（Image がたまるので多めに） |
| `--vm-type vz` | macOS 13+ の Virtualization.framework を使う（QEMU より高速） |
| `--vz-rosetta` | x86_64 向け Image を Rosetta 2 で高速エミュレーション |
| `--mount-type virtiofs` | ホスト ⇄ コンテナのファイル共有を高速化 |

初回は VM イメージのダウンロードで数分かかる。

### 3-2. 状態確認

```bash
colima status
```

以下のように出ればOK：

```
INFO[0000] colima is running using macOS Virtualization.Framework
INFO[0000] arch: aarch64
INFO[0000] runtime: docker
INFO[0000] mountType: virtiofs
INFO[0000] socket: unix:///Users/<user>/.colima/default/docker.sock
```

### 3-3. Docker CLI 接続確認

```bash
docker version
docker info
docker run --rm hello-world
```

`Hello from Docker!` が出れば成功。colima + Docker CLI が繋がっている。

### 3-4. docker-credential-osxkeychain エラーが出た場合の対処

CLI のみの環境では macOS のキーチェーン連携が動かない。`docker login` 等でエラーが出たら：

```bash
# ~/.docker/config.json を編集
cat > ~/.docker/config.json << 'EOF'
{
  "credsStore": ""
}
EOF
```

学習用途ではこれで問題なし。

---

## 4. colima のライフサイクルコマンド

今後よく使うので覚えておく：

```bash
colima start          # 起動（2回目以降はオプション不要、前回の設定が使われる）
colima stop           # 停止（Mac をシャットダウンする前に推奨）
colima restart        # 再起動
colima status         # 状態確認
colima delete         # VM を完全削除（設定もデータも消える）
colima list           # プロファイル一覧
```

> 💡 Mac を再起動したら colima も止まる。
> 毎回 `colima start` が必要。自動起動したい場合は `brew services start colima` でサービス化もできる。

---

## 5. 既存プロジェクトに Docker 関連ファイルを追加する

`~/rag-streamlit-app/` に移動：

```bash
cd ~/rag-streamlit-app
```

これから追加するファイル：

```
~/rag-streamlit-app/
├── Dockerfile                   # ★新規: Streamlit コンテナのレシピ
├── .dockerignore                # ★新規: Image に含めたくないファイル一覧
├── docker-compose.yml           # ★新規: 複数コンテナのまとめ役
├── docker/
│   └── nginx/
│       └── nginx.conf           # ★新規: Nginx 設定
├── .env                         # 既存
├── requirements.txt             # 既存
├── app.py                       # 既存
├── app_graph.py                 # 既存
├── rag/                         # 既存
└── docs/                        # 既存
```

---

## 6. `.dockerignore` の作成

まず**コンテナに入れたくないファイル**を宣言しておく。これを後回しにすると、`.venv/` を丸ごと Image に入れてしまって数GB になる事故が起きる。

```bash
cat > .dockerignore << 'EOF'
.venv/
venv/
__pycache__/
*.pyc
*.pyo
.git/
.gitignore
.DS_Store
.env
chroma_db/
*.md
!requirements.txt
.pytest_cache/
.mypy_cache/
node_modules/
EOF
```

各行の意味：

| 行 | 意味 |
|---|---|
| `.venv/` `venv/` | ホスト側の仮想環境を除外（コンテナ内で独自に作る） |
| `__pycache__/` | Python のキャッシュ |
| `.git/` | リポジトリ情報（コンテナに不要） |
| `.env` | 秘密情報。Image に焼き付けず、実行時に注入する |
| `chroma_db/` | ホスト側のDBデータ。コンテナは Volume から読む |
| `*.md` `!requirements.txt` | Markdown は除外。ただし `requirements.txt` は必須なので除外しない |

内容確認：

```bash
cat .dockerignore
```

---

## 7. `Dockerfile` の作成

プロジェクトルートに `Dockerfile` を作成：

```bash
cat > Dockerfile << 'EOF'
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
CMD ["streamlit", "run", "app_graph.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
EOF
```

👉 **各セクションの狙い**

| セクション | 狙い |
|---|---|
| `FROM python:3.11-slim` | 軽量な Python 公式イメージ。Apple Silicon では自動で arm64 版が選ばれる |
| `RUN apt-get install` | `chromadb` のビルドには C コンパイラが必要なので事前に入れる |
| `COPY requirements.txt` を先に | **Docker のレイヤキャッシュ最適化**。コード変更のたびに pip install が走らない |
| `EXPOSE 8501` | Streamlit のデフォルトポート |
| `--server.address=0.0.0.0` | デフォルトの `localhost` だとコンテナ外から繋げない |

内容確認：

```bash
cat Dockerfile
```

---

## 8. 単体ビルドで動作確認

docker-compose に進む前に、**まず Dockerfile 単体でビルドが通るか**を確認する。

### 8-1. ビルド

```bash
docker build -t rag-streamlit:local .
```

- `-t rag-streamlit:local`：Image にタグ付け（`名前:バージョン`）
- `.`：カレントディレクトリを Dockerfile のコンテキストに使う

初回は 3〜5 分かかる（chromadb のビルドで時間を食う）。
2回目以降はキャッシュが効くので数秒。

ビルドが終わったら確認：

```bash
docker images | grep rag-streamlit
```

以下のように表示されるはず：

```
rag-streamlit    local    <IMAGE_ID>    <CREATED>    <SIZE>
```

### 8-2. 起動

```bash
docker run --rm -it \
  -p 8501:8501 \
  --env-file .env \
  -v $(pwd)/chroma_db:/app/chroma_db \
  -v $(pwd)/docs:/app/docs \
  rag-streamlit:local
```

オプションの意味：

| オプション | 意味 |
|---|---|
| `--rm` | コンテナ停止時に自動削除（ゴミが残らない） |
| `-it` | 対話モード（Ctrl+C で止められる） |
| `-p 8501:8501` | ホストの 8501 → コンテナの 8501 にフォワード |
| `--env-file .env` | `.env` を環境変数として注入 |
| `-v $(pwd)/chroma_db:/app/chroma_db` | ホストの chroma_db をコンテナにマウント（bind mount） |
| `-v $(pwd)/docs:/app/docs` | docs も同様にマウント |

ターミナルに以下のようなログが出れば成功：

```
You can now view your Streamlit app in your browser.
URL: http://0.0.0.0:8501
```

ブラウザで `http://localhost:8501` にアクセスして動けばOK。
Ctrl+C で停止。

### 8-3. CLI から別ターミナルで動作確認

起動中に**別のターミナル**から確認：

```bash
# コンテナ一覧
docker ps

# コンテナの中に入って様子を見る
docker exec -it $(docker ps -q --filter ancestor=rag-streamlit:local) bash

# コンテナ内で色々試す
ls -la /app
python -c "import langgraph; print(langgraph.__version__)"
exit
```

> 💡 **この段階で詰まったら先に進まない。** Dockerfile の問題をここで洗い出すのが鉄則。

---

## 9. Nginx 設定ファイルの作成

本番を意識して、Nginx をリバースプロキシとして前段に置く。

```bash
mkdir -p docker/nginx
cat > docker/nginx/nginx.conf << 'EOF'
# Nginx 設定 (リバースプロキシ)
# http://localhost:8080/ に来たリクエストを streamlit コンテナ (8501) に流す

events {}

http {
    # Streamlit のアップストリーム定義
    upstream streamlit_app {
        server rag-app:8501;   # docker-compose の service 名で解決
    }

    server {
        listen 80;
        server_name localhost;

        # リバースプロキシ設定
        location / {
            proxy_pass http://streamlit_app;

            # --- WebSocket サポート (Streamlit に必須) ---
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";

            # --- 通常のプロキシヘッダ ---
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # --- タイムアウト (LLM呼び出しは長いので長めに) ---
            proxy_read_timeout 600s;
            proxy_send_timeout 600s;
        }
    }
}
EOF
```

👉 **ポイント**
- `upstream streamlit_app` の `server rag-app:8501` → `rag-app` は docker-compose の **サービス名で名前解決**される
- **WebSocket ヘッダ**（Upgrade / Connection）は Streamlit には必須。省くと画面が動かない
- `proxy_read_timeout 600s` は LLM の長い応答に耐えるため

---

## 10. `docker-compose.yml` の作成

複数コンテナ（Streamlit + Nginx）を束ねる設定ファイル。

```bash
cat > docker-compose.yml << 'EOF'
services:
  # ---------- Streamlit アプリ ----------
  rag-app:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: rag-app
    env_file:
      - .env
    volumes:
      # chroma_db は named volume で永続化 (bind mount より高速)
      - chroma_data:/app/chroma_db
      # docs はホストと同期 (編集したらすぐ反映させたい)
      - ./docs:/app/docs
    expose:
      - "8501"
    # 直接ホストにポート公開しない (nginx 経由のみアクセス可能)
    restart: unless-stopped

  # ---------- Nginx リバースプロキシ ----------
  nginx:
    image: nginx:1.27-alpine
    container_name: rag-nginx
    ports:
      - "8080:80"   # ホストの 8080 → コンテナの 80
    volumes:
      - ./docker/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - rag-app
    restart: unless-stopped

# ---------- Volume 定義 ----------
volumes:
  chroma_data:
    # Docker 管理の named volume。macOS でも I/O が速い
EOF
```

👉 **設計の要点**

- **`rag-app` は `ports` ではなく `expose`**：ホストから直アクセスできないようにし、必ず Nginx 経由にする（本番運用と同じ）
- **`chroma_data` は named volume**：macOS では bind mount より高速
- **`./docs:/app/docs` は bind mount**：ドキュメントを追加したらすぐコンテナに反映したいため
- **`depends_on`**：Nginx は rag-app の後に起動

設定のバリデーション：

```bash
docker compose config
```

エラーなく yml の内容が出力されればOK。

---

## 11. 起動

### 11-1. フォアグラウンド起動（初回確認用）

```bash
docker compose up --build
```

- `--build`：Dockerfile から改めてビルドしてから起動
- 2回目以降は `docker compose up` だけでOK

ログが大量に流れ、以下のようなメッセージが出ればOK：

```
rag-app    | You can now view your Streamlit app in your browser.
rag-app    | URL: http://0.0.0.0:8501
rag-nginx  | /docker-entrypoint.sh: Configuration complete; ready for start up
```

ブラウザで：

- `http://localhost:8080` ← **Nginx 経由**（本来のアクセス経路）
- `http://localhost:8501` ← **アクセス不可**（意図通り）

Ctrl+C で停止（フォアグラウンド起動のみ）。

### 11-2. バックグラウンド起動（日常利用）

```bash
docker compose up -d
```

- `-d`：detached モード（バックグラウンドで起動）

起動後の状態確認：

```bash
docker compose ps
```

以下のように出ればOK：

```
NAME         IMAGE              STATUS         PORTS
rag-app      rag-app            Up 10 seconds  8501/tcp
rag-nginx    nginx:1.27-alpine  Up 9 seconds   0.0.0.0:8080->80/tcp
```

ログを見るには：

```bash
docker compose logs -f          # 全コンテナのログを追跡
docker compose logs -f rag-app  # 特定コンテナのみ
```

停止：

```bash
docker compose down
```

---

## 12. 動作確認のチェックリスト

### 12-1. 基本チェック（CLI で完結）

```bash
# colima が動いているか
colima status

# コンテナが起動しているか
docker compose ps

# ポートが開いているか
curl -I http://localhost:8080
# → HTTP/1.1 200 OK が返ればOK

# 8501 が外から繋がらないことの確認 (セキュリティ)
curl -I http://localhost:8501
# → curl: (7) Failed to connect ... が返ればOK（正しい挙動）
```

### 12-2. アプリ動作チェック

- [ ] `http://localhost:8080` でアプリが開く
- [ ] 「コアタイムは？」と聞くと「10時から15時」と答える
- [ ] LangGraph のステップ表示（`retrieve` → `grade_documents` ...）が出る
- [ ] `docker compose logs -f rag-app` でアプリログが見える

---

## 13. よくあるトラブルと対処（colima / CLI 固有）

| エラー | 原因と対処 |
|---|---|
| `Cannot connect to the Docker daemon` | colima が起動していない。`colima status` で確認 → `colima start` |
| `error during connect: ... dial unix /var/run/docker.sock` | Docker Desktop 用の socket を見に行っている。`unset DOCKER_HOST` してリトライ |
| `docker-credential-osxkeychain not found` | 第3-4節の `~/.docker/config.json` 対処を実施 |
| colima start が `vz` エラーで失敗 | macOS が 13 未満。`--vm-type qemu` に変更（速度は落ちる） |
| `no matching manifest for linux/arm64/v8` | arm64 未対応の Image を使っている。`--platform linux/amd64` を明示 or 別Image選定 |
| ビルドが `chromadb` のところで失敗 | `build-essential` が入っていない。Dockerfile の `apt-get install` を確認 |
| `Permission denied` で `chroma_db` に書けない | named volume を使う（本手順書の構成）。bind mount だと権限問題が起きやすい |
| `http://localhost:8080` が真っ白 | WebSocket 設定が欠けている。nginx.conf の `Upgrade` / `Connection` ヘッダ確認 |
| アプリに変更が反映されない | Image にコードが焼かれているので `docker compose up -d --build` で再ビルド |
| ビルドが毎回遅い | `.dockerignore` で不要ファイルを除外。`requirements.txt` を先にコピーする順序を守る |
| ポート 8080 が既に使われている | `lsof -i :8080` で使用プロセス確認。`docker-compose.yml` の `ports` を `"8090:80"` 等に変更 |
| `.env` が読まれない | `.dockerignore` に `.env` を書いていれば Image には入らない。`env_file` で実行時注入されていればOK |
| `chroma_db` のデータが消えた | `docker volume rm` した可能性。`docker volume ls` で確認 |
| colima の VM がディスクフル | `docker system prune -a` で不要 Image 削除。それでも足りなければ `colima delete` → `--disk` 増やして再作成 |
| Mac 再起動したら動かない | colima は自動起動しない。`colima start` を打つ or `brew services start colima` で自動化 |

---

## 14. よく使う CLI コマンド集

### 14-1. colima 管理

```bash
colima start                    # 起動 (2回目以降、設定は前回のまま)
colima stop                     # 停止
colima restart                  # 再起動
colima status                   # 状態確認
colima delete                   # VM を完全削除
colima list                     # プロファイル一覧

# リソース変更したい場合は一度削除して再作成
colima delete
colima start --cpu 6 --memory 12 --disk 100 --vm-type vz --vz-rosetta --mount-type virtiofs

# ログイン時に自動起動したい場合
brew services start colima
brew services stop colima
```

### 14-2. Docker / Compose

```bash
# --- 起動・停止 ---
docker compose up                  # 起動 (フォアグラウンド)
docker compose up -d               # 起動 (バックグラウンド)
docker compose up -d --build       # 再ビルドしてから起動
docker compose down                # 停止 + コンテナ削除
docker compose down -v             # 停止 + Volume も削除 (chroma_db リセット)
docker compose restart rag-app     # 特定サービスだけ再起動

# --- 状態確認 ---
docker compose ps                  # 起動中のコンテナ
docker compose logs                # 全コンテナのログ
docker compose logs -f rag-app     # 特定コンテナのログを追跡
docker compose top                 # コンテナ内のプロセス一覧

# --- コンテナ内に入る ---
docker compose exec rag-app bash       # bash を起動
docker compose exec rag-app python -c "import langgraph; print('ok')"

# --- Image / Volume / Network 管理 ---
docker images                      # ローカルの Image 一覧
docker volume ls                   # Volume 一覧
docker network ls                  # ネットワーク一覧
docker system df                   # ディスク使用量
docker system prune                # 不要なものを一括削除
docker system prune -a --volumes   # Volume 含めて全消し (要注意)

# --- デバッグ ---
docker inspect rag-app             # コンテナ詳細
docker logs rag-app --tail 50      # 最新50行だけ
docker stats                       # リアルタイムのCPU/メモリ使用量
```

### 14-3. ワンライナー便利技

```bash
# 全コンテナ停止
docker stop $(docker ps -q)

# 停止コンテナ全削除
docker container prune -f

# 使われていない Image 全削除
docker image prune -a -f

# Volume 使用量を見る
docker system df -v | grep -A 20 VOLUMES

# rag-app のログを grep
docker compose logs rag-app | grep ERROR
```

---

## 15. 次のステップ（発展課題）

- **マルチステージビルド** … builder ステージと runtime ステージに分けて Image サイズを削減
- **ヘルスチェック追加** … `HEALTHCHECK` を Dockerfile に書く / compose の `healthcheck:` で起動順序を厳密化
- **環境変数の整理** … `.env` を `.env.example` と分離、Git 管理できるテンプレートを用意
- **Chroma を独立コンテナ化** … `chromadb/chroma` の公式 Image を別サービスとして立てる（マイクロサービス化の第一歩）
- **Redis + Celery の追加** … インデックス更新を非同期タスク化（本格的な業務アプリに近づく）
- **Langfuse コンテナ追加** … LLM のトークン・レイテンシ可視化
- **Admin 用 Streamlit を別サービスに** … `/chat` と `/admin` を Nginx で URL 振り分け
- **docker-compose の override** … `docker-compose.override.yml` で開発用設定を分離（ホットリロード等）
- **本番デプロイ** … VPS (Conoha / さくら等) や RHEL サーバーに `docker-compose` をそのまま持っていく
- **Podman への移行** … RHEL 標準の Podman は Docker CLI 互換。ほぼそのまま動く

---

## 16. ディレクトリ最終構成

```
~/rag-streamlit-app/
├── .venv/                       # 既存 (Git管理しない、コンテナには入らない)
├── .env                         # 既存 (Git管理しない、env_file で注入)
├── .dockerignore                # ★新規
├── .gitignore                   # 既存
├── Dockerfile                   # ★新規
├── docker-compose.yml           # ★新規
├── docker/
│   └── nginx/
│       └── nginx.conf           # ★新規
├── requirements.txt             # 既存
├── app.py                       # 既存 (Vanilla RAG)
├── app_graph.py                 # 既存 (LangGraph版、コンテナの主役)
├── rag/                         # 既存
│   ├── __init__.py
│   ├── state.py
│   ├── nodes.py
│   └── graph.py
├── docs/                        # 既存 (bind mount)
│   └── RagDB.md
└── chroma_db/                   # 既存 (使われなくなる。named volume に移行)
```

> 💡 ローカルの `chroma_db/` ディレクトリは**使われなくなる**。
> コンテナは `chroma_data` という名前の Docker 管理 Volume を使う。
> 完全リセットしたいときは `docker compose down -v`。

---

## 17. 用語ミニ辞典

- **colima** … macOS 上で Linux VM を立てて Docker を動かす CLI ツール。Container + Lima の造語
- **Lima** … colima の土台。macOS 向けの Linux 仮想マシン基盤
- **vz (Virtualization.framework)** … macOS 13+ の仮想化API。QEMU より高速
- **Rosetta 2** … Apple 製の x86_64 → ARM64 エミュレータ。`--vz-rosetta` で Docker イメージのエミュレーションが高速化
- **virtiofs** … ホスト ⇄ VM のファイル共有プロトコル。sshfs より高速
- **Image（イメージ）** … アプリと依存と OS を固めた読み取り専用のテンプレート
- **Container（コンテナ）** … Image を元に動いているプロセス
- **Dockerfile** … Image を作るレシピ
- **Layer（レイヤ）** … Dockerfile の各命令が作る差分。キャッシュの単位
- **bind mount** … ホストのディレクトリをそのままコンテナに見せる方式。編集がすぐ反映
- **named volume** … Docker が管理する保存領域。ホスト依存なし、I/O 高速
- **`.dockerignore`** … `docker build` がコンテキストから除外するファイル一覧
- **docker compose** … 複数コンテナを yml でまとめて管理するコマンド
- **service（サービス）** … compose の1コンテナ定義
- **リバースプロキシ** … クライアントとアプリの間に入り、ルーティング / SSL / キャッシュ等を担う（今回は Nginx）
- **Docker context** … どの Docker デーモンに繋ぐかの設定。colima がインストール時に自動設定する

---

## 18. 資料のアーキとの対応

本手順書で作ったものが、業務用 RAG システムのインフラ構成とどう対応するか：

| 本手順書 | 業務用 RAG システム |
|---|---|
| colima (Linux VM on macOS) | RHEL 8.1 on VM |
| Docker CLI | Podman CLI（互換） |
| `Dockerfile` (Python 3.11-slim) | 同 (Python 3.12.x) |
| `requirements.txt` | 同（`pip install -r` 原則） |
| `docker-compose.yml` | 複数 Pod / サービス定義 |
| `nginx` サービス | Nginx 1.27 (リバースプロキシ) |
| `chroma_data` volume | Azure AI Search（外部サービス） |
| `rag-app` サービス | Streamlit 1.42 + LangChain 0.3 + LangGraph |
| (次のステップ) Redis/Celery | Celery 5.4 + Redis 7.4 |
| (次のステップ) Langfuse | Langfuse 3.47 |

👉 Podman は Docker CLI とほぼ完全互換。本手順書を終えた時点で、**RHEL 上の Podman 環境にそのまま持っていける知識**が身についている。
`docker` を `podman` に置き換えるだけで動く。

---

## 19. まとめ

この手順書で身についたこと：

- colima + Docker CLI による **GUI レス**のコンテナ環境構築
- Apple Silicon での最適な VM 設定（vz + rosetta + virtiofs）
- Dockerfile の書き方（レイヤキャッシュ最適化含む）
- `docker compose` でのマルチコンテナ運用
- Nginx リバースプロキシの基本設定（WebSocket サポート含む）
- CLI から全てを操作・デバッグする力

**すべてがターミナルで完結するスキル**は、SSH 越しのサーバー運用・CI/CD・Podman on RHEL へのそのままの移行など、**業務で必ず効いてくる土台**になる。

次は発展課題から好きなものを選んで、業務用 AI アプリ基盤に少しずつ近づけていく 🚀
