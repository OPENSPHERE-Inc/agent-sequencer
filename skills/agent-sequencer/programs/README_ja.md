# シーケンサプログラムディレクトリ（プラグイン同梱）

*[English](README.md)*

このディレクトリには、プラグイン同梱のシーケンサプログラムを配置します。

## 探索パスの優先順位

`registry.py` は次の順でプログラムを探索します（先勝ち）:

1. `$AGENT_SEQUENCER_PROGRAMS_DIR`（環境変数）
2. `<cwd>/.claude/sequencer/programs/`（プロジェクト固有）
3. `~/.claude/sequencer/programs/`（ユーザ全体）

このディレクトリ（`skills/agent-sequencer/programs/`）はプラグイン同梱で、
**プラグインの `.mcp.json` から `AGENT_SEQUENCER_PROGRAMS_DIR` 経由で指定** されます。
MCP サーバの自動探索パスには含まれません（PyPI インストール先とプラグイン
インストール先が別の場所になるため）。

## 同梱プログラム

| ファイル | 名前 | 概要 |
|---|---|---|
| `hello.py` | `hello` | 最小サンプル / スモークテスト用プログラム。`params["names"]`（既定 `["world"]`）の各名前を順に挨拶する。新規プログラム作者の参考実装、および agent-sequencer の動作確認用 |
| `review_rounds.py` | `review-rounds` | シーケンサプログラムを 3 体の専門家エージェント（python-sensei / sequencer-sensei / prompt-sensei）でレビューし、対応 → 検証を最大 N ラウンド繰り返して収束させる。自作プログラムの自己レビューヘルパーとして利用 |

`review_rounds.py` が参照するスキル / エージェント / スクリプトは、隣接する
`review_rounds/` ディレクトリに自己完結しています。詳細は
[`README_ja.md`](review_rounds/README_ja.md) を参照してください。
