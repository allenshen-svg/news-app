#!/usr/bin/env python3
"""
模块二 + 模块三：NLP文本处理流水线 & 热度评估与突发检测算法
============================================================

【NLP 流水线】
Raw Text → 清洗 → 分词(jieba) → 停用词过滤 → 实体识别
→ TF-IDF 关键词提取 → TextRank 短语提取 → 新词发现

【热度评估数学模型】

1. 实时热力值公式 (Heat Score):
   ┌────────────────────────────────────────────────────────────┐
   │ H(t) = α·F(t)·e^{-λ(t_now - t_last)}                    │
   │      + β·A(t)                                             │
   │      + γ·S(t)                                             │
   │      + δ·E(t)                                             │
   ├────────────────────────────────────────────────────────────┤
   │ F(t) = 词频 (滑动窗口内出现次数)                            │
   │ e^-λΔt = 牛顿冷却衰减 (半衰期 = ln2/λ)                    │
   │ A(t) = 加速度 = dF/dt (频率变化率, 越快越热)               │
   │ S(t) = 来源多样性 (跨平台出现 → 更可能是真热点)             │
   │ E(t) = 互动量归一 (点赞+评论+分享加权)                     │
   │ α=0.4, β=0.3, γ=0.2, δ=0.1 (可调权重)                    │
   └────────────────────────────────────────────────────────────┘

2. 突发检测 (Burst Detection):
   - Z-Score 异常检测：z = (x_t - μ) / σ，当 z > 2.5 标记为 burst
   - MACD 趋势动量：
     * 短期 EMA(12窗口) vs 长期 EMA(26窗口)
     * MACD = Short_EMA - Long_EMA
     * Signal = EMA(MACD, 9)
     * MACD 上穿 Signal → 趋势启动

3. 热度衰减 (Newton's Cooling Law):
   T(t) = T_env + (T_0 - T_env) · e^{-λt}
   → 简化为: heat(t) = heat_peak · e^{-λ·hours_since_peak}
   → λ = ln(2) / half_life_hours (默认半衰期4小时)
"""

import json
import math
import re
import time
import hashlib
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field, asdict

logger = logging.getLogger('trend_engine')

# ==================== 配置 ====================
# 热度评估权重
ALPHA = 0.4   # 词频权重
BETA  = 0.3   # 加速度权重
GAMMA = 0.2   # 来源多样性权重
DELTA = 0.1   # 互动量权重

# 牛顿冷却参数
HALF_LIFE_HOURS = 4.0   # 半衰期（小时）
LAMBDA_DECAY = math.log(2) / HALF_LIFE_HOURS

# 突发检测参数
BURST_Z_THRESHOLD = 2.5    # Z-Score 超过此值视为突发
MACD_SHORT_PERIOD = 12     # MACD 短期 EMA 窗口
MACD_LONG_PERIOD = 26      # MACD 长期 EMA 窗口
MACD_SIGNAL_PERIOD = 9     # MACD 信号线 EMA 窗口

# 时间窗口配置
WINDOW_SIZE_MINUTES = 10   # 每个统计窗口大小
HISTORY_WINDOWS = 144      # 保留历史窗口数 (144 × 10min = 24h)

# 数据路径
DATA_DIR = Path(__file__).parent.parent / "data"


# ==================== 中文 NLP 停用词表 ====================
STOPWORDS = set("""
的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你
会 着 没有 看 好 自己 这 他 她 它 们 那 些 什么 于 能 吗 又 与
把 从 其 比 只 之 对 为 通过 而 可以 被 开始 以 已 但 所 让 更
将 应 该 行 向 下 然 年月日 时 中 还 里 后 没 最 第 如 因 不是
等 就是 呢 吧 能够 怎么 为什么 怎样 这样 那样 这个 那个 可能
包括 成为 因为 所以 虽然 但是 然后 或者 而且 因此 否则 另外
同时 然而 此外 以及 相关 关于 已经 正在 可以 需要 进行 或
来自 之间 其中 方面 通过 过程 结构 地区 问题 工作 部分
原来 目前 今天 昨天 明天 今年 去年 今日 记者 报道 据悉
""".split())

# 额外停用词（社交平台常见噪音词）
SOCIAL_STOPWORDS = set("""
哈哈 哈哈哈 hhh 笑死 绝了 太好了 真的 好的 不错 求 想
赞 顶 沙发 前排 收藏 转发 关注 点赞 评论 分享 链接
视频 图片 直播 发布 更新 推荐 热门 热搜 最新 速看 震惊
""".split())

# 分类标签词（不是实体热点，应排除）
CATEGORY_STOPWORDS = set("""
时事 财经 国际 科技 政治 政经 社会 娱乐 体育 军事 教育 文化
新闻 快讯 头条 资讯 热点 消息 事件 简讯 要闻 早报 晚报
""".split())

# 英文停用词
ENGLISH_STOPWORDS = set("""
the a an is are was were be been being have has had do does did
will would shall should can could may might must need dare
to of in for on with at by from as into through during before
after above below between out off over under again further then
once here there when where why how all each every both few more
most other some such no nor not only own same so than too very
and but or if while because until although since about
it its he his she her they them their we our you your
this that these those what which who whom whose
how much many more most just also back even still
said says new report year years month day time people
""".split())

