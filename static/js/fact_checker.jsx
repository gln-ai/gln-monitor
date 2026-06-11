const { useState, useCallback } = React;

const INITIAL_COUNTRIES = [
  { id: 1,  name: "베트남",      region: "동남아", qr: true,  atm: true,  active: true  },
  { id: 2,  name: "태국",        region: "동남아", qr: true,  atm: true,  active: true  },
  { id: 3,  name: "일본",        region: "동북아", qr: true,  atm: true,  active: true  },
  { id: 4,  name: "홍콩",        region: "동북아", qr: true,  atm: false, active: true  },
  { id: 5,  name: "중국·마카오", region: "동북아", qr: true,  atm: false, active: true  },
  { id: 6,  name: "필리핀",      region: "동남아", qr: true,  atm: false, active: true  },
  { id: 7,  name: "라오스",      region: "동남아", qr: true,  atm: false, active: true  },
  { id: 8,  name: "대만",        region: "동북아", qr: true,  atm: false, active: true  },
  { id: 9,  name: "몽골",        region: "기타",   qr: true,  atm: false, active: true  },
  { id: 10, name: "싱가포르",    region: "동남아", qr: true,  atm: false, active: true  },
  { id: 11, name: "캄보디아",    region: "동남아", qr: true,  atm: false, active: true  },
  { id: 12, name: "괌",          region: "기타",   qr: true,  atm: false, active: true  },
  { id: 13, name: "사이판",      region: "기타",   qr: true,  atm: false, active: true  },
  { id: 14, name: "말레이시아",  region: "동남아", qr: true,  atm: false, active: true  },
];

const REGION_COLORS = {
  "동남아": { bg: "#E6F1FB", text: "#0C447C", border: "#85B7EB" },
  "동북아": { bg: "#EEEDFE", text: "#3C3489", border: "#AFA9EC" },
  "기타":   { bg: "#F1EFE8", text: "#444441", border: "#B4B2A9" },
};

