#!/usr/bin/env python3
"""
模块四：系统整体架构 - 热点发现编排器
============================================================

系统架构：
┌────────────────────────────────────────────────────────────┐
│                     定时调度层 (Cron / Loop)                │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  抖音     │  │  小红书   │  │  微博     │  │ B站/知乎  │  │
│  │ Crawler  │  │ Crawler  │  │ Crawler  │  │ Crawler  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │             │             │             │          │
│       └──────┬──────┴──────┬──────┘             │          │
│              ▼             │                    │          │
│  ┌───────────────────┐     │                    │          │
│  │  Raw Data Store   │◄────┴────────────────────┘          │
│  │  data/raw_feeds/  │                                     │
│  └────────┬──────────┘                                     │
│           ▼                                                │
│  ┌───────────────────┐                                     │
│  │  NLP Pipeline     │  jieba分词 → TF-IDF → TextRank     │
│  │  关键词提取        │  实体识别 → 新词发现                │
│  └────────┬──────────┘                                     │
│           ▼                                                │
│  ┌───────────────────┐                                     │
│  │  Time Series DB   │  keyword_history.json               │
│  │  滑动窗口统计      │  10min窗口 × 144 = 24h             │
│  └────────┬──────────┘                                     │
│           ▼                                                │
│  ┌───────────────────┐                                     │
│  │  Burst Detector   │  Z-Score + MACD + Newton Cooling    │
│  │  突发检测          │                                     │
│  └────────┬──────────┘                                     │
│           ▼                                                │
│  ┌───────────────────┐                                     │
│  │  Heat Scorer      │  H(t) = αF·e^{-λΔt} + βA + γS + δE│
│  │  热力计算          │                                     │
│  └────────┬──────────┘                                     │
│           ▼                                                │
│  ┌───────────────────┐     ┌───────────────────┐           │
│  │  trends.json      │────▶│  前端展示层        │           │
│  │  (趋势结果)        │     │  index.html        │           │
│  └───────────────────┘     └───────────────────┘           │
│                                                            │
├────────────────────────────────────────────────────────────┤
│  核心技术栈：                                               │
│  • Python 3.9+ (采集+处理)                                  │
│  • jieba (中文分词/TF-IDF/TextRank)                         │
│  • requests + Session (HTTP采集)                            │
│  • JSON文件 (轻量存储，可替换为Redis/MongoDB)                │
│  • 前端: Vanilla JS + SVG Sparklines (可视化)               │
│                                                            │
│  生产级扩展方向：                                            │
│  • 采集层 → Scrapy/Playwright 集群                          │
│  • 消息队列 → Kafka/RabbitMQ                                │
│  • 流处理 → Flink/Spark Streaming                           │
│  • 存储 → Redis(热数据) + MongoDB(冷数据)                    │
│  • NLP → HanLP/LAC + BERT-NER                              │
│  • 部署 → Docker + K8s                                      │
└────────────────────────────────────────────────────────────┘

使用方式：
  # 单次运行
  python3 scripts/discover_trends.py
  
  # 循环运行 (每10分钟)
  python3 scripts/discover_trends.py --loop 10
  
  # 指定平台
  python3 scripts/discover_trends.py --platforms weibo,bilibili,zhihu
  
  # 同时更新新闻
  python3 scripts/discover_trends.py --with-news
"""

import os
import sys
import json
import time
import logging
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 确保可以导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))

from feed_crawler import CrawlOrchestrator, RawContent
from trend_engine import TrendEngine

# ==================== 配置 ====================
DATA_DIR = Path(__file__).parent.parent / "data"
TRENDS_FILE = DATA_DIR / "trends.json"
NEWS_FILE = DATA_DIR / "news.json"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('discover')


