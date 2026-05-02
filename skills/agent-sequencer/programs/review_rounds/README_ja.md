# review_rounds — シーケンサプログラム向けのレビュー & 修正バンドル

*[English](README.md)*

これは **公式機能バンドル** で、agent-sequencer のシーケンサプログラム
（および周辺の Python コード）に対して 3 体の専門家エージェントによる
並列レビューを実行し、トリアージ → 見積 → 修正 → 検証 を最大 N ラウンド
繰り返して収束させます。

ユーザが自作のシーケンサプログラムを `review-rounds` に通すことで、
agent-sequencer のベストプラクティスに照らしたレビューを受けられます。

## 自己完結配布

`review_rounds.py` は隣接する `review_rounds/` ディレクトリにのみ依存します。
プログラムとディレクトリをまとめてコピーすれば、別プロジェクトでも同じ
レビュー機能が動作します（このバンドル自体がポータビリティのサンプルにも
なっています）。

## 3 体の専門家

シーケンサプログラムは **Python ジェネレータ + プロンプト + シーケンサ API**
の複合物なので、レビューを 3 軸に分割して並列に実行します:

| 専門家 | 観点 |
|---|---|
| **python-sensei** | Python 言語のセマンティクス、型ヒント、async/await、PEP 準拠、ミュータブルなデフォルト引数など、言語固有の落とし穴 |
| **sequencer-sensei** | agent-sequencer API（Instruction / Done / Abort / Context）、決定論性、ジェネレータの双方向通信、ライフサイクル、`expect_schema` の設計、バンドル化 |
| **prompt-sensei** | Instruction.text の構造、`expect_schema` との整合、暴走を防ぐ明示的な制約、テンプレート設計、過剰装飾の排除 |

## レイアウト

```
review_rounds.py                       — シーケンサプログラム本体
review_rounds/
├── README.md                           — このファイル
├── skills/                             — Instruction text から参照されるスキル定義
│   ├── sequencer-review.md             — 3 専門家による並列レビュー
│   ├── sequencer-review-respond.md     — トリアージ → 見積 → 修正（担当割り当て付き）
│   └── sequencer-review-resolve.md     — 修正の検証
├── agents/
│   ├── python-sensei.md                — エージェント定義テンプレート
│   ├── sequencer-sensei.md             — 同上
│   └── prompt-sensei.md                — 同上
└── scripts/
    ├── fetch-diff.sh                   — git diff の取得
    ├── rm-tmp.sh                       — .claude/tmp/ 配下の安全な削除
    └── render-review.py                — events.jsonl をレビュードキュメントに反映
```

## 使い方

`sequencer_start` で起動するときに、`params` でレビュー対象と関連オプション
を指定します:

```jsonc
{
  "program": "review-rounds",
  "params": {
    "max_rounds": 3,                    // 既定 5
    "base": "main",                     // 既定: エージェントが解決
    "target": "src/my_program",         // 省略すると diff 全体をレビュー
    "confirm": true,                    // 既定 true（見積後にユーザ確認を待つ）
    "output_base": ".claude/tmp"        // 既定 .claude/tmp
  }
}
```

### 例 1: agent-sequencer 自体をレビュー（agent-sequencer リポジトリ内で実行する場合）

```jsonc
"params": {
  "target": "src/agent_sequencer",
  "base": "main"
}
```

### 例 2: 特定のユーザプログラムをレビュー

```jsonc
"params": {
  "target": "src/my_workflow.py",
  "max_rounds": 2,
  "confirm": false
}
```

### 例 3: ブランチ全体の diff をレビュー（target を省略）

```jsonc
"params": {
  "base": "main"
}
```

## パラメータ詳細

| パラメータ | 既定 | 説明 |
|---|---|---|
| `max_rounds` | `5` | 外側ループの最大ラウンド数（1-10） |
| `base` | エージェントが解決 | レビュー対象 git diff のベースブランチ |
| `target` | （未設定） | レビュー対象パス。指定すると、そのパス配下のファイルのみが指摘対象になる |
| `output_base` | `.claude/tmp` | レビュードキュメントの出力ベースディレクトリ |
| `confirm` | `true` | `sequencer-review-respond` の `--confirm` を有効化（見積後にユーザ確認を待つ） |

## 収束（プログラムが決定論的に判定）

| 条件 | 結果 |
|---|---|
| `findings_total == 0` | `Done(reason="Converged with zero findings")` |
| `code_changed == False` | `Done(reason="Converged with no code changes")` |
| `max_rounds` 到達 | `Abort(reason="...")` |

## 関心の分離

- **スキル**（`sequencer-review*.md`）は汎用ツールです。`--target` /
  `--confirm` などのオプションを受け付けますが、既定値は中立です。
- **プログラム**（`review_rounds.py`）が Instruction text で `target` /
  `confirm` を明示的に渡し、このユースケース固有の挙動（シーケンサ
  プログラム向けの 3 sensei、確認を既定で有効、等）を決定します。

別の対象をレビューしたい、または別の専門家構成を使いたい場合は、
`review_rounds.py` を新しいプログラムとしてコピーしてください。
`sequencer-review*` スキルはそのまま再利用できます。

## frontmatter の命名規則

スキル / エージェントの `name` フィールドは **ハイフン以外の記号を含めら
れない** ため、ファイル名と完全一致するシンプルな ID を使います:

| File | name |
|---|---|
| `skills/sequencer-review.md` | `sequencer-review` |
| `skills/sequencer-review-respond.md` | `sequencer-review-respond` |
| `skills/sequencer-review-resolve.md` | `sequencer-review-resolve` |
| `agents/python-sensei.md` | `python-sensei` |
| `agents/sequencer-sensei.md` | `sequencer-sensei` |
| `agents/prompt-sensei.md` | `prompt-sensei` |

## エージェントの起動方法

3 体の sensei エージェントは Claude Code に登録されていません。実行時に
`subagent_type=general-purpose` でサブエージェントを起動し、それぞれに
対応する `agents/<name>.md` をコンテキストとして読み込ませることで、
専門家としての役割を与えます。

これにより:
- Claude Code 側の追加登録は不要。
- バンドルをコピーしたその場でバンドルが動作する。
- ユーザの設定を汚染しない。