ALL_STOPWORDS = STOPWORDS | SOCIAL_STOPWORDS | CATEGORY_STOPWORDS | ENGLISH_STOPWORDS


# ==================== 数据结构 ====================
@dataclass
class TrendTopic:
    """一个发现的热点话题"""
    keyword: str                    # 核心关键词
    heat_score: float = 0.0         # 综合热力值
    frequency: int = 0              # 当前窗口词频
    acceleration: float = 0.0       # 频率变化率
    source_diversity: int = 0       # 出现的平台数
    engagement: float = 0.0         # 互动量归一化
    is_burst: bool = False          # 是否突发
    burst_z_score: float = 0.0      # Z-Score
    macd_signal: str = 'neutral'    # MACD信号: bullish/bearish/neutral
    macd_value: float = 0.0         # MACD值
    trend_direction: str = '→'      # 趋势方向: ↑↗→↘↓
    platforms: List[str] = field(default_factory=list)  # 出现的平台
    related_titles: List[str] = field(default_factory=list)  # 相关原始标题
    category: str = ''              # 分类
    sparkline: List[float] = field(default_factory=list)  # 最近N个窗口的热度
    first_seen: str = ''            # 首次出现时间
    peak_time: str = ''             # 峰值时间
    
    def to_dict(self):
        return asdict(self)


