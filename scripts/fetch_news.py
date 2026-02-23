#!/usr/bin/env python3
"""
全球时事政经新闻聚合器
从多个RSS源和API抓取新闻，分类整理后输出JSON
"""

import json
import os
import re
import hashlib
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 配置 ====================
DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "news.json"
MAX_NEWS = 300  # 最多保留条数

# RSS 源配置
RSS_SOURCES = [
    # ---- 国际综合 ----
    {
        "name": "Reuters World",
        "url": "https://feeds.reuters.com/Reuters/worldNews",
        "category": "国际",
        "lang": "en",
        "icon": "🌐",
        "priority": 1
    },
    {
        "name": "BBC World",
        "url": "http://feeds.bbci.co.uk/news/world/rss.xml",
        "category": "国际",
        "lang": "en",
        "icon": "🇬🇧",
        "priority": 1
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "category": "国际",
        "lang": "en",
        "icon": "🌍",
        "priority": 2
    },
    {
        "name": "NPR World",
        "url": "https://feeds.npr.org/1004/rss.xml",
        "category": "国际",
        "lang": "en",
        "icon": "📻",
        "priority": 2
    },
    # ---- 财经 ----
    {
        "name": "CNBC Top",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "category": "财经",
        "lang": "en",
        "icon": "💹",
        "priority": 1
    },
    {
        "name": "Bloomberg",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "category": "财经",
        "lang": "en",
        "icon": "📊",
        "priority": 1
    },
    {
        "name": "FT World",
        "url": "https://www.ft.com/rss/home",
        "category": "财经",
        "lang": "en",
        "icon": "🇬🇧",
        "priority": 2
    },
    # ---- 政治 ----
    {
        "name": "BBC Politics",
        "url": "http://feeds.bbci.co.uk/news/politics/rss.xml",
        "category": "政治",
        "lang": "en",
        "icon": "🏛️",
        "priority": 2
    },
    {
        "name": "Reuters Politics",
        "url": "https://feeds.reuters.com/Reuters/PoliticsNews",
        "category": "政治",
        "lang": "en",
        "icon": "🗳️",
        "priority": 1
    },
    # ---- 科技 ----
    {
        "name": "Reuters Tech",
        "url": "https://feeds.reuters.com/reuters/technologyNews",
        "category": "科技",
        "lang": "en",
        "icon": "💻",
        "priority": 2
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "category": "科技",
        "lang": "en",
        "icon": "🚀",
        "priority": 2
    },
    # ---- 中文源 ----
    {
        "name": "新浪财经",
        "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=20&page=1&r=0.1&callback=",
        "category": "财经",
        "lang": "zh",
        "icon": "📈",
        "priority": 1,
        "type": "sina_api"
    },
    {
        "name": "BBC中文",
        "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "category": "国际",
        "lang": "zh",
        "icon": "🇬🇧",
        "priority": 1
    },
    {
        "name": "纽约时报中文",
        "url": "https://cn.nytimes.com/rss/",
        "category": "国际",
        "lang": "zh",
        "icon": "🇺🇸",
        "priority": 1
    },
    {
        "name": "韩联社中文",
        "url": "https://cn.yna.co.kr/RSS/news.xml",
        "category": "国际",
        "lang": "zh",
        "icon": "🇰🇷",
        "priority": 2
    },
    {
        "name": "DW德国之声",
        "url": "https://rss.dw.com/xml/rss-chi-all",
        "category": "国际",
        "lang": "zh",
        "icon": "🇩🇪",
        "priority": 2
    },
    {
        "name": "Nikkei Asia",
        "url": "https://asia.nikkei.com/rss",
        "category": "财经",
        "lang": "en",
        "icon": "🇯🇵",
        "priority": 2
    },
    {
        "name": "The Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "category": "国际",
        "lang": "en",
        "icon": "🇬🇧",
        "priority": 2
    },
    {
        "name": "WSJ Markets",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "category": "财经",
        "lang": "en",
        "icon": "📊",
        "priority": 1
    },
]

