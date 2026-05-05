# シーケンサプログラム作者ガイド

*[English](authoring-programs.md)*

このドキュメントは、agent-sequencer の **シーケンサプログラム**
（Python ジェネレータとして書かれた古典プログラム）を作成・変更する
すべての人のためのガイドです。

## 1. 思想と責務分離

シーケンサプログラムは「**AI エージェントを駆動するための古典プログラム**」です。

| 責務担当 | 役割 |
|---|---|
| プログラム（あなたが書く Python） | 制御フロー、判断ロジック、状態遷移、収束判定、集計 |
| AI エージェント | プログラムが yield した `Instruction.text` を実行し、結果を JSON で報告する |
| ドライバ / ランタイム | スキーマ検証、step_no 管理、ジェネレータ駆動、JSONL 永続化、再生 |

**判断はプログラム内に閉じ込め、エージェントは命令を実行する純粋なドライバとして振る舞う** ——
これが基本原則です。プログラム内では `if result["x"] == ...:` のような分岐を書き、
エージェントには「これをやってください」とだけ伝えます。

## 2. 基本的なプログラム構造

最小限のプログラムは次の 4 要素から構成されます。

```python
from agent_sequencer.api import Done, Instruction

NAME = "hello"
DESCRIPTION = "One-line description of what the program does"
PARAMS_SCHEMA = {
    "name": {"type": "string", "default": "world"},
}

def run(ctx):
    result = yield Instruction(
        text=f"Please greet '{ctx.params.get('name', 'world')}'. "
             "Return JSON in the form {\"message\": \"...\"}.",
        expect_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    )
    yield Done(summary={"echo": result["message"]})
```

| 名前 | 必須 | 説明 |
|---|:-:|---|
| `NAME` | 必須 | プログラム名（`sequencer_start program=...` で指定する ID） |
| `DESCRIPTION` | – | `sequencer_list_programs` で表示される説明 |
| `PARAMS_SCHEMA` | – | `sequencer_start params={...}` で渡せるパラメータの説明 |
| `run(ctx)` | 必須 | ジェネレータ関数。`ctx` はランタイムコンテキスト |

## 3. プログラムの配置場所

`.py` ファイルを下記の探索パスのいずれかに置きます。MCP サーバは起動時（および
`--watch` の再スキャンごと）にこれらをスキャンし、各プログラムを `NAME = "..."`
定数で登録します。

### 探索パスの優先順位（先勝ち）

| 順 | パス | 用途 |
|---|---|---|
| 1 | `<cwd>/.claude/sequencer/programs/` | プロジェクト固有のプログラム（利用するプロジェクトと一緒にコミットする） |
| 2 | `~/.claude/sequencer/programs/` | ユーザ全体のプログラム（開くプロジェクトに関わらず利用できる） |
| 3 | `$AGENT_SEQUENCER_PROGRAMS_DIR` | 環境変数によるフォールバック（プラグインが同梱プログラムを公開する手段。開発時のフォールバックとしても使える） |

複数のパスに同じ `NAME` のプログラムが存在する場合、優先度の高い方が採用され、
低い方は黙ってシャドウされます。プラグイン同梱プログラムは末尾に追加されるため、
同名のプロジェクト固有プログラム／ユーザ全体プログラムが透過的に優先されます。

### 配置場所の選び方

