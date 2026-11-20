"""요청 페이지 모아 알림(GitHub Actions가 15분마다 부른다, .github/workflows/request-digest.yml).

하는 일: 저장소의 요청 페이지(index.html에 `var KIND = 'request'`)마다
  1. Firebase pages/<PAGE>/settings/hours("9,18" 꼴, 한국 시간 정시)를 읽는다. 비어 있으면 건너뜀.
  2. 지금보다 앞선 가장 최근 정한 시각(slot)을 찾는다. pages/<PAGE>/digest/sent(마지막으로 처리한 slot)가 그것과 같으면 끝.
  3. (sent 또는 바로 앞 slot) < 요청 시각 <= slot 인 새 요청 수를 세어, 1개 이상이면 ntfy로 한 번 보낸다(요청 글은 보내지 않고 개수만).
  4. sent = slot 으로 적는다(새 요청이 없어도). 그래서 Actions가 늦거나 몇 번 건너뛰어도 빠짐·겹침이 없다.
손으로 돌리기: python3 -I .github/request_digest.py [보기]   ← '보기'면 보내지도 적지도 않고 결과만 찍는다.
"""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
DRY = "보기" in sys.argv[1:]


def var(html, name):
    m = re.search(r"var %s = '((?:[^'\\]|\\.)*)';" % name, html)
    return m.group(1).replace("\\'", "'").replace("\\\\", "\\") if m else ""


def req(url, data=None, method="GET", headers=None):
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=20) as f:
        return f.read()


def get(db, path):
    return json.loads(req(f"{db}/{path}.json") or b"null")


def slots(hours, now):
    """now 앞뒤 사흘의 정한 시각들(ms, 오름차순)."""
    out = []
    today = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    for dd in (-2, -1, 0):
        for h in hours:
            out.append(int((today + timedelta(days=dd, hours=h)).timestamp() * 1000))
    return sorted(out)


def run(page_dir, now):
    html = (page_dir / "index.html").read_text(encoding="utf-8")
    title, page, db, ntfy = var(html, "TITLE"), var(html, "PAGE"), var(html, "DB_URL").rstrip("/"), var(html, "NTFY")
    base = f"pages/{page}"
    hours = sorted({int(x) for x in (get(db, base + "/settings/hours") or "").split(",") if x.strip().isdigit() and int(x) < 24})
    if not hours:
        return f"{page}: 알림 시간 없음"
    nowms = int(now.timestamp() * 1000)
    past = [s for s in slots(hours, now) if s <= nowms]
    slot = past[-1]
    sent = get(db, base + "/digest/sent") or 0
    if sent >= slot:
        return f"{page}: 이번 시각({datetime.fromtimestamp(slot / 1000, KST):%m-%d %H시}) 이미 처리"
    start = sent if sent else (past[-2] if len(past) > 1 else slot - 86400000)
    reqs = get(db, base + "/requests") or {}
    new = [r for r in reqs.values() if isinstance(r, dict) and start < (r.get("at") or 0) <= slot]
    left = sum(1 for r in reqs.values() if isinstance(r, dict) and r.get("status") != "done")
    msg = f"{page}: 새 요청 {len(new)}개, 남은 요청 {left}개"
    if DRY:
        return msg + " (보기: 안 보냄)"
    if new and ntfy:
        owner = os.environ.get("GITHUB_REPOSITORY", "taetaego/questions").split("/")
        link = f"https://{owner[0].lower()}.github.io/{owner[1]}/{page_dir.name}/"
        body = {"topic": ntfy, "title": title, "message": f"새 요청 {len(new)}개 · 남은 요청 {left}개", "tags": ["inbox_tray"], "click": link}
        req("https://ntfy.sh/", data=json.dumps(body, ensure_ascii=False).encode(), method="POST", headers={"Content-Type": "application/json"})
        msg += " → 알림 보냄"
    req(f"{db}/{base}/digest/sent.json", data=str(slot).encode(), method="PUT", headers={"Content-Type": "application/json"})
    return msg


def main():
    now = datetime.now(timezone.utc)
    pages = [p.parent for p in sorted(ROOT.glob("*/index.html")) if "var KIND = 'request'" in p.read_text(encoding="utf-8")]
    if not pages:
        print("요청 페이지 없음")
    bad = 0
    for p in pages:
        try:
            print(run(p, now))
        except Exception as e:  # 한 페이지가 실패해도 다른 페이지는 돈다
            bad += 1
            print(f"{p.name}: 실패 {e!r}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
