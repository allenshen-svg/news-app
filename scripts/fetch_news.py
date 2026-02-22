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
MAX_NEWS = 200  # 最多保留条数

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
    """并发抓取所有RSS源"""
    print(f"\n🌐 开始抓取全球新闻 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"   共 {len(RSS_SOURCES)} 个源\n")
    
    all_items = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_single_rss, src): src for src in RSS_SOURCES}
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
    parser = argparse.ArgumentParser(description='全球时事新闻聚合器')
    parser.add_argument('--loop', type=int, default=0, help='循环抓取间隔(分钟), 0=只执行一次')
    args = parser.parse_args()
    
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