# ==================== NLP 文本处理器 ====================
class ChineseNLP:
    """
    中文 NLP 处理流水线
    
    流程：Raw Text → 清洗 → 分词 → 过滤 → 关键词提取
    
    技术栈：
    - jieba 分词（支持自定义词典）
    - jieba.analyse (TF-IDF + TextRank)
    - 自定义停用词过滤
    - 新词发现（基于互信息和左右熵）
    """
    
    def __init__(self):
        self._init_jieba()

    def _init_jieba(self):
        """初始化 jieba 分词器"""
        try:
            import jieba
            import jieba.analyse
            self.jieba = jieba
            self.analyse = jieba.analyse
            
            # 添加领域专有词汇（避免被错误切分）
            custom_words = [
                'A股', '港股', '美股', '比特币', '数字货币', '区块链', '元宇宙',
                '人工智能', '大模型', '自动驾驶', '量子计算', '半导体', '芯片',
                '特朗普', '拜登', '普京', '习近平', '马斯克',
                'ChatGPT', 'DeepSeek', 'OpenAI', 'Kimi', 'GPT4',
                '一带一路', '中美关系', '台海', '南海', '北约',
                '新能源', '光伏', '锂电池', '新质生产力',
                '降息', '加息', '央行', '美联储', 'GDP', 'CPI', 'PMI',
                '内卷', '躺平', '考公', '考研', '就业率',
            ]
            for word in custom_words:
                jieba.add_word(word, freq=10000)
            
            logger.info("  ✅ jieba 分词器初始化完成")
        except ImportError:
            logger.warning("  ⚠️ jieba 未安装，将自动安装...")
            import subprocess, sys
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'jieba', '-q'])
            import jieba
            import jieba.analyse
            self.jieba = jieba
            self.analyse = jieba.analyse

    def clean_text(self, text: str) -> str:
        """
        文本清洗
        
        处理：
        1. 去除HTML标签
        2. 去除URL
        3. 去除emoji (保留中英文和数字)
        4. 去除@提及
        5. 去除#话题标记
        6. 压缩空白
        """
        if not text:
            return ''
        
        # 去HTML
        text = re.sub(r'<[^>]+>', '', text)
        # 去URL
        text = re.sub(r'https?://\S+', '', text)
        # 去@提及
        text = re.sub(r'@\w+', '', text)
        # 去HTML实体
        text = re.sub(r'&\w+;', ' ', text)
        # 保留中英文、数字、常见标点
        text = re.sub(r'[^\u4e00-\u9fff\u3000-\u303fA-Za-z0-9\s，。！？、；：""''（）《》【】·%‰℃]', ' ', text)
        # 压缩空白
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    def tokenize(self, text: str, min_len: int = 2) -> List[str]:
        """
        分词 + 过滤
        
        使用 jieba 精确模式分词，去除停用词和短词。
        """
        text = self.clean_text(text)
        if not text:
            return []
        
        words = self.jieba.cut(text, cut_all=False)
        result = []
        for word in words:
            word = word.strip()
            if len(word) < min_len:
                continue
            if word.lower() in ALL_STOPWORDS:
                continue
            if re.match(r'^[\d\s]+$', word):  # 纯数字
                continue
            result.append(word)
        
        return result

    def extract_keywords_tfidf(self, text: str, topK: int = 20) -> List[Tuple[str, float]]:
        """
        TF-IDF 关键词提取
        
        使用 jieba.analyse.extract_tags，返回 (关键词, 权重) 列表。
        TF-IDF 适合提取在当前文档中重要但在语料库中不常见的词。
        """
        text = self.clean_text(text)
        if not text:
            return []
        
        tags = self.analyse.extract_tags(text, topK=topK, withWeight=True)
        # 过滤停用词
        return [(word, weight) for word, weight in tags 
                if word not in ALL_STOPWORDS and len(word) >= 2]

    def extract_keywords_textrank(self, text: str, topK: int = 20) -> List[Tuple[str, float]]:
        """
        TextRank 关键词提取
        
        基于图的排序算法（类似 PageRank），
        考虑词与词之间的共现关系，适合提取核心概念。
        """
        text = self.clean_text(text)
        if not text:
            return []
        
        tags = self.analyse.textrank(text, topK=topK, withWeight=True)
        return [(word, weight) for word, weight in tags 
                if word not in ALL_STOPWORDS and len(word) >= 2]

    def batch_extract_keywords(self, texts: List[str], topK: int = 50) -> List[Tuple[str, float]]:
        """
        批量文本关键词提取
        
        合并多篇文本，同时使用 TF-IDF 和 TextRank，
        取两种方法的交集作为高置信度关键词。
        """
        if not texts:
            return []
        
        combined = ' '.join(self.clean_text(t) for t in texts if t)
        
        tfidf_kws = dict(self.extract_keywords_tfidf(combined, topK=topK * 2))
        textrank_kws = dict(self.extract_keywords_textrank(combined, topK=topK * 2))
        
        # 融合两种方法的得分
        all_words = set(tfidf_kws.keys()) | set(textrank_kws.keys())
        scores = {}
        for word in all_words:
            tf_score = tfidf_kws.get(word, 0)
            tr_score = textrank_kws.get(word, 0)
            # 两种方法都出现的词得分更高
            if word in tfidf_kws and word in textrank_kws:
                scores[word] = (tf_score + tr_score) * 1.5
            else:
                scores[word] = tf_score + tr_score
        
        sorted_kws = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_kws[:topK]

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        简易实体识别（基于规则 + 词典）
        
        识别：人名、地名、组织名、品牌
        注：完整 NER 建议使用 HanLP 或 LAC，这里用轻量规则方案
        """
        entities = {
            'person': [],
            'location': [],
            'organization': [],
            'brand': [],
        }
        
        text = self.clean_text(text)
        
        # 人名词典
        person_dict = {'习近平', '特朗普', '拜登', '普京', '马斯克', '马克龙', 
                       '岸田', '泽连斯基', '莫迪', '李强', '王毅', '布林肯',
                       '比尔盖茨', '扎克伯格', '黄仁勋', '任正非', '马云'}
        
        # 地名词典
        location_dict = {'北京', '上海', '深圳', '广州', '杭州', '成都', '武汉',
                         '美国', '中国', '日本', '韩国', '俄罗斯', '欧洲', '台湾',
                         '华盛顿', '纽约', '伦敦', '东京', '莫斯科', '巴黎',
                         '加沙', '以色列', '乌克兰', '叙利亚', '伊朗', '朝鲜'}
        
        # 组织词典
        org_dict = {'央行', '美联储', '欧央行', '国务院', '发改委', '外交部',
                    '联合国', '北约', '欧盟', '世卫组织', '世贸组织', '亚投行',
                    '人大', '政协', '最高法', '最高检'}
        
        # 品牌词典
        brand_dict = {'华为', '苹果', '特斯拉', '小米', '腾讯', '阿里', '字节跳动',
                      '百度', 'OpenAI', '谷歌', '微软', '英伟达', '台积电',
                      '比亚迪', '宁德时代', '中芯国际', '理想', '蔚来', '小鹏'}
        
        words = set(self.tokenize(text, min_len=2))
        
        entities['person'] = list(words & person_dict)
        entities['location'] = list(words & location_dict)
        entities['organization'] = list(words & org_dict)
        entities['brand'] = list(words & brand_dict)
        
        return entities

    def discover_new_words(self, texts: List[str], min_freq: int = 3, 
                           max_len: int = 6) -> List[Tuple[str, int]]:
        """
        新词发现
        
        基于 n-gram 频率统计 + 互信息(PMI) 的新词发现。
        识别在常规词典中不存在但频繁出现的新词。
        
        适用场景：发现网络热梗、新事件名称、新产品名等。
        """
        # 统计 2-gram 到 max_len-gram
        ngram_freq = Counter()
        char_freq = Counter()
        
        for text in texts:
            text = self.clean_text(text)
            chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
            
            for c in chars:
                char_freq[c] += 1
            
            for n in range(2, max_len + 1):
                for i in range(len(chars) - n + 1):
                    gram = ''.join(chars[i:i+n])
                    ngram_freq[gram] += 1
        
        total_chars = sum(char_freq.values()) or 1
        
        # 过滤低频 + 计算 PMI
        new_words = []
        for gram, freq in ngram_freq.items():
            if freq < min_freq:
                continue
            
            # 检查是否已在词典中
            seg_result = list(self.jieba.cut(gram, cut_all=False))
            if len(seg_result) == 1 and seg_result[0] == gram:
                continue  # 已经是词典中的词
            
            # 简化 PMI: 用各字符独立频率的乘积 vs 联合频率
            char_probs = 1.0
            for c in gram:
                char_probs *= (char_freq.get(c, 1) / total_chars)
            
            joint_prob = freq / total_chars
            pmi = math.log(joint_prob / char_probs + 1e-10) if char_probs > 0 else 0
            
            if pmi > 2.0:  # PMI 阈值
                new_words.append((gram, freq))
        
        new_words.sort(key=lambda x: -x[1])
        return new_words[:50]


# ==================== 时间序列管理 ====================
class TimeSeriesStore:
    """
    关键词时间序列存储
    
    维护每个关键词在每个时间窗口的出现频率，
    用于突发检测和趋势计算。
    
    存储结构:
    {
        "keyword": {
            "windows": [{"time": "...", "count": N, "platforms": [...], "engagement": F}],
            "first_seen": "...",
            "peak_count": N,
            "peak_time": "..."
        }
    }
    """
    
    def __init__(self, store_path: Path = None):
        self.store_path = store_path or (DATA_DIR / "keyword_history.json")
        self.data: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        """加载历史数据"""
        if self.store_path.exists():
            try:
                with open(self.store_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                logger.info(f"  📂 加载历史数据: {len(self.data)} 个关键词")
            except (json.JSONDecodeError, IOError):
                self.data = {}

    def save(self):
        """保存历史数据"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False)
        logger.info(f"  💾 保存历史数据: {len(self.data)} 个关键词")

    def record(self, keyword: str, count: int, platforms: List[str], 
               engagement: float = 0, window_time: str = None):
        """
        记录一个关键词在当前窗口的数据
        """
        now = window_time or datetime.now(timezone.utc).isoformat()
        
        if keyword not in self.data:
            self.data[keyword] = {
                'windows': [],
                'first_seen': now,
                'peak_count': 0,
                'peak_time': now,
            }
        
        rec = self.data[keyword]
        rec['windows'].append({
            'time': now,
            'count': count,
            'platforms': platforms,
            'engagement': engagement,
        })
        
        # 更新峰值
        if count > rec.get('peak_count', 0):
            rec['peak_count'] = count
            rec['peak_time'] = now
        
        # 只保留最近 HISTORY_WINDOWS 个窗口
        if len(rec['windows']) > HISTORY_WINDOWS:
            rec['windows'] = rec['windows'][-HISTORY_WINDOWS:]

    def get_series(self, keyword: str) -> List[Dict]:
        """获取关键词的时间序列"""
        return self.data.get(keyword, {}).get('windows', [])

    def get_counts(self, keyword: str) -> List[int]:
        """获取关键词的计数序列"""
        return [w['count'] for w in self.get_series(keyword)]

    def cleanup(self, max_age_hours: int = 48):
        """清理过期数据"""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        to_delete = []
        
        for keyword, rec in self.data.items():
            windows = rec.get('windows', [])
            if not windows:
                to_delete.append(keyword)
                continue
            # 如果最新窗口都过期了，删除
            if windows[-1].get('time', '') < cutoff:
                to_delete.append(keyword)
        
        for kw in to_delete:
            del self.data[kw]
        
        if to_delete:
            logger.info(f"  🧹 清理 {len(to_delete)} 个过期关键词")


