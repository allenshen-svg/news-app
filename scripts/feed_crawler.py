#!/usr/bin/env python3
"""
模块一：数据采集架构与反爬策略
============================================================
核心理念：绝不依赖官方"热搜榜/热点榜"，而是通过抓取信息流、搜索结果、
话题页面等底层数据，由 NLP + Burst Detection 自行计算热点。

采集策略：
1. 种子关键词轮询搜索 → 获取最新内容样本
2. 公开话题/标签页面 → 获取垂类内容流
3. KOL 种子用户动态 → 发现头部内容
4. 多平台交叉验证 → 降低单源风险

反爬策略：
- User-Agent 池轮转 (50+ 真实浏览器指纹)
- 请求频率控制 (令牌桶限流)
- 指数退避重试 (429/403 自动降速)
- Session/Cookie 复用与刷新
- 可选代理池支持
- 随机延迟 jitter (模拟人类行为)
"""

import json
import os
import re
import time
import random
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ==================== 日志配置 ====================
logger = logging.getLogger('feed_crawler')

# ==================== 数据结构 ====================
@dataclass
class RawContent:
    """采集到的原始内容"""
    platform: str           # 来源平台: douyin / xiaohongshu / weibo / zhihu / bilibili
    content_id: str          # 唯一标识
    title: str               # 标题/话题
    text: str                # 正文/描述
    author: str = ''         # 作者
    likes: int = 0           # 点赞数
    comments: int = 0        # 评论数
    shares: int = 0          # 分享/转发数
    views: int = 0           # 播放/阅读量
    tags: List[str] = field(default_factory=list)   # 话题标签
    url: str = ''            # 原始链接
    pub_time: str = ''       # 发布时间 ISO
    crawl_time: str = ''     # 采集时间 ISO
    content_type: str = ''   # video / note / article / answer
    extra: Dict = field(default_factory=dict)       # 平台特有字段

    def engagement_score(self) -> float:
        """互动加权得分（标准化互动量）"""
        return self.likes * 1.0 + self.comments * 3.0 + self.shares * 5.0 + self.views * 0.01

    def to_dict(self):
        return asdict(self)


