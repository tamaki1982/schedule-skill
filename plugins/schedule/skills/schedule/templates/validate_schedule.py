"""スケジュールデータ（schema/schedule.schema.json 準拠の JSON）を検証する。

このファイルは2段の検証を行う。

  1. validate_schema(doc)      -- 形の確認（必須項目・型・選べる値・書式など）
  2. validate_invariants(doc)  -- 辻褄の確認（不変条件 C1〜C19）

どちらもエラーは日本語の平文で返す（SPEC.md 6-13 の言い換え方針に合わせてある）。
外部ライブラリは使わない。標準ライブラリだけで完結する。

使い方:
    from validate_schedule import validate
    ok, errors = validate(doc)
    if not ok:
        for e in errors:
            print(e)

このモジュールは生成側（コマンドラインでの検証・出力前チェック）専用であり、
生成した HTML の中で動く画面側の検証（validateDoc()）とは別実装である。
画面側は projects[] が導入される前の実装のままで、プロジェクト単位の判定
（C1 のプロジェクトをまたいだ一意性、C18、C19 など）に対応していない。
生成側はこのモジュールで、その分もあわせて確認する。
"""

import re
from datetime import date, timedelta

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
STATUS_VALUES = ("notstarted", "inprogress", "done")
COLOR_VALUES = ("accent", "cool", "warm", "purple", "neutral")
ZOOM_VALUES = ("all", "month", "week", "day")
EXPAND_VALUES = ("auto", "milestone", "task")
DEP_TYPES = ("FS", "SS", "FF", "SF")

LABEL = {
    "notstarted": "未着手", "inprogress": "進行中", "done": "完了",
}


# ======================================================================
# 共通のユーティリティ
# ======================================================================

class Path:
    """名前でたどった経路を表す。文字列化すると SPEC 6-13 の形式になる。"""

    def __init__(self, segments=None):
        self.segments = segments or []

    def child(self, kind, name):
        return Path(self.segments + [(kind, name)])

    def field(self, field_label):
        base = self.render()
        if base:
            return base + " の " + field_label
        return field_label

    def render(self):
        return " > ".join(f"{kind}『{name}』" for kind, name in self.segments)

    def __str__(self):
        return self.render()


def is_str(v):
    return isinstance(v, str)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def disp(v):
    if v is None or v == "":
        return "未設定"
    return str(v)


# ======================================================================
# 1. 形の確認（スキーマ相当）
# ======================================================================

def validate_schema(doc):
    """必須項目・型・選べる値・書式を確認する。schema/schedule.schema.json (5.1) に対応する。
    戻り値は日本語のエラー文字列のリスト。空リストなら通過。
    """
    errors = []

    if not isinstance(doc, dict):
        return ["データ全体がオブジェクトの形になっていません。"]

    root_allowed = {"meta", "calendar", "display", "resources", "projects", "risks", "changeLog"}
    _check_unknown_keys(doc, root_allowed, Path(), errors, where="このファイル")

    # --- meta ---
    meta = doc.get("meta")
    if meta is None:
        errors.append("meta（ドキュメントの基本情報）が書かれていません。")
    else:
        _validate_meta(meta, errors)

    # --- calendar ---
    calendar = doc.get("calendar")
    if calendar is None:
        errors.append("calendar（稼働日とタイムラインの範囲）が書かれていません。")
    else:
        _validate_calendar(calendar, errors)

    # --- display（任意） ---
    if "display" in doc and doc["display"] is not None:
        _validate_display(doc["display"], errors)

    # --- resources（任意） ---
    if "resources" in doc and doc["resources"] is not None:
        _validate_resources(doc["resources"], errors)

    # --- projects（必須・1件以上） ---
    projects = doc.get("projects")
    if projects is None:
        errors.append("projects（プロジェクトの一覧）が書かれていません。")
    elif not isinstance(projects, list) or len(projects) == 0:
        errors.append("projects（プロジェクトの一覧）にはプロジェクトが1件以上必要です。いまは0件です。")
    else:
        for i, prj in enumerate(projects):
            _validate_project(prj, i, errors)

    # --- risks（任意） ---
    for i, r in enumerate(doc.get("risks") or []):
        _validate_risk(r, i, errors)

    # --- changeLog（任意） ---
    for i, c in enumerate(doc.get("changeLog") or []):
        _validate_change_log_entry(c, i, errors)

    return errors


def _check_unknown_keys(obj, allowed, path, errors, where=None):
    if not isinstance(obj, dict):
        return
    label = where or str(path)
    for key in obj.keys():
        if key not in allowed:
            errors.append(f"{label}に、いまは使わない項目『{key}』が書かれています。この項目は無くなったので、行ごと削ってください。")