# ==================== 突发检测算法 ====================
class BurstDetector:
    """
    突发检测器
    
    集成三种检测方法：
    1. Z-Score 异常检测 - 统计偏离度
    2. MACD 趋势动量 - 趋势启动/结束判断
    3. Newton Cooling 衰减 - 热度时间价值
    """

    @staticmethod
    def z_score_detect(counts: List[int]) -> Tuple[float, bool]:
        """
        Z-Score 异常检测
        
        公式: z = (x_t - μ) / σ
        
        当前值相对历史平均值的偏离程度。
        z > 2.5 视为突发（99.4%置信度）
        z > 3.0 视为强突发（99.7%置信度）
        
        Args:
            counts: 历史计数序列
            
        Returns:
            (z_score, is_burst)
        """
        if len(counts) < 3:
            return 0.0, False
        
        current = counts[-1]
        historical = counts[:-1]
        
        mean = sum(historical) / len(historical)
        variance = sum((x - mean) ** 2 for x in historical) / len(historical)
        std = math.sqrt(variance) if variance > 0 else 1.0
        
        z = (current - mean) / std if std > 0 else 0.0
        
        return z, z > BURST_Z_THRESHOLD

    @staticmethod
    def ema(values: List[float], period: int) -> List[float]:
        """计算指数移动平均 (EMA)"""
        if not values:
            return []
        
        multiplier = 2.0 / (period + 1)
        ema_values = [values[0]]
        
        for val in values[1:]:
            ema_val = (val - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema_val)
        
        return ema_values

    @staticmethod
    def macd_detect(counts: List[int]) -> Tuple[float, str]:
        """
        MACD 趋势动量检测
        
        借鉴金融技术分析的 MACD 指标：
        - 短期EMA(12) - 长期EMA(26) = MACD线
        - MACD的EMA(9) = 信号线
        - MACD > Signal 且上穿 → bullish (趋势启动)
        - MACD < Signal 且下穿 → bearish (趋势衰退)
        
        Args:
            counts: 历史计数序列
            
        Returns:
            (macd_value, signal_str) signal_str ∈ {'bullish', 'bearish', 'neutral'}
        """
        if len(counts) < MACD_LONG_PERIOD:
            return 0.0, 'neutral'
        
        float_counts = [float(c) for c in counts]
        
        short_ema = BurstDetector.ema(float_counts, MACD_SHORT_PERIOD)
        long_ema = BurstDetector.ema(float_counts, MACD_LONG_PERIOD)
        
        macd_line = [s - l for s, l in zip(short_ema, long_ema)]
        signal_line = BurstDetector.ema(macd_line, MACD_SIGNAL_PERIOD)
        
        if not signal_line or not macd_line:
            return 0.0, 'neutral'
        
        macd_current = macd_line[-1]
        signal_current = signal_line[-1]
        
        # 判断交叉
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            prev_diff = macd_line[-2] - signal_line[-2]
            curr_diff = macd_current - signal_current
            
            if prev_diff <= 0 and curr_diff > 0:
                return macd_current, 'bullish'   # 金叉
            elif prev_diff >= 0 and curr_diff < 0:
                return macd_current, 'bearish'   # 死叉
        
        if macd_current > signal_current:
            return macd_current, 'bullish'
        elif macd_current < signal_current:
            return macd_current, 'bearish'
        
        return macd_current, 'neutral'

    @staticmethod
    def newton_cooling_decay(peak_value: float, hours_since_peak: float) -> float:
        """
        牛顿冷却定律衰减
        
        T(t) = T_peak · e^{-λt}
        
        模拟热点的自然降温过程。
        半衰期 = ln(2)/λ ≈ 4小时（即4小时热度降一半）
        
        Args:
            peak_value: 峰值热度
            hours_since_peak: 距离峰值的小时数
            
        Returns:
            衰减后的热度值
        """
        return peak_value * math.exp(-LAMBDA_DECAY * max(0, hours_since_peak))

    @staticmethod
    def calculate_acceleration(counts: List[int]) -> float:
        """
        计算频率加速度 (dF/dt)
        
        使用最近3个窗口的二阶差分。
        正值 = 加速增长，负值 = 减速/下降。
        """
        if len(counts) < 3:
            if len(counts) == 2:
                return float(counts[-1] - counts[-2])
            return 0.0
        
        # 一阶差分
        d1 = counts[-1] - counts[-2]
        d2 = counts[-2] - counts[-3]
        
        # 二阶差分 (加速度)
        acceleration = d1 - d2
        
        # 也考虑一阶变化率 (速度)
        velocity = d1
        
        # 综合: 速度 + 加速度各占一半
        return velocity * 0.6 + acceleration * 0.4


