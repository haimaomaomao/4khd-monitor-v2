import requests
from bs4 import BeautifulSoup
import time
import json
import os
import sys
import re
import struct
import subprocess
from io import BytesIO
import urllib3
from urllib.parse import urljoin
from PIL import Image
from openai import OpenAI

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 环境变量 ──────────────────────────────────────────────
TOKEN       = os.getenv("TG_TOKEN")
CHAT_ID     = os.getenv("TG_CHAT_ID")
GROUP_ID    = os.getenv("TG_GROUP_ID")
AI_API_KEY  = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
AI_MODEL    = os.getenv("AI_MODEL", "deepseek-chat")

# ── 常量 ──────────────────────────────────────────────────
MAX_PAGES           = 10
MIN_CAT_PAGES       = 5
MAX_IMAGES          = 9999
SEEN_FILE           = "seen_posts.json"
BASE_URL            = "https://www.4khd.com/"
TELEGRAPH_TOKEN_FILE = "telegraph_token.txt"
CROP_RATIO          = 0.015   # 四边各裁 1.5%

ALL_CATEGORIES = [
    "https://www.4khd.com/",
    "https://www.4khd.com/pages/cosplay",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TELEGRAPH_TOKEN = None
ai_client = None


# ============================================================
#  AI 标签（增强版新增）
# ============================================================

def init_ai():
    global ai_client
    if AI_API_KEY:
        try:
            ai_client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
            print(f"✅ AI 标签已启用 → {AI_BASE_URL} / {AI_MODEL}")
        except Exception as e:
            print(f"⚠️ AI 初始化失败: {e}，回退到本地标签库")
            ai_client = None
    else:
        print("ℹ️ 未配置 AI_API_KEY，使用本地标签库")


def generate_tags_with_ai(title):
    if not ai_client:
        return None   # 返回 None 表示 AI 不可用，由调用方回退
    prompt = (
        "你是一个写真/Cosplay标签专家。根据以下写真标题，提取3-5个最贴切的标签。\n"
        "标签用中文或英文都可以，每个标签以#开头。\n"
        "重点关注：角色名、作品/游戏名、服装类型、风格特征。\n"
        "只返回标签，用空格分隔，不要任何解释。\n\n"
        f"标题: {title}\n\n"
        "示例输出: #Cosplay #兔女郎 #碧蓝航线 #泳装 #黑丝"
    )
    try:
        response = ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.3,
        )
        tags_text = response.choices[0].message.content.strip()
        tags = re.findall(r"#[\w一-鿿\-_]+", tags_text)
        if not tags:
            tags = re.findall(r"[\w一-鿿]{2,8}", tags_text)
            tags = [f"#{t}" for t in tags]
        return list(dict.fromkeys(tags))[:5]
    except Exception as e:
        print(f"  ⚠️ AI标签生成失败: {e}")
        return None


# ============================================================
#  本地标签库（原版逻辑，作为 AI 回退方案）
# ============================================================

def load_tag_library():
    try:
        with open("tags.json", "r", encoding="utf-8") as f:
            lib = json.load(f)
            print(f"✅ 标签库已加载，共 {len(lib)} 个标签")
            return lib
    except Exception as e:
        print(f"⚠️ 无法加载 tags.json: {e}，使用空标签库")
        return {}

TAG_LIBRARY = load_tag_library()


def generate_tags_local(title, image_urls):
    tags = set()
    title_lower = title.lower()
    for key, tag in TAG_LIBRARY.items():
        if tag and key.lower() in title_lower:
            tags.add(f"#{tag}")
    for url in image_urls[:10]:
        url_lower = url.lower()
        for key, tag in TAG_LIBRARY.items():
            if tag and key.lower() in url_lower:
                tags.add(f"#{tag}")
    name_matches = re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)+", title)
    for name in name_matches[:2]:
        tag_name = name.replace(" ", "")
        mapped_tag = TAG_LIBRARY.get(tag_name.lower())
        if mapped_tag:
            tags.add(f"#{mapped_tag}")
    if not tags:
        tags = {"#美女", "#写真"}
    return list(tags)[:6]