function generatePrompt(countries) {
  const active = countries.filter(c => c.active);
  const atmList = active.filter(c => c.atm).map(c => c.name).join(", ");
  const qrOnlyList = active.filter(c => c.qr && !c.atm).map(c => c.name).join(", ");
  const inactive = countries.filter(c => !c.active).map(c => c.name);

  return `당신은 GLN 콘텐츠 팩트 체커입니다.
(주)지엘엔인터내셔널(GLN International)이 운영하는 해외 여행자용 QR 결제·ATM 출금 플랫폼 GLN의
모든 마케팅 콘텐츠가 발행 전 반드시 당신을 통과해야 합니다.

당신의 유일한 역할은 사실 정확성과 금칙 위반 여부를 판단하는 것입니다.
콘텐츠를 개선하거나 수정안을 제시하지 마세요. 오직 판단만 합니다.

---

## 판정 등급

- GREEN: 사실 오류 없음, 금칙 위반 없음 → 발행 가능
- YELLOW: 확인 권고 항목 있음 → 담당자 검토 후 발행 결정
- RED: 하드 블로킹 이슈 → 수정 전 발행 불가, 즉시 반려

RED 항목이 하나라도 있으면 전체 등급은 RED.
RED 없이 YELLOW만 있으면 전체 등급은 YELLOW.

---

## 검증 기준 1 — 국가별 서비스 지원 현황 [자동 생성 → 임의 편집 금지]

### ATM 출금 지원 국가 (${active.filter(c=>c.atm).length}개국)
${atmList}

### QR 결제만 지원, ATM 없음 (${active.filter(c=>c.qr&&!c.atm).length}개국)
${qrOnlyList}
${inactive.length > 0 ? `
### 서비스 중단 국가 (콘텐츠 언급 금지)
${inactive.join(", ")}
` : ""}
### ATM RED 판정 규칙
ATM 미지원 국가(${atmList} 제외 전체)에서
아래 표현이 하나라도 등장하면 즉시 RED:
- "ATM", "현금 출금", "현금 인출", "ATM에서 뽑다", "현금 뽑기"
- GLN으로 현금을 인출하는 행위를 묘사하는 모든 표현
단, "ATM 지원 안됨", "ATM 미지원" 같은 안내성 표현은 예외(YELLOW).

### QR RED 판정 규칙
현 서비스 국가 목록에 없는 나라에서 GLN 결제가 된다고 암시하면 RED.
${inactive.length > 0 ? `서비스 중단 국가(${inactive.join(", ")})에서 GLN 서비스 언급 시 RED.` : ""}

---

## 검증 기준 2 — 금칙어 및 과장 표현

### RED (즉시 반려)
- 수수료 수치 직접 명시: "수수료 X%", "X원", "0원 수수료" 등 어떤 수치도
- "무료" 단독 사용: "수수료 무료", "무료로 결제" 등
- "무제한" 표현: "무제한 결제", "한도 없이" 등
- 경쟁사 직접 비방: 특정 경쟁 서비스를 부정적으로 직접 비교
- ATM 미지원 국가에서 ATM 출금 긍정 묘사

### YELLOW (확인 권고)
- "저렴한 수수료": 수치 없지만 비교 우위 암시
- "빠른 환율", "유리한 환율": 근거 없는 우위 표현
- 특정 가게·장소명과 GLN 결제 가능 여부 단정
- 목록에 없는 신규 국가·가맹점 언급

---

## 검증 기준 3 — 면책 문구

모든 콘텐츠에 아래 문구 또는 축약형이 반드시 포함되어야 합니다.
"정확한 수수료와 이용 가능 여부는 GLN 앱에서 확인하세요. 현지 사정에 따라 서비스가 제한될 수 있습니다."
면책 문구 없으면: YELLOW

---

## 검증 기준 4 — 브랜드 정보

- 브랜드명: GLN / GLN International / 퍼플GLN
- 운영사: (주)지엘엔인터내셔널 (하나은행 자회사)
- 지원 플랫폼: iOS, Android (PC 불가)
- 파트너 앱: 토스, 네이버페이, 카카오페이, 삼성페이, 하나은행, 하나머니, 하나카드, KB스타뱅킹, iM뱅크

---

## 출력 포맷 (이 형식 외 출력 금지)

---
[GLN 팩트 체크 검증 결과]

최종 등급: [GREEN / YELLOW / RED]
검증 채널: [메인채널 / 고라니채널]
검증 포맷: [블로그 / 인스타 / 카드뉴스 / 스레드 / 릴스 / 숏츠]
대상 국가: [국가명]

[RED 항목] → RED 없으면 생략
- (번호). (위반 내용) | (위치) | 근거: (해당 규칙)

[YELLOW 항목] → YELLOW 없으면 생략
- (번호). (확인 권고 내용) | (위치) | 권고 이유

[GREEN 확인]
- 국가·서비스 정보: [이상 없음 / N건 지적]
- 금칙어: [이상 없음 / N건 지적]
- 면책 문구: [포함됨 / 미포함 → 추가 필요]
- 브랜드 정보: [이상 없음 / N건 확인 필요]

처리 권고: [발행 가능 / 담당자 검토 후 결정 / 즉시 반려 후 재작성]
---

---

## 금지 사항

- 콘텐츠 수정안 제시 금지
- 칭찬·격려 등 정치 문장 금지
- 추측 판정 금지 (불확실하면 YELLOW)
- 정해진 출력 포맷 외 형식 사용 금지`;
}

