#!/usr/bin/env python3
"""スケジュール JSON を検証し、テンプレートに埋め込んで単一 HTML を出力する。

使い方:
    python3 templates/build.py <スケジュールJSONのパス> [--out-root <出力先ルートの絶対パス>]

やること:
    1. JSON を読み込み、検証する（validate_schedule.py。形の確認 → 辻褄の確認）。
       検証を通らない場合は何も書き出さず、エラーの一覧を表示して終了する。
    2. templates/schedule.template.html を読み込み、
       「差し替え対象ブロック」を SCHEDULE_DOCUMENT で
       置き換えた単一 HTML を組み立てる。
       このスケジュールを作るのに使った定義ファイル（definitions/ 配下）の中身は
       HTML には埋め込まない（配布版の判断。社外に渡すファイルに見積もり基準等の
       内部情報を含めないため）。どの定義バージョンで作ったかは
       meta.definitionsVersion に記録が残る。
    3. <出力先ルート>/<meta.id>/ に、JSON と HTML を対で書き出す。
       出力先ルートは既定でこのスクリプト自身の隣（`output/`）だが、
       `--out-root` を渡すと差し替えられる。プラグインとして配布された場合、
       スキル本体のフォルダ（更新・再インストールで消える可能性がある場所）の外に
       書き出すために使う。
       ファイル名は <meta.id>_r<版数2桁>_<生成日>.{json,html}。
       ファイル名の組み立ては plan_output_paths() の1か所にまとめてある。
    4. <出力先ルート>/<meta.id>/CHANGELOG.md に、今回の版の行を追記する（無ければ新規作成）。

追加のインストールは要らない。標準ライブラリだけで動く。
このスクリプト自身は他のプロジェクトへコピーしても動くよう、
テンプレート・検証処理のパスは自分の置き場所（templates/ の位置）を基準に解決する。
出力先だけは `--out-root` で自分の置き場所の外を指せる。
"""

import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_schedule import validate, collect_warnings  # noqa: E402

TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEMPLATE_DIR)
TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, "schedule.template.html")
OUTPUT_ROOT = os.path.join(PROJECT_ROOT, "output")

START_MARKER = "[ 差し替え対象ブロック ここから ]"
END_MARKER = "[ 差し替え対象ブロック ここまで ]"

SIZE_WARN_BYTES = 500 * 1024  # 500KB。超えたら警告する（処理は止めない）。


class BuildError(Exception):
    """処理を止めて、利用者にそのまま伝えるエラー。"""