def generate_tags(title, image_urls):
    """优先 AI，回退本地标签库"""
    ai_tags = generate_tags_with_ai(title)
    if ai_tags:
        return ai_tags
    return generate_tags_local(title, image_urls)


# ============================================================
#  Telegraph
# ============================================================

def load_or_create_telegraph_token():
    global TELEGRAPH_TOKEN
    if os.path.exists(TELEGRAPH_TOKEN_FILE):
        try:
            with open(TELEGRAPH_TOKEN_FILE, "r") as f:
                token = f.read().strip()
            if token:
                TELEGRAPH_TOKEN = token
                print(f"✅ Telegraph token 已从文件加载")
                return
        except Exception:
            pass
    try:
        r = requests.post(
            "https://api.telegra.ph/createAccount",
            json={"short_name": "4KHD", "author_name": "4KHD Bot"},
            timeout=15,
        )
        if r.status_code == 200 and r.json().get("ok"):
            TELEGRAPH_TOKEN = r.json()["result"]["access_token"]
            with open(TELEGRAPH_TOKEN_FILE, "w") as f:
                f.write(TELEGRAPH_TOKEN)
            print(f"✅ Telegraph token 已创建并保存")
        else:
            print(f"❌ Telegraph token 创建失败: {r.text}")
    except Exception as e:
        print(f"❌ Telegraph 初始化异常: {e}")


def create_telegraph_page(title, image_urls):
    if not TELEGRAPH_TOKEN:
        return None
    children = [{"tag": "img", "attrs": {"src": url}} for url in image_urls]
    print(f"  📝 创建 Telegraph 页面，共 {len(children)} 张")
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.telegra.ph/createPage",
                data={
                    "access_token": TELEGRAPH_TOKEN,
                    "title": title[:256],
                    "content": json.dumps(children, ensure_ascii=False),
                    "return_content": "false",
                },
                timeout=60,
            )
            if r.status_code == 200 and r.json().get("ok"):
                url = r.json()["result"]["url"]
                print(f"  ✅ Telegraph: {url}")
                return url
            else:
                print(f"  ❌ Telegraph 失败 (attempt {attempt+1}): {r.text[:120]}")
                time.sleep(2)
        except Exception as e:
            print(f"  ❌ Telegraph 异常 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


# ============================================================
#  seen 持久化（含 git 自动提交）
# ============================================================

def load_seen():
    if not os.path.exists(SEEN_FILE) or os.path.getsize(SEEN_FILE) == 0:
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)
    try:
        subprocess.run(["git", "config", "user.email", "bot@4khd"], check=False, capture_output=True)
        subprocess.run(["git", "config", "user.name", "4KHD Bot"], check=False, capture_output=True)
        files_to_commit = [SEEN_FILE]
        if os.path.exists(TELEGRAPH_TOKEN_FILE):
            files_to_commit.append(TELEGRAPH_TOKEN_FILE)
        subprocess.run(["git", "add"] + files_to_commit, check=False, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"chore: update seen [{len(seen)} posts]"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            subprocess.run(["git", "push"], check=False, capture_output=True)
            print(f"  📌 seen 已 git commit 持久化")
    except Exception as e:
        print(f"  ⚠️ git commit 失败: {e}")


# ============================================================
#  工具函数
# ============================================================

def clean_title(title):
    title = re.sub(r"\[[^\]]*\]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def fix_image_url(src):
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    elif not src.startswith("http"):
        src = BASE_URL.rstrip("/") + "/" + src.lstrip("/")
    src = re.sub(r"https?://i\d+\.wp\.com/", "https://", src)
    src = src.replace("pic.4khd.com", "img.4khd.com")
    if "?" in src:
        src = src.split("?")[0]
    return src


def fetch_with_retry(url, retries=3, delay=2, **kwargs):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20, **kwargs)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"  ⚠️ 限流，等待 {wait}s")
                time.sleep(wait)
            else:
                print(f"  ⚠️ {url} 返回 {r.status_code}，第 {attempt+1} 次重试")
                time.sleep(delay)
        except Exception as e:
            print(f"  ⚠️ 请求异常 (attempt {attempt+1}): {e}")
            time.sleep(delay)
    return None


