# agent-sequencer

> **AI エージェントが古典プログラム（Python ジェネレータ）をステップ実行するデバッガとして駆動する MCP スキル + サーバ**

ロジックの真実は **プログラム側** に閉じ、AI は **ドライバ役** に徹します。
ガードレールはプロンプトではなくコードに閉じるため、長時間タスクのコンテキスト劣化に耐性があります。

- **対応エディタ**: Claude Code
- **言語 / ランタイム**: Python ≥ 3.11
- **配布**: Claude Code プラグイン（git ベース）
- **ライセンス**: MIT

---

## できること

- AI に「複数ステップの長時間タスク」を実行させる際、**外側ループの判断はプログラム** に閉じ、AI には各ステップの実行だけを任せる
- スキーマ違反 / 中断 / compact 後の再同期に対して **JSONL イベントログ + 決定論的再生** で復旧可能
- スキル同梱の `review-rounds` プログラムで、自分のシーケンサプログラムを 3 体の専門家エージェント（python-sensei / sequencer-sensei / prompt-sensei）でレビュー → 修正 → 検証

詳しくは [`skills/agent-sequencer/SKILL.md`](skills/agent-sequencer/SKILL.md) と
[`skills/agent-sequencer/docs/authoring-programs.md`](skills/agent-sequencer/docs/authoring-programs.md) を参照。

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

Claude Code でプラグインを有効にしたら、自然言語で依頼できます:

### A. プログラム名を指定

```
review-rounds プログラムを agent-sequencer で回してください
（max_rounds=3, base=main）
```

### B. やりたい内容で依頼

```
agent-sequencer で src/my_program.py をレビュー＆修正してください
```

エージェントが `sequencer_list_programs` → `sequencer_start` → 駆動ループ → `sequencer_close`
の流れを自動で進めます。

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
| `AGENT_SEQUENCER_PROGRAMS_DIR` | 追加のプログラム探索パス（最優先） | （未設定） |
| `AGENT_SEQUENCER_STATE_DIR` | JSONL イベントログの配置ディレクトリ | `~/.claude/sequencer/state/` |

プログラム探索は次の順（先勝ち）:

1. `$AGENT_SEQUENCER_PROGRAMS_DIR`
2. `<cwd>/.claude/sequencer/programs/`
3. `~/.claude/sequencer/programs/`

---

## 制限事項（v1）

- フィードバック再修正ループ（review-respond → review-resolve の最大 3 回繰り返し）は未実装
- `ParallelInstructions`（プログラム内ファンアウト宣言）は未実装
- HTTP/SSE トランスポート（複数 Claude Code セッション間で共有）は未実装
- プログラムサンドボックス（信頼境界の強化）は未実装
- TypeScript / Lua プログラムの実行は未実装
- PyPI 公開は未対応（Phase 2 で対応予定。現時点は git ベース配布のみ）

---

## ライセンス

[MIT License](LICENSE) © 2026 OPENSPHERE Inc.