def load_news_as_raw(news_file=None) -> list:
    """
    将 news.json 中的已抓取新闻转换为 RawContent 格式
    
    用途：即使爬虫全部失败，也能利用已有新闻数据做趋势分析
    """
    if news_file is None:
        news_file = NEWS_FILE
    
    items = []
    try:
        if not Path(news_file).exists():
            return items
        
        with open(news_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        news_list = data.get('items', [])
        for n in news_list:
            # 跳过之前发现的趋势（避免循环引用）
            if n.get('is_discovered_trend'):
                continue
            
            title = n.get('title', '').strip()
            if not title or len(title) < 4:
                continue
            
            source = n.get('source', '')
            # 映射到平台名
            platform_map = {
                '抖音热搜': 'douyin',
                '小红书热门': 'xiaohongshu', 
                '今日头条': 'toutiao',
                '36氪快讯': '36kr',
                '新浪财经': 'sina',
            }
            platform = 'news'
            for key, val in platform_map.items():
                if key in source:
                    platform = val
                    break
            
            items.append(RawContent(
                platform=platform,
                content_id=f"news_{n.get('id', '')}",
                title=title,
                text=n.get('summary', title),
                views=n.get('hot_value', 0) or 0,
                tags=[n.get('category', '')],
                url=n.get('link', ''),
                pub_time=n.get('pub_date', ''),
                crawl_time=n.get('fetch_time', ''),
                content_type='article',
                extra={'source': source, 'lang': n.get('lang', '')}
            ))
        
        logger.info(f"  📰 从 news.json 加载 {len(items)} 条新闻作为补充数据")
    except Exception as e:
        logger.error(f"  ⚠️ 加载 news.json 失败: {str(e)[:60]}")
    
    return items


def discover_trends(platforms=None, keyword_count=10, topK=50, proxy=None):
    """
    执行一次完整的热点发现流程
    
    1. 多平台数据采集
    2. 补充已有新闻数据 (news.json)
    3. NLP 关键词提取
    4. 突发检测
    5. 热力值计算
    6. 输出趋势排名
    """
    start_time = time.time()
    
    print(f"\n{'='*60}")
    print(f"🔬 热点发现系统 v2.0")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   平台: {', '.join(platforms) if platforms else '全部'}")
    print(f"{'='*60}")
    
    # ── 阶段1: 数据采集 ──
    orchestrator = CrawlOrchestrator(proxy=proxy)
    raw_items = orchestrator.crawl_all(
        platforms=platforms,
        keyword_count=keyword_count
    )
    
    # ── 阶段1.5: 补充已有新闻数据 ──
    news_items = load_news_as_raw()
    if news_items:
        raw_items.extend(news_items)
        print(f"  📊 爬虫 {len(raw_items) - len(news_items)} 条 + 新闻 {len(news_items)} 条 = 总计 {len(raw_items)} 条")
    
    if not raw_items:
        print("\n⚠️ 未采集到任何数据，请先运行 python3 scripts/fetch_news.py")
        return []
    
    # ── 阶段2: 趋势分析 ──
    engine = TrendEngine()
    trends = engine.process(raw_items, topK=topK)
    
    # ── 阶段3: 保存结果 ──
    engine.save_trends(trends, TRENDS_FILE)
    
    # ── 阶段4: 合并到新闻数据 ──
    merge_trends_to_news(trends)
    
    elapsed = time.time() - start_time
    
    # 输出摘要
    print(f"\n{'='*60}")
    print(f"✅ 热点发现完成 ({elapsed:.1f}s)")
    print(f"   采集内容: {len(raw_items)} 条")
    print(f"   发现趋势: {len(trends)} 个")
    print(f"   突发热点: {sum(1 for t in trends if t.is_burst)} 个")
    print(f"   上升趋势: {sum(1 for t in trends if t.trend_direction in ('↑','↗'))} 个")
    print(f"{'='*60}")
    
    if trends:
        print(f"\n🏆 Top 10 热点:")
        for i, t in enumerate(trends[:10]):
            burst = '🔴 BURST' if t.is_burst else ''
            direction = t.trend_direction
            platforms = ','.join(t.platforms[:3])
            print(f"   {i+1:2d}. [{t.heat_score:5.1f}] {direction} {t.keyword:12s} "
                  f"| freq={t.frequency:3d} | {platforms:20s} {burst}")
    
    return trends


def merge_trends_to_news(trends):
    """
    将发现的趋势合并到 news.json 中
    
    趋势会作为特殊的新闻项出现在列表中，
    标记 source='热点发现' 以区分常规新闻
    """
    if not trends:
        return
    
    # 加载现有新闻
    news_items = []
    if NEWS_FILE.exists():
        try:
            with open(NEWS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                news_items = data.get('items', [])
        except (json.JSONDecodeError, IOError):
            pass
    
    # 移除旧的发现趋势
    news_items = [n for n in news_items if n.get('source') != '🔬 热点发现']
    
    # 添加新趋势
    for trend in trends[:30]:  # 最多30个
        if trend.heat_score < 10:
            continue  # 过滤低热度
        
        # 构建摘要
        parts = []
        if trend.is_burst:
            parts.append('⚡ 突发热点')
        parts.append(f'🔥 热力值: {trend.heat_score:.0f}')
        parts.append(f'📊 频率: {trend.frequency}')
        if trend.platforms:
            parts.append(f'📱 {",".join(trend.platforms[:3])}')
        if trend.macd_signal == 'bullish':
            parts.append('📈 趋势上升')
        if trend.related_titles:
            parts.append(f'相关: {trend.related_titles[0][:50]}')
        
        importance = 3
        if trend.is_burst:
            importance = 5
        elif trend.heat_score >= 60:
            importance = 4
        elif trend.heat_score >= 30:
            importance = 3
        
        import hashlib
        news_item = {
            'id': f"trend_{hashlib.md5(trend.keyword.encode()).hexdigest()[:10]}",
            'title': f"{trend.trend_direction} {trend.keyword}",
            'summary': ' · '.join(parts),
            'link': '',
            'source': '🔬 热点发现',
            'source_icon': '🔬',
            'category': trend.category or '时事',
            'lang': 'zh',
            'image': '',
            'pub_date': trend.peak_time or datetime.now(timezone.utc).isoformat(),
            'fetch_time': datetime.now(timezone.utc).isoformat(),
            'importance': importance,
            'regions': [],
            'priority': 0,  # 高优先级
            'hot_value': int(trend.heat_score * 1000),
            'is_discovered_trend': True,
            'trend_data': {
                'heat_score': trend.heat_score,
                'frequency': trend.frequency,
                'acceleration': trend.acceleration,
                'is_burst': trend.is_burst,
                'z_score': trend.burst_z_score,
                'macd_signal': trend.macd_signal,
                'direction': trend.trend_direction,
                'platforms': trend.platforms,
                'sparkline': trend.sparkline[-20:],
            }
        }
        news_items.append(news_item)
    
    # 保存
    output = {
        'last_update': datetime.now(timezone.utc).isoformat(),
        'total': len(news_items),
        'sources': len(set(n.get('source', '') for n in news_items)),
        'items': news_items
    }
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    trend_count = sum(1 for n in news_items if n.get('is_discovered_trend'))
    print(f"  📰 已合并 {trend_count} 个趋势到新闻数据")


# ==================== CLI 入口 ====================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='🔬 抖音/小红书 实时热点发现系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 单次运行 (全平台)
  python3 scripts/discover_trends.py
  
  # 循环运行 (每10分钟)
  python3 scripts/discover_trends.py --loop 10
  
  # 只采集特定平台
  python3 scripts/discover_trends.py --platforms weibo,bilibili,zhihu
  
  # 增加种子关键词数量
  python3 scripts/discover_trends.py --keywords 20
  
  # 同时运行新闻抓取
  python3 scripts/discover_trends.py --with-news

算法说明:
  热力值公式: H(t) = α·F(t)·e^{-λΔt} + β·A(t) + γ·S(t) + δ·E(t)
  突发检测: Z-Score > 2.5 + MACD金叉
  衰减模型: Newton冷却定律, 半衰期4小时
        """
    )
    
    parser.add_argument('--loop', type=int, default=0,
                        help='循环运行间隔(分钟), 0=单次运行')
    parser.add_argument('--platforms', type=str, default='',
                        help='采集平台(逗号分隔): bilibili,baidu,xiaohongshu,weibo,zhihu,douyin (默认: bilibili,baidu,xiaohongshu)')
    parser.add_argument('--keywords', type=int, default=10,
                        help='种子关键词数量 (默认10)')
    parser.add_argument('--topk', type=int, default=50,
                        help='输出 Top-K 趋势 (默认50)')
    parser.add_argument('--proxy', type=str, default='',
                        help='HTTP代理 (如 http://127.0.0.1:7890)')
    parser.add_argument('--with-news', action='store_true',
                        help='同时运行新闻抓取 (fetch_news.py)')
    parser.add_argument('--verbose', action='store_true',
                        help='详细日志')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 安装依赖
    try:
        import jieba
    except ImportError:
        print("📦 安装 jieba 中文分词库...")
        os.system(f"{sys.executable} -m pip install jieba -q")
    
    try:
        import requests
    except ImportError:
        print("📦 安装 requests...")
        os.system(f"{sys.executable} -m pip install requests -q")
    
    # 解析平台参数（默认只用可靠平台）
    platforms = None
    if args.platforms:
        platforms = [p.strip() for p in args.platforms.split(',') if p.strip()]
    else:
        # 默认只用不需要登录的可靠平台
        platforms = ['bilibili', 'baidu', 'xiaohongshu']
    
    if args.loop > 0:
        print(f"🔄 循环模式: 每 {args.loop} 分钟运行一次 (Ctrl+C 退出)")
        while True:
            try:
                discover_trends(
                    platforms=platforms,
                    keyword_count=args.keywords,
                    topK=args.topk,
                    proxy=args.proxy or None
                )
                
                # 同时运行新闻抓取
                if args.with_news:
                    print("\n📰 运行新闻抓取...")
                    os.system(f"{sys.executable} {Path(__file__).parent / 'fetch_news.py'}")
                
                next_run = (datetime.now() + timedelta(minutes=args.loop)).strftime('%H:%M:%S')
                print(f"\n⏰ 下次运行: {next_run}")
                time.sleep(args.loop * 60)
                
            except KeyboardInterrupt:
                print("\n👋 已停止")
                break
            except Exception as e:
                print(f"\n❌ 出错: {e}")
                traceback.print_exc()
                time.sleep(60)
    else:
        trends = discover_trends(
            platforms=platforms,
            keyword_count=args.keywords,
            topK=args.topk,
            proxy=args.proxy or None
        )
        
        if args.with_news:
            print("\n📰 运行新闻抓取...")
            os.system(f"{sys.executable} {Path(__file__).parent / 'fetch_news.py'}")
        
        print("\n✅ 完成!")
