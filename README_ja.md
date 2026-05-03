# agent-sequencer

*[English README](README.md)*

**Python スクリプトで AI エージェントを制御し、厳密なワークフローに沿ったタスクや
長時間タスクを実行させる MCP スキル + サーバ。**

```
+--------------+    MCP tool call     +-------------------------------+
|              | -------------------> |       agent-sequencer         |
|   AI Agent   |                      |     (MCP stdio server)        |
| (Claude Code)| <------------------- |                               |
|              |    yield Instruction |  +-------------------------+  |
+------+-------+                      |  |   Sequencer Program     |  |
       |                              |  |   (Python generator)    |  |
       | step execution via own tools |  |   branching / control   |  |
       | (Bash / Edit / Skill / ...)  |  +-------------------------+  |
       v                              |                               |
     User                             |  +-------------------------+  |
                                      |  |   JSONL event log       |  |
                                      |  |   (deterministic replay)|  |
                                      |  +-------------------------+  |
                                      +-------------------------------+
```

## アーキテクチャ概要

- **シーケンサプログラム**: Python ジェネレータで書く古典プログラム。
  ワークフローの分岐 / 集計 / 終了判定はここに閉じる。
- **ステップ境界**: プログラム内の `yield Instruction(...)` が 1 ステップに相当。
  指示文と JSON Schema 応答スキーマを宣言し、AI エージェントが自身のツール
  （Bash / Edit / Skill / ...）で実行 → JSON で結果を返す。
- **決定論的再生**: 全イベントを JSONL に追記し、サーバ再起動 / interrupt /
  compact 後もプログラムを最初から再実行 + 記録済み入力を再注入することで完全復旧。

ガードレールがプロンプトではなく **コード** に置かれるため、長時間タスクのコンテキスト
劣化に対して安定します。

- **対応エディタ**: Claude Code
- **言語 / ランタイム**: Python ≥ 3.11
- **配布**: Claude Code プラグイン（git ベース）
- **ライセンス**: MIT

---

## できること

- **Python でシーケンサプログラムを書く** — ワークフローの分岐 / 集計 / 終了判定を
  プログラムに閉じ、各ステップの実行は AI エージェントに委譲できる。プログラム作者
  向けガイド: [`docs/authoring-programs_ja.md`](skills/agent-sequencer/docs/authoring-programs_ja.md)
