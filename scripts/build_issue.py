"""네이버 블로그 RSS를 수집해 조간/석간 호(issue) JSON을 생성한다.

사용:
  python scripts/build_issue.py                      # 현재 KST 기준 자동 판단
  python scripts/build_issue.py --date 2026-11-11 --edition am [--force]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
ISSUES = DATA / "issues"
UA = "Mozilla/5.0 (compatible; econews-bot)"
MODEL = os.environ.get("ECONEWS_MODEL", "claude-haiku-5-5")
MAX_BODY = 6000


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def window_for(date: str, edition: str):
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST)
    if edition == "am":
        return d - timedelta(hours=6), d + timedelta(hours=8)  # 전일 18:00 ~ 당일 08:00
    return d + timedelta(hours=8), d + timedelta(hours=18)  # 당일 08:00 ~ 18:00


def fetch_feed(blog: dict, start, end):
    xml = http_get(f"https://rss.blog.naver.com/{blog['id']}.xml")
    channel = ET.fromstring(xml).find("channel")
    items = []
    for it in channel.findall("item"):
        pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        if not (start <= pub < end):
            continue
        guid = it.findtext("guid") or ""
        m = re.search(r"/(\d+)$", guid)
        items.append({
            "blog_id": blog["id"],
            "blog": blog["name"],
            "log_no": m.group(1) if m else guid,
            "title": (it.findtext("title") or "").strip(),
            "url": guid or it.findtext("link"),
            "published": pub.isoformat(),
            "excerpt": BeautifulSoup(it.findtext("description") or "", "html.parser").get_text(" ", strip=True),
        })
    return items


def fetch_body(item: dict) -> str:
    """글 본문 전체 텍스트(줄 단위). 실패 시 RSS 발췌문으로 대체."""
    url = (f"https://blog.naver.com/PostView.naver?blogId={item['blog_id']}"
           f"&logNo={item['log_no']}&redirect=Dlog&widgetTypeCall=true&directAccess=false")
    try:
        soup = BeautifulSoup(http_get(url), "html.parser")
        node = (soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
                or soup.select_one(".post-view"))
        text = node.get_text("\n", strip=True) if node else ""
        if len(text) > 100:
            return text
    except Exception as e:  # noqa: BLE001
        print(f"  본문 수집 실패({item['url']}): {e}", file=sys.stderr)
    return item["excerpt"]


def extract_comment(body: str, marker: str) -> str:
    """본문에서 marker(예: '한줄 코멘트')로 시작하는 마지막 문단을 원문 그대로 추출."""
    pat = re.compile(r"^\s*" + r"\s?".join(map(re.escape, marker.replace(" ", ""))))
    lines = [ln.replace("​", "").strip() for ln in body.split("\n")]
    lines = [ln for ln in lines if ln]
    idx = [i for i, ln in enumerate(lines) if pat.match(ln)]
    if not idx:
        return ""
    i = idx[-1]
    text = pat.sub("", lines[i]).lstrip(" .:：-")
    # 마커만 있는 줄이면 다음 줄을 본문으로 사용
    j = i + 1
    while len(text) < 5 and j < len(lines):
        text = (text + " " + lines[j]).strip()
        j += 1
    return text


class Summarizer:
    """우선순위: ANTHROPIC_API_KEY(Claude) → GITHUB_TOKEN(GitHub Models) → 발췌문."""

    GH_URL = "https://models.github.ai/inference/chat/completions"

    def __init__(self):
        self.provider = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic
            self.client = anthropic.Anthropic()
            self.provider = "claude"
        elif os.environ.get("GITHUB_TOKEN"):
            self.provider = "github"
            self.gh_models = [m.strip() for m in os.environ.get(
                "ECONEWS_GH_MODELS", "openai/gpt-4.1-mini,openai/gpt-4o-mini").split(",") if m.strip()]
        print(f"요약 방식: {self.provider or '발췌문(대체)'}")

    def _ask_github(self, prompt: str, max_tokens: int) -> str:
        last = None
        for model in self.gh_models:
            body = json.dumps({"model": model, "max_tokens": max_tokens, "temperature": 0.3,
                               "messages": [{"role": "user", "content": prompt}]}).encode()
            req = urllib.request.Request(self.GH_URL, data=body, method="POST", headers={
                "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                "Content-Type": "application/json", "Accept": "application/json"})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=90) as r:
                        j = json.loads(r.read().decode())
                    time.sleep(3)  # 분당 요청 한도 여유
                    return j["choices"][0]["message"]["content"].strip()
                except urllib.error.HTTPError as e:
                    last = f"{model} HTTP {e.code}: {e.read()[:300]!r}"
                    if e.code == 429:
                        time.sleep(30 * (attempt + 1))
                        continue
                    break  # 다른 오류 → 다음 모델
                except Exception as e:  # noqa: BLE001
                    last = f"{model}: {e}"
                    break
        raise RuntimeError(last)

    def _ask(self, prompt: str, max_tokens: int) -> str:
        if self.provider == "github":
            return self._ask_github(prompt, max_tokens)
        msg = self.client.messages.create(
            model=MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text").strip()

    def article(self, item: dict, body: str) -> str:
        body = body[:MAX_BODY]
        if not self.provider:
            return item["excerpt"][:200]
        try:
            return self._ask(
                "다음 블로그 글을 한국어로 3~5문장으로 요약하세요. 핵심 주장과 근거 위주로, "
                "군더더기·인사말 없이 요약문만 출력하세요.\n\n"
                f"제목: {item['title']}\n본문:\n{body}", 600)
        except Exception as e:  # noqa: BLE001
            print(f"  요약 실패({item['url']}): {e}", file=sys.stderr)
            return item["excerpt"][:200]

    def overview(self, articles: list) -> str:
        if not self.provider:
            return ""
        lines = "\n".join(f"- [{a['blog']}] {a['title']}: {a['summary']}" for a in articles)
        try:
            return self._ask(
                "아래는 이번 호 신문에 실린 블로그 글 요약입니다. 전체 흐름을 한국어 2~3문장 총평으로 "
                "써주세요. 총평 본문만 출력하세요.\n\n" + lines, 400)
        except Exception as e:  # noqa: BLE001
            print(f"  총평 실패: {e}", file=sys.stderr)
            return ""


def update_index():
    entries = []
    for p in sorted(ISSUES.glob("*.json"), reverse=True):
        j = json.loads(p.read_text(encoding="utf-8"))
        entries.append({"id": p.stem, "date": j["date"], "edition": j["edition"],
                        "count": len(j["articles"])})
    # 같은 날짜는 석간(pm)이 조간(am)보다 위
    entries.sort(key=lambda e: (e["date"], e["edition"]), reverse=True)
    (DATA / "index.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--edition", choices=["am", "pm"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    now = datetime.now(KST)
    date = a.date or now.strftime("%Y-%m-%d")
    edition = a.edition or ("am" if now.hour < 13 else "pm")
    issue_id = f"{date}-{edition}"
    out = ISSUES / f"{issue_id}.json"
    ISSUES.mkdir(parents=True, exist_ok=True)

    if out.exists() and not a.force:
        print(f"{issue_id} 이미 존재 → 건너뜀")
        return

    start, end = window_for(date, edition)
    print(f"{issue_id}: {start.isoformat()} ~ {end.isoformat()}")
    blogs = json.loads((ROOT / "blogs.json").read_text(encoding="utf-8"))

    items = []
    for b in blogs:
        try:
            got = fetch_feed(b, start, end)
            print(f"  {b['name']}: {len(got)}건")
            items += got
        except Exception as e:  # noqa: BLE001
            print(f"  {b['name']} RSS 실패: {e}", file=sys.stderr)
    items.sort(key=lambda x: x["published"])

    if not items:
        print("새 글 없음 → 호 생성 안 함")
        return

    markers = {b["id"]: b.get("comment_marker") for b in blogs}
    s = Summarizer()
    articles = []
    for it in items:
        body = fetch_body(it)
        art = {
            "blog": it["blog"], "title": it["title"], "url": it["url"],
            "published": it["published"], "summary": s.article(it, body),
        }
        marker = markers.get(it["blog_id"])
        if marker:
            art["comment"] = extract_comment(body, marker)
            if not art["comment"]:
                print(f"  '{marker}' 없음: {it['title']}")
        articles.append(art)

    issue = {
        "id": issue_id, "date": date, "edition": edition,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "generated": now.isoformat(),
        "overview": s.overview(articles),
        "articles": articles,
    }
    out.write_text(json.dumps(issue, ensure_ascii=False, indent=1), encoding="utf-8")
    update_index()
    print(f"{issue_id} 생성 완료 ({len(articles)}건)")


if __name__ == "__main__":
    main()