# ==================== 热度评分器 ====================
class HeatScorer:
    """
    综合热度评分器
    
    将 NLP 提取的关键词 + 时间序列数据 + 突发检测结果
    综合计算每个关键词的实时热力值。
    """

    @staticmethod
    def compute_heat(keyword: str, freq: int, acceleration: float,
                     source_count: int, engagement: float,
                     hours_since_peak: float = 0) -> float:
        """
        计算实时热力值
        
        H(t) = α·F(t)·e^{-λΔt} + β·A(t) + γ·S(t) + δ·E(t)
        
        Args:
            keyword: 关键词
            freq: 当前窗口频率
            acceleration: 频率加速度
            source_count: 来源平台数（1-6）
            engagement: 归一化互动量（0-1）
            hours_since_peak: 距离峰值时间（小时）
            
        Returns:
            热力值（0-100 标准化）
        """
        # F(t) · 衰减因子
        freq_with_decay = freq * math.exp(-LAMBDA_DECAY * hours_since_peak)
        
        # 标准化各分量到相似量级
        f_norm = min(freq_with_decay / 10.0, 10.0)  # 频率标准化
        a_norm = max(min(acceleration / 5.0, 5.0), -5.0)  # 加速度标准化
        s_norm = source_count / 3.0  # 来源多样性标准化 (3个平台=1.0)
        e_norm = min(engagement, 1.0)  # 互动量已经是0-1
        
        # 综合打分
        raw_score = (
            ALPHA * f_norm +
            BETA  * max(0, a_norm) +   # 加速度只取正值
            GAMMA * s_norm +
            DELTA * e_norm
        )
        
        # 映射到 0-100
        heat = min(100.0, raw_score * 15.0)
        
        return round(heat, 2)

    @staticmethod
    def determine_direction(counts: List[int]) -> str:
        """
        判断趋势方向
        
        基于最近窗口的变化率:
        ↑ 快速上升 (>50%)
        ↗ 缓慢上升 (10-50%)
        → 持平 (-10% to +10%)
        ↘ 缓慢下降 (-50% to -10%)
        ↓ 快速下降 (<-50%)
        """
        if len(counts) < 2:
            return '→'
        
        current = counts[-1]
        previous = counts[-2] if counts[-2] > 0 else 1
        
        change_rate = (current - previous) / previous
        
        if change_rate > 0.5:
            return '↑'
        elif change_rate > 0.1:
            return '↗'
        elif change_rate > -0.1:
            return '→'
        elif change_rate > -0.5:
            return '↘'
        else:
            return '↓'