- **AI エージェント (Claude Code) から MCP ツールで呼び出す** —
  `sequencer_list_programs` でプログラム一覧、`sequencer_start` で起動、
  `sequencer_next` で結果投函、`sequencer_resume` で中断インスタンスの復旧。
  全ツール一覧: [`skills/agent-sequencer/README_ja.md`](skills/agent-sequencer/README_ja.md#mcp-ツール一覧)
- **長時間ワークフローの安定実行** — JSON Schema で各ステップの応答を厳密検証し、
  違反は自動リトライ。interrupt / compact 後も JSONL の決定論的再生で完全復旧。
  `--watch` でプログラム編集をホットリロード

詳しくは [`SKILL.md`](skills/agent-sequencer/SKILL.md)（駆動ルール）と
[`docs/authoring-programs_ja.md`](skills/agent-sequencer/docs/authoring-programs_ja.md)
（プログラム作者ガイド）を参照。

なお、自作プログラムの動作確認 / 自己レビュー用ヘルパーとして
[`review-rounds`](skills/agent-sequencer/programs/review_rounds/README_ja.md)
プログラム（3 体の専門家エージェントで並列レビュー → 修正 → 検証を繰り返す）を
同梱しています — 自作プログラムのサンプル実装としても参照できます。

---

## インストール

### 前提

- [uv](https://docs.astral.sh/uv/) （MCP サーバ実行に使用）。未インストールなら:
  ```powershell
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 方法 A: Claude Code プラグインとしてインストール（推奨）

```text
/plugin marketplace add OPENSPHERE-Inc/agent-sequencer
/plugin install agent-sequencer@agent-sequencer
```

これでスキル / 同梱プログラム / MCP サーバすべてが自動セットアップされます。
プラグインの `.mcp.json` が `${CLAUDE_PLUGIN_ROOT}` を経由して `uv run` を呼び、
バンドル `programs/` を `AGENT_SEQUENCER_PROGRAMS_DIR` で MCP サーバに渡します。

### 方法 B: 開発者として clone して使う

```bash
git clone https://github.com/OPENSPHERE-Inc/agent-sequencer.git
cd agent-sequencer
uv sync
uv run agent-sequencer --help
```

このディレクトリを cwd にして Claude Code を起動すれば、リポジトリ同梱の
[`.mcp.json`](.mcp.json) と [`skills/agent-sequencer/`](skills/agent-sequencer/)
が読み込まれます。`${CLAUDE_PLUGIN_ROOT}` 変数は Claude Code プラグイン
コンテキストでのみ展開されるため、開発時は別途 `.claude/.mcp.json` を作るか
環境変数を設定してください（[開発時セットアップ](#開発時セットアップ) 参照）。

---

## クイックスタート

Claude Code でプラグインを有効にしたら、自然言語で依頼できます。動作確認には
同梱の `hello` プログラム（最小サンプル / スモークテスト）が便利です。

### A. プログラム名を指定（動作確認）

```
hello プログラムを agent-sequencer で起動してください
（names=["Alice", "Bob"]）
```

エージェントは `sequencer_list_programs` で一覧を確認 → `sequencer_start program="hello"
params={"names": ["Alice", "Bob"]}` で起動 → 各名前への挨拶を 1 ステップずつ生成して
`sequencer_next` で投函し、最後に `sequencer_close` で解放します。

### B. やりたい内容で依頼（自作プログラム）

```
agent-sequencer で my-workflow プログラムを動かしてください
```

`<cwd>/.claude/sequencer/programs/my_workflow.py` 等に置いた自作プログラムを呼び出せます。
エージェントが `sequencer_list_programs` で確認し、適切なプログラムを選んで起動します。

### C. 中断したインスタンスを再開

```
instance_id=abc123 を resume して続きから進めてください
```

---

## 開発時セットアップ

リポジトリを clone して MCP サーバを直接動かす場合の `.mcp.json` 例
（`<repo>/.mcp.json` を上書きしないように、ローカル設定ファイルを使う）:

```jsonc
// ~/.claude/.mcp.json または <project>/.claude/.mcp.local.json
{
  "mcpServers": {
    "agent-sequencer": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/agent-sequencer",
        "agent-sequencer",
        "--watch"
      ],
      "env": {
        "AGENT_SEQUENCER_PROGRAMS_DIR": "/absolute/path/to/agent-sequencer/skills/agent-sequencer/programs",
        "AGENT_SEQUENCER_STATE_DIR": "${HOME}/.claude/sequencer/state",
        "VIRTUAL_ENV": "",
        "UV_LINK_MODE": "copy"
      }
    }
  }
}
```

`--watch` は開発時のホットリロード（2 秒スロットルで `programs/*.py` の変更を検知）。

### MCP ツール許可

`.claude/settings.local.json` 等で以下を allow に追加:

```jsonc
"permissions": {
  "allow": [
    "mcp__agent-sequencer__sequencer_list_programs",
    "mcp__agent-sequencer__sequencer_start",
    "mcp__agent-sequencer__sequencer_current",
    "mcp__agent-sequencer__sequencer_next",
    "mcp__agent-sequencer__sequencer_resume",
    "mcp__agent-sequencer__sequencer_close",
    "mcp__agent-sequencer__sequencer_list"
  ]
}
```

### テスト

```bash
uv run pytest
```

---

## ディレクトリ構成

```
agent-sequencer/
├── pyproject.toml                     # Python パッケージ（MCP サーバ）
├── src/agent_sequencer/               # Python パッケージ本体（8 モジュール）
├── tests/                             # pytest テスト
├── .claude-plugin/
│   ├── plugin.json                    # プラグイン manifest
│   └── marketplace.json               # マーケットプレース listing
├── .mcp.json                          # プラグイン同梱の MCP 登録
├── skills/
│   └── agent-sequencer/
│       ├── SKILL.md                   # 駆動ルール
│       ├── README.md                  # スキル詳細
│       ├── docs/
│       │   └── authoring-programs.md  # プログラム作者ガイド
│       └── programs/                  # 同梱シーケンサプログラム
│           ├── review_rounds.py
│           └── review_rounds/         # 自己完結バンドル
│               ├── agents/            # python-sensei / sequencer-sensei / prompt-sensei
│               ├── scripts/
│               └── skills/            # sequencer-review / -respond / -resolve
└── .github/workflows/
    └── ci.yml                         # pytest + git install verification
```

---

## 環境変数

| 変数 | 用途 | 既定 |
|---|---|---|
| `AGENT_SEQUENCER_PROGRAMS_DIR` | 追加のプログラム探索パス（最低優先度のフォールバック。プラグインが同梱プログラムを公開するために使用） | （未設定） |
| `AGENT_SEQUENCER_STATE_DIR` | JSONL イベントログの配置ディレクトリ | `~/.claude/sequencer/state/` |

プログラム探索は次の順（先勝ち）:

1. `<cwd>/.claude/sequencer/programs/`
2. `~/.claude/sequencer/programs/`
3. `$AGENT_SEQUENCER_PROGRAMS_DIR`

末尾配置にすることで、同じ `NAME` のプロジェクト固有プログラム／ユーザー全体プログラムが
プラグイン同梱版を透過的に上書きできます。

---

## 制限事項（v1）

- `ParallelInstructions`（プログラム内ファンアウト宣言）は未実装
- HTTP/SSE トランスポート（複数 Claude Code セッション間で共有）は未実装
- プログラムサンドボックス（信頼境界の強化）は未実装
- TypeScript / Lua プログラムの実行は未実装
- PyPI 公開は未対応（現時点は git ベース配布のみ）

---

## ライセンス

[MIT License](LICENSE) © 2026 OPENSPHERE Inc.