def extract_images_from_content(soup):
    images, seen = [], set()
    content = None
    for sel in ["article", ".entry-content", ".post-body", ".single-content", "main"]:
        content = soup.select_one(sel)
        if content:
            break
    if not content:
        content = soup.find("body")
    if not content:
        return images

    AD_WORDS = {"related", "recommend", "popular", "ad", "banner", "widget", "sidebar", "footer"}

    def is_ad(tag):
        node = tag.parent
        for _ in range(3):
            if node and node.name in ["div", "aside", "section", "article", "li", "figure", "a"]:
                txt = " ".join(node.get("class", [])) + " " + (node.get("id") or "")
                if any(w in txt.lower() for w in AD_WORDS):
                    return True
            node = node.parent if node else None
        return False

    for ns in content.find_all("noscript"):
        inner = BeautifulSoup(ns.text, "html.parser")
        for img in inner.find_all("img"):
            src = fix_image_url(img.get("src"))
            if src and "4khd.com" in src and src not in seen:
                images.append(src)
                seen.add(src)

    for img in content.find_all("img"):
        if is_ad(img):
            continue
        src = fix_image_url(
            img.get("src") or img.get("data-src") or img.get("data-original") or ""
        )
        if src and "4khd.com" in src and src not in seen:
            images.append(src)
            seen.add(src)
    return images


def get_all_page_urls(first_url, soup):
    urls = [first_url]
    for a in soup.select("div.page-link-box ul.page-links li.numpages a.page-numbers"):
        href = a.get("href")
        if href:
            full = urljoin(first_url, href)
            if full not in urls:
                urls.append(full)
    return urls


def get_real_images(post_url):
    print(f"  🔍 {post_url}")
    r = fetch_with_retry(post_url)
    if not r:
        return []
    soup_first = BeautifulSoup(r.text, "html.parser")
    page_urls = get_all_page_urls(post_url, soup_first)[:MAX_PAGES]
    print(f"  📖 {len(page_urls)} 个分页（最多{MAX_PAGES}页）")
    all_images, seen = [], set()
    for idx, url in enumerate(page_urls, 1):
        r = fetch_with_retry(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        imgs = extract_images_from_content(soup)
        for u in re.findall(r"https://yt4\.googleusercontent\.com[^\s\"]+\.webp", r.text):
            u = u.split("?")[0]
            if u not in seen:
                imgs.append(u)
        new_imgs = [u for u in imgs if u not in seen]
        all_images.extend(new_imgs)
        seen.update(new_imgs)
        print(f"  📄 第{idx}页 {len(new_imgs)} 张，累计 {len(all_images)} 张")
        time.sleep(0.5)
    return all_images[:MAX_IMAGES]


# ============================================================
#  图片裁剪（增强版新增）
# ============================================================

def crop_image(img_bytes, crop_ratio=CROP_RATIO):
    """裁剪图片四边各 crop_ratio（默认1.5%）"""
    try:
        with Image.open(img_bytes) as img:
            w, h = img.size
            l = int(w * crop_ratio)
            t = int(h * crop_ratio)
            r = int(w * (1 - crop_ratio))
            b = int(h * (1 - crop_ratio))
            cropped = img.crop((l, t, r, b))

            output = BytesIO()
            img_format = img.format or "JPEG"
            cropped.save(output, format=img_format, quality=95)
            output.seek(0)
            return output
    except Exception as e:
        print(f"  ⚠️ 裁剪失败: {e}")
        img_bytes.seek(0)
        return img_bytes


# ============================================================
#  图片下载（增强版：支持裁剪 / 原图两种模式）
# ============================================================

def download_image_raw(url, referer, retries=2):
    """下载原图（不裁剪，用于列表页缩略图）"""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=15, verify=False)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "image/jpeg")
            if not ct.startswith("image/"):
                return None
            return BytesIO(r.content), ct
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ⚠️ 下载失败 {url[:60]}: {e}")
            time.sleep(1)
    return None


