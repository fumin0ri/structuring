# 仮想ノウハウ20件・共通部分構造探索

Python 3.10以降の標準ライブラリだけで動作します。ネットワーク通信やLLM呼び出しは行いません。

原文・構造化グラフ・期待グループは、この実験用に同じAI作成者が設計した統制データです。自動抽出プロンプトや実データの精度評価ではありません。

## 実行

このフォルダを作業ディレクトリとして実行します。

```powershell
python -X utf8 mine_patterns.py graphs.json --output results/my-run.json
python -X utf8 evaluate.py
python -X utf8 test_miner.py
python -X utf8 write_report.py
```

PythonがPATHにない場合は、インストール済みのPython実行ファイルのパスを指定してください。

mine_patterns.pyは入力ファイルを変更しません。--outputの既存ファイルは上書きします。evaluate.pyはresults内の各比較方式の結果を再生成します。build_dataset.pyは人工データと期待グループを再生成するので、実データのファイルを同じ名前で置いた場合は実行しないでください。

## 入力

graphs.jsonは、knowhow-graph-0.1のJSONオブジェクト20件を配列にしたものです。原文を渡しても構造化は行いません。任意の原文を自動構造化するコードは本実験には含みません。

追加辞書を使ったグラフには、現行プロンプトのadditional_dictionaryと同じ形式（version、concepts）を別ファイルに保存し、--dictionaryを指定します。混在する辞書バージョンは拒否します。辞書の定義が原文に適合するかはプログラムでは判定しません。

```powershell
python -X utf8 mine_patterns.py my-graphs.json --dictionary my-dictionary.json --output results/my-patterns.json
```

## 探索設定

- --max-claims 1 または2：連結する意味関係の数。既定値2。
- --min-support：繰り返し構造として表示する最低文書数。既定値2。
- --profile strict：意味の比較情報と条件を保持する既定方式。ENTITYは名前・種類の一致を必須にせず、一対一の対象変数として対応づけます。その置換の妥当性は別途確認します。
- --profile entity_kind：strictに加えてENTITYの粗い種類も一致必須にします。
- no_concepts、no_polarity、no_conditions、shape_only：情報を意図的に落とす比較実験用です。採用判定には使用しないでください。

QUOTE・原文中の名称・IDを比較キーに使いません。QUOTEは入力検査と根拠保存に使います。パターンIDは登録順に決まるため、入力順を変えると番号は変わることがあります。構造のグループ分けはIDに依存しません。

## 出力

results/strict.jsonには、1文書だけに現れるものも含む全パターンを保存しています。recurring_pattern_idsが最低支持件数を満たすパターンの一覧です。

各パターンに以下を保存します。

- 代表事例のグラフと、比較に使ったmatch_labels。
- 代表のノード・接続から各出現事例への対応表。
- 対応先のENTITYの名前・種類・概念ID。
- 原文引用、文書別支持数、出現回数。

代表グラフのENTITYには元の名前が残っていますが、strictでの比較ラベルはENTITYだけです。代表の具体名がすべての支持事例に当てはまるとは解釈しないでください。

deferredには未登録概念・未解決の限定等により探索を保留した断片を保存します。statsの時間は入力検査と探索を含みますが、ファイル読書き、プロセス起動、原文・注釈作成は含みません。

## ファイル

| ファイル | 内容 |
|---|---|
| 仮想ノウハウ20件.md / corpus.json | 原文20件 |
| graphs.json | 現行仕様に沿って作成した構造化グラフ |
| expected_patterns.json | 探索から分離した期待グループと対照例 |
| build_dataset.py | 今回の指定済み注釈を再生成する補助コード。自動抽出器ではない |
| mine_patterns.py | データに依存しない探索処理と入力契約の検査 |
| evaluate.py | 6方式を実行し、探索後に期待対応と照合 |
| test_miner.py | 19テスト。独立した全順列判定との80組の比較を含む |
| write_report.py | 保存した実測結果から日本語レポートを再生成 |
| 実験レポート.md | 結果と限界の説明 |
| results/ | 検出パターン、対応表、評価、実行ログ |

## 実装の限界

同型判定はラベル・近傍による枝刈り付きの完全バックトラックです。小断片用で、最悪計算量は大きく、1,000件や高密度グラフの速度は検証していません。探索サイズはCLAIM1〜2個に制限しています。

任意サイズの最大共通部分グラフ、gSpan、意味の近さによる概念統合、条件の論理的同値判定、単位換算、原文の意味の正しさの判定は実装していません。表現上の一致を原理の正しさとして採用する機能もありません。