def _require_type(value, expected, field_desc, errors, allow_null=False):
    if value is None and allow_null:
        return True
    ok = {
        "string": is_str, "integer": is_int, "number": is_number,
        "boolean": lambda v: isinstance(v, bool), "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }[expected](value)
    if not ok:
        errors.append(f"{field_desc}の書き方が違います。")
    return ok


def _validate_meta(meta, errors):
    allowed = {
        "schemaVersion", "id", "title", "groupName", "revision", "generatedAt",
        "sourceRequest", "previousRevisionFile", "definitionsVersion", "preset", "assumptions", "notes",
    }
    _check_unknown_keys(meta, allowed, None, errors, where="meta")

    for req in ("schemaVersion", "id", "title", "revision", "generatedAt"):
        if req not in meta:
            errors.append(f"meta に『{req}』が書かれていません。")

    if "id" in meta:
        idv = meta["id"]
        if not is_str(idv) or not ID_RE.match(idv or ""):
            errors.append("meta.id（ドキュメント識別子）は、英小文字・数字・ハイフンだけで書いてください。")
    if "title" in meta:
        if not is_str(meta["title"]) or len(meta["title"]) < 1:
            errors.append("表題（meta.title）が書かれていません。")
    if "groupName" in meta and meta["groupName"] is not None:
        if not is_str(meta["groupName"]) or len(meta["groupName"]) < 1:
            errors.append("まとめた呼び名（meta.groupName）は、書く場合は空にできません。")
    if "revision" in meta:
        if not is_int(meta["revision"]) or meta["revision"] < 1:
            errors.append("meta.revision（版数）は 1 以上の整数で書いてください。")
    if "generatedAt" in meta:
        if not is_str(meta["generatedAt"]) or not DATETIME_RE.match(meta["generatedAt"]):
            errors.append("meta.generatedAt（生成時刻）の書き方が違います。")
    if "previousRevisionFile" in meta and meta["previousRevisionFile"] is not None:
        if not is_str(meta["previousRevisionFile"]):
            errors.append("meta.previousRevisionFile の書き方が違います。")
    if "preset" in meta and meta["preset"] is not None:
        preset = meta["preset"]
        if not isinstance(preset, dict):
            errors.append("meta.preset の書き方が違います。")
        else:
            for req in ("id", "label", "version"):
                if req not in preset:
                    errors.append(f"meta.preset に『{req}』が書かれていません。")
    if "assumptions" in meta and meta["assumptions"] is not None:
        if not isinstance(meta["assumptions"], list) or not all(is_str(a) for a in meta["assumptions"]):
            errors.append("meta.assumptions（前提の一覧）の書き方が違います。文字列の一覧にしてください。")


def _validate_calendar(cal, errors):
    allowed = {"timelineStart", "timelineEnd", "today", "workingDays", "holidays", "exceptionalWorkingDays"}
    _check_unknown_keys(cal, allowed, None, errors, where="calendar")
    for req in ("timelineStart", "timelineEnd", "today"):
        if req not in cal:
            errors.append(f"calendar に『{req}』が書かれていません。")
        elif not is_str(cal[req]) or not DATE_RE.match(cal[req]):
            errors.append(f"calendar.{req} は 2026-11-27 のような形で書いてください。")
    if "workingDays" in cal and cal["workingDays"] is not None:
        wd = cal["workingDays"]
        if not isinstance(wd, list) or not all(is_int(x) and 0 <= x <= 6 for x in wd):
            errors.append("calendar.workingDays（稼働曜日）は 0〜6 の整数の一覧で書いてください。")
        elif len(wd) != len(set(wd)):
            errors.append("calendar.workingDays（稼働曜日）に同じ曜日が重複しています。")
    if "holidays" in cal and cal["holidays"] is not None:
        hd = cal["holidays"]
        if not isinstance(hd, list) or not all(is_str(x) and DATE_RE.match(x) for x in hd):
            errors.append("calendar.holidays（休業日）の書き方が違います。")
    if "exceptionalWorkingDays" in cal and cal["exceptionalWorkingDays"] is not None:
        ex = cal["exceptionalWorkingDays"]
        if not isinstance(ex, list) or not all(is_str(x) and DATE_RE.match(x) for x in ex):
            errors.append("calendar.exceptionalWorkingDays（例外稼働日）の書き方が違います。")
        elif len(ex) != len(set(ex)):
            errors.append("calendar.exceptionalWorkingDays（例外稼働日）に同じ日付が重複しています。")


def _validate_display(disp_obj, errors):
    allowed = {
        "defaultZoom", "defaultProjectId", "defaultPhaseId", "defaultExpandLevel",
        "allowNonWorkingDayPlacement", "nonWorkingDayShading", "barColorKey",
    }
    _check_unknown_keys(disp_obj, allowed, None, errors, where="display")
    if "defaultZoom" in disp_obj and disp_obj["defaultZoom"] is not None:
        if disp_obj["defaultZoom"] not in ZOOM_VALUES:
            errors.append("display.defaultZoom に選べない値が入っています。選べるのは all / month / week / day です。")
    if "defaultProjectId" in disp_obj and disp_obj["defaultProjectId"] is not None:
        v = disp_obj["defaultProjectId"]
        if not is_str(v) or not ID_RE.match(v):
            errors.append("最初に開くプロジェクトの指定（display.defaultProjectId）の書き方が違います。")
    if "defaultPhaseId" in disp_obj and disp_obj["defaultPhaseId"] is not None:
        v = disp_obj["defaultPhaseId"]
        if not is_str(v) or not ID_RE.match(v):
            errors.append("最初に開くフェーズの指定（display.defaultPhaseId）の書き方が違います。")
    if "defaultExpandLevel" in disp_obj and disp_obj["defaultExpandLevel"] is not None:
        if disp_obj["defaultExpandLevel"] not in EXPAND_VALUES:
            errors.append("display.defaultExpandLevel に選べない値が入っています。選べるのは auto / milestone / task です。")
    if "allowNonWorkingDayPlacement" in disp_obj and disp_obj["allowNonWorkingDayPlacement"] is not None:
        if not isinstance(disp_obj["allowNonWorkingDayPlacement"], bool):
            errors.append("display.allowNonWorkingDayPlacement の書き方が違います。true か false で書いてください。")
    if "nonWorkingDayShading" in disp_obj and disp_obj["nonWorkingDayShading"] is not None:
        if not isinstance(disp_obj["nonWorkingDayShading"], bool):
            errors.append("display.nonWorkingDayShading の書き方が違います。true か false で書いてください。")
    if "barColorKey" in disp_obj and disp_obj["barColorKey"] is not None:
        if disp_obj["barColorKey"] not in COLOR_VALUES:
            errors.append("display.barColorKey に選べない値が入っています。")


def _validate_resources(res, errors):
    allowed = {"roles", "maxParallelTracks"}
    _check_unknown_keys(res, allowed, None, errors, where="resources")
    for i, role in enumerate(res.get("roles") or []):
        if not isinstance(role, dict):
            errors.append(f"resources.roles の{i + 1}番目の書き方が違います。")
            continue
        for req in ("id", "label"):
            if req not in role:
                errors.append(f"resources.roles の{i + 1}番目に『{req}』が書かれていません。")
        if "id" in role and (not is_str(role["id"]) or not ID_RE.match(role["id"])):
            errors.append(f"resources.roles の{i + 1}番目の id の書き方が違います。")
    if "maxParallelTracks" in res and res["maxParallelTracks"] is not None:
        if not is_int(res["maxParallelTracks"]) or res["maxParallelTracks"] < 1:
            errors.append("resources.maxParallelTracks は 1 以上の整数で書いてください。")


def _validate_project(prj, i, errors):
    label = prj.get("name") if isinstance(prj, dict) and is_str(prj.get("name")) else f"{i + 1}番目"
    path = Path().child("プロジェクト", label)
    if not isinstance(prj, dict):
        errors.append(f"{path} の書き方が違います。")
        return
    allowed = {
        "id", "name", "order", "code", "status", "goal", "description",
        "ownerRole", "startDate", "endDate", "colorKey", "phases",
    }
    _check_unknown_keys(prj, allowed, path, errors)

    for req in ("id", "name", "order", "phases"):
        if req not in prj:
            errors.append(f"{path} に『{_field_label(req)}』が書かれていません。")

    if "id" in prj and (not is_str(prj["id"]) or not ID_RE.match(prj["id"])):
        errors.append(f"{path} の id の書き方が違います。")
    if "name" in prj and (not is_str(prj["name"]) or len(prj["name"]) < 1):
        errors.append(f"{path} の 名前 が書かれていません。")
    if "order" in prj and (not is_int(prj["order"]) or prj["order"] < 1):
        errors.append(f"{path} の 順番（order） は 1 以上の整数で書いてください。")
    if "colorKey" in prj and prj["colorKey"] is not None and prj["colorKey"] not in COLOR_VALUES:
        errors.append(f"{path} の 色（colorKey） に選べない値が入っています。")
    for f in ("startDate", "endDate"):
        if f in prj and prj[f] is not None and not (is_str(prj[f]) and DATE_RE.match(prj[f])):
            errors.append(f"{path.field(_field_label(f))} は 2026-11-27 のような形で書いてください。")

    phases = prj.get("phases")
    if phases is not None:
        if not isinstance(phases, list) or len(phases) == 0:
            errors.append(f"{path} の フェーズの一覧（phases） には1件以上必要です。")
        else:
            for j, ph in enumerate(phases):
                _validate_phase(ph, j, path, errors)


def _validate_phase(ph, j, parent_path, errors):
    label = ph.get("name") if isinstance(ph, dict) and is_str(ph.get("name")) else f"{j + 1}番目"
    path = parent_path.child("フェーズ", label)
    if not isinstance(ph, dict):
        errors.append(f"{path} の書き方が違います。")
        return
    allowed = {
        "id", "name", "order", "status", "goal", "description",
        "startDate", "endDate", "colorKey", "stages", "milestones",
    }
    _check_unknown_keys(ph, allowed, path, errors)

    for req in ("id", "name", "order"):
        if req not in ph:
            errors.append(f"{path} に『{_field_label(req)}』が書かれていません。")

    has_stages = "stages" in ph and ph["stages"] is not None
    has_milestones = "milestones" in ph and ph["milestones"] is not None
    if has_stages and has_milestones:
        errors.append(f"{path} の中に、工程とマイルストンの両方が書かれています。どちらか一方にしてください。")
    elif not has_stages and not has_milestones:
        errors.append(f"{path} の中に、工程もマイルストンも書かれていません。どちらか一方を入れてください。")
    elif has_stages:
        if not isinstance(ph["stages"], list) or len(ph["stages"]) == 0:
            errors.append(f"{path} の 工程の一覧（stages） には1件以上必要です。")
        else:
            for k, st in enumerate(ph["stages"]):
                _validate_stage(st, k, path, errors)
    elif has_milestones:
        if not isinstance(ph["milestones"], list) or len(ph["milestones"]) == 0:
            errors.append(f"{path} の マイルストンの一覧（milestones） には1件以上必要です。")
        else:
            for k, ms in enumerate(ph["milestones"]):
                _validate_milestone(ms, k, path, errors)

    if "colorKey" in ph and ph["colorKey"] is not None and ph["colorKey"] not in COLOR_VALUES:
        errors.append(f"{path} の 色（colorKey） に選べない値が入っています。")


def _validate_stage(st, k, parent_path, errors):
    label = st.get("name") if isinstance(st, dict) and is_str(st.get("name")) else f"{k + 1}番目"
    path = parent_path.child("工程", label)
    if not isinstance(st, dict):
        errors.append(f"{path} の書き方が違います。")
        return
    allowed = {
        "id", "name", "order", "status", "goal", "description",
        "startDate", "endDate", "colorKey", "milestones",
    }
    _check_unknown_keys(st, allowed, path, errors)
    for req in ("id", "name", "order", "milestones"):
        if req not in st:
            errors.append(f"{path} に『{_field_label(req)}』が書かれていません。")
    milestones = st.get("milestones")
    if milestones is not None:
        if not isinstance(milestones, list) or len(milestones) == 0:
            errors.append(f"{path} の マイルストンの一覧（milestones） には1件以上必要です。")
        else:
            for j, ms in enumerate(milestones):
                _validate_milestone(ms, j, path, errors)


def _validate_milestone(ms, j, parent_path, errors):
    label = ms.get("name") if isinstance(ms, dict) and is_str(ms.get("name")) else f"{j + 1}番目"
    path = parent_path.child("マイルストン", label)
    if not isinstance(ms, dict):
        errors.append(f"{path} の書き方が違います。")
        return
    allowed = {
        "id", "name", "order", "status", "date", "ownerRole", "description",
        "deliverables", "exitCriteria", "dependsOn", "progress", "tasks",
    }
    _check_unknown_keys(ms, allowed, path, errors)
    for req in ("id", "name", "order", "date", "tasks"):
        if req not in ms:
            errors.append(f"{path} に『{_field_label(req)}』が書かれていません。期日が未定の場合は null と書いてください。")
    if "date" in ms and ms["date"] is not None:
        if not (is_str(ms["date"]) and DATE_RE.match(ms["date"])):
            errors.append(f"{path.field('期日')} は 2026-11-27 のような形で書いてください。")
    if "dependsOn" in ms and ms["dependsOn"] is not None:
        if not isinstance(ms["dependsOn"], list) or not all(is_str(x) for x in ms["dependsOn"]):
            errors.append(f"{path.field('先行マイルストン（dependsOn）')} の書き方が違います。")
    tasks = ms.get("tasks")
    if tasks is not None:
        if not isinstance(tasks, list) or len(tasks) == 0:
            errors.append(f"{path} の タスクの一覧（tasks） には1件以上必要です。")
        else:
            for k, tk in enumerate(tasks):
                _validate_task(tk, k, path, errors)


def _validate_task(tk, k, parent_path, errors):
    label = tk.get("name") if isinstance(tk, dict) and is_str(tk.get("name")) else f"{k + 1}番目"
    path = parent_path.child("タスク", label)
    if not isinstance(tk, dict):
        errors.append(f"{path} の書き方が違います。")
        return
    allowed = {
        "id", "name", "order", "status", "startDate", "endDate", "durationDays",
        "effortDays", "assigneeRole", "progress", "dependsOn", "isBuffer",
        "isCriticalPath", "parallelizable", "notes",
    }
    _check_unknown_keys(tk, allowed, path, errors)

    for req in ("id", "name", "order", "status", "startDate", "endDate"):
        if req not in tk:
            errors.append(f"{path} に『{_field_label(req)}』が書かれていません。")

    if "status" in tk:
        if tk["status"] not in STATUS_VALUES:
            errors.append(
                f"{path.field('状態')} に『{disp(tk['status'])}』が入っています。"
                f"未着手として表示しています。選べるのは notstarted（未着手）/ inprogress（進行中）/ done（完了）の3つです。"
            )
    for f in ("startDate", "endDate"):
        if f in tk and tk[f] is not None and not (is_str(tk[f]) and DATE_RE.match(tk[f])):
            errors.append(f"{path.field(_field_label(f))} は 2026-11-27 のような形で書いてください。いまは『{disp(tk.get(f))}』が入っています。")
    if "durationDays" in tk and tk["durationDays"] is not None:
        if not is_int(tk["durationDays"]) or tk["durationDays"] < 0:
            errors.append(f"{path.field('営業日')} は 0 以上の整数で書いてください。")
    if "effortDays" in tk and tk["effortDays"] is not None:
        if not is_number(tk["effortDays"]) or tk["effortDays"] < 0:
            errors.append(f"{path.field('概算工数')} は 0 以上の数値で書いてください。")
    if "isBuffer" in tk and tk["isBuffer"] is not None and not isinstance(tk["isBuffer"], bool):
        errors.append(f"{path.field('予備日かどうか（isBuffer）')} の書き方が違います。")
    if "dependsOn" in tk and tk["dependsOn"] is not None:
        deps = tk["dependsOn"]
        if not isinstance(deps, list):
            errors.append(f"{path.field('依存関係（dependsOn）')} の書き方が違います。")
        else:
            for d in deps:
                if not isinstance(d, dict) or "taskId" not in d:
                    errors.append(f"{path.field('依存関係（dependsOn）')} の書き方が違います。taskId が必要です。")
                    continue
                if "type" in d and d["type"] is not None and d["type"] not in DEP_TYPES:
                    errors.append(f"{path.field('依存関係（dependsOn）')} の種類（type）に選べない値が入っています。")

    # C8 のうち工数と担当は「予備日はタスクの独立分類」であり、schema 上は必須ではなく
    # 不変条件（validate_invariants）側で確認する。


def _validate_risk(r, i, errors):
    if not isinstance(r, dict):
        errors.append(f"risks の{i + 1}番目の書き方が違います。")
        return
    allowed = {"id", "description", "impact", "likelihood", "mitigation", "relatedIds"}
    _check_unknown_keys(r, allowed, None, errors, where=f"risks の{i + 1}番目")
    if "id" not in r:
        errors.append(f"risks の{i + 1}番目に『id』が書かれていません。")
    if "relatedIds" in r and r["relatedIds"] is not None:
        if not isinstance(r["relatedIds"], list) or not all(is_str(x) for x in r["relatedIds"]):
            errors.append(f"risks の{i + 1}番目の relatedIds の書き方が違います。")


def _validate_change_log_entry(c, i, errors):
    if not isinstance(c, dict):
        errors.append(f"changeLog の{i + 1}番目の書き方が違います。")
        return
    allowed = {"revision", "date", "instruction", "summary", "changes"}
    _check_unknown_keys(c, allowed, None, errors, where=f"changeLog の{i + 1}番目")
    for req in ("revision", "date", "summary"):
        if req not in c:
            errors.append(f"changeLog の{i + 1}番目に『{req}』が書かれていません。")


_FIELD_LABELS = {
    "id": "id", "name": "名前", "order": "順番", "phases": "フェーズの一覧",
    "milestones": "マイルストンの一覧", "tasks": "タスクの一覧", "date": "期日",
    "startDate": "開始日", "endDate": "終了日", "status": "状態",
}


def _field_label(f):
    return _FIELD_LABELS.get(f, f)


# ======================================================================
# 2. 辻褄の確認（不変条件 C1〜C19）
# ======================================================================

def validate_invariants(doc):
    """schema を満たしていることを前提に、不変条件 C1〜C19 を確認する。
    戻り値は日本語のエラー文字列のリスト。空リストなら通過。
    """
    errors = []

    all_ids = {}          # id -> (kind_label, name, owner_project_name)
    dup_ids = set()
    tasks_by_id = {}
    milestones_by_id = {}
    projects_by_id = {}
    phases_by_id = {}
    project_of = {}        # 任意の id -> 所属プロジェクト id（プロジェクト自身は自分自身）

    calendar = doc.get("calendar") or {}
    working_days = set(calendar.get("workingDays") or [1, 2, 3, 4, 5])
    holidays = set(calendar.get("holidays") or [])
    # 版13.0: 稼働日 = (workingDays に含まれる曜日 かつ holidays に含まれない日) ∪ exceptionalWorkingDays。
    # exceptionalWorkingDays は holidays より優先する。省略時は空集合で、挙動は本項目が無かった頃と同じ。
    # C5 と C9 はどちらも _is_working_day() だけを見るので、この集合はここ1か所で作れば両方に効く。
    exceptional_working_days = set(calendar.get("exceptionalWorkingDays") or [])
    timeline_start = _parse_date(calendar.get("timelineStart"))
    timeline_end = _parse_date(calendar.get("timelineEnd"))

    projects = doc.get("projects") or []

    def register_id(id_value, kind_label, name, project_id):
        if not id_value:
            return
        if id_value in all_ids:
            dup_ids.add(id_value)
        else:
            all_ids[id_value] = (kind_label, name, project_id)
        project_of[id_value] = project_id

    # --- C11: order の連番（プロジェクト） ---
    _check_order_sequence(projects, "プロジェクト", Path(), errors)

    for prj in projects:
        if not isinstance(prj, dict):
            continue
        pid = prj.get("id")
        pname = prj.get("name") or "?"
        projects_by_id[pid] = prj
        register_id(pid, "プロジェクト", pname, pid)
        prj_path = Path().child("プロジェクト", pname)

        phases = prj.get("phases") or []
        _check_order_sequence(phases, "フェーズ", prj_path, errors)

        for ph in phases:
            if not isinstance(ph, dict):
                continue
            phid = ph.get("id")
            phname = ph.get("name") or "?"
            phases_by_id[phid] = ph
            register_id(phid, "フェーズ", phname, pid)
            ph_path = prj_path.child("フェーズ", phname)

            if "stages" in ph and ph["stages"]:
                _check_order_sequence(ph["stages"], "工程", ph_path, errors)
                for st in ph["stages"]:
                    if not isinstance(st, dict):
                        continue
                    stid = st.get("id")
                    stname = st.get("name") or "?"
                    register_id(stid, "工程", stname, pid)
                    st_path = ph_path.child("工程", stname)
                    mss = st.get("milestones") or []
                    _check_order_sequence(mss, "マイルストン", st_path, errors)
                    for ms in mss:
                        _collect_milestone(ms, st_path, pid, register_id,
                                            milestones_by_id, tasks_by_id, errors)
            elif "milestones" in ph and ph["milestones"]:
                mss = ph["milestones"]
                _check_order_sequence(mss, "マイルストン", ph_path, errors)
                for ms in mss:
                    _collect_milestone(ms, ph_path, pid, register_id,
                                        milestones_by_id, tasks_by_id, errors)

    # --- C1: id の重複（プロジェクトをまたいでも） ---
    for dup in sorted(dup_ids):
        kind, name, _ = all_ids[dup]
        owners = sorted({projects_by_id.get(project_of.get(dup), {}).get("name", "?")})
        errors.append(
            f"id『{dup}』が2か所以上で使われています。id はファイル全体で1つだけにしてください。"
        )

    # --- C6 / C7: タスクの依存関係（参照先の実在・循環） ---
    _check_task_dependencies(tasks_by_id, errors)

    # --- C6: マイルストンの dependsOn ---
    for ms_id, (ms, path) in milestones_by_id.items():
        for dep in (ms.get("dependsOn") or []):
            if dep not in milestones_by_id:
                errors.append(f"{path.field('先行マイルストン（dependsOn）')} が指している『{dep}』が見つかりません。")

    # --- C6: risks.relatedIds ---
    for i, r in enumerate(doc.get("risks") or []):
        for rel in (r.get("relatedIds") or []):
            if rel not in all_ids:
                errors.append(f"risks の{i + 1}番目（{r.get('id', '?')}）の relatedIds が指している『{rel}』が見つかりません。")

    # --- C2 / C3 / C5 / C9: タスクの日付の妥当性 ---
    for tid, (tk, path) in tasks_by_id.items():
        _check_task_dates(tk, path, working_days, holidays, exceptional_working_days, timeline_start, timeline_end, errors)

    # --- C8: 予備日 ---
    for tid, (tk, path) in tasks_by_id.items():
        if tk.get("isBuffer"):
            if tk.get("effortDays") not in (0, 0.0, None):
                errors.append(f"予備日『{tk.get('name', '?')}』（{path}）に 概算工数 が入っています。予備日の工数は 0 にしてください。")
            if tk.get("assigneeRole"):
                errors.append(f"予備日『{tk.get('name', '?')}』（{path}）に 担当役割 が入っています。予備日には担当を付けないでください。")

    # --- C4: マイルストンの期日が配下タスクの最遅終了日以降 ---
    for ms_id, (ms, path) in milestones_by_id.items():
        ms_date = _parse_date(ms.get("date"))
        if ms_date is None:
            continue
        latest = None
        for tk in (ms.get("tasks") or []):
            end = _parse_date(tk.get("endDate"))
            if end and (latest is None or end > latest):
                latest = end
        if latest and ms_date < latest:
            errors.append(
                f"{path.field('期日')} が {latest.isoformat()}（配下タスクの最遅終了日）より前になっています。"
                f"マイルストンの期日は、配下タスクの最遅終了日以降にしてください。"
            )

    # --- C10: 同一役割の重なり（プロジェクト内に限る） ---
    for prj in projects:
        if not isinstance(prj, dict):
            continue
        pname = prj.get("name") or "?"
        by_role = {}
        for ph in (prj.get("phases") or []):
            for tk, path in _iter_tasks_in_phase(ph, Path().child("プロジェクト", pname).child("フェーズ", ph.get("name") or "?")):
                role = tk.get("assigneeRole")
                if not role or tk.get("isBuffer"):
                    continue
                s = _parse_date(tk.get("startDate"))
                e = _parse_date(tk.get("endDate"))
                if not s or not e:
                    continue
                by_role.setdefault(role, []).append((s, e, tk.get("name"), path))
        for role, spans in by_role.items():
            spans.sort()
            for a, b in zip(spans, spans[1:]):
                if a[1] >= b[0]:
                    errors.append(
                        f"プロジェクト『{pname}』の中で、役割『{role}』のタスク『{a[2]}』と『{b[2]}』の期間が重なっています。"
                    )

    # --- C13: 親の宣言期間を配下がはみ出す ---
    for prj in projects:
        if not isinstance(prj, dict):
            continue
        pname = prj.get("name") or "?"
        prj_path = Path().child("プロジェクト", pname)
        p_start = _parse_date(prj.get("startDate"))
        p_end = _parse_date(prj.get("endDate"))
        child_start, child_end = _derive_range(prj.get("phases") or [], "phase")
        _check_containment(p_start, p_end, child_start, child_end, prj_path, errors)
        for ph in (prj.get("phases") or []):
            ph_path = prj_path.child("フェーズ", ph.get("name") or "?")
            ph_start = _parse_date(ph.get("startDate"))
            ph_end = _parse_date(ph.get("endDate"))
            kids = ph.get("stages") or ph.get("milestones") or []
            kind = "stage" if ph.get("stages") else "milestone"
            c_start, c_end = _derive_range(kids, kind)
            _check_containment(ph_start, ph_end, c_start, c_end, ph_path, errors)
            if ph.get("stages"):
                for st in ph["stages"]:
                    st_path = ph_path.child("工程", st.get("name") or "?")
                    st_start = _parse_date(st.get("startDate"))
                    st_end = _parse_date(st.get("endDate"))
                    ms_start, ms_end = _derive_range(st.get("milestones") or [], "milestone")
                    _check_containment(st_start, st_end, ms_start, ms_end, st_path, errors)

    # --- C15 / C16 / C17: display の参照先 ---
    display = doc.get("display") or {}
    dpid = display.get("defaultProjectId")
    dphid = display.get("defaultPhaseId")
    if dpid and dpid not in projects_by_id:
        errors.append(f"最初に開くプロジェクトとして『{dpid}』が指定されていますが、そのプロジェクトがありません。")
    if dphid and dphid not in phases_by_id:
        errors.append(f"最初に開くフェーズとして『{dphid}』が指定されていますが、そのフェーズがありません。")
    if dpid and dphid and dphid in phases_by_id and dpid in projects_by_id:
        if project_of.get(dphid) != dpid:
            errors.append(
                f"最初に開くプロジェクトの指定（『{dpid}』）と、最初に開くフェーズの指定（『{dphid}』）が、別のプロジェクトを指しています。"
            )

    # --- C18 / C19: 表題と groupName ---
    meta = doc.get("meta") or {}
    n_projects = len(projects)
    group_name = meta.get("groupName")
    if n_projects == 1:
        if group_name:
            errors.append("プロジェクトが1件なのに、まとめた呼び名（meta.groupName）が書かれています。1件のときは書かないでください。")
        expected_title = (projects[0].get("name") or "") + " スケジュール" if projects else None
    elif n_projects >= 2:
        if not group_name:
            errors.append("プロジェクトが2件以上あるのに、まとめた呼び名（meta.groupName）が書かれていません。")
        expected_title = (group_name + " スケジュール") if group_name else None
    else:
        expected_title = None

    actual_title = meta.get("title")
    if expected_title and actual_title != expected_title:
        errors.append(
            f"表題（meta.title）が階層と合っていません。「{expected_title}」であるべきですが、いまは「{disp(actual_title)}」です。"
        )

    # 同じ文面が複数経路から重複して積まれることがある（例: 循環依存を複数の起点から検出した場合）ので、
    # 出現順を保ったまま重複だけ落とす。
    seen = set()
    deduped = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            deduped.append(e)
    return deduped


def _collect_milestone(ms, parent_path, project_id, register_id, milestones_by_id, tasks_by_id, errors):
    if not isinstance(ms, dict):
        return
    msid = ms.get("id")
    msname = ms.get("name") or "?"
    register_id(msid, "マイルストン", msname, project_id)
    ms_path = parent_path.child("マイルストン", msname)
    milestones_by_id[msid] = (ms, ms_path)
    tasks = ms.get("tasks") or []
    _check_order_sequence(tasks, "タスク", ms_path, errors)
    for tk in tasks:
        if not isinstance(tk, dict):
            continue
        tid = tk.get("id")
        tname = tk.get("name") or "?"
        register_id(tid, "タスク", tname, project_id)
        tk_path = ms_path.child("タスク", tname)
        tasks_by_id[tid] = (tk, tk_path)


def _iter_tasks_in_phase(ph, ph_path):
    out = []
    if ph.get("stages"):
        for st in ph["stages"]:
            st_path = ph_path.child("工程", st.get("name") or "?")
            for ms in (st.get("milestones") or []):
                ms_path = st_path.child("マイルストン", ms.get("name") or "?")
                for tk in (ms.get("tasks") or []):
                    out.append((tk, ms_path.child("タスク", tk.get("name") or "?")))
    elif ph.get("milestones"):
        for ms in ph["milestones"]:
            ms_path = ph_path.child("マイルストン", ms.get("name") or "?")
            for tk in (ms.get("tasks") or []):
                out.append((tk, ms_path.child("タスク", tk.get("name") or "?")))
    return out


def _check_order_sequence(items, kind_label, parent_path, errors):
    orders = []
    for it in items:
        if isinstance(it, dict) and is_int(it.get("order")):
            orders.append(it["order"])
    expected = list(range(1, len(items) + 1))
    if sorted(orders) != expected:
        where = str(parent_path) if str(parent_path) else "ドキュメント"
        errors.append(f"{where} の中の{kind_label}の 順番（order） が、1から始まる連番になっていません。")


def _check_task_dependencies(tasks_by_id, errors):
    # C6: 参照先の実在
    for tid, (tk, path) in tasks_by_id.items():
        for dep in (tk.get("dependsOn") or []):
            target = dep.get("taskId") if isinstance(dep, dict) else None
            if target and target not in tasks_by_id:
                errors.append(f"{path.field('依存関係（dependsOn）')} が指している『{target}』が見つかりません。")

    # C7: 循環の検出（深さ優先探索）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in tasks_by_id}

    def visit(tid, stack):
        if color.get(tid) == BLACK:
            return
        if color.get(tid) == GRAY:
            cycle = " → ".join(stack[stack.index(tid):] + [tid])
            errors.append(f"依存関係が循環しています（{cycle}）。")
            return
        color[tid] = GRAY
        tk, _ = tasks_by_id.get(tid, (None, None))
        if tk:
            for dep in (tk.get("dependsOn") or []):
                target = dep.get("taskId") if isinstance(dep, dict) else None
                if target and target in tasks_by_id:
                    visit(target, stack + [tid])
        color[tid] = BLACK

    for tid in list(tasks_by_id.keys()):
        if color.get(tid) == WHITE:
            visit(tid, [])


def _check_task_dates(tk, path, working_days, holidays, exceptional_working_days, timeline_start, timeline_end, errors):
    s = _parse_date(tk.get("startDate"))
    e = _parse_date(tk.get("endDate"))

    if (s is None) != (e is None):
        errors.append(f"{path} の 開始日 と 終了日 は、片方が未設定ならもう片方も未設定にしてください。")
    if s and e and s > e:
        errors.append(f"{path} の 開始日 が 終了日 より後になっています。")

    for label, d in (("開始日", s), ("終了日", e)):
        if d is None:
            continue
        if timeline_start and d < timeline_start:
            errors.append(f"{path.field(label)} が、タイムラインの範囲（{timeline_start.isoformat()} 以降）より前です。")
        if timeline_end and d > timeline_end:
            errors.append(f"{path.field(label)} が、タイムラインの範囲（{timeline_end.isoformat()} 以前）より後です。")
        # 例外稼働日（この計画ではその日だけ働く、と決めた日）は holidays より優先するので、
        # 稼働日の判定そのものが true になる。その場合は下の2つの理由を出さない。
        if not _is_working_day(d, working_days, holidays, exceptional_working_days):
            if d.weekday() not in _iso_to_python_weekdays(working_days):
                errors.append(f"{path.field(label)}（{d.isoformat()}）が稼働日ではありません。")
            if d.isoformat() in holidays:
                errors.append(f"{path.field(label)}（{d.isoformat()}）が休業日に指定されています。")

    if s and e and tk.get("durationDays") is not None:
        actual = _count_working_days(s, e, working_days, holidays, exceptional_working_days)
        if tk["durationDays"] != actual:
            errors.append(
                f"{path.field('営業日')} が、開始日〜終了日から数えた実際の稼働日数（{actual}日）と一致していません"
                f"（いまの値は {tk['durationDays']}日）。"
            )


def _iso_to_python_weekdays(working_days_iso):
    # calendar.workingDays は 0=日〜6=土。Python date.weekday() は 0=月〜6=日。
    out = set()
    for d in working_days_iso:
        out.add((d - 1) % 7)
    return out


def _is_working_day(d, working_days_iso, holidays, exceptional_working_days=None):
    """稼働日かどうかの判定は、C5（日付が稼働日か）と C9（durationDays の一致）の両方が
    ここだけを見る。稼働日の定義（版13.0）:
        ( workingDays に含まれる曜日 かつ holidays に含まれない日 ) ∪ exceptionalWorkingDays
    exceptionalWorkingDays は holidays より優先する。省略時（exceptional_working_days が空）は
    この関数が無かった頃とまったく同じ判定になる。
    """
    iso = d.isoformat()
    if exceptional_working_days and iso in exceptional_working_days:
        return True
    py_wd = _iso_to_python_weekdays(working_days_iso)
    return d.weekday() in py_wd and iso not in holidays


def _count_working_days(start, end, working_days_iso, holidays, exceptional_working_days=None):
    n = 0
    d = start
    one = timedelta(days=1)
    while d <= end:
        if _is_working_day(d, working_days_iso, holidays, exceptional_working_days):
            n += 1
        d += one
    return n


def _derive_range(children, kind):
    starts, ends = [], []
    for c in children:
        if not isinstance(c, dict):
            continue
        if kind == "milestone":
            d = _parse_date(c.get("date"))
            if d:
                starts.append(d)
                ends.append(d)
            for tk in (c.get("tasks") or []):
                s = _parse_date(tk.get("startDate"))
                e = _parse_date(tk.get("endDate"))
                if s:
                    starts.append(s)
                if e:
                    ends.append(e)
        else:
            s = _parse_date(c.get("startDate"))
            e = _parse_date(c.get("endDate"))
            if s:
                starts.append(s)
            if e:
                ends.append(e)
            if kind == "stage":
                for ms in (c.get("milestones") or []):
                    cs, ce = _derive_range([ms], "milestone")
                    if cs:
                        starts.append(cs)
                    if ce:
                        ends.append(ce)
            if kind == "phase":
                sub = c.get("stages") or c.get("milestones") or []
                subkind = "stage" if c.get("stages") else "milestone"
                cs, ce = _derive_range(sub, subkind)
                if cs:
                    starts.append(cs)
                if ce:
                    ends.append(ce)
    return (min(starts) if starts else None, max(ends) if ends else None)


def _check_containment(decl_start, decl_end, child_start, child_end, path, errors):
    if decl_start and child_start and child_start < decl_start:
        errors.append(f"{path} の 開始日 の宣言（{decl_start.isoformat()}）を、配下の実際の開始（{child_start.isoformat()}）が前へはみ出しています。")
    if decl_end and child_end and child_end > decl_end:
        errors.append(f"{path} の 終了日 の宣言（{decl_end.isoformat()}）を、配下の実際の終了（{child_end.isoformat()}）が後ろへはみ出しています。")


def _parse_date(s):
    if not s or not isinstance(s, str) or not DATE_RE.match(s):
        return None
    y, m, d = s.split("-")
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


# ======================================================================
# 公開 API
# ======================================================================

def validate(doc):
    """schema → 不変条件 の順に確認する。戻り値は (ok, errors)。
    schema の段階でエラーがあれば、不変条件の確認は行わない
    （構造が壊れていると、C1〜C19 の判定自体が信用できないため）。
    """
    schema_errs = validate_schema(doc)
    if schema_errs:
        return False, schema_errs
    inv_errs = validate_invariants(doc)
    return (len(inv_errs) == 0), inv_errs


def collect_warnings(doc):
    """検証には落とさないが、利用者に伝えたほうがよい状態を集める。
    戻り値は日本語の文字列のリスト（0件のこともある）。

    ここに入るのは「正当に起きうる状態」であって不正データではない
    （`20-scheduling-rules.md` 第7章）。エラーとは別の扱いにする。
    """
    warnings = []
    calendar = doc.get("calendar") or {}
    today = _parse_date(calendar.get("today"))
    start = _parse_date(calendar.get("timelineStart"))
    end = _parse_date(calendar.get("timelineEnd"))
    if today and start and end and not (start <= today <= end):
        warnings.append(
            "「今日」（calendar.today）がタイムラインの範囲外です。画面に「今日」の線は表示されません。"
            "着手が数か月先の計画では正常に起きる状態です。"
        )
    return warnings