| シナリオ | 推奨配置 |
|---|---|
| 1 つのプロジェクト固有のワークフロー（例: プロジェクト X 用のリリース準備チェックリスト） | `<repo>/.claude/sequencer/programs/<your_program>.py` — プロジェクトと一緒にコミット |
| どこからでも使いたい個人用ヘルパー | `~/.claude/sequencer/programs/<your_program>.py` |
| 第三者に配布したいプログラム | 自身の Claude Code プラグインにバンドル（[§ 10 プログラムのバンドル化](#10-プログラムのバンドル化推奨) を参照）し、プラグインの `.mcp.json` から `AGENT_SEQUENCER_PROGRAMS_DIR` 経由で公開 |

### ファイル命名規約

- ファイル名: `snake_case.py`。`_` で始まるファイルはスキップされます。
- 各ファイルで `NAME = "kebab-case-name"` を宣言 — これが
  `sequencer_start program="..."` で指定する ID になります。
- スキャン対象は各探索パスの**直下のみ**で、サブディレクトリは無視されます（隣接する
  `<program-name>/` バンドルがプログラムファイルと衝突しない理由 — § 10 参照）。

## 4. ジェネレータの基礎

Python ジェネレータの **双方向通信** を利用します。
`yield` 式は値を送出すると同時に値を受け取ります。

```python
def run(ctx):
    result = yield Instruction(text="...", expect_schema={...})
    #   ↑                ↑
    #   |                エージェントに送られる Instruction
    #   |
    #   エージェントから返ってきた検証済み JSON 結果を受け取る
```

ランタイムの `Driver` は次のように動作します。
1. `next(gen)` で最初の yield まで進める。
2. yield された `Instruction` をエージェントに送信する。
3. エージェントが返した結果を `expect_schema` で検証する。
4. 検証済み結果を `gen.send(validated_result)` で `result =` の左辺に注入し、次の yield まで進める。

### yield の種類

| 種類 | 用途 |
|---|---|
| `Instruction(text, expect_schema, on_invalid, timeout_minutes)` | エージェントへの命令 |
| `Done(summary)` | プログラムの正常終了。`summary` はユーザーへの最終レポートに使われる |
| `Abort(reason)` | プログラムの異常終了。`reason` はユーザーに表示される |

裸の `return` も `StopIteration` として完了扱いになりますが、
**明示的に `yield Done()` を書くことを推奨します**
（可観測性のため、また JSONL ログでの追跡を容易にするため）。

## 5. Instruction の設計

### 5.1 `text` の書き方

これはエージェントが読む命令文です。**AI に読ませることが前提なので**、
見出しや空行段落といった Markdown の装飾は最小限に抑えてかまいません（`review_rounds.py` を参照）。
代わりに次の点に集中します。

- **使用するスキル / 手順を冒頭で参照する**（`Skill: <path>`）
- **対象範囲を明示する**（曖昧だとエージェントが越境する）
- **報告フォーマット（JSON）をインラインで例示する**
- **やってはいけないことを明記する**
  （例: 理解のために他のコンテキストを参照するのは可だが、
  対象外の事項についてコメントするのは禁止）

### 5.2 `expect_schema`

JSON Schema（`jsonschema>=4.0`）で **エージェントが返さなければならない形** を厳密に定義します。

```python
expect_schema = {
    "type": "object",
    "properties": {
        "findings_total": {"type": "integer", "minimum": 0},
        "doc_path": {"type": "string", "minLength": 1},
    },
    "required": ["findings_total", "doc_path"],
    "additionalProperties": True,  # エージェントが追加情報を含めるのを許可する
}
```

**ポイント**:
- `required` は必ず指定する（必須フィールドの欠落を検出するため）。
- 数値型には `minimum` / `maximum`、文字列には `minLength` / `enum` を活用する。
- `additionalProperties: True` を明示的に設定する
  （エージェントが理由付けや統計などを含められるようにし、
  プログラム側は実際に必要なフィールドだけを消費する）。

### 5.3 `on_invalid` 戦略

スキーマ違反時の挙動を選択できます。

| 値 | 挙動 |
|---|---|
| `"retry"`（デフォルト） | 同じ Instruction を新しい step_no で再発行する。違反内容は `last_yield.validation_error` を介してエージェントに伝えられる。 |
| `"abort"` | インスタンスをアボートする。 |

通常は `"retry"` で十分です。「不正な値が来たら挙動を切り替える」という
プログラム側のフォールバックを書くより、
スキーマで縛ってエージェントに修正させる方が堅牢です。

### 5.4 `timeout_minutes`

エージェントへの目安時間です。ドライバが強制中断することはありませんが、
値は `current.last_yield` に公開されるため、
エージェントが「これはこれくらい時間がかかる種類の作業だ」と把握する助けになります。

## 6. コンテキスト (`ctx`) の利用

```python
def run(ctx):
    # パラメータの取得
    target = ctx.params.get("target", "default-value")

    # 進捗ヒントの発行（観測専用。判断には使わない）
    ctx.publish_progress(current=1, of=10, label="Round 1/10")

    # ...
```

| 属性 / メソッド | 用途 |
|---|---|
| `ctx.params` | `sequencer_start` に渡された `params` 辞書 |
| `ctx.env` | ランタイムメタデータ（読み取り専用想定。現状ではほぼ未使用） |
| `ctx.publish_progress(current, of, label)` | 進捗ヒント。`sequencer_current` の応答に `progress_hint` として現れる |

## 7. 決定論性の制約（最重要）

**プログラムは決定論的に書かなければなりません。** これは JSONL ログの再生（resume）によって
まったく同じ最終状態に到達するための前提です。

### 7.1 してはいけないこと

- `time.time()` / `datetime.now()` / `random.*` を直接使う。
- プログラム内で直接ファイル I/O、HTTP、DB アクセスを行う。
- グローバル状態を変更する。
- 外部プロセスを起動する。

これらが必要な場合は、**Instruction を介してエージェントに委譲します**。

```python
# NG: プログラム内で時刻を取得する
timestamp = datetime.now().isoformat()  # 再生時に値が変わってしまう

# OK: エージェントに尋ねる
result = yield Instruction(
    text="Please report the current time in ISO 8601 format: {\"timestamp\": \"...\"}",
    expect_schema={"type": "object", "required": ["timestamp"]},
)
timestamp = result["timestamp"]  # JSONL に記録され、再生時も同一になる
```

### 7.2 `or` でデフォルトを与えない

```python
# NG: []、0、"" も「欠損」として扱ってしまう
names = ctx.params.get("names") or ["world"]

# OK: キー自体が欠けているときだけフォールバックする
names = ctx.params.get("names", ["world"])
```

### 7.3 純粋計算で集計する

```python
total_fixed = 0
for round_num in range(1, max_rounds + 1):
    ...
    total_fixed += result["fixed_count"]  # OK: 入力からの純粋計算
```

## 8. プログラムのライフサイクル

```
RUNNING → AWAITING_RESULT → (Done | Abort | exception) → TERMINAL_QUERYABLE
                                                            ↓
                                            (close / TTL / server stop)
                                                            ↓
                                                       ARCHIVED → PRUNED
```

- **Done**: 正常終了。`summary` をユーザーに報告できる。
- **Abort**: 異常だが想定された終了。`reason` がユーザーに表示される。
- **Exception**: 想定外のバグ。状態は `failed` となり、
  `error` 種別の `last_yield` が自動的にエージェントに返される。

## 9. ホットリロードの注意点

`agent-sequencer --watch` で起動すると、`programs/*.py` の変更時に
自動でリロード（レジストリ再スキャン）が行われます。

### モジュールレベルに重い初期化を置かない

`--watch` モードでは、再スキャンのたびにモジュールが再 `compile()` および `exec()` されます。
モジュールレベルでの重い処理（外部サーバーへの接続、巨大ファイルの読み込みなど）は、
リロードのたびに実行されてしまいます。

```python
# NG: モジュール読み込み時に I/O
HEAVY_DATA = open("big.json").read()  # 再スキャンのたびに走る

# OK: 必要なときに run() 内で Instruction を介して尋ねる
def run(ctx):
    result = yield Instruction(text="Please read big.json and report its contents.", ...)
```

### 実行中のインスタンスはリロードの影響を受けない

実行中のインスタンスは `Driver` がすでに捕捉している `run_fn` を保持し続けるため、
途中でファイルを書き換えても **そのインスタンスは旧バージョンで完走します**。
新バージョンは次回の `sequencer_start` から有効になります。

### resume 時の整合性チェック

JSONL の `header.source_hash` は現在のソースのハッシュと比較され、
一致しない場合は resume が `ProgramChanged` エラーで拒否されます。
これは「決定論的再生の前提が崩れた」ことを検知するための仕組みであり、回避手段はありません。

## 9.5 揮発性メモストア（サブエージェント間 IPC）

MCP サーバは小さなインメモリ KV を背後に持つ 4 つの `sequencer_memo_*` ツール
を公開しています。これは **1 つの Instruction の実行中に並列で動くサブエージェ
ント間で中間データをやり取りするため**のもので、サブエージェントが書き込んだ
中間 JSON をオーケストレーターのコンテキストを経由せずに別のサブエージェント
に渡すために使います。

### 9.5.1 ライフサイクル（要暗記）

- インスタンスのメモバケットは **`sequencer_next` 呼び出しの先頭で必ずクリア
  される**（プログラムが進行する直前）。1 つの Instruction を跨いでメモ項目
  を保持する手段はありません。
- `sequencer_close` および TTL アーカイブ時にもバケットは破棄されます。
- ディスクには永続化されず、`sequencer_resume` でも復元されません。

これらの効果により、メモは新規実行でも resume 後でも、Instruction 実行開始時
点で常に空です。プログラム作者が特別な配慮をしなくても、決定論的再生と整合
する仕組みになっています。

### 9.5.2 作成ルール

- **`run(ctx)` の中から `sequencer_memo_*` ツールを呼んではいけません。**
  プログラム内からの呼び出しは I/O であり決定論性を壊します。`ctx` は意図的
  にメモ API を公開していません。
- `Instruction.text` の中で、サブエージェント自身にメモを使うよう指示します。
  オーケストレーター側で既知の `instance_id` とキー命名規則を渡します。例:

  ```
  サブエージェントは指摘ごとの結果をメモに書き込んでください:
    tool: sequencer_memo_set
    instance_id: <オーケストレーターの instance_id>
    key: round{N}/triage/<finding-id>
    value: {triage の JSON}

  集約サブエージェントは
  `sequencer_memo_keys(prefix="round{N}/triage/")` でキー一覧を取り、
  最終的な triage テーブルを生成します。
  ```

- 階層キー（`round1/triage/C-1` 等）を採用し、
  `sequencer_memo_keys(prefix=...)` で集約サブエージェントを駆動できる
  ようにしてください。
- 現ステップを跨いで残す必要があるデータ（ラウンド跨ぎ、resume 跨ぎ）は
  **ファイル**を使ってください（パスをディスクに書き出し、Instruction の
  result でパスを返す）。メモはファイルの代替ではありません。

### 9.5.3 クォータ

- 1 値あたりのバイト上限: デフォルト 1 MiB
  （`AGENT_SEQUENCER_MEMO_VALUE_LIMIT`）。
- 1 インスタンス合計のバイト上限: デフォルト 64 MiB
  （`AGENT_SEQUENCER_MEMO_INSTANCE_LIMIT`）。

いずれも値の JSON エンコード結果の UTF-8 バイト数を計上します。

## 10. プログラムのバンドル化（推奨）

プログラムが外部のスキル、エージェント定義、スクリプトに依存する場合、
**それらの依存物を隣接した `<program-name>/` ディレクトリにまとめ**、
プログラムとそのディレクトリを 1 セットとして他のプロジェクトへ持ち運べるようにします。

参考実装: [`programs/review_rounds/`](../programs/review_rounds/README.md)

```
programs/
├── my_program.py            ← シーケンサプログラム本体
└── my_program/              ← 自己完結バンドル
    ├── README.md
    ├── skills/
    │   └── <skill>.md       ← Instruction text から参照される
    ├── agents/
    │   └── <agent>.md       ← general-purpose にコンテキストとして読み込ませる
    └── scripts/
        └── ...
```

### 注意点

- Claude Code は、バンドル内の `.md` ファイルをスキルやエージェントとして **登録しません**
  （登録されるのは `.claude/skills/<name>/SKILL.md` および `.claude/agents/<name>.md` のみ）。
  バンドル内のファイルは、Instruction text 内で **パスを参照することで** 利用します。
- レジストリは `programs/*.py` の **直下のみ** をスキャンするので、
  `<program-name>/` サブディレクトリは無視されます（衝突の心配なし）。
- バンドル内の `.md` ファイルを変更しても `source_hash` は変化しません
  （ハッシュはプログラムの `.py` 本体に対してのみ取られます）。
  テンプレートを別の `.md` に分離する場合、
  モジュール読み込み時にメモリへロードするのは実用上動作しますが、
  resume 時の整合性チェックでは捕捉できません。
- バンドル内ファイルへのパスは `Path(__file__).parent / "<program-name>"` を起点に組み立て、
  プラグインのインストール場所に依存しないようにしてください
  （`review_rounds.py` の `_BUNDLE` 定数を参照）。

## 11. プロンプト（Instruction.text）の書き方のコツ

`review_rounds.py` の実装から得られた教訓です。

### 11.1 テンプレート定数 + `format()`

f-string を連結するよりも、`textwrap.dedent` + 三重引用符で組み立てたテンプレート定数のほうが、
可読性と保守性に優れます。

```python
import textwrap

_TPL_REVIEW = textwrap.dedent("""\
    [Round {round_num}/{max_rounds} Step 1: {skill_name}]
    Skill: {skill_path}
    Target: {target}
    Report format (JSON): {{"result": <int>}}\
""")

# 呼び出し側
text = _TPL_REVIEW.format(
    round_num=round_num,
    max_rounds=max_rounds,
    skill_name="python-review",
    skill_path=_PYTHON_REVIEW_SKILL,
    target=_TARGET,
)
```

JSON 例の中の `{}` は `{{}}` としてエスケープする必要があります。

### 11.2 装飾を最小化する

プロンプトは AI しか読まないので、`## h1` / `**bold**` / 空行段落は不要です。
`[Step name]` のヘッダ行 1 本と `key: value` 行だけで十分です。

### 11.3 責務分離

スキルは汎用に、対象の絞り込みはプログラム側で行います。`review_rounds.py` の場合:

- `python-review.md` — 汎用の Python レビュースキル（対象範囲は `--target` 引数で渡される）
- `review_rounds.py` — Instruction text 内で
  `--target .claude/skills/agent-sequencer/server` を明示的に渡す

これにより python-review スキルは他のプログラムからも再利用可能なまま保たれます。

## 12. テスト方法

シーケンサプログラムは MCP サーバーを介さずに、**ドライバを直接駆動する** ことで単体テストできます。

```python
from pathlib import Path
from agent_sequencer.registry import ProgramRegistry
from agent_sequencer.runtime import Driver, KIND_DONE, KIND_ABORT

reg = ProgramRegistry([Path("programs").resolve()])
entry = reg.get("my-program")

driver = Driver(entry.run_fn, params={...})
driver.start()

# Step 1: 最初の Instruction が発行されたことを確認
assert driver.last_yield["kind"] == "instruction"
assert "expected text" in driver.last_yield["text"]

# 結果を渡して次のステップへ進める
driver.send({"key": "value"})
assert driver.last_yield["kind"] == "instruction"  # Step 2

# ...

# 終端状態を検証
driver.send({"final": "result"})
assert driver.last_yield["kind"] == KIND_DONE
assert driver.last_yield["summary"]["..."] == ...
```

参考: 動作する実例としてリポジトリの `tests/test_hello.py` および
`tests/test_review_rounds.py` を参照してください。

## 13. 公開前チェックリスト

- [ ] `NAME` / `DESCRIPTION` / `PARAMS_SCHEMA` / `run` がすべて存在する
- [ ] すべての Instruction に `expect_schema` が設定されている（最低限 `required` が指定されている）
- [ ] 終端状態が明示的な `yield Done(summary=...)` または `yield Abort(reason=...)` になっている
- [ ] `time.time()` / `random.*` / 直接 I/O を使用していない
- [ ] デフォルト値は `or` イディオムではなく `ctx.params.get(key, default)` を使っている
- [ ] モジュールレベルに重い初期化がない
- [ ] バンドルが必要な場合、依存物が隣接する `<program-name>/` ディレクトリに集約されている
- [ ] すべての分岐を `Driver` 直接駆動で動作確認済み
