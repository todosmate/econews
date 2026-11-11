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
        text = ""
        if node:
            # 글자색·굵기 등으로 나뉜 <span> 조각이 줄바꿈으로 끊기지 않도록 문단(<p>) 단위로 합친다.
            for br in node.find_all("br"):
                br.replace_with("\n")
            for s in node.find_all(["strike", "s", "del"]):
                t = s.get_text("")
                s.replace_with(f"~~{t.strip()}~~" if t.strip() else t)  # 취소선 보존
            for p in node.find_all("p"):
                joined = p.get_text("")
                p.clear()
                p.append(joined)
            text = node.get_text("\n", strip=True)
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


def clean_lines(body: str) -> list:
    lines = [ln.replace("​", "").strip() for ln in body.split("\n")]
    return [ln for ln in lines if ln]


def target_length(body: str):
    """본문 길이에 비례한 요약 분량: (문장 수, 글자 수). 본문의 약 15%, 3~15문장."""
    n = len("".join(clean_lines(body)))
    floor = min(250, int(n * 0.5))  # 짧은 글은 본문 절반을 넘지 않게
    chars = max(floor, min(1500, int(n * 0.15)))
    sents = max(1 if n < 400 else 3, min(15, round(chars / 90)))
    return sents, chars


# ---------- 추출 요약 (키 불필요) ----------
_STOP = set("그리고 그러나 하지만 그래서 그런데 또한 이런 저런 그런 이것 저것 그것 있는 있음 없는 없음 "
            "하는 했다 한다 합니다 입니다 있습니다 것이 것은 것을 수가 수도 때문에 대한 대해 통해 위해 "
            "정도 경우 지금 오늘 이번 그냥 아주 정말 많이 다시 이후 이전 같은 같음 이라는 라고".split())


def _sentences(body: str) -> list:
    out = []
    for ln in clean_lines(body):
        if "©" in ln or "출처 OGQ" in ln or re.search(r"네이버\s?블로그$", ln):
            continue  # 사진 저작권 표기·링크 카드 제거
        for s in re.split(r"(?<=[.!?。])\s+|(?<=[다음함임요])\.\s*", ln):
            s = s.strip()
            if len(s) >= 15 and not s.startswith(("http", "출처", "PS)")):
                out.append(s)
    return out