def download_image(url, referer, retries=2):
    """下载图片并自动裁剪（用于内容页图片）"""
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                headers={**HEADERS, "Referer": referer},
                timeout=15,
                verify=False,
            )
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "image/jpeg")
            if not ct.startswith("image/"):
                return None
            cropped = crop_image(BytesIO(r.content))
            return cropped, ct
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ⚠️ 下载失败 {url[:60]}: {e}")
            time.sleep(1)
    return None


# ============================================================
#  智能封面选择（原版逻辑：按尺寸选最佳竖图）
# ============================================================

def download_and_select_cover(urls, referer):
    """从内容图列表中下载前20张，按尺寸选择最佳竖图作为封面"""
    candidates_urls = urls[:20]
    downloaded = []
    for url in candidates_urls:
        res = download_image(url, referer)
        if res:
            downloaded.append((url, res))
        time.sleep(0.05)
    if not downloaded:
        return None, None
    best_url, best_item = downloaded[0]
    best_h, found_portrait = 0, False
    for url, (data, ctype) in downloaded:
        data.seek(0)
        w, h = get_image_dimensions(data.read(), ctype)
        if w == 0:
            continue
        if h > w and (not found_portrait or h > best_h):
            best_url, best_item = url, (data, ctype)
            best_h, found_portrait = h, True
        elif not found_portrait and h > best_h:
            best_url, best_item = url, (data, ctype)
            best_h = h
    best_item[0].seek(0)
    return best_url, best_item


def get_image_dimensions(data_bytes, content_type):
    try:
        ct = (content_type or "").lower()
        if "webp" in ct:
            if len(data_bytes) < 30 or data_bytes[:4] != b"RIFF":
                return 0, 0
            chunk = data_bytes[12:16]
            if chunk == b"VP8 ":
                return struct.unpack_from("<H", data_bytes, 26)[0] & 0x3FFF, \
                       struct.unpack_from("<H", data_bytes, 28)[0] & 0x3FFF
            elif chunk == b"VP8L":
                b = struct.unpack_from("<I", data_bytes, 21)[0]
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            elif chunk == b"VP8X":
                return (struct.unpack_from("<I", data_bytes, 24)[0] + 1) & 0xFFFFFF, \
                       (struct.unpack_from("<I", data_bytes, 27)[0] + 1) & 0xFFFFFF
        elif "png" in ct:
            if len(data_bytes) < 24:
                return 0, 0
            return struct.unpack(">II", data_bytes[16:24])
        elif "jpeg" in ct or "jpg" in ct:
            if len(data_bytes) < 4 or data_bytes[0] != 0xFF or data_bytes[1] != 0xD8:
                return 0, 0
            pos = 2
            while pos < len(data_bytes) - 9:
                if data_bytes[pos] != 0xFF:
                    break
                marker = data_bytes[pos + 1]
                if marker in (0xD8, 0xD9):
                    pos += 2
                    continue
                if marker == 0xDA:
                    break
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xCC):
                    h = struct.unpack_from(">H", data_bytes, pos + 5)[0]
                    w = struct.unpack_from(">H", data_bytes, pos + 7)[0]
                    return w, h
                pos += 2 + struct.unpack_from(">H", data_bytes, pos + 2)[0]
    except Exception:
        pass
    return 0, 0


# ============================================================
#  Telegram 发送
# ============================================================

def send_photo_with_retry(chat_id, cover_item, caption, retries=3):
    cover_data, cover_ctype = cover_item
    ext = cover_ctype.split("/")[-1].replace("jpeg", "jpg")
    for attempt in range(retries):
        cover_data.seek(0)
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": (f"cover.{ext}", cover_data, cover_ctype)},
                timeout=30,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 30)
                print(f"  ⚠️ Telegram 限流，等待 {wait}s")
                time.sleep(wait)
            else:
                print(f"  ❌ sendPhoto 失败 ({r.status_code}): {r.text[:200]}")
                time.sleep(2)
        except Exception as e:
            print(f"  ❌ sendPhoto 异常 (attempt {attempt+1}): {e}")
            time.sleep(2)
    return False