# ==================== 工具函数 ====================

def clean_html(text):
    """去除HTML标签"""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:500]  # 限制长度

def make_id(title, source):
    """生成唯一ID"""
    raw = f"{title}_{source}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def parse_date(date_str):
    """解析各种日期格式"""
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    
    # fallback: try dateutil
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except:
        return datetime.now(timezone.utc).isoformat()

def classify_importance(title, summary):
    """根据关键词判断重要性 1-5"""
    text = f"{title} {summary}".lower()
    
    # 重大事件关键词
    critical = ['war', 'invasion', 'nuclear', 'crash', 'crisis', 'emergency', 'breaking',
                 '战争', '核', '崩盘', '危机', '紧急', '突发', '重大', '地震', '海啸']
    high = ['summit', 'sanctions', 'election', 'fed', 'interest rate', 'gdp', 'inflation',
            'trump', 'biden', 'xi jinping', 'putin',
            '峰会', '制裁', '选举', '央行', '利率', 'GDP', '通胀', '关税', '贸易战',
            '习近平', '普京', '特朗普', '拜登', '两会', '政策']
    medium = ['trade', 'market', 'stock', 'oil', 'gold', 'bitcoin',
              '贸易', '市场', '股市', '石油', '黄金', '比特币', '科技', '芯片']
    
    score = 3  # default medium
    for kw in critical:
        if kw in text:
            score = 5
            break
    if score < 5:
        for kw in high:
            if kw in text:
                score = 4
                break
    if score < 4:
        for kw in medium:
            if kw in text:
                score = 3
                break
    
    return score

def detect_region(title, summary, source_name):
    """检测新闻涉及的地区"""
    text = f"{title} {summary} {source_name}".lower()
    
    regions = []
    region_map = {
        '中国': ['china', 'chinese', 'beijing', 'shanghai', '中国', '北京', '上海', '习近平', '新华', '澎湃', '财新'],
        '美国': ['us', 'usa', 'america', 'washington', 'trump', 'biden', 'fed', 'wall street', '美国', '华盛顿', '美联储'],
        '欧洲': ['europe', 'eu', 'european', 'brussels', 'london', 'paris', 'berlin', '欧洲', '欧盟', '英国', '法国', '德国'],
        '俄罗斯': ['russia', 'russian', 'moscow', 'putin', 'kremlin', '俄罗斯', '莫斯科', '普京'],
        '中东': ['middle east', 'israel', 'iran', 'saudi', 'gaza', 'syria', '中东', '以色列', '伊朗', '沙特'],
        '亚太': ['japan', 'korea', 'india', 'asean', 'asia', 'pacific', '日本', '韩国', '印度', '东盟', '亚洲'],
        '全球': ['global', 'world', 'international', 'un ', 'united nations', '全球', '世界', '联合国'],
    }
    
    for region, keywords in region_map.items():
        for kw in keywords:
            if kw in text:
                regions.append(region)
                break
    
    return regions if regions else ['其他']

# ==================== 翻译 (SiliconFlow/DeepSeek AI) ====================

TRANSLATE_API_URL = os.environ.get('TRANSLATE_API_URL', 'https://api.siliconflow.cn/v1/chat/completions')
TRANSLATE_API_KEY = os.environ.get('TRANSLATE_API_KEY', '')
TRANSLATE_MODEL = os.environ.get('TRANSLATE_MODEL', 'deepseek-ai/DeepSeek-V3')