def extractive_summary(body: str, title: str) -> str:
    sents = _sentences(body)
    if not sents:
        return ""
    k, _ = target_length(body)
    tok = lambda s: [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", s) if w not in _STOP]
    freq = {}
    for s in sents:
        for w in set(tok(s)):
            freq[w] = freq.get(w, 0) + 1
    title_words = set(tok(title))
    n = len(sents)
    scored = []
    for i, s in enumerate(sents):
        ws = tok(s)
        if not ws:
            continue
        score = sum(freq.get(w, 0) for w in ws) / (len(ws) ** 0.5)
        score += 2 * len(title_words & set(ws))
        if i < 3:
            score *= 1.2  # 도입부 가중
        scored.append((score, i))
    top = sorted(i for _, i in sorted(scored, reverse=True)[:k])
    return " ".join(sents[i] for i in top)


# ---------- Gemini (무료 등급) ----------
class Gemini:
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, key: str):
        self.key = key
        env = os.environ.get("GEMINI_MODEL")
        self.models = [env] if env else self._pick_models()
        print(f"Gemini 모델 후보: {self.models}")

    def _req(self, path, payload=None):
        req = urllib.request.Request(
            f"{self.BASE}/{path}", data=json.dumps(payload).encode() if payload else None,
            method="POST" if payload else "GET",
            headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())

    def _pick_models(self):
        try:
            ms = self._req("models?pageSize=1000").get("models", [])
        except Exception as e:  # noqa: BLE001
            print(f"  Gemini 모델 목록 실패: {e}", file=sys.stderr)
            return ["gemini-flash-latest", "gemini-flash-lite-latest"]
        bad = ("image", "tts", "audio", "live", "embedding", "vision", "robotics", "computer")
        names = [m["name"].split("/", 1)[1] for m in ms
                 if "generateContent" in m.get("supportedGenerationMethods", [])
                 and "flash" in m["name"] and not any(b in m["name"] for b in bad)]
        ver = lambda n: tuple(float(x) for x in re.findall(r"\d+(?:\.\d+)?", n)[:1] or [0])
        full = sorted([n for n in names if "lite" not in n], key=ver, reverse=True)
        lite = sorted([n for n in names if "lite" in n], key=ver, reverse=True)
        # 정식(비-preview) 우선, Flash → Flash-Lite 순으로 시도 (한도 초과 시 다음 모델)
        pref = lambda lst: [n for n in lst if "preview" not in n and "exp" not in n] + \
                           [n for n in lst if "preview" in n or "exp" in n]
        return (pref(full)[:2] + pref(lite)[:2]) or ["gemini-flash-latest"]

    def ask(self, prompt: str) -> str:
        last = None
        for m in list(self.models):
            for attempt in range(2):
                t0 = time.time()
                try:
                    j = self._req(f"models/{m}:generateContent", {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192}})
                    parts = j["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                    if text:
                        print(f"    gemini {m}: {time.time() - t0:.0f}초, {len(text)}자")
                        time.sleep(5)  # 분당 한도 여유
                        return text
                    last = f"{m}: 빈 응답 {str(j)[:200]}"
                    break
                except urllib.error.HTTPError as e:
                    last = f"{m} HTTP {e.code}: {e.read()[:300]!r}"
                    print(f"    gemini {last[:200]} ({time.time() - t0:.0f}초)")
                    if e.code == 429 and attempt == 0:
                        time.sleep(30)
                        continue
                    if e.code in (404, 429, 403):  # 없는 모델/한도 소진 → 이후엔 다음 모델 사용
                        self.models.remove(m)
                    break
                except Exception as e:  # noqa: BLE001
                    last = f"{m}: {e}"
                    print(f"    gemini {last[:200]} ({time.time() - t0:.0f}초)")
                    break
        raise RuntimeError(last)


def preview(body: str, max_lines: int = 5, max_chars: int = 400) -> str:
    """요약하지 않는 블로그: 본문 앞부분 4~5줄을 그대로 보여준다."""
    out, total = [], 0
    for ln in clean_lines(body):
        if "©" in ln or "출처 OGQ" in ln or ln.startswith("http"):
            continue
        if total + len(ln) > max_chars and out:
            break
        out.append(ln)
        total += len(ln)
        if len(out) >= max_lines:
            break
    return "\n".join(out)


class Summarizer:
    """blogs.json에서 "summarize": true 인 블로그 글만 Gemini로 요약한다.
    Gemini 실패(키 없음·한도 초과 등) 시 추출 요약으로 대체."""

    def __init__(self):
        key = os.environ.get("GEMINI_API_KEY")
        self.gemini = Gemini(key) if key else None
        if not key:
            print("GEMINI_API_KEY 없음 → 추출 요약으로 대체", file=sys.stderr)

    @staticmethod
    def _prompt(item, body):
        k, chars = target_length(body)
        return (
            f"다음 블로그 글을 한국어로 요약하세요.\n"
            f"- 분량: 약 {k}문장, {chars}자 내외 (본문 길이에 맞춘 분량이니 지켜주세요)\n"
            f"- 글쓴이의 핵심 주장, 근거가 되는 수치·사실, 결론을 빠짐없이 담으세요.\n"
            f"- 내용이 바뀌는 부분은 줄바꿈으로 문단을 나누세요.\n"
            f"- 인사말·머리말 없이 요약문만 출력하세요. 마크다운 기호(#, *, -)는 쓰지 마세요.\n\n"
            f"제목: {item['title']}\n본문:\n{body[:MAX_BODY * 2]}")

    def article(self, item: dict, body: str):
        """(요약문, 방식) 반환. 방식: gemini | extractive"""
        if self.gemini:
            try:
                return self.gemini.ask(self._prompt(item, body)), "gemini"
            except Exception as e:  # noqa: BLE001
                print(f"  gemini 요약 실패({item['url']}): {e}", file=sys.stderr)
        return extractive_summary(body, item["title"]) or item["excerpt"][:300], "extractive"

    def overview(self, articles: list) -> str:
        """요약된 글이 2건 이상일 때만 Gemini로 총평 생성."""
        summed = [a for a in articles if a.get("summary_by") == "gemini"]
        if not self.gemini or len(summed) < 2:
            return ""
        lines = "\n".join(f"- [{a['blog']}] {a['title']}: {a['summary']}" for a in summed)
        try:
            return self.gemini.ask("아래는 이번 호 신문에 실린 블로그 글 요약입니다. 전체 흐름을 한국어 "
                                   "3~4문장 총평으로 써주세요. 총평 본문만 출력하세요.\n\n" + lines)
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

    conf = {b["id"]: b for b in blogs}
    s = Summarizer() if any(b.get("summarize") for b in blogs) else None
    articles = []
    for it in items:
        body = fetch_body(it)
        bc = conf[it["blog_id"]]
        art = {
            "blog": it["blog"], "title": it["title"], "url": it["url"],
            "published": it["published"], "length": len("".join(clean_lines(body))),
        }
        if bc.get("summarize") and s:
            art["summary"], art["summary_by"] = s.article(it, body)
        else:
            art["preview"] = preview(body) or it["excerpt"][:300]
        marker = bc.get("comment_marker")
        if marker:
            art["comment"] = extract_comment(body, marker)
            if not art["comment"]:
                print(f"  '{marker}' 없음: {it['title']}")
        articles.append(art)

    issue = {
        "id": issue_id, "date": date, "edition": edition,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "generated": now.isoformat(),
        "overview": s.overview(articles) if s else "",
        "articles": articles,
    }
    out.write_text(json.dumps(issue, ensure_ascii=False, indent=1), encoding="utf-8")
    update_index()
    print(f"{issue_id} 생성 완료 ({len(articles)}건)")


if __name__ == "__main__":
    main()
