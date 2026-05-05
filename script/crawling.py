import os
import re
import json
import time
import logging
import argparse

from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "td", "th"}

def _html_to_clean_text(element):
    # 인라인 태그는 원본 공백 유지, 블록 태그는 줄바꿈으로 분리해 깔끔한 텍스트를 반환
    lines, current = [], []

    def flush():
        line = re.sub(r" {2,}", " ", "".join(current)).strip()
        if line:
            lines.append(line)
        current.clear()

    def walk(node):
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                current.append(text)
        elif isinstance(node, Tag):
            if node.name in _BLOCK_TAGS:
                flush()
                for child in node.children:
                    walk(child)
                flush()
            else:
                for child in node.children:
                    walk(child)

    walk(element)
    flush()
    return "\n".join(lines)


class Crawling:
    BROWSER_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, data_path=os.getcwd(), site_name="", max_posts=None):
        if not os.path.exists(data_path):
            os.makedirs(data_path)
        self.data_path = data_path
        self.site_name = site_name
        self.max_posts = max_posts  # None이면 전체, 숫자면 해당 개수만 처리
        self.filenames = {
            "url_list":     os.path.join(data_path, f"{site_name}.url_list.json"),
            "content_info": os.path.join(data_path, f"{site_name}.content_info.json"),
            "result":       os.path.join(data_path, f"{site_name}.result.jsonl"),
        }

    # Playwright 브라우저 세팅
    def _make_page(self, playwright):
        browser = playwright.chromium.launch(headless=True, args=self.BROWSER_ARGS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=self.USER_AGENT,
            locale="ko-KR",
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return browser, context, page

    def _load_json(self, path, default):
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return default

    def _save_json(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False)

    # 무한 스크롤 처리
    def _scroll_to_bottom(self, page, pause=1.5, max_scrolls=20):
        prev_height = 0
        for _ in range(max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(int(pause * 1000))
            curr_height = page.evaluate("document.body.scrollHeight")
            if curr_height == prev_height:
                break
            prev_height = curr_height

    def run(self):
        raise NotImplementedError

# 원티드
class CrawlingWanted(Crawling):
    ENDPOINT = "https://www.wanted.co.kr"

    JD_SECTIONS = {
        "주요업무", "주요 업무",
        "자격요건", "자격 요건",
        "우대사항", "우대 사항",
        "복지 및 혜택", "혜택 및 복지",
        "기술스택", "기술 스택", "사용 기술",
    }

    def __init__(self, data_path=os.getcwd(), site_name="wanted", max_posts=None):
        super().__init__(data_path=data_path, site_name=site_name, max_posts=max_posts)

    # 목록 페이지에서 URL 수집
    def get_url_list(self):
        url_list = self._load_json(self.filenames["url_list"], {})

        with sync_playwright() as p:
            browser, context, page = self._make_page(p)

            cat_id   = "all"
            cat_name = "전체"

            url = f"{self.ENDPOINT}/wdlist"
            logger.info(f"[wanted] collecting urls ({url})")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            self._scroll_to_bottom(page)

            links = page.query_selector_all("a[href^='/wd/']")
            today_list = list({
                link.get_attribute("href")
                for link in links
                if re.match(r"^/wd/\d+", link.get_attribute("href") or "")
            })

            # 기존 누적 목록과 합산 (신규 ID만 추가)
            existing = url_list.get(cat_id, {}).get("position_list", [])
            merged   = list(dict.fromkeys(existing + today_list))
            new_cnt  = len(merged) - len(existing)

            url_list[cat_id] = {"cat_name": cat_name, "position_list": merged}
            self._save_json(self.filenames["url_list"], url_list)
            logger.info(f"  → 오늘 {len(today_list)}개 수집, 신규 {new_cnt}개")

            context.close()
            browser.close()

        return url_list

    # 각 공고 상세 페이지 HTML 수집
    def get_recruit_content_info(self, url_list=None):
        if url_list is None:
            url_list = self._load_json(self.filenames["url_list"], {})

        content_info = self._load_json(self.filenames["content_info"], {})

        with sync_playwright() as p:
            browser, context, page = self._make_page(p)

            for cat_id, cat_data in url_list.items():
                if cat_id not in content_info:
                    content_info[cat_id] = {}

                position_list = cat_data["position_list"]
                if self.max_posts:
                    position_list = position_list[:self.max_posts]

                for position_url in position_list:
                    if position_url in content_info[cat_id]:
                        continue

                    full_url = f"{self.ENDPOINT}{position_url}"
                    logger.info(f"[wanted] fetching: {full_url}")
                    try:
                        page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("h1", timeout=15000)

                        for btn_text in ["상세 정보 더 보기", "상세정보 더보기"]:
                            btn = page.query_selector(f"button:has-text('{btn_text}')")
                            if btn:
                                btn.click()
                                page.wait_for_timeout(1500)
                                break

                        content_info[cat_id][position_url] = page.content()
                    except Exception as e:
                        logger.warning(f"  실패: {e}")
                        content_info[cat_id][position_url] = ""

                    self._save_json(self.filenames["content_info"], content_info)
                    time.sleep(0.5)

            context.close()
            browser.close()

        return content_info

    # BeautifulSoup으로 파싱하여 JSON 저장 
    def postprocess(self, content_info=None):
        if content_info is None:
            content_info = self._load_json(self.filenames["content_info"], {})

        url_list = self._load_json(self.filenames["url_list"], {})

        results = []
        with open(self.filenames["result"], "w") as f:
            for cat_id, posts in content_info.items():
                cat_name = url_list.get(cat_id, {}).get("cat_name", cat_id)
                for position_url, html in posts.items():
                    if not html:
                        continue
                    result = self._parse_detail(html, position_url, cat_name)
                    results.append(result)
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")

        logger.info(f"[wanted] postprocess 완료: {len(results)}개")
        return results

    def _parse_detail(self, html, position_url, cat_name):
        soup = BeautifulSoup(html, "html.parser")

        # __NEXT_DATA__에서 직무 카테고리 추출 (없으면 cat_name 폴백)
        job_category = cat_name
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if next_data_tag:
            try:
                nd = json.loads(next_data_tag.string or "")
                ct = nd["props"]["pageProps"]["initialData"].get("category_tag", {})
                child_tags = ct.get("child_tags", [])
                if child_tags:
                    job_category = child_tags[0].get("text", cat_name)
                elif ct.get("parent_tag"):
                    job_category = ct["parent_tag"].get("text", cat_name)
            except Exception:
                pass

        result = {
            "url":            f"{self.ENDPOINT}{position_url}",
            "job_category":   job_category,
            "site":           "wanted",
            "title":          "",
            "company_name":   "",
            "location":       "",
            "experience":     "",
            "deadline":       "",
            "employee_count": "",
            "description":    "",
        }

        # 제목: h1
        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)

        # 회사명·경력: h1 부모 하위에서 ∙ 구분자를 가진 짧은 요소 탐색
        if h1:
            for el in h1.parent.descendants:
                if not hasattr(el, "get_text") or el is h1:
                    continue
                text = el.get_text(strip=True)
                if "∙" in text and 5 < len(text) < 120:
                    parts = [p.strip() for p in text.split("∙")]
                    if len(parts) >= 2 and parts[0]:
                        result["company_name"] = parts[0]
                        result["experience"]   = parts[2] if len(parts) > 2 else ""
                        break

        # 마감일·근무지역·태그(직원수 추출): h2 레이블 → 다음 형제 요소
        for h2 in soup.find_all("h2"):
            label = h2.get_text(strip=True)
            sib = h2.find_next_sibling()
            if not sib:
                continue
            if label == "마감일":
                result["deadline"] = sib.get_text(strip=True)
            elif label == "근무지역":
                loc = sib.get_text(strip=True)
                loc = re.sub(r"©.*?OpenStreetMap", "", loc).strip()
                result["location"] = loc
            elif label == "태그":
                tag_els = sib.find_all(["span", "a", "li"])
                seen = set()
                tags = []
                for t in tag_els:
                    txt = t.get_text(strip=True)
                    if txt and txt not in seen:
                        seen.add(txt)
                        tags.append(txt)
                # 직원 수 태그 추출 (예: "301~1,000명")
                emp_tags = [t for t in tags if re.match(r"\d", t) and "명" in t]
                result["employee_count"] = emp_tags[0] if emp_tags else ""

        # description: JD 섹션 h3들을 하나의 텍스트로 수집
        desc_parts = []
        for h3 in soup.find_all("h3"):
            section_name = h3.get_text(strip=True)
            if section_name not in self.JD_SECTIONS:
                continue
            parts = []
            for sib in h3.next_siblings:
                if hasattr(sib, "get_text"):
                    txt = sib.get_text(separator="\n", strip=True)
                    if txt:
                        parts.append(txt)
            content = "\n".join(parts).strip()
            # 섹션명이 본문 첫 줄에 중복될 경우 제거 (예: "1. 혜택 및 복지\n실제 내용")
            first_line = content.split("\n")[0].strip() if content else ""
            if re.sub(r"^\d+\.\s*", "", first_line) == section_name:
                content = "\n".join(content.split("\n")[1:]).strip()
            if content:
                desc_parts.append(f"[{section_name}]\n{content}")
        result["description"] = "\n\n".join(desc_parts)

        return result

    def run(self):
        url_list = self.get_url_list()
        content_info = self.get_recruit_content_info(url_list)
        return self.postprocess(content_info)

# 잡플래닛
class CrawlingJobplanet(Crawling):
    ENDPOINT = "https://www.jobplanet.co.kr"
    LIST_URL  = "https://www.jobplanet.co.kr/job?order_by=recent"

    META_KEYS = {
        "마감일":   "deadline",
        "경력":     "experience",
        "고용형태": "employment_type",
        "근무지역": "location",
    }

    def __init__(self, data_path=os.getcwd(), site_name="jobplanet", max_posts=None):
        super().__init__(data_path=data_path, site_name=site_name, max_posts=max_posts)

    # 목록 페이지에서 URL 수집
    def get_url_list(self):
        url_list = self._load_json(self.filenames["url_list"], {})

        with sync_playwright() as p:
            browser, context, page = self._make_page(p)

            job_name = "all"
            today_meta = {}  # {pid: job_category}

            def handle_response(resp):
                if "api/v3/job/postings" in resp.url:
                    try:
                        data     = resp.json()
                        recruits = data.get("data", {}).get("recruits", [])
                        if not recruits:
                            recruits = data.get("data", data.get("postings", data.get("items", [])))
                        for item in recruits:
                            pid = str(item.get("id") or item.get("posting_id") or "")
                            if not pid:
                                continue
                            occ      = item.get("occupation_names", {})
                            level2   = occ.get("level2", [])
                            level1   = occ.get("level1", [])
                            category = level2[0] if level2 else (level1[0] if level1 else "")
                            today_meta[pid] = category
                    except Exception:
                        pass

            page.on("response", handle_response)
            logger.info(f"[jobplanet] collecting urls ({self.LIST_URL})")
            page.goto(self.LIST_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            self._scroll_to_bottom(page, pause=2.0, max_scrolls=10)

            # HTML 링크에서 보완 수집 (API 인터셉트 누락 대비)
            for link in page.query_selector_all("a[href*='posting_ids']"):
                m = re.search(r"posting_ids%5B%5D=(\d+)", link.get_attribute("href") or "")
                if m and m.group(1) not in today_meta:
                    today_meta[m.group(1)] = ""

            page.remove_listener("response", handle_response)

            # 기존 누적 목록과 합산 (신규 ID만 추가, 카테고리도 보존)
            existing  = url_list.get(job_name, {})
            new_cnt   = sum(1 for pid in today_meta if pid not in existing)
            merged    = {**existing, **today_meta}

            url_list[job_name] = merged
            self._save_json(self.filenames["url_list"], url_list)
            logger.info(f"  → 오늘 {len(today_meta)}개 수집, 신규 {new_cnt}개")

            context.close()
            browser.close()

        return url_list

    # 공고 상세 HTML 수집
    def get_recruit_content_info(self, url_list=None):
        if url_list is None:
            url_list = self._load_json(self.filenames["url_list"], {})

        content_info = self._load_json(self.filenames["content_info"], {})

        with sync_playwright() as p:
            browser, context, page = self._make_page(p)

            for job_name, posting_ids in url_list.items():
                if job_name not in content_info:
                    content_info[job_name] = {}

                pid_list = list(posting_ids)
                if self.max_posts:
                    pid_list = pid_list[:self.max_posts]

                for pid in pid_list:
                    if pid in content_info[job_name]:
                        continue

                    detail_url = f"{self.ENDPOINT}/job/search?posting_ids%5B%5D={pid}"
                    logger.info(f"[jobplanet] fetching: {detail_url}")
                    try:
                        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(5000)
                        main_html = page.content()

                        # JD 본문은 jobkorea iframe 안에 있으므로 별도 추출
                        jd_html = ""
                        for frame in page.frames:
                            if "jobkorea" in frame.url:
                                try:
                                    jd_html = frame.content()
                                except Exception:
                                    pass
                                break

                        content_info[job_name][pid] = json.dumps(
                            {"main": main_html, "jd": jd_html}, ensure_ascii=False
                        )
                    except Exception as e:
                        logger.warning(f"  실패: {e}")
                        content_info[job_name][pid] = ""

                    self._save_json(self.filenames["content_info"], content_info)
                    time.sleep(0.8)

            context.close()
            browser.close()

        return content_info

    # BeautifulSoup으로 파싱하여 JSON 저장
    def postprocess(self, content_info=None):
        if content_info is None:
            content_info = self._load_json(self.filenames["content_info"], {})

        url_list = self._load_json(self.filenames["url_list"], {})

        results = []
        with open(self.filenames["result"], "w") as f:
            for job_name, posts in content_info.items():
                meta = url_list.get(job_name, {})
                for pid, raw in posts.items():
                    if not raw:
                        continue
                    # url_list에서 카테고리 조회 (dict면 값, list면 빈 문자열)
                    job_category = meta.get(pid, "") if isinstance(meta, dict) else ""
                    result = self._parse_detail(raw, pid, job_category)
                    results.append(result)
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")

        logger.info(f"[jobplanet] postprocess 완료: {len(results)}개")
        return results

    def _parse_detail(self, raw, pid, job_category):
        # raw는 {"main": html, "jd": iframe_html} JSON 또는 빈 문자열
        try:
            data = json.loads(raw)
            main_html = data.get("main", "")
            jd_html   = data.get("jd", "")
        except Exception:
            main_html = raw
            jd_html   = ""

        main_soup = BeautifulSoup(main_html, "html.parser")

        result = {
            "url":             f"{self.ENDPOINT}/job/search?posting_ids%5B%5D={pid}",
            "job_category":    job_category,
            "site":            "jobplanet",
            "title":           "",
            "company_name":    "",
            "location":        "",
            "experience":      "",
            "employment_type": "",
            "deadline":        "",
            "description":     "",
        }

        # 제목: h1
        h1 = main_soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)
            # 회사명: h1 부모의 다음 형제 div ("|" 기준으로 회사명|지역)
            meta_div = h1.parent.find_next_sibling()
            if meta_div:
                meta_text = meta_div.get_text(strip=True)
                result["company_name"] = meta_text.split("|")[0].strip()

        # 메타 정보: dl/dt/dd 쌍
        for dl in main_soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                key   = dt.get_text(strip=True)
                val   = dd.get_text(strip=True)
                field = self.META_KEYS.get(key)
                if not field:
                    continue
                if field == "deadline":
                    m = re.search(r"\d{4}\.\d{2}\.\d{2}", val)
                    result[field] = m.group().replace(".", "-") if m else val
                else:
                    result[field] = val

        # description: iframe(jobkorea) 본문 전체 텍스트
        if jd_html:
            jd_soup = BeautifulSoup(jd_html, "html.parser")
            # 실제 JD 컨테이너만 추출 (id="wrap"), 없으면 body 전체
            container = jd_soup.find("div", id="wrap") or jd_soup.find("body") or jd_soup
            for tag in container(["script", "style", "noscript"]):
                tag.decompose()
            result["description"] = _html_to_clean_text(container)

        return result

    def run(self):
        url_list = self.get_url_list()
        content_info = self.get_recruit_content_info(url_list)
        return self.postprocess(content_info)


# 진입점 
CRAWLING_CLASS = {
    "wanted":    CrawlingWanted,
    "jobplanet": CrawlingJobplanet,
}


def main(args):
    logging.basicConfig(
        level=logging.DEBUG if args.log_type.lower() == "debug" else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info(f"[main] site={args.site_type}, method={args.method}")
    crawler = CRAWLING_CLASS.get(args.site_type.lower())

    if crawler is None:
        logger.error(f"지원하지 않는 사이트: {args.site_type}")
        return

    instance = crawler(data_path=args.data_path, max_posts=args.max_posts)

    if args.method == "all":
        instance.run()
    else:
        method = getattr(instance, args.method, None)
        if method:
            method()
        else:
            logger.error(f"존재하지 않는 메서드: {args.method}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--site_type", required=True, help="wanted | jobplanet")
    parser.add_argument("-l", "--log_type",  default="info", help="info | debug")
    parser.add_argument("-d", "--data_path", default=os.path.join(os.getcwd(), "data"))
    parser.add_argument("-m", "--method",    default="all",
                        help="all | get_url_list | get_recruit_content_info | postprocess")
    parser.add_argument("-n", "--max_posts", type=int, default=None,
                        help="크롤링할 최대 공고 수 (테스트용, 미지정 시 전체)")
    main(parser.parse_args())