def ai_translate_batch(texts, batch_size=20):
    """用AI大模型批量翻译英文为中文"""
    import requests as req
    
    if not TRANSLATE_API_KEY:
        print("  ⚠️ 未设置 TRANSLATE_API_KEY 环境变量，跳过翻译")
        return texts
    
    results = list(texts)  # copy
    
    # 筛选出需要翻译的
    to_translate = []
    for i, t in enumerate(texts):
        if not t or not t.strip():
            continue
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', t))
        if cn_chars > len(t) * 0.3:
            continue  # 已经是中文
        to_translate.append((i, t[:300]))
    
    if not to_translate:
        return results
    
    # 分批翻译
    for batch_start in range(0, len(to_translate), batch_size):
        batch = to_translate[batch_start:batch_start + batch_size]
        
        # 构建prompt：编号列表方便解析
        lines = []
        for j, (idx, text) in enumerate(batch):
            lines.append(f"{j+1}. {text}")
        prompt_text = "\n".join(lines)
        
        system_prompt = """你是一个专业的新闻翻译器。将以下编号的英文新闻标题/摘要翻译成简洁流畅的中文。
规则：
1. 保持编号格式，每行一条
2. 只输出翻译结果，不加解释
3. 人名/地名用通用中文译名
4. 保持新闻标题的简洁风格
5. 专业术语用常见中文表达"""
        
        try:
            resp = req.post(TRANSLATE_API_URL, 
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {TRANSLATE_API_KEY}'
                },
                json={
                    'model': TRANSLATE_MODEL,
                    'messages': [
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': prompt_text}
                    ],
                    'max_tokens': 2000,
                    'temperature': 0.3
                },
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            # 解析翻译结果
            translated_lines = reply.strip().split('\n')
            for line in translated_lines:
                line = line.strip()
                if not line:
                    continue
                # 匹配 "1. 翻译内容" 或 "1、翻译内容" 或 "1.翻译内容"
                m = re.match(r'^(\d+)\s*[.、．]\s*(.+)', line)
                if m:
                    num = int(m.group(1)) - 1
                    translated = m.group(2).strip()
                    if 0 <= num < len(batch):
                        orig_idx = batch[num][0]
                        results[orig_idx] = translated
            
        except Exception as e:
            print(f"  ⚠️ 翻译批次失败: {str(e)[:60]}")
        
        # 避免API限流
        if batch_start + batch_size < len(to_translate):
            time.sleep(1)
    
    return results

def translate_items(items):
    """翻译所有英文新闻的标题和摘要"""
    en_items = [(i, item) for i, item in enumerate(items) if item.get('lang') == 'en']
    if not en_items:
        return items
    
    if not TRANSLATE_API_KEY:
        print(f"\n⚠️ 跳过翻译（未设置 TRANSLATE_API_KEY）")
        print(f"   用法: TRANSLATE_API_KEY=sk-xxx python3 scripts/fetch_news.py")
        return items
    
    print(f"\n🌐 翻译 {len(en_items)} 条英文新闻 (使用 {TRANSLATE_MODEL})...")
    
    titles = [item['title'] for _, item in en_items]
    summaries = [item.get('summary', '') for _, item in en_items]
    
    translated_titles = ai_translate_batch(titles, batch_size=25)
    translated_summaries = ai_translate_batch(summaries, batch_size=15)
    
    success = 0
    for j, (i, item) in enumerate(en_items):
        if translated_titles[j] and translated_titles[j] != item['title']:
            items[i]['title_original'] = item['title']
            items[i]['title'] = translated_titles[j]
            success += 1
        if translated_summaries[j] and translated_summaries[j] != item.get('summary', ''):
            items[i]['summary_original'] = item.get('summary', '')
            items[i]['summary'] = translated_summaries[j]
        items[i]['lang'] = 'zh-translated'
    
    print(f"  ✅ 成功翻译 {success}/{len(en_items)} 条标题")
    return items

# ==================== RSS 抓取 ====================

def fetch_sina_finance(source, resp):
    """解析新浪财经API"""
    items = []
    try:
        text = resp.text.strip()
        # Remove JSONP callback if present
        if text.startswith('('):
            text = text[1:-1]
        data = json.loads(text)
        for entry in (data.get('result', {}).get('data', []))[:20]:
            title = entry.get('title', '')
            if not title:
                continue
            items.append({
                'id': make_id(title, source['name']),
                'title': title,
                'summary': clean_html(entry.get('intro', '') or entry.get('summary', ''))[:300],
                'link': entry.get('url', ''),
                'source': source['name'],
                'source_icon': source['icon'],
                'category': source['category'],
                'lang': source['lang'],
                'image': entry.get('img', {}).get('u', '') if isinstance(entry.get('img'), dict) else '',
                'pub_date': parse_date(entry.get('ctime', '') or entry.get('createTime', '')),
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': classify_importance(title, entry.get('intro', '')),
                'regions': detect_region(title, entry.get('intro', ''), source['name']),
                'priority': source['priority'],
            })
        print(f"  ✅ {source['name']}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {source['name']}: {str(e)[:80]}")
    return items

