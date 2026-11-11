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
    """글 본문 텍스트. 실패 시 RSS 발췌문으로 대체."""
    url = (f"https://blog.naver.com/PostView.naver?blogId={item['blog_id']}"
           f"&logNo={item['log_no']}&redirect=Dlog&widgetTypeCall=true&directAccess=false")
    try:
        soup = BeautifulSoup(http_get(url), "html.parser")
        node = (soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
                or soup.select_one(".post-view"))
        text = node.get_text("\n", strip=True) if node else ""
        if len(text) > 100:
            return text[:MAX_BODY]
    except Exception as e:  # noqa: BLE001
        print(f"  본문 수집 실패({item['url']}): {e}", file=sys.stderr)
    return item["excerpt"]


class Summarizer:
    def __init__(self):
        key = os.environ.get("ANTHROPIC_API_KEY")
        self.client = None
        if key:
            import anthropic
            self.client = anthropic.Anthropic(api_key=key)
        else:
            print("ANTHROPIC_API_KEY 없음 → 발췌문으로 대체", file=sys.stderr)

    def _ask(self, prompt: str, max_tokens: int) -> str:
        msg = self.client.messages.create(
            model=MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if b.type == "text").strip()

    def article(self, item: dict, body: str) -> str:
        if not self.client:
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
        if not self.client:
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

    s = Summarizer()
    articles = []
    for it in items:
        body = fetch_body(it)
        articles.append({
            "blog": it["blog"], "title": it["title"], "url": it["url"],
            "published": it["published"], "summary": s.article(it, body),
        })

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
