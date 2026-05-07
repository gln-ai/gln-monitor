"""
routes/pr.py — 보도자료 생성기 라우트
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from config import KST, PR_RULES_PATH
from db import get_db
from utils import get_claude_client

pr_bp = Blueprint("pr", __name__)


@pr_bp.route("/pr")
def pr_generator():
    conn   = get_db()
    drafts = conn.execute(
        "SELECT * FROM pr_drafts ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template("pr_generator.html", drafts=drafts)


@pr_bp.route("/pr/drafts")
def pr_drafts_list():
    conn = get_db()
    drafts = conn.execute(
        "SELECT * FROM pr_drafts ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return render_template("pr_drafts.html", drafts=drafts)


@pr_bp.route("/pr/drafts/<int:draft_id>")
def pr_draft_detail(draft_id):
    conn = get_db()
    draft = conn.execute("SELECT * FROM pr_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not draft:
        return "초안을 찾을 수 없습니다.", 404
    return render_template("pr_draft_detail.html", draft=draft)


@pr_bp.route("/api/pr/save", methods=["POST"])
def api_pr_save():
    data = request.get_json(silent=True) or {}
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute("""
        INSERT INTO pr_drafts (headline, subheadline, body, key_messages, verify_list, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("headline", ""),
        data.get("subheadline", ""),
        data.get("body", ""),
        data.get("key_messages", ""),
        data.get("verify_list", ""),
        now, now,
    ))
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"id": row_id, "saved_at": now})


@pr_bp.route("/api/pr/drafts")
def api_pr_list():
    conn   = get_db()
    drafts = conn.execute(
        "SELECT id, headline, subheadline, verify_list, approval_status, created_at FROM pr_drafts ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in drafts])


@pr_bp.route("/api/pr/drafts/<int:draft_id>", methods=["DELETE"])
def api_pr_delete(draft_id):
    conn = get_db()
    conn.execute("DELETE FROM pr_drafts WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


@pr_bp.route("/api/pr/drafts/<int:draft_id>/approve", methods=["POST"])
def api_pr_approve(draft_id):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute("UPDATE pr_drafts SET approval_status='approved', updated_at=? WHERE id=?", (now, draft_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "approved"})


@pr_bp.route("/api/pr/generate", methods=["POST"])
def api_pr_generate():
    data   = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt 필수"}), 400
    try:
        client = get_claude_client()
        msg    = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"result": msg.content[0].text})
    except Exception as e:
        print(f"[PR Generate 오류] {e}")
        return jsonify({"error": str(e)}), 500


@pr_bp.route("/api/pr/revise", methods=["POST"])
def api_pr_revise():
    data   = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt 필수"}), 400
    try:
        client = get_claude_client()
        msg    = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"result": msg.content[0].text})
    except Exception as e:
        print(f"[PR Revise 오류] {e}")
        return jsonify({"error": str(e)}), 500