# ==================== 国内热搜平台抓取 ====================

def fetch_douyin_hot():
    """抓取抖音热搜榜"""
    import requests as req
    items = []
    name = '抖音热搜'
    icon = '🎵'
    try:
        r = req.get('https://www.douyin.com/aweme/v1/web/hot/search/list/',
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/'
            }, timeout=15)
        r.raise_for_status()
        data = r.json()
        word_list = data.get('data', {}).get('word_list', [])
        
        for entry in word_list[:30]:
            title = entry.get('word', '').strip()
            if not title:
                continue
            hot_value = entry.get('hot_value', 0)
            event_time = entry.get('event_time', 0)
            
            # 根据热度判断重要性
            importance = 3
            if hot_value > 10000000:
                importance = 5
            elif hot_value > 5000000:
                importance = 4
            
            pub_date = datetime.fromtimestamp(event_time, tz=timezone.utc).isoformat() if event_time else datetime.now(timezone.utc).isoformat()
            
            # 智能分类
            category = auto_classify_cn(title)
            
            items.append({
                'id': make_id(title, name),
                'title': title,
                'summary': f'🔥 热度: {hot_value:,}',
                'link': f'https://www.douyin.com/search/{title}',
                'source': name,
                'source_icon': icon,
                'category': category,
                'lang': 'zh',
                'image': '',
                'pub_date': pub_date,
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': importance,
                'regions': detect_region(title, '', name),
                'priority': 1,
                'hot_value': hot_value,
            })
        print(f"  ✅ {name}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:80]}")
    return items

def fetch_toutiao_hot():
    """抓取今日头条热榜"""
    import requests as req
    items = []
    name = '今日头条'
    icon = '📱'
    try:
        r = req.get('https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc',
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'},
            timeout=15)
        r.raise_for_status()
        data = r.json()
        entries = data.get('data', [])
        
        for i, entry in enumerate(entries[:30]):
            title = entry.get('Title', '').strip()
            if not title:
                continue
            hot_value = entry.get('HotValue', 0)
            try:
                hot_value = int(hot_value)
            except (ValueError, TypeError):
                hot_value = 0
            url = entry.get('Url', '')
            label = entry.get('Label', '')
            
            importance = 3
            if label == 'hot' or hot_value > 10000000:
                importance = 4
            if label == 'boom' or hot_value > 20000000:
                importance = 5
            if i < 3:
                importance = max(importance, 4)
            
            category = auto_classify_cn(title)
            
            items.append({
                'id': make_id(title, name),
                'title': title,
                'summary': f'🔥 热度: {hot_value:,}' + (f' · {label}' if label else ''),
                'link': url,
                'source': name,
                'source_icon': icon,
                'category': category,
                'lang': 'zh',
                'image': '',
                'pub_date': datetime.now(timezone.utc).isoformat(),
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': importance,
                'regions': detect_region(title, '', name),
                'priority': 1,
                'hot_value': hot_value,
            })
        print(f"  ✅ {name}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:80]}")
    return items