def main(argv):
    try:
        json_path, out_root_override = parse_args(argv)
    except BuildError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    if json_path is None:
        print("使い方: python3 build.py <スケジュールJSONのパス> [--out-root <出力先ルートの絶対パス>]", file=sys.stderr)
        return 2

    try:
        doc = load_json(json_path)
        ok, errors = validate(doc)
        if not ok:
            print_errors(errors)
            return 1

        warnings = collect_warnings(doc)

        template_text = load_template()
        json_text = json.dumps(doc, ensure_ascii=False, indent=2)

        html_text, shell_size = embed_data(template_text, json_text)

        out_root = os.path.abspath(out_root_override) if out_root_override else OUTPUT_ROOT
        out_dir, json_filename, html_filename, rNN, date_str = plan_output_paths(doc, out_root)
        json_path_out = os.path.join(out_dir, json_filename)
        html_path_out = os.path.join(out_dir, html_filename)

        refuse_if_exists(json_path_out)
        refuse_if_exists(html_path_out)

        os.makedirs(out_dir, exist_ok=True)

        with open(json_path_out, "w", encoding="utf-8") as f:
            f.write(json_text)
            f.write("\n")

        with open(html_path_out, "w", encoding="utf-8") as f:
            f.write(html_text)

        # 2-9 の守ること 5: 置換後のファイルサイズを確認する。
        # テンプレートの「データ以外の部分（shell_size）」より出力が小さければ、
        # 差し替え対象ブロックの範囲を取り違えて余計に消してしまった可能性がある。
        # （埋め込むデータそのものの大小は、案件ごとに変わってよい）
        html_size = os.path.getsize(html_path_out)
        if html_size < shell_size:
            raise BuildError(
                "出力した HTML が、テンプレートの本体部分より小さくなりました。"
                "差し替え対象ブロックの範囲を取り違えている可能性があるため、処理を止めます。"
                f"（テンプレート本体 {shell_size} バイト / 出力 {html_size} バイト）"
            )

        update_changelog(out_dir, doc, rNN, date_str)

    except BuildError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"エラー: ファイルが見つかりません: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"エラー: JSON として読めませんでした（{e.lineno}行目付近）: {e.msg}", file=sys.stderr)
        return 1

    print("検証を通過しました。")
    if warnings:
        print(f"警告（出力は行った。{len(warnings)}件）:")
        for w in warnings:
            print(f"  - {w}")
    print(f"出力した JSON: {json_path_out}")
    print(f"出力した HTML: {html_path_out}")
    print(f"開くコマンド: open \"{html_path_out}\"")
    print("定義ファイル: HTML には含めない（配布版の既定。definitions/ 配下の中身は埋め込まない）")
    print(f"出力 HTML のサイズ: {html_size} バイト（{html_size / 1024:.1f} KB）")
    if html_size > SIZE_WARN_BYTES:
        print(f"警告: 出力 HTML が {SIZE_WARN_BYTES / 1024:.0f} KB を超えています。", file=sys.stderr)
    return 0


SUPPORTED_OPTIONS = "--out-root <出力先ルートの絶対パス>"


def parse_args(argv):
    """<JSONパス> と、任意の `--out-root <パス>` を読む。
    戻り値は (json_path, out_root_override)。out_root_override は指定が無ければ None。

    知らないオプション（`-` から始まるが --out-root ではないもの）が来た場合は、
    黙って無視せずここで止める。無視して素通りさせると、例えば --out-root を
    打ち間違えた場合に「指定が効かず既定の場所へ書き出す」という、気づきにくい形で
    意図と違う結果になる。
    """
    json_path = None
    out_root_override = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--out-root":
            if i + 1 >= len(argv):
                raise BuildError("--out-root の後ろに出力先ルートのパスを指定してください。")
            out_root_override = argv[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            raise BuildError(
                f"知らないオプションです: {a}\n"
                f"使えるオプション: {SUPPORTED_OPTIONS}"
            )
        json_path = a
        i += 1
    return json_path, out_root_override


def load_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_template():
    if not os.path.exists(TEMPLATE_PATH):
        raise BuildError(
            f"テンプレートが見つかりません（{TEMPLATE_PATH}）。"
            "templates/schedule.template.html を用意してから、もう一度実行してください。"
        )
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def escape_closing_script_tag(text):
    """</script> を含む文字列が埋め込みデータに入っていた場合、
    スクリプトが途中で切れないようエスケープする。
    """
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.IGNORECASE)


def embed_data(template_text, json_text):
    """テンプレートの「差し替え対象ブロック」を SCHEDULE_DOCUMENT で置き換える。
    戻り値は (置き換え後の HTML 全文, テンプレートのデータ以外の部分のバイト数)。
    後者は、置き換えで余計な範囲を消していないかの目安に使う
    （埋め込むデータ自体の大小は案件ごとに違ってよいため、テンプレート全体のサイズとは比べない）。
    """
    lines = template_text.split("\n")
    start_idx = [i for i, line in enumerate(lines) if START_MARKER in line]
    end_idx = [i for i, line in enumerate(lines) if END_MARKER in line]

    if len(start_idx) != 1 or len(end_idx) != 1:
        raise BuildError(
            "テンプレートの中に「差し替え対象ブロック」の目印が、ちょうど1組ありません"
            f"（開始 {len(start_idx)} 個 / 終了 {len(end_idx)} 個）。テンプレートを確認してください。"
        )
    start, end = start_idx[0], end_idx[0]
    if end <= start:
        raise BuildError("「差し替え対象ブロック」の終了の目印が、開始より前にあります。テンプレートを確認してください。")

    old_block = "\n".join(lines[start + 1 : end])
    shell_size = len(template_text.encode("utf-8")) - len(old_block.encode("utf-8"))

    schedule_js = f"var SCHEDULE_DOCUMENT = {json_text};"
    replacement = escape_closing_script_tag(schedule_js)

    new_lines = lines[: start + 1] + [replacement] + lines[end:]
    return "\n".join(new_lines), shell_size