# ==================== 趋势发现引擎 ====================
class TrendEngine:
    """
    趋势发现引擎 - 串联 NLP + 时间序列 + 突发检测
    
    完整流程：
    1. 接收原始采集数据
    2. NLP 提取关键词
    3. 统计词频 + 来源分布 + 互动量
    4. 更新时间序列
    5. 运行突发检测
    6. 计算综合热力值
    7. 排序输出 Top-N 趋势
    """
    
    def __init__(self):
        self.nlp = ChineseNLP()
        self.ts_store = TimeSeriesStore()
        self.burst_detector = BurstDetector()
        self.heat_scorer = HeatScorer()

    def process(self, raw_contents: list, topK: int = 50) -> List[TrendTopic]:
        """
        主处理流程
        
        Args:
            raw_contents: RawContent 对象列表（来自 feed_crawler）
            topK: 返回前K个趋势
            
        Returns:
            TrendTopic 列表，按热力值降序
        """
        if not raw_contents:
            return []
        
        logger.info(f"\n🔬 趋势分析引擎启动 [{len(raw_contents)} 条内容]")
        
        # ── Step 1: NLP 关键词提取 ──
        logger.info("  📝 Step 1/5: NLP 关键词提取...")
        keyword_data = self._extract_all_keywords(raw_contents)
        logger.info(f"     → 提取 {len(keyword_data)} 个关键词")
        
        # ── Step 2: 统计词频、来源、互动 ──
        logger.info("  📊 Step 2/5: 统计分析...")
        freq_stats = self._compute_frequency_stats(keyword_data, raw_contents)
        
        # ── Step 3: 更新时间序列 ──
        logger.info("  📈 Step 3/5: 更新时间序列...")
        self._update_time_series(freq_stats)
        
        # ── Step 4: 突发检测 ──
        logger.info("  🚨 Step 4/5: 突发检测...")
        burst_results = self._run_burst_detection(freq_stats)
        
        # ── Step 5: 计算热力值 & 排序 ──
        logger.info("  🔥 Step 5/5: 计算热力值...")
        trends = self._score_and_rank(freq_stats, burst_results, topK)
        
        # 保存时间序列
        self.ts_store.save()
        self.ts_store.cleanup(max_age_hours=48)
        
        logger.info(f"\n🎯 发现 {len(trends)} 个趋势话题:")
        for i, t in enumerate(trends[:10]):
            burst_mark = '🔴' if t.is_burst else '⚪'
            logger.info(f"   {i+1}. [{t.heat_score:5.1f}] {burst_mark} {t.keyword} "
                        f"{t.trend_direction} ({','.join(t.platforms)})")
        
        return trends

    def _extract_all_keywords(self, raw_contents: list) -> Dict[str, Dict]:
        """
        从所有内容中提取关键词
        
        Returns:
            {keyword: {
                'weight': float,         # NLP权重
                'sources': set(),        # 来源平台集合
                'titles': list(),        # 相关标题
                'engagement': float,     # 互动量
            }}
        """
        keyword_data = defaultdict(lambda: {
            'weight': 0.0,
            'sources': set(),
            'titles': [],
            'engagement': 0.0,
        })
        
        # 收集所有文本
        all_texts = []
        for item in raw_contents:
            text = getattr(item, 'title', item.get('title', '')) if isinstance(item, dict) else item.title
            all_texts.append(text)
        
        # 批量提取关键词
        combined_keywords = self.nlp.batch_extract_keywords(all_texts, topK=100)
        keyword_set = {kw for kw, _ in combined_keywords}
        
        # 为每个内容项标记关键词
        for item in raw_contents:
            if isinstance(item, dict):
                title = item.get('title', '')
                platform = item.get('platform', '')
                engagement = float(item.get('likes', 0)) * 1 + float(item.get('comments', 0)) * 3
            else:
                title = item.title
                platform = item.platform
                engagement = item.engagement_score()
            
            # 分词并匹配关键词
            words = self.nlp.tokenize(title)
            for word in words:
                # 必须在关键词集中或是有意义的长词（排除停用词）
                if word.lower() in ALL_STOPWORDS:
                    continue
                if word in keyword_set or len(word) >= 3:
                    kd = keyword_data[word]
                    kd['weight'] += 1.0
                    kd['sources'].add(platform)
                    if len(kd['titles']) < 5:
                        kd['titles'].append(title)
                    kd['engagement'] += engagement
            
            # 提取标签
            tags = []
            if isinstance(item, dict):
                tags = item.get('tags', [])
            else:
                tags = item.tags if hasattr(item, 'tags') else []
            
            for tag in tags:
                if tag and len(tag) >= 2 and tag not in ALL_STOPWORDS:
                    kd = keyword_data[tag]
                    kd['weight'] += 2.0  # 标签权重更高
                    kd['sources'].add(platform)
                    if len(kd['titles']) < 5:
                        kd['titles'].append(title)
                    kd['engagement'] += engagement
        
        # 合并 NLP 权重
        for kw, w in combined_keywords:
            if kw in keyword_data:
                keyword_data[kw]['weight'] += w * 10
        
        return dict(keyword_data)

    def _compute_frequency_stats(self, keyword_data: Dict, 
                                  raw_contents: list) -> Dict[str, Dict]:
        """
        计算词频统计
        
        Returns:
            {keyword: {
                'frequency': int,
                'platforms': list,
                'engagement_norm': float,
                'weight': float,
                'titles': list,
            }}
        """
        # 计算互动量的最大值用于归一化
        max_engagement = max((kd['engagement'] for kd in keyword_data.values()), default=1.0) or 1.0
        
        stats = {}
        for keyword, kd in keyword_data.items():
            freq = int(kd['weight'])
            if freq < 2:  # 过滤低频词
                continue
            
            stats[keyword] = {
                'frequency': freq,
                'platforms': list(kd['sources']),
                'engagement_norm': min(kd['engagement'] / max_engagement, 1.0),
                'weight': kd['weight'],
                'titles': kd['titles'],
            }
        
        return stats

    def _update_time_series(self, freq_stats: Dict[str, Dict]):
        """更新时间序列存储"""
        now = datetime.now(timezone.utc).isoformat()
        
        for keyword, stats in freq_stats.items():
            self.ts_store.record(
                keyword=keyword,
                count=stats['frequency'],
                platforms=stats['platforms'],
                engagement=stats['engagement_norm'],
                window_time=now
            )

    def _run_burst_detection(self, freq_stats: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        对每个关键词运行突发检测
        
        Returns:
            {keyword: {
                'z_score': float,
                'is_burst': bool,
                'macd_value': float,
                'macd_signal': str,
                'acceleration': float,
                'direction': str,
                'sparkline': list,
            }}
        """
        results = {}
        
        for keyword in freq_stats:
            counts = self.ts_store.get_counts(keyword)
            
            # Z-Score 突发检测
            z_score, is_burst = self.burst_detector.z_score_detect(counts)
            
            # MACD 趋势动量
            macd_value, macd_signal = self.burst_detector.macd_detect(counts)
            
            # 加速度
            acceleration = self.burst_detector.calculate_acceleration(counts)
            
            # 趋势方向
            direction = self.heat_scorer.determine_direction(counts)
            
            # 迷你 sparkline (最近 20 个窗口)
            sparkline = counts[-20:] if len(counts) >= 2 else counts
            
            results[keyword] = {
                'z_score': z_score,
                'is_burst': is_burst,
                'macd_value': macd_value,
                'macd_signal': macd_signal,
                'acceleration': acceleration,
                'direction': direction,
                'sparkline': sparkline,
            }
        
        burst_count = sum(1 for r in results.values() if r['is_burst'])
        bullish_count = sum(1 for r in results.values() if r['macd_signal'] == 'bullish')
        logger.info(f"     → {burst_count} 个突发, {bullish_count} 个上升趋势")
        
        return results

    def _score_and_rank(self, freq_stats: Dict, burst_results: Dict, 
                        topK: int) -> List[TrendTopic]:
        """计算综合热力值并排序"""
        trends = []
        
        for keyword, stats in freq_stats.items():
            burst = burst_results.get(keyword, {})
            series = self.ts_store.get_series(keyword)
            
            # 计算距峰值时间
            rec = self.ts_store.data.get(keyword, {})
            peak_time_str = rec.get('peak_time', '')
            hours_since_peak = 0
            if peak_time_str:
                try:
                    peak_dt = datetime.fromisoformat(peak_time_str.replace('Z', '+00:00'))
                    hours_since_peak = (datetime.now(timezone.utc) - peak_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass
            
            # 计算热力值
            heat = self.heat_scorer.compute_heat(
                keyword=keyword,
                freq=stats['frequency'],
                acceleration=burst.get('acceleration', 0),
                source_count=len(stats['platforms']),
                engagement=stats['engagement_norm'],
                hours_since_peak=hours_since_peak,
            )
            
            # 突发加成 (burst 的话热度 ×1.5)
            if burst.get('is_burst', False):
                heat = min(100, heat * 1.5)
            
            # MACD bullish 加成
            if burst.get('macd_signal', '') == 'bullish':
                heat = min(100, heat * 1.2)
            
            # 分类
            category = self._classify_keyword(keyword)
            
            trend = TrendTopic(
                keyword=keyword,
                heat_score=heat,
                frequency=stats['frequency'],
                acceleration=burst.get('acceleration', 0),
                source_diversity=len(stats['platforms']),
                engagement=stats['engagement_norm'],
                is_burst=burst.get('is_burst', False),
                burst_z_score=burst.get('z_score', 0),
                macd_signal=burst.get('macd_signal', 'neutral'),
                macd_value=burst.get('macd_value', 0),
                trend_direction=burst.get('direction', '→'),
                platforms=stats['platforms'],
                related_titles=stats['titles'][:5],
                category=category,
                sparkline=burst.get('sparkline', []),
                first_seen=rec.get('first_seen', ''),
                peak_time=rec.get('peak_time', ''),
            )
            trends.append(trend)
        
        # 按热力值降序排序
        trends.sort(key=lambda t: -t.heat_score)
        
        return trends[:topK]

    def _classify_keyword(self, keyword: str) -> str:
        """关键词分类"""
        text = keyword
        
        finance_kw = {'股', '基金', '理财', '投资', '财经', '上市', '涨停', '跌停', 
                      'A股', '港股', '美股', '央行', '利率', 'GDP', '经济', '金融',
                      '银行', '保险', '期货', '比特币', '数字货币', '黄金', '石油',
                      '房价', '楼市', '消费', '出口', '进口', '贸易'}
        politics_kw = {'政治', '政府', '政策', '外交', '制裁', '选举', '军事', '国防',
                       '总统', '领导', '改革', '法案', '条约', '两会'}
        tech_kw = {'AI', '人工智能', '芯片', '半导体', '大模型', '机器人', '科技',
                   '互联网', '手机', '华为', '苹果', '新能源', '自动驾驶', '量子'}
        intl_kw = {'美国', '俄罗斯', '日本', '韩国', '欧洲', '中东', '以色列',
                   '乌克兰', '北约', '联合国', '国际', '全球'}
        
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

    def save_trends(self, trends: List[TrendTopic], 
                    output_path: Path = None) -> Path:
        """保存趋势分析结果"""
        path = output_path or (DATA_DIR / "trends.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        
        output = {
            'update_time': datetime.now(timezone.utc).isoformat(),
            'total_trends': len(trends),
            'burst_count': sum(1 for t in trends if t.is_burst),
            'algorithm': {
                'heat_weights': {'alpha': ALPHA, 'beta': BETA, 'gamma': GAMMA, 'delta': DELTA},
                'decay_half_life_hours': HALF_LIFE_HOURS,
                'burst_z_threshold': BURST_Z_THRESHOLD,
                'macd_periods': {'short': MACD_SHORT_PERIOD, 'long': MACD_LONG_PERIOD, 'signal': MACD_SIGNAL_PERIOD},
            },
            'trends': [t.to_dict() for t in trends],
        }
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        
        logger.info(f"  💾 趋势结果保存: {path}")
        return path


# ================================
# 模块导出
# ================================
__all__ = [
    'TrendTopic', 'ChineseNLP', 'TimeSeriesStore',
    'BurstDetector', 'HeatScorer', 'TrendEngine',
]

if __name__ == '__main__':
    # 独立测试：NLP 关键词提取示例
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    
    nlp = ChineseNLP()
    
    test_texts = [
        "央行宣布降息25个基点，A股市场全线高开",
        "华为发布最新自研芯片，突破美国制裁封锁",
        "特斯拉上海超级工厂产能再创新高",
        "DeepSeek发布新一代大模型，性能超越GPT-4",
        "中美关系紧张，外交部回应制裁措施",
        "比特币突破10万美元，加密货币市场狂欢",
        "春节档电影票房突破100亿，刷新历史纪录",
        "新能源汽车销量首次超过燃油车",
    ]
    
    print("\n" + "="*60)
    print("📝 NLP 关键词提取测试")
    print("="*60)
    
    for text in test_texts:
        kws = nlp.extract_keywords_tfidf(text, topK=5)
        print(f"\n📰 {text}")
        print(f"   关键词: {', '.join(f'{w}({s:.2f})' for w, s in kws)}")
    
    print("\n" + "="*60)
    print("📊 批量关键词提取")
    print("="*60)
    
    batch_kws = nlp.batch_extract_keywords(test_texts, topK=20)
    for i, (word, score) in enumerate(batch_kws):
        print(f"   {i+1:2d}. {word:10s} → {score:.4f}")
    
    # 新词发现测试
    print("\n" + "="*60)
    print("🆕 新词发现")
    print("="*60)
    
    new_words = nlp.discover_new_words(test_texts * 3, min_freq=2)
    for word, freq in new_words[:10]:
        print(f"   {word} (出现 {freq} 次)")
    
    # 突发检测测试
    print("\n" + "="*60)
    print("🚨 突发检测测试")
    print("="*60)
    
    # 模拟时间序列：前面平稳，最后突增
    normal_counts =  [5, 6, 4, 7, 5, 6, 5, 4, 6, 5, 5, 7, 6, 5]
    burst_counts  =  [5, 6, 4, 7, 5, 6, 5, 4, 6, 5, 5, 7, 6, 25]  # 最后一个突增
    
    z1, is_burst1 = BurstDetector.z_score_detect(normal_counts)
    z2, is_burst2 = BurstDetector.z_score_detect(burst_counts)
    
    print(f"   正常序列: z={z1:.2f}, burst={is_burst1}")
    print(f"   突发序列: z={z2:.2f}, burst={is_burst2}")
    
    print("\n✅ 测试完成!")