def fetch_36kr_newsflash():
    """抓取36氪快讯（财经科技）"""
    import requests as req
    items = []
    name = '36氪快讯'
    icon = '💼'
    try:
        r = req.get('https://36kr.com/newsflashes',
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'},
            timeout=15)
        r.raise_for_status()
        
        m = re.search(r'window\.initialState\s*=\s*({.+?})\s*</script>', r.text, re.DOTALL)
        if not m:
            print(f"  ❌ {name}: 无法解析页面数据")
            return items
        
        raw = m.group(1)
        data = json.loads(raw)
        flash_list = data.get('newsflashCatalogData', {}).get('data', {}).get('newsflashList', {}).get('data', {}).get('itemList', [])
        
        for entry in flash_list[:20]:
            mat = entry.get('templateMaterial', {})
            title = mat.get('widgetTitle', '').strip()
            if not title:
                continue
            summary = clean_html(mat.get('widgetContent', ''))[:300]
            pub_time = mat.get('publishTime', 0)
            item_id = mat.get('itemId', '')
            
            pub_date = datetime.fromtimestamp(pub_time / 1000, tz=timezone.utc).isoformat() if pub_time > 1000000000 else datetime.now(timezone.utc).isoformat()
            
            # 36氪主要是财经科技
            category = auto_classify_cn(title + ' ' + summary)
            if category == '时事':
                category = '财经'  # 36氪偏向财经
            
            items.append({
                'id': make_id(title, name),
                'title': title,
                'summary': summary,
                'link': f'https://36kr.com/newsflashes/{item_id}' if item_id else '',
                'source': name,
                'source_icon': icon,
                'category': category,
                'lang': 'zh',
                'image': '',
                'pub_date': pub_date,
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': classify_importance(title, summary),
                'regions': detect_region(title, summary, name),
                'priority': 1,
            })
        print(f"  ✅ {name}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:80]}")
    return items

def fetch_xiaohongshu_explore():
    """抓取小红书探索热门内容"""
    import requests as req
    items = []
    name = '小红书热门'
    icon = '📕'
    try:
        r = req.get('https://www.xiaohongshu.com/explore',
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
            timeout=15)
        r.raise_for_status()
        
        m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.+?)</script>', r.text, re.DOTALL)
        if not m:
            print(f"  ❌ {name}: 无法解析页面数据")
            return items
        
        raw = m.group(1).strip().rstrip(';').replace('undefined', 'null')
        data = json.loads(raw)
        feeds = data.get('feed', {}).get('feeds', [])
        
        for entry in feeds[:20]:
            nc = entry.get('noteCard', entry)
            title = nc.get('displayTitle', '').strip()
            if not title:
                continue
            
            user = nc.get('user', {}).get('nickname', '')
            likes = nc.get('interactInfo', {}).get('likedCount', '')
            note_type = nc.get('type', 'normal')
            note_id = entry.get('id', '')
            
            category = auto_classify_cn(title)
            
            # 根据点赞估算重要性
            importance = 3
            try:
                like_num = int(str(likes).replace('万', '0000').replace('.', ''))
                if like_num > 50000:
                    importance = 5
                elif like_num > 10000:
                    importance = 4
            except:
                pass
            
            items.append({
                'id': make_id(title, name),
                'title': title,
                'summary': f'👤 {user} · ❤️ {likes}' + (f' · 🎬 视频' if note_type == 'video' else ''),
                'link': f'https://www.xiaohongshu.com/explore/{note_id}' if note_id else '',
                'source': name,
                'source_icon': icon,
                'category': category,
                'lang': 'zh',
                'image': '',
                'pub_date': datetime.now(timezone.utc).isoformat(),
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': importance,
                'regions': detect_region(title, '', name),
                'priority': 2,
            })
        print(f"  ✅ {name}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:80]}")
    return items