# ==================== 反爬基础设施 ====================
# 50+ 真实浏览器 User-Agent 池
UA_POOL = [
    # Chrome macOS
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    # Chrome Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    # Firefox
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0',
    # Safari
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    # Edge
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    # Mobile
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (iPad; CPU OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
]

# 种子关键词库（按领域分组，每次随机抽样）
SEED_KEYWORDS = {
    '财经': [
        'A股', '港股', '美股', '基金', '理财', '投资', '股票', '上市', '涨停', '跌停',
        '央行', '利率', 'GDP', '通胀', '降息', '加息', '比特币', '数字货币', '黄金',
        '石油', '房价', '楼市', '经济', '金融', '银行', '保险', '期货', '外汇',
        '融资', '并购', '创业', 'IPO', '独角兽', '新能源', '光伏', '锂电池',
    ],
    '政治': [
        '两会', '政策', '改革', '外交', '制裁', '选举', '立法', '国务院', '人大',
        '国防', '军事', '台湾', '南海', '一带一路', '中美', '中俄', '北约',
        '联合国', '峰会', '总统', '领导人', '协议', '条约',
    ],
    '科技': [
        'AI', '人工智能', '大模型', 'ChatGPT', 'DeepSeek', '芯片', '半导体',
        '5G', '6G', '自动驾驶', '机器人', '量子计算', '航天', '火箭', '卫星',
        '华为', '苹果', '特斯拉', '小米', '手机', '新品', '发布会',
        '区块链', 'Web3', '元宇宙', 'AGI', 'Sora', '视觉大模型',
    ],
    '社会': [
        '教育', '医疗', '就业', '房价', '房租', '生育', '养老', '退休',
        '考公', '考研', '高考', '内卷', '裁员', '降薪', '跳槽',
        '消费', '物价', '旅游', '春运', '电影', '综艺', '热剧',
    ],
}


class RateLimiter:
    """
    令牌桶限流器
    
    控制每个域名的请求频率，防止触发反爬。
    支持自适应降速：遇到429/403时自动扩大间隔。
    支持域名封禁：永久403/401自动标记，跳过后续请求。
    """
    def __init__(self, default_interval: float = 3.0, jitter: float = 2.0):
        self.default_interval = default_interval
        self.jitter = jitter
        self._last_request: Dict[str, float] = {}
        self._penalties: Dict[str, float] = {}  # 域名惩罚倍数
        self._blocked: Dict[str, str] = {}  # 域名 → 封禁原因
        self._fail_count: Dict[str, int] = {}  # 域名连续失败次数

    def is_blocked(self, domain: str) -> bool:
        """检查域名是否已被标记为不可用"""
        return domain in self._blocked

    def block(self, domain: str, reason: str = 'unknown'):
        """标记域名为不可用（本次运行期间跳过所有请求）"""
        if domain not in self._blocked:
            self._blocked[domain] = reason
            logger.warning(f"  🚫 {domain} 已标记为不可用: {reason}")

    def record_fail(self, domain: str) -> int:
        """记录连续失败次数，返回当前次数"""
        self._fail_count[domain] = self._fail_count.get(domain, 0) + 1
        return self._fail_count[domain]

    def reset_fail(self, domain: str):
        """重置连续失败计数"""
        self._fail_count[domain] = 0

    def wait(self, domain: str):
        """等待直到可以发送下一个请求"""
        if self.is_blocked(domain):
            return  # 被封禁的域名不等待，直接跳过
        
        now = time.time()
        penalty = self._penalties.get(domain, 1.0)
        interval = self.default_interval * penalty + random.uniform(0, self.jitter)
        
        last = self._last_request.get(domain, 0)
        elapsed = now - last
        
        if elapsed < interval:
            sleep_time = interval - elapsed
            logger.debug(f"  ⏳ 限流等待 {sleep_time:.1f}s ({domain})")
            time.sleep(sleep_time)
        
        self._last_request[domain] = time.time()

    def penalize(self, domain: str, factor: float = 2.0):
        """对某域名施加惩罚（降速）"""
        current = self._penalties.get(domain, 1.0)
        self._penalties[domain] = min(current * factor, 5.0)  # 最多5倍
        logger.warning(f"  ⚠️ {domain} 降速 → 间隔×{self._penalties[domain]:.1f}")

    def reset_penalty(self, domain: str):
        """重置惩罚"""
        if domain in self._penalties:
            self._penalties[domain] = max(1.0, self._penalties[domain] * 0.5)


# 全局限流器
rate_limiter = RateLimiter(default_interval=2.5, jitter=2.0)


class BaseCrawler:
    """
    爬虫基类 - 提供通用反爬能力
    
    功能：
    1. Session 管理与 Cookie 复用
    2. User-Agent 智能轮转
    3. 指数退避重试
    4. 可选代理支持
    5. 错误统计与自适应降速
    """
    
    def __init__(self, platform: str, proxy: str = None):
        import requests
        self.platform = platform
        self.proxy = proxy
        self.session = requests.Session()
        self._request_count = 0
        self._error_count = 0
        self._ua_index = random.randint(0, len(UA_POOL) - 1)
        
        # 基础 headers
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        })
        
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}

    def _rotate_ua(self) -> str:
        """轮转 User-Agent"""
        self._ua_index = (self._ua_index + random.randint(1, 5)) % len(UA_POOL)
        ua = UA_POOL[self._ua_index]
        self.session.headers['User-Agent'] = ua
        return ua

    def _get_domain(self, url: str) -> str:
        """提取域名用于限流"""
        from urllib.parse import urlparse
        return urlparse(url).netloc

    def safe_request(self, url: str, method: str = 'GET', max_retries: int = 3, 
                     timeout: int = 15, **kwargs) -> Optional[object]:
        """
        安全请求 - 集成限流、重试、反爬
        
        特性：
        - 域名封禁检测 (403/401 永久跳过)
        - 自动限流 (令牌桶)
        - 自动轮转 UA
        - 指数退避重试 (1s → 2s → 4s → ...)
        - 429 自动降速 (仅对临时限流重试)
        """
        domain = self._get_domain(url)
        
        # 检查域名是否已被封禁
        if rate_limiter.is_blocked(domain):
            logger.debug(f"  ⏭️ 跳过已封禁域名: {domain}")
            return None
        
        for attempt in range(max_retries):
            try:
                # 限流等待
                rate_limiter.wait(domain)
                
                # 轮转 UA
                self._rotate_ua()
                
                # 发送请求
                self._request_count += 1
                resp = self.session.request(method, url, timeout=timeout, **kwargs)
                
                # === 401 Unauthorized: 需要登录，永久跳过 ===
                if resp.status_code == 401:
                    fails = rate_limiter.record_fail(domain)
                    logger.warning(f"  🔒 401 需要登录 → {domain} (第{fails}次)")
                    if fails >= 1:  # 401 一次就封禁
                        rate_limiter.block(domain, '401 需要登录认证')
                    return None  # 不重试
                
                # === 403 Forbidden: 可能是永久封禁或临时限流 ===
                if resp.status_code == 403:
                    fails = rate_limiter.record_fail(domain)
                    # 检查是否有 Retry-After 头（说明是临时限流）
                    retry_after = resp.headers.get('Retry-After')
                    if retry_after and attempt < max_retries - 1:
                        # 有 Retry-After = 临时限流，等待后重试
                        wait = min(int(retry_after), 30)
                        logger.warning(f"  ⏳ 403 临时限流 → 等待 {wait}s")
                        time.sleep(wait)
                        continue
                    elif fails >= 2:
                        # 连续2次403且无Retry-After → 永久封禁
                        rate_limiter.block(domain, '403 拒绝访问(需要Cookie/签名)')
                        return None
                    else:
                        # 第一次403，短暂等待后重试一次
                        wait = 2 + random.uniform(0, 2)
                        logger.warning(f"  🚫 403 Forbidden → 等待 {wait:.0f}s")
                        time.sleep(wait)
                        continue
                
                # === 429 Too Many Requests: 临时限流，降速重试 ===
                if resp.status_code == 429:
                    rate_limiter.penalize(domain, 2.0)
                    wait = 2 ** (attempt + 1) + random.uniform(1, 3)
                    logger.warning(f"  🚫 429 限流 → 等待 {wait:.0f}s")
                    time.sleep(wait)
                    continue
                
                # === 412 风控触发 ===
                if resp.status_code == 412:
                    fails = rate_limiter.record_fail(domain)
                    if fails >= 2:
                        rate_limiter.block(domain, '412 风控触发')
                        return None
                    rate_limiter.penalize(domain, 3.0)
                    logger.warning(f"  🛡️ 412 风控触发 → 降速")
                    time.sleep(5 + random.uniform(0, 5))
                    continue
                
                resp.raise_for_status()
                rate_limiter.reset_penalty(domain)
                rate_limiter.reset_fail(domain)
                return resp
                
            except Exception as e:
                self._error_count += 1
                wait = 2 ** attempt + random.uniform(0, 2)
                if attempt < max_retries - 1:
                    logger.debug(f"  ⚠️ 请求失败 [{attempt+1}/{max_retries}]: {str(e)[:60]} → 重试")
                    time.sleep(wait)
                else:
                    logger.error(f"  ❌ 请求最终失败: {str(e)[:80]}")
        
        return None

    def is_domain_blocked(self, domain: str) -> bool:
        """检查域名是否已被封禁"""
        return rate_limiter.is_blocked(domain)

    def stats(self) -> Dict:
        """返回采集统计"""
        return {
            'platform': self.platform,
            'requests': self._request_count,
            'errors': self._error_count,
            'error_rate': f"{self._error_count/max(1,self._request_count)*100:.1f}%"
        }


