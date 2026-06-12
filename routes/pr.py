"""
routes/pr.py — 보도자료 생성기 라우트
"""
import json
import os
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from config import KST, PR_RULES_PATH, SHARED_DIR
from db import get_db
from utils import get_claude_client

pr_bp = Blueprint("pr", __name__)

_FACT_DB_PATH = os.path.join(SHARED_DIR, "fact_db.json")
_FORBIDDEN_PATH = os.path.join(SHARED_DIR, "forbidden_words.json")


def _load_fact_db() -> dict:
    try:
        with open(_FACT_DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_pr_rules() -> dict:
    try:
        with open(PR_RULES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_forbidden() -> dict:
    try:
        with open(_FORBIDDEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# PR 유형 → DB 코드 매핑
_PRESS_TYPE_MAP = {
    "서비스 출시 / 론칭":   "product_launch",
    "제휴 · 파트너십 체결": "partnership",
    "서비스 확대 / 지역 확장": "expansion",
    "프로모션 · 이벤트":   "event",
    "투자 유치 / 실적 성과": "achievement",
    "기술 · 솔루션 발표":  "tech",
    "위기 대응":          "crisis",
}
_PR_TYPE_LABEL = {v: k for k, v in _PRESS_TYPE_MAP.items()}

# 국가 이모지 (agent_map과 동일)
_COUNTRY_EMOJI = {
    "vietnam":     "🇻🇳",
    "thailand":    "🇹🇭",
    "japan":       "🇯🇵",
    "taiwan":      "🇹🇼",
    "philippines": "🇵🇭",
    "singapore":   "🇸🇬",
    "hongkong":    "🇭🇰",
    "macau":       "🇲🇴",
    "china":       "🇨🇳",
    "cambodia":    "🇰🇭",
    "mongolia":    "🇲🇳",
    "laos":        "🇱🇦",
    "guam":        "🇬🇺",
    "saipan":      "🇲🇵",
}


# ── 페이지 라우트 ─────────────────────────────────────────────────────────

@pr_bp.route("/pr")
def pr_generator():
    conn   = get_db()
    drafts = conn.execute(
        "SELECT * FROM pr_drafts ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    source_id    = request.args.get("source_id", "")
    source_title = request.args.get("title", "")

    # fact_db에서 국가 목록 추출
    fdb = _load_fact_db()
    countries = [
        {
            "code":   code,
            "name":   info.get("name_ko", code),
            "flag":   _COUNTRY_EMOJI.get(code, "🌐"),
            "active": info.get("launch_status", "서비스 중") == "서비스 중",
            "atm":    bool(info.get("atm")),
            "qr":     bool(info.get("qr_payment")),
        }
        for code, info in fdb.get("countries", {}).items()
    ]
    pr_rules = _load_pr_rules()
    checklist = pr_rules.get("checklist", [])

    return render_template(
        "pr_generator.html",
        drafts=drafts,
        source_id=source_id,
        source_title=source_title,
        countries=countries,
        checklist=checklist,
    )


@pr_bp.route("/pr/drafts")
def pr_drafts_list():
    conn = get_db()
    drafts = conn.execute(
        "SELECT * FROM pr_drafts ORDER BY created_at DESC LIMIT 100"
    ).fetchall()

    # 통계
    stats = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime') THEN 1 ELSE 0 END) AS this_month,
            SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent
        FROM pr_drafts
    """).fetchone()

    conn.close()

    fdb = _load_fact_db()
    countries = [
        {"code": c, "name": info.get("name_ko", c), "flag": _COUNTRY_EMOJI.get(c, "🌐")}
        for c, info in fdb.get("countries", {}).items()
    ]

    return render_template(
        "pr_drafts.html",
        drafts=drafts,
        stats=dict(stats) if stats else {},
        countries=countries,
        country_emoji=_COUNTRY_EMOJI,
        pr_type_label=_PR_TYPE_LABEL,
    )


@pr_bp.route("/pr/drafts/<int:draft_id>")
def pr_draft_detail(draft_id):
    conn = get_db()
    draft = conn.execute("SELECT * FROM pr_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not draft:
        return "초안을 찾을 수 없습니다.", 404

    fdb      = _load_fact_db()
    pr_rules = _load_pr_rules()
    checklist = pr_rules.get("checklist", [])

    country_info = None
    if draft["country"]:
        country_info = fdb.get("countries", {}).get(draft["country"])

    return render_template(
        "pr_draft_detail.html",
        draft=draft,
        checklist=checklist,
        country_info=country_info,
        country_emoji=_COUNTRY_EMOJI,
        pr_type_label=_PR_TYPE_LABEL,
    )


# ── API ──────────────────────────────────────────────────────────────────

@pr_bp.route("/api/pr/candidates")
def api_pr_candidates():
    """소재 후보 게시글 목록 — importance_score >= 7, 최근 30일."""
    limit = int(request.args.get("limit", 30))
    conn  = get_db()
    rows  = conn.execute("""
        SELECT p.id, p.title, p.description, p.cafe_name, p.created_at,
               a.summary, a.category, a.sentiment, a.importance_score
        FROM posts p
        JOIN ai_analysis a ON a.post_id = p.id
        WHERE a.importance_score >= 7
          AND p.created_at >= datetime('now', '-30 days', 'localtime')
          AND (a.is_relevant IS NULL OR a.is_relevant = 1)
        ORDER BY a.importance_score DESC, p.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@pr_bp.route("/api/pr/fact-context")
def api_pr_fact_context():
    """국가 코드로 fact_db 서비스 현황 반환."""
    country = request.args.get("country", "").strip()
    if not country:
        return jsonify({"error": "country 필수"}), 400
    fdb  = _load_fact_db()
    info = fdb.get("countries", {}).get(country)
    if not info:
        return jsonify({"error": "국가 정보 없음"}), 404
    return jsonify({
        "code":        country,
        "name_ko":     info.get("name_ko", country),
        "flag":        _COUNTRY_EMOJI.get(country, "🌐"),
        "qr_payment":  info.get("qr_payment", False),
        "atm":         info.get("atm", False),
        "launch_status": info.get("launch_status", ""),
        "qr_network":  info.get("qr_network", []),
        "atm_network": info.get("atm_network", []),
        "major_cities": info.get("major_cities", []),
        "currency":    info.get("currency", ""),
        "fee_note":    info.get("fee_note", ""),
        "travel_tips": info.get("travel_tips", ""),
    })


@pr_bp.route("/api/pr/stats")
def api_pr_stats():
    """PR 통계 — 에이전트맵 연동용."""
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime') THEN 1 ELSE 0 END) AS this_month,
            SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN approval_status = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM pr_drafts
    """).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {
        "total": 0, "this_month": 0, "approved": 0, "sent": 0, "pending": 0
    })


@pr_bp.route("/api/pr/save", methods=["POST"])
def api_pr_save():
    data = request.get_json(silent=True) or {}
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    # pressType UI값 → DB pr_type 코드 변환
    press_type_raw = data.get("press_type", data.get("pr_type", "general"))
    pr_type = _PRESS_TYPE_MAP.get(press_type_raw, press_type_raw)

    conn = get_db()
    conn.execute("""
        INSERT INTO pr_drafts
            (headline, subheadline, body, key_messages, verify_list,
             source_post_id, pr_type, country, tags, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("headline", ""),
        data.get("subheadline", ""),
        data.get("body", ""),
        data.get("key_messages", ""),
        data.get("verify_list", ""),
        data.get("source_post_id"),
        pr_type,
        data.get("country") or None,
        json.dumps(data.get("tags", []), ensure_ascii=False) if data.get("tags") else None,
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
        "SELECT id, headline, subheadline, verify_list, approval_status, "
        "country, pr_type, sent_at, created_at FROM pr_drafts ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in drafts])


@pr_bp.route("/api/pr/drafts/<int:draft_id>", methods=["PATCH"])
def api_pr_update(draft_id):
    data = request.get_json(silent=True) or {}
    allowed = ("headline", "subheadline", "body", "key_messages", "verify_list",
               "pr_type", "country", "tags")
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "변경할 내용 없음"}), 400
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [now, draft_id]
    conn = get_db()
    conn.execute(f"UPDATE pr_drafts SET {sets}, updated_at=? WHERE id=?", vals)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@pr_bp.route("/api/pr/drafts/<int:draft_id>/send", methods=["POST"])
def api_pr_send(draft_id):
    data  = request.get_json(silent=True) or {}
    conn  = get_db()
    draft = conn.execute("SELECT * FROM pr_drafts WHERE id=?", (draft_id,)).fetchone()
    if not draft:
        conn.close()
        return jsonify({"error": "초안 없음"}), 404

    from services.email_svc import send_pr_draft
    ok, msg = send_pr_draft(dict(draft))

    if ok:
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        recipients = data.get("recipients") or ""
        conn.execute(
            "UPDATE pr_drafts SET sent_at=?, sent_to=?, updated_at=? WHERE id=?",
            (now, recipients, now, draft_id)
        )
        conn.commit()

    conn.close()
    return jsonify({"ok": ok, "msg": msg})


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
    conn.execute(
        "UPDATE pr_drafts SET approval_status='approved', updated_at=? WHERE id=?",
        (now, draft_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "approved"})


@pr_bp.route("/api/pr/drafts/<int:draft_id>/copy-text")
def api_pr_copy_text(draft_id):
    """클립보드용 포맷 텍스트 반환."""
    conn  = get_db()
    draft = conn.execute("SELECT * FROM pr_drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    if not draft:
        return jsonify({"error": "초안 없음"}), 404
    d = dict(draft)
    parts = []
    if d.get("headline"):
        parts.append(d["headline"])
    if d.get("subheadline"):
        parts.append(d["subheadline"])
    if d.get("body"):
        parts.append("\n" + d["body"])
    if d.get("key_messages"):
        parts.append("\n[핵심 메시지]\n" + d["key_messages"])
    return jsonify({"text": "\n".join(parts)})


@pr_bp.route("/api/pr/generate", methods=["POST"])
def api_pr_generate():
    data       = request.get_json(silent=True) or {}
    prompt_raw = data.get("prompt", "").strip()
    country    = data.get("country", "").strip()
    pr_type    = data.get("pr_type", "").strip()
    source_post_id = data.get("source_post_id")

    if not prompt_raw:
        return jsonify({"error": "prompt 필수"}), 400

    # 국가 컨텍스트 주입
    country_ctx = ""
    if country:
        fdb  = _load_fact_db()
        info = fdb.get("countries", {}).get(country, {})
        if info:
            services = []
            if info.get("qr_payment"):
                qr_nets = "·".join(info.get("qr_network", []))
                services.append(f"QR결제({qr_nets or '가능'})")
            if info.get("atm"):
                atm_nets = "·".join(info.get("atm_network", []))
                services.append(f"QR출금({atm_nets or '가능'})")
            city_str = "·".join(info.get("major_cities", []))
            country_ctx = (
                f"\n[{info.get('name_ko', country)} 서비스 현황]\n"
                f"- 서비스: {', '.join(services) if services else '정보 없음'}\n"
                f"- 주요 도시: {city_str or '미정'}\n"
                f"- 통화: {info.get('currency', '')}\n"
                f"- 유의사항: {info.get('fee_note', '')}"
            )

    # 소재 게시글 컨텍스트 주입
    post_ctx = ""
    if source_post_id:
        try:
            conn = get_db()
            row  = conn.execute("""
                SELECT p.title, p.description, a.summary, a.category, a.importance_score
                FROM posts p LEFT JOIN ai_analysis a ON a.post_id = p.id
                WHERE p.id = ?
            """, (source_post_id,)).fetchone()
            conn.close()
            if row:
                post_ctx = (
                    f"\n[소재 게시글]\n"
                    f"- 제목: {row['title']}\n"
                    f"- AI 요약: {row['summary'] or row['description'] or ''}\n"
                    f"- 카테고리: {row['category'] or ''}\n"
                    f"- 중요도: {row['importance_score'] or ''}/10"
                )
        except Exception as e:
            print(f"[PR Generate] 소재 게시글 조회 오류: {e}")

    # pr_rules 컨텍스트
    rules_ctx = ""
    pr_rules  = _load_pr_rules()
    forbidden = _load_forbidden()
    hard_blocks = forbidden.get("hard_block", [])
    if hard_blocks:
        rules_ctx += f"\n[절대 금지어] {', '.join(hard_blocks)}"
    fixed = pr_rules.get("fixed_phrases", {})
    if fixed.get("ceo_quote_suffix"):
        rules_ctx += f"\n[대표 발언 고정 뒷인용구] \"{fixed['ceo_quote_suffix']}\""
    if fixed.get("company_intro"):
        rules_ctx += f"\n[회사 소개 고정 문구] {fixed['company_intro']}"

    full_prompt = prompt_raw
    if country_ctx or post_ctx or rules_ctx:
        full_prompt = (
            prompt_raw
            + "\n\n[참고 컨텍스트 — 아래 정보를 보도자료에 정확히 반영하세요]"
            + country_ctx
            + post_ctx
            + rules_ctx
        )

    try:
        client = get_claude_client()
        msg    = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": full_prompt}]
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
            max_tokens=3000,
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
        current_rules = _load_pr_rules()
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
        client = get_claude_client()
        msg    = client.messages.create(
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
        rules = _load_pr_rules()
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