def auto_classify_cn(text):
    """中文内容智能分类"""
    finance_kw = ['股', '基金', '理财', '投资', '财经', '上市', '涨停', '跌停', '市值', 
                  'A股', '港股', '美股', '债券', '期货', '外汇', '央行', '利率', '通胀',
                  'GDP', '经济', '金融', '银行', '保险', '证券', '融资', '资本', '估值',
                  '营收', '利润', '回购', '分红', '减持', '增持', '收购', '并购', '上涨',
                  '下跌', '牛市', '熊市', '交易', '资金', '指数', '板块', '概念股', '市场',
                  '消费', '零售', '出口', '进口', '税', '油价', '金价', '比特币', '数字货币']
    politics_kw = ['政治', '政府', '国务院', '全国人大', '政协', '两会', '总书记', '主席',
                   '总统', '选举', '外交', '制裁', '条约', '法案', '立法', '法院', '政策',
                   '改革', '一带一路', '台湾', '南海', '国防', '军事', '部队']
    tech_kw = ['AI', '人工智能', '芯片', '半导体', '5G', '6G', '机器人', '自动驾驶',
               '大模型', '算法', 'ChatGPT', '量子', '航天', '火箭', '卫星', '科技',
               '互联网', '手机', '苹果', '华为', '特斯拉', '新能源', '电池', '光伏',
               '生物', '医药', '疫苗', '基因', 'Kimi', 'DeepSeek', '千问']
    intl_kw = ['美国', '俄罗斯', '欧洲', '日本', '韩国', '朝鲜', '中东', '以色列',
               '乌克兰', '北约', '联合国', '国际', '全球', '海外', '出海']
    
    for kw in finance_kw:
        if kw in text:
            return '财经'
    for kw in politics_kw:
        if kw in text:
            return '政治'
    for kw in tech_kw:
        if kw in text:
            return '科技'
    for kw in intl_kw:
        if kw in text:
            return '国际'
    return '时事'

# ==================== RSS 抓取 ====================

def fetch_single_rss(source):
    """抓取单个RSS源"""
    import feedparser
    import requests
    
    items = []
    try:
        # 使用requests获取内容（更好的超时控制）
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(source['url'], headers=headers, timeout=15)
        resp.raise_for_status()
        
        # 新浪API特殊处理
        if source.get('type') == 'sina_api':
            return fetch_sina_finance(source, resp)
        
        feed = feedparser.parse(resp.content)
        
        for entry in feed.entries[:20]:  # 每个源最多取20条
            title = clean_html(entry.get('title', ''))
            if not title:
                continue
            
            summary = clean_html(
                entry.get('summary', '') or 
                entry.get('description', '') or 
                entry.get('content', [{}])[0].get('value', '') if entry.get('content') else ''
            )
            
            link = entry.get('link', '')
            pub_date = entry.get('published', '') or entry.get('updated', '')
            
            # 提取图片
            image = ''
            if entry.get('media_content'):
                image = entry['media_content'][0].get('url', '')
            elif entry.get('media_thumbnail'):
                image = entry['media_thumbnail'][0].get('url', '')
            elif entry.get('enclosures'):
                for enc in entry['enclosures']:
                    if 'image' in enc.get('type', ''):
                        image = enc.get('href', '')
                        break
            
            importance = classify_importance(title, summary)
            regions = detect_region(title, summary, source['name'])
            
            item = {
                'id': make_id(title, source['name']),
                'title': title,
                'summary': summary[:300],
                'link': link,
                'source': source['name'],
                'source_icon': source['icon'],
                'category': source['category'],
                'lang': source['lang'],
                'image': image,
                'pub_date': parse_date(pub_date),
                'fetch_time': datetime.now(timezone.utc).isoformat(),
                'importance': importance,
                'regions': regions,
                'priority': source['priority'],
            }
            items.append(item)
        
        print(f"  ✅ {source['name']}: {len(items)} 条")
    except Exception as e:
        print(f"  ❌ {source['name']}: {str(e)[:80]}")
    
    return items

