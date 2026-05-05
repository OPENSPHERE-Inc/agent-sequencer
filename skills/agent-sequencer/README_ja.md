# agent-sequencer スキル

*[English README](README.md)*

古典プログラム（Python ジェネレータで書かれたシーケンサプログラム）のステップ実行デバッガとして
AI エージェントを駆動する MCP スキル。

## 設計ドキュメント

- [プログラム作者ガイド](docs/authoring-programs.md)
- [同梱プログラム README](programs/README.md)
- [review-rounds バンドル README](programs/review_rounds/README.md)

リポジトリ全体に関わるトピック（インストール、配布、CI など）は
[トップレベル README](../../README.md) を参照してください。

## 駆動フロー（要約）

1. ユーザーから依頼を受けたら、`sequencer_list_programs` で該当するプログラムを特定する。
2. `sequencer_start` でインスタンスを起動し、返却された `instance_id` を最優先で記憶する。
3. `last_yield.text` を（自前のツールで）実行する。
4. `expect_schema` に沿って結果を組み立て、
   `sequencer_next(for_step_no=<現在値>, result=...)` で投函する。
5. `state` が `completed` / `aborted` / `failed` になったら、
   **最終結果をユーザーへ報告し `sequencer_close` を呼ぶ**。

詳細は [SKILL.md](SKILL.md)（9 個の駆動ルール）を参照。

## MCP ツール一覧

| ツール | 役割 |
|---|---|
| `sequencer_list_programs` | 利用可能なプログラムの一覧 |
| `sequencer_start` | インスタンスの起動 |
| `sequencer_current` | 直近 yield の再取得（再同期用） |
| `sequencer_next` | 結果の投函と次の yield の取得 |
| `sequencer_resume` | JSONL からの復元 |
| `sequencer_close` | 解放（推奨経路） |
| `sequencer_list` | アクティブなインスタンスの一覧 |
| `sequencer_memo_set` | インスタンスごとのメモに JSON 値を保存（次の `sequencer_next` でクリア） |
| `sequencer_memo_get` | インスタンスごとのメモから値を取得 |
| `sequencer_memo_keys` | インスタンスごとのメモのキー一覧（任意で prefix フィルタ） |
| `sequencer_memo_delete` | インスタンスごとのメモからキーを削除 |

## プログラム探索パス（先勝ち）

| 順序 | パス | 用途 |
|---|---|---|
| 1 | `<cwd>/.claude/sequencer/programs/` | プロジェクト固有プログラム |
| 2 | `~/.claude/sequencer/programs/` | ユーザー全体プログラム |
| 3 | `$AGENT_SEQUENCER_PROGRAMS_DIR` | プラグイン同梱プログラム（プラグインの `.mcp.json` で設定）。開発時のフォールバックとしても利用可 |

プラグイン同梱の `programs/` ディレクトリ（このディレクトリ）は
`AGENT_SEQUENCER_PROGRAMS_DIR` 環境変数経由で渡されます。**末尾**に配置されるため、
同じ `NAME` のプログラムを `<cwd>/.claude/sequencer/programs/` または
`~/.claude/sequencer/programs/` に置くと、プラグイン同梱版を透過的に上書きできます。

## スキルの呼び出し方

agent-sequencer スキルは **MCP ツール群を提供する基盤** であり、
`/agent-sequencer` のようにユーザーが直接叩くスラッシュコマンドではありません。
実際の依頼は次のいずれかの形を取ります。

### A. プログラム名を指定する（推奨）

```
review-rounds プログラムを agent-sequencer で回してください
（max_rounds=3, base=main）
```

エージェントは `sequencer_list_programs` で確認した上で、
`sequencer_start program="review-rounds" params={"max_rounds": 3, "base": "main"}` を呼び、
`last_yield.text` で返ってきた指示を順次実行します。

### B. やりたい内容で依頼する（プログラムはエージェントが選ぶ）

```
agent-sequencer で src/my_program.py のシーケンサプログラムをレビュー＆修正してください
```

エージェントは `sequencer_list_programs` を確認し、
ユーザーの要望に合致するプログラム（この例なら `review-rounds`）を選んで起動します。

### C. 中断したインスタンスを再開する

```
instance_id=abc123 を resume して止まったところから続けてください
```

エージェントは `sequencer_resume` で JSONL からインスタンスを復元し、
`last_yield` を確認してループを再開します。`source_hash` が一致しない場合は
`ProgramChanged` エラーが返され、エージェントはその旨をユーザーへ報告します。

## 開発 Tips

| 目的 | 方法 |
|---|---|
| プログラム編集を即座に反映 | `--watch` を有効化（2 秒スロットル付き） |
| state ディレクトリの場所を知る | `AGENT_SEQUENCER_STATE_DIR` を確認（既定: `~/.claude/sequencer/state/`） |
| デバッグ用にイベント履歴を見る | `<state_dir>/<instance_id>.jsonl` を読む |
| アクティブなインスタンスを一覧 | `sequencer_list filter="active"` |
| プログラムを単体テスト | `Driver` を直接駆動する（[authoring-programs.md §12](docs/authoring-programs.md) 参照） |

## 制限事項（v1）

- `ParallelInstructions`（プログラム内ファンアウト宣言）は未実装
- HTTP/SSE トランスポート（複数 Claude Code セッション間で共有）は未実装
- プログラムサンドボックス（信頼境界の強化）は未実装
- TypeScript / Lua プログラムの実行は未実装
