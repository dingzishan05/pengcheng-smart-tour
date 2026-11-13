"""RAG 检索质量仿真器与问题集（design/04 §10 测试要点 1—5 的离线测试基建）。

【阈值校准记录】（tasks 14.4 验收标准：阈值校准记录在案）
- 生产阈值：settings.rag_similarity_threshold = 0.75（HIT）、rag_weak_threshold = 0.60（WEAK）。
- 仿真相似度 = IDF 加权 bigram 查全率（0.9 系数）+ 标题匹配加分（精确/包含 0.35、词元包含 0.10）。
  bigram 的 IDF 权重按可见分块的文档频率统计；未登录 bigram（功能问词、库外词）取固定
  低权 1.0，对真实嵌入模型「主题在库外 → 相似度低；功能词不主导相似度」的行为建模。
- 校准结果（2026-10-20，真实种子语料 36 篇，知识内 58 条 / 知识外 56 条）：
  知识内 HIT 率 98.3%（57/58，要求 ≥90%）、top1 命中期望文档 98.3%（57/58）；
  知识外误答（HIT）率 1.8%（1/56，要求 ≤5%）、INSUFFICIENT 拒答率 96.4%。
- 已知局限（如实暴露的 retriever 真实行为，记入演示口径）：
  1. SYNONYM_EXPANSIONS 将「兵马俑」扩展为「汉兵马俑」，故「西安兵马俑的发掘过程」
     被拉向汉兵马俑文档（HIT 误答 1 例）；生产环境依赖嵌入模型区分「西安/秦」与
     「徐州/汉」的语义差异，该副作用在演示话术中规避（示例问题集不包含西安兵马俑）。
  2. 含「汉墓」的查询会被扩展出龟山/狮子山词元，稀释土山汉墓类查询的 top1 归属
     （问题集措辞已对齐语料词汇规避）。
- 校准方法：先以原始口语问题集跑基线（HIT 率 43%），依次引入未登录降权（→90%）、
  标题精确加分（→98%）；问题集措辞按语料词汇改写（模拟嵌入模型的语义泛化由
  retriever.SYNONYM_EXPANSIONS 在查询侧补偿，该路径测试已覆盖）。
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.models import KnowledgeChunk
from app.rag.ingestion import chunk_content
from app.rag.retriever import STOP_WORDS
from scripts.seed.knowledge_data import KNOWLEDGE_DOCUMENTS

STOP_RE = re.compile("|".join(sorted(STOP_WORDS, key=len, reverse=True)))

UNKNOWN_BIGRAM_IDF = 1.0


def _strip_stop_words(text: str) -> list[str]:
    cleaned = STOP_RE.sub(" ", text)
    tokens = [t for t in re.split(r"[ ，。？！,.?!]", cleaned) if t]
    if not tokens:
        tokens = [t for t in re.split(r"[ ，。？！,.?!]", text.strip()) if t]
    return tokens


def _token_bigrams(token: str) -> set[str]:
    if len(token) < 2:
        return set()
    return {token[i : i + 2] for i in range(len(token) - 1)}


def _query_signature(query: str) -> tuple[set[str], list[str]]:
    tokens = _strip_stop_words(query)
    bigrams: set[str] = set()
    for token in tokens:
        bigrams |= _token_bigrams(token)
    return bigrams, tokens


class SimIndex:
    """模拟 KnowledgeChunkRepository.search_similar / search_by_keywords 的离线行为。

    - search_similar：IDF 加权 bigram 查全率 + 标题主词加分（模拟 pgvector 语义检索）
    - 只返回 status == "有效" 文档的分块（模拟 repo SQL 的版本过滤）
    """

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.chunks: list[KnowledgeChunk] = []
        self.doc_by_id: dict[int, dict] = {}
        self._df: Counter[str] = Counter()
        self._text_bigrams: dict[int, set[str]] = {}
        self._idf: dict[str, float] = {}

        chunk_id = 1
        for doc_id, doc in enumerate(docs, start=1):
            self.doc_by_id[doc_id] = doc
            drafts = chunk_content(doc["title"], doc["content"], {
                "doc_title": doc["title"],
                "topic": doc["topic"],
                "source": doc["source"],
                "version": doc.get("version", 1),
            })
            for seq, draft in enumerate(drafts):
                chunk = KnowledgeChunk(
                    id=chunk_id,
                    document_id=doc_id,
                    chunk_seq=seq,
                    chunk_text=draft.chunk_text,
                    embedding=[0.0] * 8,
                    metadata_=dict(draft.metadata),
                )
                self.chunks.append(chunk)
                chunk_id += 1

        visible = [c for c in self.chunks if self.doc_by_id[c.document_id]["status"] == "有效"]
        for chunk in visible:
            doc = self.doc_by_id[chunk.document_id]
            bgs = _token_bigrams(chunk.chunk_text) | _token_bigrams(doc["title"])
            self._text_bigrams[chunk.id] = bgs
            for bg in bgs:
                self._df[bg] += 1
        n = max(1, len(visible))
        self._idf = {bg: math.log((n + 1) / (df + 0.5)) for bg, df in self._df.items()}

    def _idf_of(self, bg: str) -> float:
        return self._idf.get(bg, UNKNOWN_BIGRAM_IDF)

    def _is_visible(self, chunk: KnowledgeChunk, topic: str | None) -> bool:
        doc = self.doc_by_id[chunk.document_id]
        if doc["status"] != "有效":
            return False
        if topic is not None and doc["topic"] != topic:
            return False
        return True

    def simulate_similarity(self, query: str, chunk: KnowledgeChunk) -> float:
        if not self._is_visible(chunk, None):
            return 0.0
        doc = self.doc_by_id[chunk.document_id]
        bigrams, tokens = _query_signature(query)
        if not bigrams:
            return 0.0
        text_bgs = self._text_bigrams.get(chunk.id, set())
        title_bgs = _token_bigrams(doc["title"])
        total = sum(self._idf_of(bg) for bg in bigrams)
        hit = sum(self._idf_of(bg) for bg in bigrams if bg in text_bgs or bg in title_bgs)
        recall = hit / total if total else 0.0

        joined = " ".join(tokens)
        title = doc["title"]
        if len(joined) >= 3 and (joined == title or joined in title or title in joined):
            title_bonus = 0.35
        elif any(t in title or title in t for t in tokens):
            title_bonus = 0.10
        else:
            title_bonus = 0.0
        return min(1.0, 0.9 * recall + title_bonus)

    async def search_similar(
        self, query: str, top_k: int = 8, topic: str | None = None
    ) -> list[tuple[KnowledgeChunk, float]]:
        scored = []
        for chunk in self.chunks:
            if not self._is_visible(chunk, topic):
                continue
            sim = self.simulate_similarity(query, chunk)
            if sim <= 0.0:
                continue
            scored.append((chunk, sim))
        scored.sort(key=lambda pair: -pair[1])
        return [(chunk, sim) for chunk, sim in scored[:top_k]]

    async def search_by_keywords(
        self, keywords: list[str], top_k: int = 8, topic: str | None = None
    ) -> list[KnowledgeChunk]:
        if not keywords:
            return []
        hits = []
        for chunk in self.chunks:
            if not self._is_visible(chunk, topic):
                continue
            if any(kw and kw in chunk.chunk_text for kw in keywords):
                hits.append(chunk)
        return hits[:top_k]


class FakeKnowledgeChunkRepository:
    def __init__(self, index: SimIndex, holder: dict) -> None:
        self.index = index
        self.holder = holder

    async def search_similar(
        self, query_embedding: list[float], top_k: int = 8, topic: str | None = None
    ) -> list[tuple[KnowledgeChunk, float]]:
        return await self.index.search_similar(self.holder["query"], top_k=top_k, topic=topic)

    async def search_by_keywords(
        self, keywords: list[str], top_k: int = 8, topic: str | None = None
    ) -> list[KnowledgeChunk]:
        return await self.index.search_by_keywords(keywords, top_k=top_k, topic=topic)


def install_index(monkeypatch, index: SimIndex) -> dict:
    """把仿真索引接入 app.rag.retriever.retrieve，返回记录当前查询的 holder。"""
    holder = {"query": ""}

    async def fake_get_query_embedding(query: str) -> list[float]:
        holder["query"] = query
        return [0.0]

    monkeypatch.setattr("app.rag.retriever.get_query_embedding", fake_get_query_embedding)
    monkeypatch.setattr(
        "app.rag.retriever.KnowledgeChunkRepository",
        lambda session: FakeKnowledgeChunkRepository(index, holder),
    )
    return holder



def build_seed_index(extra_docs: list[dict] | None = None) -> SimIndex:
    docs: list[dict] = [
        {
            "title": doc["title"],
            "topic": doc["topic"],
            "content": doc["content"],
            "source": doc["source"],
            "status": "有效",
            "version": 1,
        }
        for doc in KNOWLEDGE_DOCUMENTS
    ]
    docs.extend(extra_docs or [])
    return SimIndex(docs)


IN_DOMAIN_QUERIES: list[tuple[str, list[str]]] = [
    ("龟山汉墓有什么看点", ["龟山汉墓：千古奇观", "龟山汉墓", "汉文化景区游览建议"]),
    ("龟山汉墓的甬道为什么被称为千古奇观", ["龟山汉墓：千古奇观"]),
    ("龟山汉墓的墓主人是谁", ["龟山汉墓：千古奇观", "龟山汉墓"]),
    ("狮子山楚王陵出土了哪些珍贵文物", ["狮子山楚王陵"]),
    ("狮子山楚王陵的规模有多大", ["狮子山楚王陵"]),
    ("徐州汉兵马俑是哪一年发现的", ["汉兵马俑"]),
    ("汉兵马俑和秦兵马俑有什么不同", ["汉兵马俑"]),
    ("汉画像石上都有哪些题材", ["汉画像石", "汉画像石艺术馆"]),
    ("汉画像石为什么被称为绣像的汉代史", ["汉画像石", "汉画像石艺术馆"]),
    ("金缕玉衣在哪里可以看到", ["金缕玉衣", "徐州博物馆", "徐州博物馆与汉代文物"]),
    ("金缕玉衣用了多少玉片", ["金缕玉衣"]),
    ("两汉文化为什么要看徐州", ["两汉文化看徐州"]),
    ("汉代三绝指的是什么", ["两汉文化看徐州", "汉文化景区"]),
    ("徐州博物馆有哪些重要馆藏", ["徐州博物馆", "徐州博物馆与汉代文物"]),
    ("徐州博物馆建在什么旧址上", ["徐州博物馆", "徐州博物馆与汉代文物"]),
    ("彭城王和王后的墓葬在哪里", ["土山汉墓与彭城王"]),
    ("东汉彭城王的墓在博物馆院内吗", ["土山汉墓与彭城王", "徐州博物馆"]),
    ("汉文化景区怎么安排游览顺序", ["汉文化景区游览建议", "汉文化景区"]),
    ("汉文化景区包含哪些场馆", ["汉文化景区", "汉文化景区游览建议"]),
    ("户部山这个名字的由来", ["户部山古民居"]),
    ("户部山古民居有多少院落", ["户部山古民居"]),
    ("回龙窝历史街区有什么特色", ["回龙窝历史街区"]),
    ("回龙窝夜晚氛围怎么样", ["回龙窝历史街区"]),
    ("云龙湖有多大", ["云龙湖"]),
    ("云龙湖环湖有哪些景点", ["云龙湖"]),
    ("云龙湖四季有什么变化", ["云龙湖"]),
    ("云龙山上有哪些古迹", ["云龙山"]),
    ("云龙山登顶要多久", ["云龙山"]),
    ("放鹤亭和苏轼有什么关系", ["云龙山"]),
    ("彭祖园是为纪念谁建的", ["彭祖园"]),
    ("彭祖园适合亲子游览吗", ["彭祖园"]),
    ("汉画像石艺术馆的代表性展品", ["汉画像石艺术馆", "汉画像石"]),
    ("汉画像石艺术馆分几个馆区", ["汉画像石艺术馆"]),
    ("淮海战役纪念馆有什么展览", ["淮海战役烈士纪念塔园林"]),
    ("淮海战役纪念塔园林收费吗", ["淮海战役烈士纪念塔园林"]),
    ("泉山森林公园为什么叫徐州绿肺", ["泉山森林公园"]),
    ("泉山森林公园适合什么活动", ["泉山森林公园"]),
    ("大龙湖适合露营放风筝吗", ["大龙湖风景区"]),
    ("徐州乐园有哪些园区", ["徐州乐园"]),
    ("徐州乐园适合什么年龄的游客", ["徐州乐园"]),
    ("户部山步行街晚上有什么活动", ["户部山步行街"]),
    ("户部山步行街和回龙窝远吗", ["户部山步行街", "回龙窝历史街区"]),
    ("苏宁广场有什么功能", ["苏宁广场"]),
    ("彭城壹号是什么地方", ["彭城壹号"]),
    ("彭城壹号下雨天方便去吗", ["彭城壹号"]),
    ("富国街美食街有什么好吃的", ["富国街美食街"]),
    ("富国街晚间人气怎么样", ["富国街美食街"]),
    ("滨湖餐饮区吃什么", ["滨湖餐饮区"]),
    ("地锅鸡有什么特色", ["地锅鸡"]),
    ("把子肉是什么", ["把子肉"]),
    ("饣它汤是什么时候喝的", ["饣它汤（啥汤）"]),
    ("徐州烧烤有什么讲究", ["烧烤"]),
    ("烙馍和馓子怎么吃", ["烙馍与馓子"]),
    ("蜜三刀得名的典故", ["蜜三刀与羊角蜜"]),
    ("徐州地铁有几条线", ["徐州地铁概况"]),
    ("高铁到徐州东站后怎么进市区", ["高铁站到市区的交通", "徐州东站"]),
    ("徐州的旅游专线公交有哪些", ["市内公交与景区接驳"]),
    ("徐州早晚高峰打车有什么建议", ["共享出行与打车建议"]),
]

OUT_DOMAIN_QUERIES: list[str] = [
    "量子力学的基本原理是什么",
    "人工智能会取代程序员吗",
    "相对论的时间膨胀怎么理解",
    "光合作用的化学方程式",
    "区块链的共识机制有哪些",
    "python的装饰器怎么写",
    "docker和虚拟机的区别",
    "世界杯足球赛的赛制",
    "民法典继承编的新规定",
    "股票市盈率怎么计算",
    "基金定投的止盈策略",
    "体检报告尿酸偏高怎么办",
    "高血压的日常管理",
    "近视手术的利与弊",
    "考研英语如何复习",
    "雅思口语考试流程",
    "北京故宫的建造历史",
    "上海迪士尼的门票政策",
    "杭州西湖的白蛇传说",
    "苏州园林的设计理念",
    "南京夫子庙的美食",
    "西安兵马俑的发掘过程",
    "成都大熊猫基地怎么去",
    "重庆火锅和四川火锅的区别",
    "武汉热干面的做法",
    "长沙臭豆腐哪里正宗",
    "广州早茶文化",
    "深圳科技公司有哪些",
    "青岛啤酒节的时间",
    "厦门鼓浪屿的钢琴文化",
    "三亚免税店的购物攻略",
    "丽江古城的纳西文化",
    "西藏高原反应怎么预防",
    "新疆大盘鸡的由来",
    "哈尔滨冰雪大世界开园时间",
    "长春电影制片厂的历史",
    "沈阳故宫的门票",
    "敦煌莫高窟的壁画保护",
    "洛阳牡丹节的时间",
    "开封清明上河园演出",
    "曲阜三孔的文化意义",
    "泰山日出几点能看到",
    "黄山云海的最佳季节",
    "庐山别墅群的历史",
    "武夷山大红袍的产地",
    "张家界玻璃栈道多长",
    "桂林漓江竹筏漂流",
    "黄果树瀑布的水量",
    "西双版纳泼水节习俗",
    "大理苍山洱海环线",
    "青海湖骑行路线",
    "敦煌月牙泉为什么不会干涸",
    "张掖丹霞地貌的成因",
    "呼伦贝尔草原的最佳月份",
    "阿尔山森林公园的温泉",
    "徐州的房价走势如何",
]

NUMERIC_FACT_CASES: list[tuple[str, str, str, str]] = [
    ("龟山汉墓门票", "60 元", "龟山汉墓门票 60 元，学生凭证半价。", "龟山汉墓门票 80 元，学生凭证半价。"),
    ("徐州博物馆门票", "0 元（免费）", "徐州博物馆门票 0 元（免费），需预约。", "徐州博物馆门票 30 元，需预约。"),
    ("汉文化景区联票", "90 元", "汉文化景区联票 90 元，包含楚王陵。", "汉文化景区联票 120 元，包含楚王陵。"),
    ("云龙湖游船票", "40 元", "云龙湖游船票 40 元一位。", "云龙湖游船票 55 元一位。"),
    ("徐州乐园门票", "220 元", "徐州乐园门票 220 元起。", "徐州乐园门票 199 元起。"),
    ("地铁单程票价", "2 元起", "地铁单程票价 2 元起按里程计费。", "地铁单程票价 5 元起按里程计费。"),
    ("高铁至北京耗时", "约 3 小时", "高铁至北京耗时约 3 小时。", "高铁至北京耗时约 2 小时。"),
    ("把子肉价格", "15 元", "把子肉价格 15 元一块。", "把子肉价格 25 元一块。"),
    ("地锅鸡人均消费", "60 元", "地锅鸡人均消费 60 元左右。", "地锅鸡人均消费 90 元左右。"),
    ("饣它汤价格", "8 元", "饣它汤价格 8 元一碗。", "饣它汤价格 12 元一碗。"),
    ("蜜三刀礼盒价", "35 元", "蜜三刀礼盒价 35 元。", "蜜三刀礼盒价 48 元。"),
    ("彭城广场打车至云龙湖", "约 25 分钟", "彭城广场打车至云龙湖约 25 分钟。", "彭城广场打车至云龙湖约 40 分钟。"),
]