def plan_output_paths(doc, out_root):
    """出力先・ファイル名の決め方はここ1か所にまとめる
    （画面側の saveFileName() と同じ形: <meta.id>_r<版2桁>_<日付>.json/html）。
    out_root は呼び出し側が決めた出力先ルート（既定 OUTPUT_ROOT、または --out-root の値）。
    """
    meta = doc.get("meta") or {}
    meta_id = meta.get("id")
    revision = meta.get("revision")
    if not meta_id or not isinstance(revision, int):
        raise BuildError("meta.id または meta.revision が読み取れません。")

    rNN = f"r{revision:02d}"
    date_str = date.today().isoformat()
    out_dir = os.path.join(out_root, meta_id)
    json_filename = f"{meta_id}_{rNN}_{date_str}.json"
    html_filename = f"{meta_id}_{rNN}_{date_str}.html"
    return out_dir, json_filename, html_filename, rNN, date_str


def refuse_if_exists(path):
    if os.path.exists(path):
        raise BuildError(
            f"{path} はすでにあります。既存ファイルは上書きしません。"
            "改版する場合は meta.revision を上げてから、もう一度実行してください。"
        )


def print_errors(errors):
    print(f"検証で {len(errors)} 件、辻褄の合わない箇所が見つかりました。出力は行いません。", file=sys.stderr)
    for i, e in enumerate(errors[:10], start=1):
        print(f"  {i}. {e}", file=sys.stderr)
    if len(errors) > 10:
        print(f"  ...ほか {len(errors) - 10} 件", file=sys.stderr)


def update_changelog(out_dir, doc, rNN, date_str):
    meta = doc.get("meta") or {}
    title = meta.get("title") or meta.get("id") or "スケジュール"
    change_log = doc.get("changeLog") or []
    revision = meta.get("revision")

    entry = None
    for c in change_log:
        if c.get("revision") == revision:
            entry = c
    if entry is None and change_log:
        entry = change_log[-1]

    date_slash = date_str.replace("-", "/")
    # 初版の「修正指示」欄は常に (初版) にする（30-output-rules.md の CHANGELOG.md 例に合わせる）。
    # instruction フィールド自体には元の依頼文が入っていてよく、それは meta.sourceRequest 側の役割。
    instruction = "(初版)" if revision == 1 else ((entry or {}).get("instruction") or "")
    summary = (entry or {}).get("summary") or ""

    row = f"| {rNN} | {date_slash} | {instruction} | {summary} |"

    path = os.path.join(out_dir, "CHANGELOG.md")
    header = f"# {title} 改版履歴\n\n| 版 | 日付 | 修正指示 | 変更内容 |\n|---|---|---|---|\n"

    if not os.path.exists(path):
        content = header + row + "\n"
    else:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        marker = "|---|---|---|---|\n"
        idx = existing.find(marker)
        if idx == -1:
            # 想定外の形式であれば、壊さずそのまま末尾に積む。
            content = existing.rstrip("\n") + "\n" + row + "\n"
        else:
            insert_at = idx + len(marker)
            content = existing[:insert_at] + row + "\n" + existing[insert_at:]

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