# ============================================================
#  帖子处理（增强版：支持列表页缩略图作封面 + AI 标签）
# ============================================================

def process_post(title, post_url, cover_url_from_list=""):
    clean_t = clean_title(title) or title.strip()
    print(f"\n📥 {clean_t[:60]}")

    urls = get_real_images(post_url)
    if not urls:
        print("  ❌ 无图片")
        return False
    print(f"  图片总数: {len(urls)}")

    # 标签：优先 AI，回退本地库
    tags = generate_tags(clean_t, urls)
    tag_str = " ".join(tags)
    print(f"  🏷️ 标签: {tag_str}")

    telegraph_url = create_telegraph_page(clean_t, urls)

    # 封面：优先使用列表页缩略图，回退到内容页智能选择
    cover_item = None
    if cover_url_from_list:
        print(f"  📸 封面(列表页缩略图): {cover_url_from_list[:80]}")
        raw = download_image_raw(cover_url_from_list, post_url)
        if raw:
            # 对列表页缩略图也应用裁剪
            data, ctype = raw
            cropped = crop_image(data)
            cover_item = (cropped, ctype)
    if not cover_item:
        cover_url, cover_item = download_and_select_cover(urls, post_url)
    if not cover_item:
        print("  ❌ 封面下载失败")
        return False

    caption = f"<b>{clean_t}</b>"
    if tag_str:
        caption += f"\n{tag_str}"
    caption += f"\n\n{'<a href=\"' + telegraph_url + '\">👉 点击查看完整图集</a>' if telegraph_url else '⚠️ Telegraph 页面生成失败'}"

    ok = send_photo_with_retry(CHAT_ID, cover_item, caption)
    if not ok:
        print("  ❌ 频道发送失败")
        return False
    print("  ✅ 已发送到频道")
    if GROUP_ID:
        cover_data, cover_ctype = cover_item
        cover_data.seek(0)
        group_cover = (BytesIO(cover_data.read()), cover_ctype)
        group_ok = send_photo_with_retry(GROUP_ID, group_cover, caption)
        if group_ok:
            print("  ✅ 已发送到群组")
        else:
            print("  ⚠️ 群组发送失败")
    return True


# ============================================================
#  帖子抓取（增强版：WordPress 主题兼容 + 分类交错排序）
# ============================================================