@pr_bp.route("/api/pr/learn", methods=["POST"])
def api_pr_learn():
    data    = request.get_json(silent=True) or {}
    article = data.get("article", "").strip()
    note    = data.get("note", "").strip()
    scopes  = data.get("scopes", {})
    if not article:
        return jsonify({"error": "article 필수"}), 400

    try:
        with open(PR_RULES_PATH, encoding="utf-8") as f:
            current_rules = json.load(f)
    except Exception:
        current_rules = {}

    scope_lines = []
    if scopes.get("forbidden", True):
        scope_lines.append("- forbidden: 금지어·치환 규칙 (기존에 없는 새 패턴 또는 위반 발견 시)")
    if scopes.get("phrase", True):
        scope_lines.append("- phrase: 재사용 가치 있는 표현·고정 문구")
    if scopes.get("naming", True):
        scope_lines.append("- naming: 고유명사·브랜드명 표기 패턴")
    if scopes.get("checklist", True):
        scope_lines.append("- checklist: 체크리스트에 추가할 새 항목")

    prompt = f"""너는 GLN인터내셔널 보도자료 규칙 관리자야.
아래 [완성 기사]를 분석해서 [현행 규칙]에 추가할 가치가 있는 새 규칙 후보를 JSON으로만 반환해줘.

[분석 범위]
{chr(10).join(scope_lines)}

[현행 규칙 요약]
forbidden_replacements: {json.dumps([r['from'] for r in current_rules.get('forbidden_replacements', [])], ensure_ascii=False)}
fixed_phrases keys: {list(current_rules.get('fixed_phrases', {}).keys())}
naming_rules: {json.dumps(current_rules.get('naming_rules', {}), ensure_ascii=False)}
checklist 항목 수: {len(current_rules.get('checklist', []))}개

[담당자 메모]
{note or '없음'}

[완성 기사]
{article}

[응답 형식 — JSON 배열만, 다른 텍스트 없이]
[
  {{
    "type": "forbidden|phrase|naming|checklist",
    "display": "화면에 보여줄 설명 (예: \\"ATM인출\\" → \\"QR출금\\" 추가 필요)",
    "reason": "추가 이유 한 줄",
    "data": {{
      // forbidden: {{"from": "...", "to": "...", "reason": "..."}}
      // phrase:    {{"key": "...", "value": "..."}}
      // naming:    {{"key": "...", "value": "..."}}
      // checklist: {{"item": "...", "required": true}}
    }}
  }}
]

규칙:
- 현행 규칙에 이미 있는 항목은 제외
- 실제로 기사에서 발견되거나 이 기사로부터 학습할 수 있는 것만 포함
- 없으면 빈 배열 [] 반환
- summary 키를 배열 앞에 추가하지 말고, 반드시 배열만 반환"""

    try:
        client      = get_claude_client()
        msg         = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        suggestions = json.loads(text)

        counts = {}
        for s in suggestions:
            t = s.get("type", "기타")
            counts[t] = counts.get(t, 0) + 1
        type_labels = {"forbidden": "금지어", "phrase": "재사용 표현", "naming": "명칭 규칙", "checklist": "체크리스트"}
        summary_parts = [f"{type_labels.get(t, t)} {n}건" for t, n in counts.items()]
        summary = f"총 {len(suggestions)}건 발견: " + ", ".join(summary_parts) if suggestions else ""

        return jsonify({"suggestions": suggestions, "summary": summary})
    except Exception as e:
        print(f"[PR Learn 오류] {e}")
        return jsonify({"error": str(e)}), 500


@pr_bp.route("/api/pr/rules/update", methods=["POST"])
def api_pr_rules_update():
    data        = request.get_json(silent=True) or {}
    suggestions = data.get("suggestions", [])
    if not suggestions:
        return jsonify({"error": "suggestions 필수"}), 400

    try:
        with open(PR_RULES_PATH, encoding="utf-8") as f:
            rules = json.load(f)
    except Exception as e:
        return jsonify({"error": f"pr_rules.json 읽기 실패: {e}"}), 500

    saved = 0
    for s in suggestions:
        t     = s.get("type")
        sdata = s.get("data", {})
        try:
            if t == "forbidden":
                existing = [r["from"] for r in rules.get("forbidden_replacements", [])]
                if sdata.get("from") and sdata["from"] not in existing:
                    rules.setdefault("forbidden_replacements", []).append(sdata)
                    saved += 1
            elif t == "phrase":
                key = sdata.get("key")
                if key and key not in rules.get("fixed_phrases", {}):
                    rules.setdefault("fixed_phrases", {})[key] = sdata.get("value", "")
                    saved += 1
            elif t == "naming":
                key = sdata.get("key")
                if key and key not in rules.get("naming_rules", {}):
                    rules.setdefault("naming_rules", {})[key] = sdata.get("value", "")
                    saved += 1
            elif t == "checklist":
                existing_items = [c["item"] for c in rules.get("checklist", [])]
                if sdata.get("item") and sdata["item"] not in existing_items:
                    rules.setdefault("checklist", []).append(sdata)
                    saved += 1
        except Exception as e:
            print(f"[Rules Update] 항목 오류: {e}")

    rules["last_updated"] = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        with open(PR_RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"error": f"pr_rules.json 저장 실패: {e}"}), 500

    print(f"[PR Rules] {saved}건 업데이트 완료")
    return jsonify({"saved": saved, "total": len(suggestions)})