function App() {
  const [countries, setCountries] = useState(INITIAL_COUNTRIES);
  const [newName, setNewName] = useState("");
  const [newRegion, setNewRegion] = useState("동남아");
  const [newAtm, setNewAtm] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [lastUpdated] = useState(new Date().toLocaleDateString("ko-KR"));

  const toggle = useCallback((id, field) => {
    setCountries(prev => prev.map(c =>
      c.id === id ? { ...c, [field]: !c[field] } : c
    ));
  }, []);

  const addCountry = useCallback(() => {
    if (!newName.trim()) return;
    setCountries(prev => [...prev, {
      id: Date.now(),
      name: newName.trim(),
      region: newRegion,
      qr: true,
      atm: newAtm,
      active: true,
    }]);
    setNewName("");
    setNewAtm(false);
  }, [newName, newRegion, newAtm]);

  const removeCountry = useCallback((id) => {
    setCountries(prev => prev.filter(c => c.id !== id));
  }, []);

  const prompt = generatePrompt(countries);

  const copyPrompt = () => {
    navigator.clipboard.writeText(prompt).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const activeCountries = countries.filter(c => c.active);
  const atmCount = activeCountries.filter(c => c.atm).length;
  const qrOnlyCount = activeCountries.filter(c => c.qr && !c.atm).length;
  const inactiveCount = countries.filter(c => !c.active).length;

  const regions = ["동남아", "동북아", "기타"];

  return (
    <div style={{ fontFamily: "'Pretendard', 'Apple SD Gothic Neo', sans-serif", padding: "0 0 2rem", color: "var(--color-text-primary)" }}>

      {/* 헤더 */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "4px" }}>
          <h1 style={{ fontSize: "18px", fontWeight: 600, margin: 0 }}>GLN 서비스 현황 관리</h1>
          <span style={{ fontSize: "11px", color: "var(--color-text-tertiary)" }}>팩트 체크 프롬프트 자동 생성</span>
        </div>
        <p style={{ fontSize: "12px", color: "var(--color-text-secondary)", margin: 0 }}>
          국가를 추가·삭제하면 시스템 프롬프트가 자동으로 반영됩니다 · 기준일 {lastUpdated}
        </p>
      </div>

      {/* 요약 카드 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px", marginBottom: "1.5rem" }}>
        {[
          { label: "전체 국가", value: countries.length, color: "var(--color-text-primary)" },
          { label: "ATM 지원", value: atmCount, color: "var(--color-text-success)" },
          { label: "QR 전용", value: qrOnlyCount, color: "var(--color-text-info)" },
          { label: "서비스 중단", value: inactiveCount, color: "var(--color-text-danger)" },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ background: "var(--color-background-secondary)", borderRadius: "8px", padding: "12px 14px" }}>
            <div style={{ fontSize: "11px", color: "var(--color-text-secondary)", marginBottom: "4px" }}>{label}</div>
            <div style={{ fontSize: "22px", fontWeight: 600, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* 국가 매트릭스 */}
      {regions.map(region => {
        const regionCountries = countries.filter(c => c.region === region);
        if (!regionCountries.length) return null;
        const rc = REGION_COLORS[region];
        return (
          <div key={region} style={{ marginBottom: "1.25rem" }}>
            <div style={{ fontSize: "11px", fontWeight: 600, color: rc.text, background: rc.bg, border: `0.5px solid ${rc.border}`, borderRadius: "6px", padding: "3px 10px", display: "inline-block", marginBottom: "8px" }}>
              {region}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(148px, 1fr))", gap: "7px" }}>
              {regionCountries.map(c => (
                <div key={c.id} style={{
                  background: "var(--color-background-primary)",
                  border: `0.5px solid ${c.active ? "var(--color-border-tertiary)" : "var(--color-border-danger)"}`,
                  borderRadius: "10px",
                  padding: "10px 11px",
                  opacity: c.active ? 1 : 0.55,
                  position: "relative",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: c.active ? "var(--color-text-primary)" : "var(--color-text-danger)" }}>{c.name}</span>
                    <button onClick={() => removeCountry(c.id)} style={{ border: "none", background: "none", cursor: "pointer", padding: "0", fontSize: "14px", color: "var(--color-text-tertiary)", lineHeight: 1 }} title="삭제">
                      <i className="ti ti-x" aria-label="삭제" />
                    </button>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
                    {[
                      { field: "qr",     label: "QR결제",  on: c.qr  },
                      { field: "atm",    label: "ATM출금", on: c.atm },
                      { field: "active", label: "서비스중", on: c.active },
                    ].map(({ field, label, on }) => (
                      <button
                        key={field}
                        onClick={() => toggle(c.id, field)}
                        style={{
                          display: "flex", alignItems: "center", justifyContent: "space-between",
                          border: "none", background: "none", cursor: "pointer", padding: "0",
                          width: "100%",
                        }}
                      >
                        <span style={{ fontSize: "11px", color: "var(--color-text-secondary)" }}>{label}</span>
                        <span style={{
                          display: "inline-block", width: "28px", height: "15px",
                          borderRadius: "99px", position: "relative",
                          background: on
                            ? (field === "active" ? "var(--color-text-success)" : field === "atm" ? "#7F77DD" : "var(--color-text-info)")
                            : "var(--color-border-secondary)",
                          transition: "background .15s",
                          flexShrink: 0,
                        }}>
                          <span style={{
                            position: "absolute", top: "2px", left: on ? "15px" : "2px",
                            width: "11px", height: "11px", borderRadius: "50%",
                            background: "#fff", transition: "left .15s",
                          }} />
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* 국가 추가 */}
      <div style={{ background: "var(--color-background-secondary)", borderRadius: "10px", padding: "14px 16px", marginBottom: "1.5rem" }}>
        <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--color-text-secondary)", marginBottom: "10px", textTransform: "uppercase", letterSpacing: ".05em" }}>국가 추가</div>
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === "Enter" && addCountry()}
            placeholder="국가명"
            style={{ flex: "1", minWidth: "100px", fontSize: "13px", padding: "6px 10px", border: "0.5px solid var(--color-border-secondary)", borderRadius: "8px", background: "var(--color-background-primary)", color: "var(--color-text-primary)" }}
          />
          <select value={newRegion} onChange={e => setNewRegion(e.target.value)} style={{ fontSize: "13px", padding: "6px 10px", border: "0.5px solid var(--color-border-secondary)", borderRadius: "8px", background: "var(--color-background-primary)", color: "var(--color-text-primary)" }}>
            <option>동남아</option>
            <option>동북아</option>
            <option>기타</option>
          </select>
          <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "12px", color: "var(--color-text-secondary)", cursor: "pointer" }}>
            <input type="checkbox" checked={newAtm} onChange={e => setNewAtm(e.target.checked)} style={{ width: "14px", height: "14px" }} />
            ATM 지원
          </label>
          <button onClick={addCountry} style={{ fontSize: "12px", padding: "6px 14px", fontWeight: 500, background: "#7000FC", color: "#fff", border: "none", borderRadius: "8px", cursor: "pointer" }}>
            <i className="ti ti-plus" aria-hidden="true" style={{ marginRight: "4px" }} />추가
          </button>
        </div>
      </div>

      {/* 프롬프트 섹션 */}
      <div style={{ border: ".5px solid var(--color-border-secondary)", borderRadius: "12px", overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: ".5px solid var(--color-border-tertiary)", background: "var(--color-background-secondary)" }}>
          <div>
            <span style={{ fontSize: "13px", fontWeight: 600 }}>시스템 프롬프트</span>
            <span style={{ fontSize: "11px", color: "var(--color-text-tertiary)", marginLeft: "8px" }}>국가 정보 자동 반영됨</span>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button onClick={() => setShowPrompt(v => !v)} style={{ fontSize: "12px", padding: "5px 12px", background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: "8px", cursor: "pointer", color: "var(--color-text-secondary)" }}>
              {showPrompt ? "접기" : "미리보기"}
            </button>
            <button onClick={copyPrompt} style={{ fontSize: "12px", padding: "5px 12px", fontWeight: 500, color: copied ? "var(--color-text-success)" : "#7000FC", border: `.5px solid ${copied ? "var(--color-border-success)" : "#C4B5FD"}`, borderRadius: "8px", background: "var(--color-background-primary)", cursor: "pointer" }}>
              <i className={`ti ti-${copied ? "check" : "copy"}`} aria-hidden="true" style={{ marginRight: "4px" }} />
              {copied ? "복사됨!" : "복사"}
            </button>
          </div>
        </div>

        {showPrompt && (
          <pre style={{
            margin: 0, padding: "14px 16px",
            fontSize: "11px", lineHeight: "1.7",
            fontFamily: "var(--font-mono)",
            color: "var(--color-text-secondary)",
            background: "var(--color-background-primary)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            maxHeight: "360px", overflowY: "auto",
          }}>
            {prompt}
          </pre>
        )}

        {/* 변경 요약 */}
        <div style={{ padding: "10px 16px", background: "var(--color-background-secondary)", borderTop: ".5px solid var(--color-border-tertiary)", display: "flex", gap: "16px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "11px", color: "var(--color-text-tertiary)" }}>
            ATM 지원: <strong style={{ color: "var(--color-text-primary)" }}>{countries.filter(c=>c.active&&c.atm).map(c=>c.name).join(", ") || "없음"}</strong>
          </span>
          <span style={{ fontSize: "11px", color: "var(--color-text-tertiary)" }}>
            QR 전용: <strong style={{ color: "var(--color-text-primary)" }}>{countries.filter(c=>c.active&&c.qr&&!c.atm).map(c=>c.name).join(", ") || "없음"}</strong>
          </span>
          {inactiveCount > 0 && (
            <span style={{ fontSize: "11px", color: "var(--color-text-danger)" }}>
              중단: <strong>{countries.filter(c=>!c.active).map(c=>c.name).join(", ")}</strong>
            </span>
          )}
        </div>
      </div>

      <p style={{ fontSize: "11px", color: "var(--color-text-tertiary)", marginTop: "10px", marginBottom: 0 }}>
        복사 후 Claude 대화의 System Prompt에 붙여넣기하면 됩니다.
      </p>
    </div>
  );
}

const _root = ReactDOM.createRoot(document.getElementById("app-root"));
_root.render(<App />);