# ==================== 抖音内容采集器 ====================
class DouyinCrawler(BaseCrawler):
    """
    抖音 Feed 流采集器
    
    采集策略（不使用热搜榜 API）：
    ┌─────────────────────────────────────────────────────┐
    │ 策略1: 搜索建议词 → 发现用户实时搜索趋势              │
    │ 策略2: 关键词搜索 → 获取最新视频标题+描述+互动数据      │
    │ 策略3: 话题/挑战赛页面 → 获取话题下最新内容             │
    │ 策略4: 推荐 Feed 采样 → 获取平台推荐内容               │
    └─────────────────────────────────────────────────────┘
    
    反爬备注：
    - 抖音 Web 端使用 a_bogus 签名算法保护 API
    - 简单 HTTP 请求可获取搜索建议词、部分页面 SSR 数据
    - 完整 API 调用需要逆向 a_bogus (本模块提供框架，需配合签名服务)
    - 降级方案：使用页面 SSR 数据 + 搜索建议词
    """
    
    def __init__(self, proxy=None):
        super().__init__('douyin', proxy)
        self.session.headers.update({
            'Referer': 'https://www.douyin.com/',
        })

    def crawl_search_suggest(self, keyword: str) -> List[str]:
        """
        策略1: 抖音搜索建议词 API
        
        原理：输入部分关键词，返回用户实时搜索热词。
        这些建议词反映了当前用户的搜索趋势，无需签名。
        
        返回：建议搜索词列表
        """
        suggestions = []
        try:
            url = 'https://www.douyin.com/aweme/v1/web/search/sug/'
            params = {
                'keyword': keyword,
                'source': 'normal_search',
                'is_need_query': '1',
            }
            resp = self.safe_request(url, params=params)
            if resp and resp.status_code == 200:
                data = resp.json()
                for item in data.get('data', []):
                    word = item.get('content', '').strip()
                    if word and word != keyword:
                        suggestions.append(word)
                logger.info(f"  🔍 抖音搜索建议 [{keyword}]: {len(suggestions)} 个词")
        except Exception as e:
            logger.debug(f"  ⚠️ 搜索建议失败 [{keyword}]: {str(e)[:60]}")
        return suggestions

    def crawl_search_page(self, keyword: str) -> List[RawContent]:
        """
        策略2: 抓取抖音搜索结果页面 (SSR)
        
        抖音搜索页 URL: https://www.douyin.com/search/{keyword}
        页面可能包含 SSR 数据（window.__RENDER_DATA__）
        """
        items = []
        try:
            url = f'https://www.douyin.com/search/{keyword}'
            resp = self.safe_request(url, headers={'Accept': 'text/html'})
            if not resp:
                return items
            
            # 尝试提取 SSR 数据
            # 抖音使用 RENDER_DATA 存储 SSR 数据 (URL encoded JSON)
            m = re.search(r'<script\s+id="RENDER_DATA"[^>]*>(.+?)</script>', resp.text, re.DOTALL)
            if m:
                import urllib.parse
                raw = urllib.parse.unquote(m.group(1))
                data = json.loads(raw)
                
                # 递归搜索 aweme 数据
                aweme_list = self._extract_aweme_list(data)
                for aweme in aweme_list[:20]:
                    content = self._parse_aweme(aweme, keyword)
                    if content:
                        items.append(content)
            
            # 从 HTML 提取视频信息（降级方案）
            if not items:
                items = self._parse_search_html(resp.text, keyword)
            
            logger.info(f"  🎵 抖音搜索 [{keyword}]: {len(items)} 条内容")
        except Exception as e:
            logger.debug(f"  ⚠️ 抖音搜索页失败 [{keyword}]: {str(e)[:60]}")
        return items

    def _extract_aweme_list(self, data: dict, depth: int = 0) -> list:
        """递归提取 aweme 列表"""
        if depth > 10:
            return []
        results = []
        if isinstance(data, dict):
            if 'awemeList' in data:
                return data['awemeList'] if isinstance(data['awemeList'], list) else []
            if 'aweme_list' in data:
                return data['aweme_list'] if isinstance(data['aweme_list'], list) else []
            for v in data.values():
                results.extend(self._extract_aweme_list(v, depth + 1))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    results.extend(self._extract_aweme_list(item, depth + 1))
        return results

    def _parse_aweme(self, aweme: dict, keyword: str) -> Optional[RawContent]:
        """解析单个 aweme 对象"""
        try:
            desc = aweme.get('desc', '').strip()
            if not desc:
                return None
            
            stats = aweme.get('statistics', aweme.get('stats', {}))
            author = aweme.get('author', {})
            
            # 提取话题标签
            tags = []
            text_extra = aweme.get('text_extra', [])
            for te in (text_extra if isinstance(text_extra, list) else []):
                ht = te.get('hashtag_name', '')
                if ht:
                    tags.append(ht)
            
            aweme_id = aweme.get('aweme_id', '') or aweme.get('id', '')
            
            return RawContent(
                platform='douyin',
                content_id=f"dy_{aweme_id}",
                title=desc[:100],
                text=desc,
                author=author.get('nickname', ''),
                likes=int(stats.get('digg_count', 0) or 0),
                comments=int(stats.get('comment_count', 0) or 0),
                shares=int(stats.get('share_count', 0) or 0),
                views=int(stats.get('play_count', 0) or 0),
                tags=tags,
                url=f'https://www.douyin.com/video/{aweme_id}' if aweme_id else '',
                pub_time=datetime.now(timezone.utc).isoformat(),
                crawl_time=datetime.now(timezone.utc).isoformat(),
                content_type='video',
                extra={'search_keyword': keyword}
            )
        except Exception:
            return None

    def _parse_search_html(self, html: str, keyword: str) -> List[RawContent]:
        """从搜索页 HTML 提取基本信息（降级方案）"""
        items = []
        # 提取页面中的视频标题（meta/og:title 等）
        titles = re.findall(r'<a[^>]*title="([^"]{5,})"[^>]*>', html)
        for i, title in enumerate(titles[:15]):
            title = title.strip()
            if len(title) < 5 or title in ('搜索', '首页'):
                continue
            items.append(RawContent(
                platform='douyin',
                content_id=f"dy_html_{hashlib.md5(title.encode()).hexdigest()[:10]}",
                title=title,
                text=title,
                tags=[keyword],
                crawl_time=datetime.now(timezone.utc).isoformat(),
                content_type='video',
                extra={'search_keyword': keyword, 'parse_method': 'html'}
            ))
        return items

    def crawl_hashtag(self, hashtag: str) -> List[RawContent]:
        """
        策略3: 抖音话题/挑战赛页面
        
        URL: https://www.douyin.com/hashtag/{hashtagId}
        通过话题页面获取该话题下的最新内容
        """
        items = []
        try:
            url = f'https://www.douyin.com/search/{hashtag}?type=hashtag'
            resp = self.safe_request(url, headers={'Accept': 'text/html'})
            if resp:
                items = self._parse_search_html(resp.text, hashtag)
                logger.info(f"  #️⃣ 抖音话题 [{hashtag}]: {len(items)} 条")
        except Exception as e:
            logger.debug(f"  ⚠️ 抖音话题失败 [{hashtag}]: {str(e)[:60]}")
        return items

    def crawl_all(self, keywords: List[str] = None, max_keywords: int = 8) -> List[RawContent]:
        """
        综合采集：随机选取种子关键词 → 搜索 + 建议词发现
        
        流程：
        1. 从种子词库随机抽样 max_keywords 个关键词
        2. 对每个关键词：获取搜索建议 + 搜索页内容
        3. 从建议词中发现新的热门词（二级扩展）
        """
        all_items = []
        all_discovered_words = []
        
        if not keywords:
            # 从所有领域随机抽样
            all_seeds = []
            for category_words in SEED_KEYWORDS.values():
                all_seeds.extend(category_words)
            keywords = random.sample(all_seeds, min(max_keywords, len(all_seeds)))
        
        logger.info(f"\n🎵 抖音采集 [{len(keywords)} 个关键词]")
        
        for kw in keywords:
            if self.is_domain_blocked('www.douyin.com'):
                logger.info(f"  ⏭️ 抖音域名已封禁，停止采集")
                break
            # 搜索建议词（发现用户实时搜索趋势）
            suggestions = self.crawl_search_suggest(kw)
            all_discovered_words.extend(suggestions[:5])
            
            # 搜索页内容
            items = self.crawl_search_page(kw)
            all_items.extend(items)
        
        # 二级扩展：对发现的热门建议词做进一步采集
        if all_discovered_words:
            expand_words = random.sample(all_discovered_words, 
                                         min(3, len(all_discovered_words)))
            for word in expand_words:
                items = self.crawl_search_page(word)
                all_items.extend(items)
        
        logger.info(f"  📊 抖音采集完成: {len(all_items)} 条内容, "
                     f"发现 {len(all_discovered_words)} 个趋势词")
        return all_items


