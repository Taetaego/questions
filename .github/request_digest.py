"""요청 페이지 모아 알림. 두 곳에서 부른다(2026-11-21 저자 "내가 선택한 시간에 정확하게"):
  - 맥 launchd가 1분마다(`901_도구/질문 페이지 틀/요청 알림 켜기.sh`로 맥마다 한 번 켠다) ← 정시에 맞추는 쪽
  - GitHub Actions가 15분마다(.github/workflows/request-digest.yml) ← 맥이 다 꺼져 있을 때 대신. GitHub는 몇 시간씩 늦게 돌릴 때가 있다(11-21 13시 몫을 20:42에 돌림)

하는 일: 저장소의 요청 페이지(index.html에 `var KIND = 'request'`)마다
  1. Firebase pages/<PAGE>/settings/hours("9,18" 꼴, 한국 시간 정시)를 읽는다. 비어 있으면 건너뜀.
  2. 지금보다 앞선 가장 최근 정한 시각(slot)을 찾는다. pages/<PAGE>/digest/sent(마지막으로 처리한 slot)가 그것과 같으면 끝.
  3. (sent 또는 바로 앞 slot) < 요청 시각 <= slot 이고 아직 '새 요청'(status new) 상태인 것만 골라, 1개 이상이면 ntfy로 한 번 보낸다(요청 글은 보내지 않고 개수만).
     이미 '하는 중'·'끝'으로 바꾼 요청은 알리지 않는다(2026-11-21 저자 "이미 수정하고 끝냈는데 새 요청이라고 옴").
  4. 보내기 전에 sent = slot 을 '아무도 먼저 안 적었을 때만' 적는다(Firebase ETag). 맥 두 대와 GitHub가 같은 분에 돌아도 한 곳만 보낸다.
     새 요청이 없어도 적는다. 보내기에 실패하면 sent 를 되돌려 다음 번에 다시 보낸다.
손으로 돌리기: python3 -I .github/request_digest.py [보기]   ← '보기'면 보내지도 적지도 않고 결과만 찍는다.
"""
import json, os, re, sys, urllib.error, urllib.request
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


def get_tag(db, path):
    """값과 ETag(그 칸이 지금 이 값일 때만 쓰게 하는 표)를 함께 읽는다."""
    r = urllib.request.Request(f"{db}/{path}.json", headers={"X-Firebase-ETag": "true"})
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read() or b"null"), f.headers["ETag"]


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
    sent, etag = get_tag(db, base + "/digest/sent")
    sent = sent or 0
    if sent >= slot:
        return f"{page}: 이번 시각({datetime.fromtimestamp(slot / 1000, KST):%m-%d %H시}) 이미 처리"
    start = sent if sent else (past[-2] if len(past) > 1 else slot - 86400000)
    reqs = get(db, base + "/requests") or {}
    fresh = lambda r: isinstance(r, dict) and (r.get("status") or "new") == "new"
    new = [r for r in reqs.values() if fresh(r) and start < (r.get("at") or 0) <= slot]
    left = sum(1 for r in reqs.values() if fresh(r))
    msg = f"{page}: 처리 안 된 새 요청 {len(new)}개, 전체 처리 안 된 요청 {left}개"
    if DRY:
        return msg + " (보기: 안 보냄)"
    url = f"{db}/{base}/digest/sent.json"
    try:
        req(url, data=str(slot).encode(), method="PUT", headers={"Content-Type": "application/json", "if-match": etag})
    except urllib.error.HTTPError as e:
        if e.code == 412:
            return f"{page}: 다른 곳(다른 맥·GitHub)이 먼저 처리"
        raise
    if new and ntfy:
        owner = os.environ.get("GITHUB_REPOSITORY", "taetaego/questions").split("/")
        link = f"https://{owner[0].lower()}.github.io/{owner[1]}/{page_dir.name}/"
        body = {"topic": ntfy, "title": title, "message": f"처리 안 된 새 요청 {len(new)}개 · 전체 처리 안 된 요청 {left}개", "tags": ["inbox_tray"], "click": link}
        try:
            req("https://ntfy.sh/", data=json.dumps(body, ensure_ascii=False).encode(), method="POST", headers={"Content-Type": "application/json"})
        except Exception:
            req(url, data=str(sent).encode(), method="PUT", headers={"Content-Type": "application/json"})  # 다음 번에 다시 보내게 되돌림
            raise
        msg += " → 알림 보냄"
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