def get_new_posts_from_pages(pages, min_pages=MIN_CAT_PAGES):
    all_categorized_posts = []
    global_seen_urls = set()

    for page_url in pages:
        print(f"\n===== 抓取分类: {page_url} =====")
        r = fetch_with_retry(page_url)
        if not r:
            all_categorized_posts.append([])
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        cat_pages = get_all_page_urls(page_url, soup)
        print(f"  从导航提取 {len(cat_pages)} 个分页")

        # 补充到 min_pages 页
        if len(cat_pages) < min_pages:
            existing_nums = set()
            for u in cat_pages:
                m = re.search(r"/page/(\d+)/?", u)
                if m:
                    existing_nums.add(int(m.group(1)))
                else:
                    m = re.search(r"[?&]page=(\d+)", u)
                    if m:
                        existing_nums.add(int(m.group(1)))
            start = max(existing_nums) + 1 if existing_nums else 2
            base = page_url.rstrip("/")
            for p in range(start, min_pages + 1):
                new_url = f"{base}/page/{p}/"
                if new_url not in cat_pages:
                    cat_pages.append(new_url)
            print(f"  补充后共 {len(cat_pages)} 个分页")

        cat_pages_sorted = cat_pages[:min_pages][::-1]
        print(f"  页面抓取顺序（从旧到新）:")
        for c in cat_pages_sorted:
            print(f"    {c}")

        category_posts = []
        for idx, cat_url in enumerate(cat_pages_sorted, 1):
            print(f"  📄 第{idx}页: {cat_url}")
            r = fetch_with_retry(cat_url)
            page_posts = []
            if r:
                soup = BeautifulSoup(r.text, "html.parser")

                # WordPress 主题兼容：优先 wp-block-post，后备 article / .post
                articles = soup.find_all("li", class_="wp-block-post")
                if not articles:
                    articles = soup.select("article") or soup.select(".post")

                for art in reversed(articles):
                    title_el = art.find("h2", class_="wp-block-post-title") or art.find("h2")
                    if not title_el:
                        continue
                    link = title_el.find("a", href=True)
                    if not link:
                        continue
                    href = link["href"]
                    title = link.text.strip()
                    if not title:
                        continue
                    full = href if href.startswith("http") else BASE_URL.rstrip("/") + href
                    if full in global_seen_urls:
                        print(f"    ⏭️ 已抓取过: {title[:50]}...")
                        continue

                    # 提取列表页缩略图 URL
                    cover_src = ""
                    figure = art.find("figure", class_="wp-block-post-featured-image") or art.find("figure")
                    if figure:
                        cover_img = figure.find("img")
                        if cover_img:
                            cover_src = fix_image_url(cover_img.get("src") or cover_img.get("data-src") or "")

                    page_posts.append({"title": title, "url": full, "cover_url": cover_src})
                    global_seen_urls.add(full)
                    print(f"    ✅ 新帖: {title[:50]}... | {full}" +
                          (f" | 🖼️ {cover_src[:30]}..." if cover_src else ""))
                print(f"    本页新增 {len(page_posts)} 条")
            category_posts.append(page_posts)
            time.sleep(0.3)

        all_categorized_posts.append(category_posts)

    # 按页交错排列：让不同分类的帖子穿插出现
    final_posts = []
    max_pages = max((len(cp) for cp in all_categorized_posts), default=0)
    print(f"\n===== 按页交错排列（{max_pages}页 × {len(pages)}分类）=====")
    for page_idx in range(max_pages - 1, -1, -1):
        for cat_idx in range(len(pages)):
            if page_idx < len(all_categorized_posts[cat_idx]):
                posts = all_categorized_posts[cat_idx][page_idx]
                if posts:
                    cat_name = pages[cat_idx].split("/")[-2] if pages[cat_idx] != BASE_URL else "popular"
                    print(f"  🔄 分类[{cat_name}] 第{page_idx+1}页 → {len(posts)} 条")
                    final_posts.extend(posts)

    print(f"\n===== 共 {len(final_posts)} 条候选帖子（按页交错排列）=====")
    return final_posts


# ============================================================
#  主入口
# ============================================================

if __name__ == "__main__":
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 4KHD 搬运启动")
    if not TOKEN or not CHAT_ID:
        print("❌ 缺少 TG_TOKEN 或 TG_CHAT_ID")
        sys.exit(1)
    if GROUP_ID:
        print(f"✅ 群组发送已启用，目标 ID: {GROUP_ID}")
    else:
        print("ℹ️ 未配置 TG_GROUP_ID，仅发送到频道")

    init_ai()
    load_or_create_telegraph_token()

    seen = load_seen()
    print(f"📂 seen 记录: {len(seen)} 条")

    posts = get_new_posts_from_pages(ALL_CATEGORIES, MIN_CAT_PAGES)
    new_posts = [p for p in posts if p["url"] not in seen]

    if not new_posts:
        print("暂无新内容")
        save_seen(seen)
        sys.exit(0)

    print(f"发现 {len(new_posts)} 条新内容（发送顺序：从旧到新）")
    success = 0
    for i, p in enumerate(new_posts, 1):
        ok = process_post(p["title"], p["url"], p.get("cover_url", ""))
        if ok:
            seen.add(p["url"])
            success += 1
        else:
            print(f"  ⚠️ 发送失败，下次运行会重试: {p['url']}")
        print(f"  进度 {i}/{len(new_posts)}，成功 {success} 条")
        if i % 3 == 0:
            save_seen(seen)
        time.sleep(10)

    save_seen(seen)
    print(f"\n✅ 完成 {success}/{len(new_posts)} 条")
    sys.exit(0)