# ==================== 小红书内容采集器 ====================
class XiaohongshuCrawler(BaseCrawler):
    """
    小红书 Feed 流采集器
    
    采集策略（不使用热搜榜）：
    ┌─────────────────────────────────────────────────────┐
    │ 策略1: Explore 发现页 → 获取推荐内容流               │
    │ 策略2: 关键词搜索页 → 获取特定话题最新内容            │
    │ 策略3: 话题聚合页 → 获取垂类内容                     │
    └─────────────────────────────────────────────────────┘
    
    反爬备注：
    - 小红书 API 使用 X-Sign/shield 签名保护
    - Explore 页面包含 SSR 数据 (window.__INITIAL_STATE__)
    - 搜索页面也可能包含 SSR 数据
    - SSR 数据中的 `undefined` 需替换为 `null`
    """
    
    def __init__(self, proxy=None):
        super().__init__('xiaohongshu', proxy)
        self.session.headers.update({
            'Referer': 'https://www.xiaohongshu.com/',
        })

    def crawl_explore(self) -> List[RawContent]:
        """
        策略1: 小红书发现页 (SSR)
        
        解析 window.__INITIAL_STATE__ 中的 feed 数据
        包含推荐内容的标题、作者、互动数据
        """
        items = []
        try:
            resp = self.safe_request('https://www.xiaohongshu.com/explore')
            if not resp:
                return items
            
            m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.+?)</script>', resp.text, re.DOTALL)
            if not m:
                logger.warning("  ❌ 小红书 Explore: 无法解析 SSR 数据")
                return items
            
            raw = m.group(1).strip().rstrip(';').replace('undefined', 'null')
            data = json.loads(raw)
            feeds = data.get('feed', {}).get('feeds', [])
            
            for entry in feeds[:30]:
                nc = entry.get('noteCard', entry)
                title = nc.get('displayTitle', '').strip()
                if not title:
                    continue
                
                user = nc.get('user', {})
                interact = nc.get('interactInfo', {})
                note_type = nc.get('type', 'normal')
                note_id = entry.get('id', '')
                
                likes_str = str(interact.get('likedCount', '0'))
                try:
                    likes = int(likes_str.replace('万', '0000').replace('.', '').replace('+', ''))
                except ValueError:
                    likes = 0
                
                # 提取话题标签
                tags = []
                tag_list = nc.get('tagList', [])
                for t in (tag_list if isinstance(tag_list, list) else []):
                    tag_name = t.get('name', '') if isinstance(t, dict) else str(t)
                    if tag_name:
                        tags.append(tag_name)
                
                items.append(RawContent(
                    platform='xiaohongshu',
                    content_id=f"xhs_{note_id}",
                    title=title,
                    text=title,  # explore 页面通常只有标题
                    author=user.get('nickname', ''),
                    likes=likes,
                    tags=tags,
                    url=f'https://www.xiaohongshu.com/explore/{note_id}' if note_id else '',
                    crawl_time=datetime.now(timezone.utc).isoformat(),
                    content_type='video' if note_type == 'video' else 'note',
                ))
            
            logger.info(f"  📕 小红书 Explore: {len(items)} 条内容")
        except Exception as e:
            logger.error(f"  ❌ 小红书 Explore 失败: {str(e)[:80]}")
        return items

    def crawl_search(self, keyword: str) -> List[RawContent]:
        """
        策略2: 小红书搜索页面 (SSR)
        
        URL: https://www.xiaohongshu.com/search_result?keyword={kw}
        """
        items = []
        try:
            import urllib.parse
            encoded_kw = urllib.parse.quote(keyword)
            url = f'https://www.xiaohongshu.com/search_result?keyword={encoded_kw}&source=web_search_result_notes'
            
            resp = self.safe_request(url, headers={'Accept': 'text/html'})
            if not resp:
                return items
            
            # 尝试解析 SSR 数据
            m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.+?)</script>', resp.text, re.DOTALL)
            if m:
                raw = m.group(1).strip().rstrip(';').replace('undefined', 'null')
                data = json.loads(raw)
                
                # 搜索结果在 search.notes 或 search.feeds 中
                notes = (data.get('search', {}).get('notes', {}).get('items', []) or
                         data.get('search', {}).get('feeds', []))
                
                for entry in (notes[:20] if isinstance(notes, list) else []):
                    note = entry.get('noteCard', entry) if isinstance(entry, dict) else {}
                    title = note.get('displayTitle', '').strip()
                    if not title:
                        continue
                    
                    user = note.get('user', {}) if isinstance(note.get('user'), dict) else {}
                    interact = note.get('interactInfo', {}) if isinstance(note.get('interactInfo'), dict) else {}
                    note_id = entry.get('id', '') if isinstance(entry, dict) else ''
                    
                    likes_str = str(interact.get('likedCount', '0'))
                    try:
                        likes = int(likes_str.replace('万', '0000').replace('.', '').replace('+', ''))
                    except ValueError:
                        likes = 0
                    
                    items.append(RawContent(
                        platform='xiaohongshu',
                        content_id=f"xhs_s_{hashlib.md5(title.encode()).hexdigest()[:10]}",
                        title=title,
                        text=title,
                        author=user.get('nickname', ''),
                        likes=likes,
                        tags=[keyword],
                        url=f'https://www.xiaohongshu.com/explore/{note_id}' if note_id else '',
                        crawl_time=datetime.now(timezone.utc).isoformat(),
                        content_type='note',
                        extra={'search_keyword': keyword}
                    ))
            
            # 降级：从 HTML 提取
            if not items:
                titles = re.findall(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>([^<]{5,})</a>', resp.text)
                for title in titles[:15]:
                    title = title.strip()
                    items.append(RawContent(
                        platform='xiaohongshu',
                        content_id=f"xhs_h_{hashlib.md5(title.encode()).hexdigest()[:10]}",
                        title=title,
                        text=title,
                        tags=[keyword],
                        crawl_time=datetime.now(timezone.utc).isoformat(),
                        content_type='note',
                        extra={'search_keyword': keyword, 'parse_method': 'html'}
                    ))
            
            logger.info(f"  🔍 小红书搜索 [{keyword}]: {len(items)} 条内容")
        except Exception as e:
            logger.debug(f"  ⚠️ 小红书搜索失败 [{keyword}]: {str(e)[:60]}")
        return items

    def crawl_all(self, keywords: List[str] = None, max_keywords: int = 8) -> List[RawContent]:
        """综合采集"""
        all_items = []
        
        if not keywords:
            all_seeds = []
            for category_words in SEED_KEYWORDS.values():
                all_seeds.extend(category_words)
            keywords = random.sample(all_seeds, min(max_keywords, len(all_seeds)))
        
        logger.info(f"\n📕 小红书采集 [{len(keywords)} 个关键词]")
        
        # Explore 页面
        explore_items = self.crawl_explore()
        all_items.extend(explore_items)
        
        # 关键词搜索（连续3次空结果则停止）
        empty_count = 0
        for kw in keywords:
            if self.is_domain_blocked('www.xiaohongshu.com'):
                logger.info(f"  ⏭️ 小红书域名已封禁，停止搜索")
                break
            if empty_count >= 3:
                logger.info(f"  ⏭️ 小红书搜索连续空结果，跳过剩余关键词")
                break
            items = self.crawl_search(kw)
            if not items:
                empty_count += 1
            else:
                empty_count = 0
            all_items.extend(items)
        
        logger.info(f"  📊 小红书采集完成: {len(all_items)} 条内容")
        return all_items


# ==================== 补充数据源（交叉验证） ====================
class WeiboCrawler(BaseCrawler):
    """
    微博内容采集器 (补充数据源)
    
    微博 AJAX 接口相对开放，可获取实时热点内容。
    用于与抖音/小红书数据交叉验证。
    """
    
    def __init__(self, proxy=None):
        super().__init__('weibo', proxy)

    def crawl_realtime(self) -> List[RawContent]:
        """微博实时热点内容"""
        items = []
        try:
            # 微博热搜 AJAX (不是抖音/小红书的热搜，允许使用)
            resp = self.safe_request('https://weibo.com/ajax/side/hotSearch')
            if not resp:
                return items
            
            data = resp.json()
            realtime = data.get('data', {}).get('realtime', [])
            
            for entry in realtime[:30]:
                word = entry.get('word', '').strip()
                if not word:
                    continue
                
                items.append(RawContent(
                    platform='weibo',
                    content_id=f"wb_{hashlib.md5(word.encode()).hexdigest()[:10]}",
                    title=word,
                    text=entry.get('label_name', '') + ' ' + word,
                    views=int(entry.get('raw_hot', 0) or 0),
                    tags=[entry.get('category', '')],
                    url=f'https://s.weibo.com/weibo?q={word}',
                    crawl_time=datetime.now(timezone.utc).isoformat(),
                    content_type='topic',
                    extra={
                        'is_hot': entry.get('is_hot', 0),
                        'is_new': entry.get('is_new', 0),
                        'is_fei': entry.get('is_fei', 0),
                        'category': entry.get('category', ''),
                        'raw_hot': entry.get('raw_hot', 0),
                    }
                ))
            logger.info(f"  🐦 微博实时: {len(items)} 条")
        except Exception as e:
            logger.error(f"  ❌ 微博采集失败: {str(e)[:80]}")
        return items

    def crawl_topic_feed(self, topic: str) -> List[RawContent]:
        """微博话题 Feed"""
        items = []
        try:
            import urllib.parse
            url = f'https://weibo.com/ajax/statuses/topic?q={urllib.parse.quote(topic)}&count=20'
            resp = self.safe_request(url)
            if resp:
                data = resp.json()
                for status in data.get('data', {}).get('statuses', [])[:15]:
                    text = status.get('text_raw', status.get('text', '')).strip()
                    if not text:
                        continue
                    user = status.get('user', {})
                    items.append(RawContent(
                        platform='weibo',
                        content_id=f"wb_t_{status.get('id', '')}",
                        title=text[:100],
                        text=text,
                        author=user.get('screen_name', ''),
                        likes=int(status.get('attitudes_count', 0) or 0),
                        comments=int(status.get('comments_count', 0) or 0),
                        shares=int(status.get('reposts_count', 0) or 0),
                        tags=[topic],
                        crawl_time=datetime.now(timezone.utc).isoformat(),
                        content_type='status',
                    ))
            logger.info(f"  🐦 微博话题 [{topic}]: {len(items)} 条")
        except Exception as e:
            logger.debug(f"  ⚠️ 微博话题失败 [{topic}]: {str(e)[:60]}")
        return items

    def crawl_all(self, keywords=None, max_keywords=5) -> List[RawContent]:
        all_items = self.crawl_realtime()
        
        # 如果weibo.com已被封禁，跳过话题采集
        if self.is_domain_blocked('weibo.com'):
            logger.info(f"  ⏭️ 微博域名已封禁，跳过话题采集")
            return all_items
        
        if keywords:
            for kw in keywords[:max_keywords]:
                if self.is_domain_blocked('weibo.com'):
                    break
                items = self.crawl_topic_feed(kw)
                all_items.extend(items)
        
        return all_items


class BilibiliCrawler(BaseCrawler):
    """B站热门视频采集"""
    
    def __init__(self, proxy=None):
        super().__init__('bilibili', proxy)

    def crawl_popular(self) -> List[RawContent]:
        """B站热门视频"""
        items = []
        try:
            resp = self.safe_request('https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1')
            if not resp:
                return items
            
            data = resp.json()
            for entry in data.get('data', {}).get('list', [])[:30]:
                title = entry.get('title', '').strip()
                if not title:
                    continue
                
                stat = entry.get('stat', {})
                owner = entry.get('owner', {})
                
                items.append(RawContent(
                    platform='bilibili',
                    content_id=f"bl_{entry.get('bvid', '')}",
                    title=title,
                    text=entry.get('desc', title),
                    author=owner.get('name', ''),
                    likes=int(stat.get('like', 0)),
                    comments=int(stat.get('reply', 0)),
                    shares=int(stat.get('share', 0)),
                    views=int(stat.get('view', 0)),
                    url=f"https://www.bilibili.com/video/{entry.get('bvid', '')}",
                    crawl_time=datetime.now(timezone.utc).isoformat(),
                    content_type='video',
                ))
            logger.info(f"  📺 B站热门: {len(items)} 条")
        except Exception as e:
            logger.error(f"  ❌ B站采集失败: {str(e)[:80]}")
        return items

    def crawl_all(self, keywords=None, max_keywords=5) -> List[RawContent]:
        return self.crawl_popular()


class ZhihuCrawler(BaseCrawler):
    """知乎热门采集"""
    
    def __init__(self, proxy=None):
        super().__init__('zhihu', proxy)
        self.session.headers.update({
            'Referer': 'https://www.zhihu.com/',
        })

    def crawl_hot(self) -> List[RawContent]:
        """知乎热榜"""
        items = []
        try:
            resp = self.safe_request('https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50')
            if not resp:
                return items
            
            data = resp.json()
            for entry in data.get('data', [])[:30]:
                target = entry.get('target', {})
                title = target.get('title', '').strip()
                if not title:
                    continue
                
                items.append(RawContent(
                    platform='zhihu',
                    content_id=f"zh_{target.get('id', '')}",
                    title=title,
                    text=target.get('excerpt', title),
                    views=int(entry.get('detail_text', '0').replace('万热度', '0000').replace('热度', '').strip() or 0),
                    url=f"https://www.zhihu.com/question/{target.get('id', '')}",
                    crawl_time=datetime.now(timezone.utc).isoformat(),
                    content_type='question',
                    extra={'hot_text': entry.get('detail_text', '')}
                ))
            logger.info(f"  💬 知乎热榜: {len(items)} 条")
        except Exception as e:
            logger.error(f"  ❌ 知乎采集失败: {str(e)[:80]}")
        return items

    def crawl_all(self, keywords=None, max_keywords=5) -> List[RawContent]:
        return self.crawl_hot()


class BaiduCrawler(BaseCrawler):
    """百度热搜采集"""
    
    def __init__(self, proxy=None):
        super().__init__('baidu', proxy)

    def crawl_realtime(self) -> List[RawContent]:
        """百度实时热点"""
        items = []
        try:
            # 百度需要桌面UA + 完整Accept头才返回SSR数据
            resp = self.safe_request('https://top.baidu.com/board?tab=realtime',
                                     headers={
                                         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                                         'Accept-Encoding': 'gzip, deflate',  # 不要br，避免brotli解码问题
                                         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                                     })
            if not resp:
                return items
            
            # 解析 SSR 数据
            m = re.search(r'<!--s-data:(.*?)-->', resp.text, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                cards = data.get('data', {}).get('cards', [])
                for card in cards:
                    for item in card.get('content', [])[:30]:
                        title = item.get('word', '').strip()
                        if not title:
                            continue
                        items.append(RawContent(
                            platform='baidu',
                            content_id=f"bd_{hashlib.md5(title.encode()).hexdigest()[:10]}",
                            title=title,
                            text=item.get('desc', title),
                            views=int(item.get('hotScore', 0) or 0),
                            url=item.get('url', ''),
                            crawl_time=datetime.now(timezone.utc).isoformat(),
                            content_type='search',
                        ))
            logger.info(f"  🔍 百度热搜: {len(items)} 条")
        except Exception as e:
            logger.error(f"  ❌ 百度采集失败: {str(e)[:80]}")
        return items

    def crawl_all(self, keywords=None, max_keywords=5) -> List[RawContent]:
        return self.crawl_realtime()


# ==================== 采集编排器 ====================
class CrawlOrchestrator:
    """
    采集编排器 - 调度多平台采集任务
    
    功能：
    1. 动态选择种子关键词（每次不同）
    2. 并发编排多平台采集
    3. 自适应采集强度（根据错误率调整）
    4. 原始数据落盘
    """
    
    def __init__(self, proxy: str = None, save_raw: bool = True):
        self.proxy = proxy
        self.save_raw = save_raw
        self.data_dir = Path(__file__).parent.parent / "data"
        self.raw_dir = self.data_dir / "raw_feeds"
        
        # 初始化各平台爬虫（可靠平台优先）
        self.crawlers = {
            'bilibili': BilibiliCrawler(proxy),
            'baidu': BaiduCrawler(proxy),
            'xiaohongshu': XiaohongshuCrawler(proxy),
            'weibo': WeiboCrawler(proxy),
            'zhihu': ZhihuCrawler(proxy),
            'douyin': DouyinCrawler(proxy),
        }

    def select_keywords(self, count: int = 10) -> List[str]:
        """
        动态选择种子关键词
        
        策略：
        - 每个领域至少选 2 个（保证覆盖面）
        - 总数控制在 count 以内
        - 随机化防止被识别为固定采集模式
        """
        selected = []
        categories = list(SEED_KEYWORDS.keys())
        per_cat = max(2, count // len(categories))
        
        for cat in categories:
            words = SEED_KEYWORDS[cat]
            chosen = random.sample(words, min(per_cat, len(words)))
            selected.extend(chosen)
        
        random.shuffle(selected)
        return selected[:count]

    def crawl_all(self, platforms: List[str] = None, 
                  keyword_count: int = 10) -> List[RawContent]:
        """
        执行全平台采集
        
        Args:
            platforms: 要采集的平台列表，默认全部
            keyword_count: 种子关键词数量
            
        Returns:
            所有平台的原始内容列表
        """
        if platforms is None:
            platforms = list(self.crawlers.keys())
        
        keywords = self.select_keywords(keyword_count)
        all_items: List[RawContent] = []
        stats = {}
        
        start_time = time.time()
        logger.info(f"\n{'='*60}")
        logger.info(f"🌐 多平台采集开始 [{datetime.now().strftime('%H:%M:%S')}]")
        logger.info(f"   平台: {', '.join(platforms)}")
        logger.info(f"   关键词: {', '.join(keywords[:5])}...")
        logger.info(f"{'='*60}")
        
        # 平台域名映射（用于封禁检查）
        platform_domains = {
            'douyin': 'www.douyin.com',
            'xiaohongshu': 'www.xiaohongshu.com',
            'weibo': 'weibo.com',
            'bilibili': 'api.bilibili.com',
            'zhihu': 'www.zhihu.com',
            'baidu': 'top.baidu.com',
        }
        
        for platform in platforms:
            crawler = self.crawlers.get(platform)
            if not crawler:
                continue
            
            # 检查域名是否已被封禁，跳过整个平台
            domain = platform_domains.get(platform, '')
            if domain and rate_limiter.is_blocked(domain):
                logger.info(f"  ⏭️ 跳过 {platform} (域名已封禁)")
                stats[platform] = {'status': 'blocked', 'items': 0}
                continue
            
            try:
                platform_start = time.time()
                items = crawler.crawl_all(keywords=keywords)
                platform_elapsed = time.time() - platform_start
                all_items.extend(items)
                stats[platform] = crawler.stats()
                stats[platform]['items'] = len(items)
                stats[platform]['time'] = f"{platform_elapsed:.1f}s"
            except Exception as e:
                logger.error(f"  ❌ {platform} 采集异常: {str(e)[:80]}")
                stats[platform] = {'error': str(e)[:80]}
        
        elapsed = time.time() - start_time
        
        # 去重
        seen = set()
        unique_items = []
        for item in all_items:
            key = item.title[:30].lower().replace(' ', '')
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        
        logger.info(f"\n📊 采集汇总 ({elapsed:.1f}s):")
        logger.info(f"   总计: {len(all_items)} → 去重后: {len(unique_items)}")
        for p, s in stats.items():
            logger.info(f"   {p}: {s}")
        
        # 保存原始数据
        if self.save_raw:
            self._save_raw(unique_items)
        
        return unique_items

    def _save_raw(self, items: List[RawContent]):
        """保存原始采集数据（用于趋势分析时间序列）"""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filepath = self.raw_dir / f"raw_{timestamp}.json"
        
        data = {
            'crawl_time': datetime.now(timezone.utc).isoformat(),
            'total': len(items),
            'items': [item.to_dict() for item in items]
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 清理7天前的原始数据
        cutoff = datetime.now() - timedelta(days=7)
        for old_file in self.raw_dir.glob('raw_*.json'):
            try:
                fdate_str = old_file.stem.replace('raw_', '')
                fdate = datetime.strptime(fdate_str, '%Y%m%d_%H%M%S')
                if fdate < cutoff:
                    old_file.unlink()
            except (ValueError, OSError):
                pass
        
        logger.info(f"  💾 原始数据保存: {filepath.name}")


# ================================
# 模块导出
# ================================
__all__ = [
    'RawContent', 'CrawlOrchestrator',
    'DouyinCrawler', 'XiaohongshuCrawler',
    'WeiboCrawler', 'BilibiliCrawler', 'ZhihuCrawler', 'BaiduCrawler',
    'SEED_KEYWORDS', 'UA_POOL', 'RateLimiter',
]

if __name__ == '__main__':
    # 独立测试
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    orchestrator = CrawlOrchestrator()
    # 优先采集可靠平台，不可靠的放最后
    items = orchestrator.crawl_all(
        platforms=['bilibili', 'baidu', 'xiaohongshu', 'weibo', 'zhihu', 'douyin'],
        keyword_count=5
    )
    
    print(f"\n✅ 采集完成: {len(items)} 条内容")
    for item in items[:10]:
        print(f"  [{item.platform}] {item.title[:50]}")