def fetch_all_news():
    """并发抓取所有RSS源 + 国内热搜平台"""
    cn_fetchers = [
        ('抖音热搜', fetch_douyin_hot),
        ('今日头条', fetch_toutiao_hot),
        ('36氪快讯', fetch_36kr_newsflash),
        ('小红书热门', fetch_xiaohongshu_explore),
    ]
    total_sources = len(RSS_SOURCES) + len(cn_fetchers)
    
    print(f"\n🌐 开始抓取全球新闻 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"   共 {total_sources} 个源 ({len(RSS_SOURCES)} RSS + {len(cn_fetchers)} 国内热搜)\n")
    
    all_items = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        # RSS sources
        futures = {executor.submit(fetch_single_rss, src): src['name'] for src in RSS_SOURCES}
        # 国内热搜平台
        for name, func in cn_fetchers:
            futures[executor.submit(func)] = name
        
        for future in as_completed(futures):
            items = future.result()
            all_items.extend(items)
    
    # 去重（按标题相似度）
    seen_titles = set()
    unique_items = []
    for item in all_items:
        # 简单去重：标题前30字符
        title_key = re.sub(r'\s+', '', item['title'][:30]).lower()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_items.append(item)
    
    # 排序：重要性 × 优先级 × 时间
    def sort_key(item):
        try:
            dt = datetime.fromisoformat(item['pub_date'].replace('Z', '+00:00'))
            hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        except:
            hours_ago = 24
        
        # 综合分数：重要性高+源优先级高+越新越好
        return -(item['importance'] * 10 + (3 - item['priority']) * 5 - hours_ago * 0.5)
    
    unique_items.sort(key=sort_key)
    unique_items = unique_items[:MAX_NEWS]
    
    # 翻译英文新闻
    unique_items = translate_items(unique_items)
    
    print(f"\n📊 汇总: 抓取 {len(all_items)} 条, 去重后 {len(unique_items)} 条")
    
    # 统计
    cats = {}
    for item in unique_items:
        cats[item['category']] = cats.get(item['category'], 0) + 1
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"   {cat}: {count} 条")
    
    return unique_items

def save_news(items):
    """保存为JSON"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 合并历史数据（保留最近的）
    existing = []
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                existing = data.get('items', [])
        except:
            pass
    
    # 合并去重
    existing_ids = {item['id'] for item in items}
    for old_item in existing:
        if old_item['id'] not in existing_ids:
            items.append(old_item)
            existing_ids.add(old_item['id'])
    
    # 只保留7天内的
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    items = [i for i in items if i.get('pub_date', '') >= cutoff or i.get('fetch_time', '') >= cutoff]
    items = items[:MAX_NEWS]
    
    output = {
        'last_update': datetime.now(timezone.utc).isoformat(),
        'total': len(items),
        'sources': len(RSS_SOURCES),
        'items': items
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 已保存 {len(items)} 条新闻到 {OUTPUT_FILE}")

# ==================== 主程序 ====================

if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='全球时事新闻聚合器')
    parser.add_argument('--loop', type=int, default=0, help='循环抓取间隔(分钟), 0=只执行一次')
    parser.add_argument('--api-key', type=str, default='', help='AI API Key (用于翻译英文新闻)')
    parser.add_argument('--api-url', type=str, default='', help='AI API URL')
    parser.add_argument('--model', type=str, default='', help='AI模型名称')
    args = parser.parse_args()
    
    # 设置翻译API
    _mod = sys.modules[__name__]
    if args.api_key:
        _mod.TRANSLATE_API_KEY = args.api_key
    if args.api_url:
        _mod.TRANSLATE_API_URL = args.api_url
    if args.model:
        _mod.TRANSLATE_MODEL = args.model
    
    # 安装依赖
    try:
        import feedparser
    except ImportError:
        print("📦 安装 feedparser...")
        os.system("pip3 install feedparser")
        import feedparser
    
    try:
        import requests
    except ImportError:
        print("📦 安装 requests...")
        os.system("pip3 install requests")
        import requests
    
    if args.loop > 0:
        print(f"🔄 循环模式: 每 {args.loop} 分钟抓取一次 (Ctrl+C 退出)")
        while True:
            try:
                items = fetch_all_news()
                save_news(items)
                print(f"\n⏰ 下次抓取: {(datetime.now() + timedelta(minutes=args.loop)).strftime('%H:%M:%S')}")
                time.sleep(args.loop * 60)
            except KeyboardInterrupt:
                print("\n👋 已停止")
                break
            except Exception as e:
                print(f"\n❌ 出错: {e}")
                traceback.print_exc()
                time.sleep(60)
    else:
        items = fetch_all_news()
        save_news(items)
        print("\n✅ 完成!")
