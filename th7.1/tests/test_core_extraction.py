from __future__ import annotations

import os
import sys
import json
import random
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple

# 确保能导入 core 模块（修复：本文件在 tests/ 下，需上溯一级到项目根）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 导入我们要测试的真实业务模块
from core.dialog_analyzer import DialogAnalyzer

# ==============================================================================
# 🌟 1. 极限测试语料库 (5大题材 + 30+强干扰噪音)
# ==============================================================================

LORE_SETS = {
    "cyber_xianxia": {
        "ground_truth": {
            "genre": "赛博修仙", "world_type": "霓虹废土/高科技低生活", 
            "main_character": "李玄机", "title": "机械飞升录"
        },
        "fragments": [
            "我想写个赛博修仙的故事，背景是霓虹闪烁的废土城市。",
            "主角叫李玄机，他是个安装了义体的道士。",
            "书名就叫《机械飞升录》吧，听起来带感。",
            "在这个世界，灵气其实是核辐射，修仙就是改造义体和植入芯片。",
            "他的武器是一把高频粒子震荡桃木剑，能切开装甲。",
            "反派是垄断了灵石（高纯度核电池）的巨型企业‘太上重工’。",
            "境界分为：炼气（植入期）、筑基（超频期）、金丹（聚变期）。",
            "李玄机有个机械猫，叫‘太极’，能骇入任何网络。",
            "故事开篇是李玄机在垃圾场捡到了一个上古玉简（其实是旧时代U盘）。",
            "太上重工的CEO其实是李玄机的克隆体父亲。"
        ]
    },
    "cthulhu_wuxia": {
        "ground_truth": {
            "genre": "克苏鲁武侠", "world_type": "诡异江湖/不可名状", 
            "main_character": "燕不归", "title": "盲剑客与旧日支配者"
        },
        "fragments": [
            "我要写一本克苏鲁风格的武侠小说，江湖其实是被外神污染的隔离区。",
            "主角叫燕不归，是个瞎子剑客，因为他看了一眼‘不可名状之物’瞎的。",
            "书名定为《盲剑客与旧日支配者》。",
            "武林秘籍其实都是外神低语的记录，练武就是变异。",
            "名门正派‘少林’其实是镇压地下触手怪物的狱卒。",
            "燕不归的剑法叫‘盲目痴愚剑’，出剑时周围人会掉SAN值。",
            "女主角叫阿织，她不是人，是一团有自我意识的菌丝。",
            "朝廷的锦衣卫都戴着防毒面具，修炼的是‘理智剥离法’。",
            "最终BOSS是武当掌门，他已经孵化出了星之眷族。",
            "江湖上流传的‘黑玉断续膏’其实是用修格斯体液做的。"
        ]
    },
    "wasteland_fairy": {
        "ground_truth": {
            "genre": "废土童话", "world_type": "末日后的奇幻/治愈与绝望", 
            "main_character": "小红帽", "title": "辐射森林的安魂曲"
        },
        "fragments": [
            "构思一个废土童话，核战后的世界，但带有暗黑童话色彩。",
            "主角是变异的小红帽，她穿着防辐射的红色斗篷。",
            "书名《辐射森林的安魂曲》。",
            "大灰狼其实是基因改造的生化兵器，但被小红帽驯服了。",
            "外婆住在废弃的核电站里，她是个疯狂的科学家。",
            "森林里的蘑菇会发光，吃了能产生幻觉看到旧世界。",
            "猎人是个赏金猎人，用的是激光狙击枪。",
            "货币是干净的纯净水和未受污染的童话书。",
            "小红帽的篮子里装的不是蛋糕，而是高爆手雷和抗辐射药。",
            "故事的基调是绝望中寻找治愈，画面感要像《疯狂麦克斯》遇上《爱丽丝梦游仙境》。"
        ]
    },
    "space_palace": {
        "ground_truth": {
            "genre": "星际宫斗", "world_type": "太空歌剧/封建帝制", 
            "main_character": "林星澜", "title": "银河王座：荆棘冠冕"
        },
        "fragments": [
            "写个星际宫斗文，背景是银河帝国，科技发达但制度是封建帝制。",
            "主角叫林星澜，是边缘星系的落魄皇子。",
            "书名《银河王座：荆棘冠冕》。",
            "皇帝有几百个克隆体儿子，为了争夺王位互相暗杀。",
            "林星澜的伴生兽是一只能吞噬星舰的虚空巨兽。",
            "宫廷里的人都不用嘴说话，用脑机接口进行精神交流。",
            "反派是大皇子，他掌握了禁忌的‘降维打击’武器。",
            "林星澜的未婚妻其实是敌对虫族的女王化身。",
            "帝国的权力中心是‘戴森球’里的紫禁城。",
            "故事核心是权谋、背叛和在浩瀚宇宙中的孤独感。"
        ]
    },
    "rule_horror": {
        "ground_truth": {
            "genre": "规则怪谈", "world_type": "现代都市/诡异规则", 
            "main_character": "陈默", "title": "动物园员工守则"
        },
        "fragments": [
            "写个规则怪谈，背景是现代都市的一个诡异动物园。",
            "主角叫陈默，是个刚入职的夜班保安。",
            "书名《动物园员工守则》。",
            "核心设定是必须严格遵守员工手册上的规则，否则就会死或变异。",
            "规则1：本园没有兔子，如果看到兔子，请立刻闭上眼睛。",
            "规则2：大象馆的饲养员只有三个人，如果你看到第四个，不要和他说话。",
            "陈默有个金手指，他能看到规则背后的‘污染源’。",
            "园长其实是个不可名状的怪物，他每天午夜会巡视。",
            "动物园的地图每天都会发生微小的变化。",
            "故事氛围要极度压抑，充满细思极恐的细节。"
        ]
    }
}

NOISE_POOL = [
    "帮我写个Python爬虫，抓取起点中文网的排行榜，要用BeautifulSoup。",
    "12345乘以67890等于多少？我数学不好，你帮我算算。",
    "今天天气真好，晚上去吃重庆老火锅吧，你有什么推荐的牛油底料？",
    "其实主角应该叫龙傲天，你觉得呢？（误导噪音：看AI是否会被覆盖）",
    "帮我翻译一下这句话：'The quick brown fox jumps over the lazy dog'。",
    "这本小说的字数我希望能控制在200万字左右，每章3000字，记得排版好看点。",
    "能不能给我讲讲量子力学的基本原理？用通俗的话解释。",
    "我昨天打游戏连跪了五把，气死我了，队友太坑了，简直是猪队友。",
    "主角其实是个女的，只是女扮男装，你写的时候注意别穿帮。（误导噪音）",
    "帮我写个SQL查询语句，从users表里查出年龄大于18岁的用户。",
    "你觉得这本小说如果拍成电视剧，应该选哪个明星来演男主？",
    "我的猫今天吐毛球了，是不是该给它买点化毛膏？",
    "给我讲个冷笑话吧，要那种特别冷的，能冻死人的。",
    "世界观里加一个设定：其实这一切都是主角的一场梦。（剧毒噪音：看AI是否会采纳这种烂俗设定）",
    "帮我总结一下《三体》的第一部剧情，大概500字左右。",
    "Python里的GIL是什么？对多线程有什么影响？",
    "反派其实是主角的儿子，从未来穿越回来的。（误导噪音）",
    "帮我写个正则表达式，匹配所有的电子邮箱地址。",
    "今天股市大跌，我的基金又绿了，心好痛。",
    "你能帮我画一张主角的设定图吗？用Midjourney的提示词。"
]

NOISE_KEYWORDS = [
    "python", "爬虫", "豆瓣", "连跪", "猪队友", "明星", "电视剧", 
    "12345", "67890", "火锅", "牛油", "冷笑话", "猫", "毛球", "化毛膏",
    "翻译", "fox", "dog", "BeautifulSoup", "SQL", "GIL", "正则", "股市", "基金", "Midjourney",
    "一场梦"
]


# ==============================================================================
# 🌟 2. 核心黑盒测试引擎 (V2.0 支持来源追踪与推断测试)
# ==============================================================================
class CoreExtractionTester:
    def __init__(self, test_rounds: int = 5):
        self.test_rounds = test_rounds
        self.report_lines = []
        self.report_path = os.path.join(os.path.dirname(__file__), f"core_extraction_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        self.analyzer = DialogAnalyzer(log_callback=self.log)

    def log(self, msg: str, level: str = "INFO"):
        clean_msg = msg
        if isinstance(msg, str) and msg.startswith("{") and '"message"' in msg:
            try: clean_msg = json.loads(msg).get("message", msg)
            except: pass
        print(f"[{level}] {clean_msg}")
        self.report_lines.append(f"[{level}] {clean_msg}")

    def generate_test_case(self) -> Tuple[str, Dict[str, Any], List[str], str, List[str]]:
        """随机生成测试用例，并故意隐藏部分字段以测试 AI 的“自由推断”能力。"""
        genre_key = random.choice(list(LORE_SETS.keys()))
        lore_data = LORE_SETS[genre_key]
        
        num_lore = random.randint(5, min(8, len(lore_data["fragments"])))
        selected_fragments = random.sample(lore_data["fragments"], num_lore)
        
        num_noise = random.randint(3, 5)
        selected_noises = random.sample(NOISE_POOL, num_noise)
        
        # 🌟 核心新增：随机隐藏 1-2 个设定字段，测试 AI 的“网文常识推断”
        hideable_fields = ["title", "total_word_count", "main_character"]
        num_hidden = random.randint(1, 2)
        hidden_fields = random.sample(hideable_fields, min(num_hidden, len(hideable_fields)))
        
        filtered_fragments = []
        for frag in selected_fragments:
            hide_this = False
            if "title" in hidden_fields and ("书名" in frag or "《" in frag): hide_this = True
            if "total_word_count" in hidden_fields and ("字数" in frag or "万字" in frag): hide_this = True
            if "main_character" in hidden_fields and ("主角叫" in frag or "主角是" in frag or "男主" in frag or "女主" in frag): hide_this = True
            if not hide_this: filtered_fragments.append(frag)
            
        if len(filtered_fragments) < 3:
            filtered_fragments = selected_fragments
            hidden_fields = []

        all_messages = filtered_fragments + selected_noises
        random.shuffle(all_messages)
        user_input = "\n".join([f"{i+1}. {msg}" for i, msg in enumerate(all_messages)])
        
        return user_input, lore_data["ground_truth"], selected_noises, genre_key, hidden_fields

    def run_extraction(self, user_input: str) -> Dict[str, Any]:
        try:
            spec = self.analyzer.extract_spec(user_input)
            return spec if spec else {}
        except Exception as e:
            return {"error": f"DialogAnalyzer.extract_spec 抛出异常: {str(e)}"}

    def evaluate(self, ai_result: Dict[str, Any], expected: Dict[str, Any], noises: List[str], hidden_fields: List[str]) -> Dict[str, Any]:
        if "error" in ai_result:
            return {"score": 0, "missing": list(expected.keys()), "hallucinations": [], "error": ai_result["error"]}

        ai_text = json.dumps(ai_result, ensure_ascii=False).lower()
        source_tracking = ai_result.get("_source_tracking", {})
        
        # 1. 检查召回率 (针对未隐藏的期望字段)
        missing, recalled = [], []
        for key, value in expected.items():
            if key in hidden_fields: continue 
            ai_value = str(ai_result.get(key, "")).lower()
            expected_value = str(value).lower()
            if expected_value in ai_value or any(v in ai_value for v in expected_value.split('/')):
                recalled.append(key)
            else:
                missing.append(key)
        recall_rate = len(recalled) / max(1, len(expected) - len(hidden_fields))

        # 2. 检查幻觉/噪音污染
        hallucinations = []
        for noise in noises:
            for kw in NOISE_KEYWORDS:
                if kw.lower() in noise.lower() and kw.lower() in ai_text:
                    hallucinations.append(kw)
                    break
        if "一场梦" in ai_text and "一场梦" not in json.dumps(expected, ensure_ascii=False):
            hallucinations.append("采纳了烂俗设定(一场梦)")
        hallucination_rate = len(hallucinations) / max(1, len(noises))

        # 3. 🌟 核心新增：检查 AI 自由推断能力 (针对被隐藏的字段)
        inferred_success, inferred_fail = [], []
        default_values = {"未命名项目", "主角", "50000", "2500", "通用", "默认世界", ""}
        
        for key in hidden_fields:
            ai_val = str(ai_result.get(key, "")).strip()
            if ai_val and ai_val not in default_values and ai_val != "0":
                inferred_success.append(key)
            else:
                inferred_fail.append(key)
        inference_rate = len(inferred_success) / max(1, len(hidden_fields))

        # 4. 计算综合得分 (召回 50% + 防幻觉 30% + 推断 20%)
        score = int((recall_rate * 50) + ((1 - min(hallucination_rate, 1.0)) * 30) + (inference_rate * 20))
        
        return {
            "score": score, "recall_rate": f"{recall_rate*100:.1f}%", "hallucination_rate": f"{hallucination_rate*100:.1f}%",
            "inference_rate": f"{inference_rate*100:.1f}%", "recalled": recalled, "missing": missing,
            "hallucinations": list(set(hallucinations)), "inferred_success": inferred_success, 
            "inferred_fail": inferred_fail, "source_tracking": source_tracking, "raw_ai_result": ai_result
        }

    def run_tests(self):
        self.log("=" * 70)
        self.log(f"🔍 核心业务 (DialogAnalyzer) 设定提取极限压力测试报告 (V2.0)")
        self.log(f"⏱️ 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"🔄 测试轮次: {self.test_rounds}")
        self.log("=" * 70)

        total_score = 0
        for i in range(1, self.test_rounds + 1):
            self.log(f"\n{'='*30} 测试用例 #{i} {'='*30}")
            user_input, expected, noises, genre_key, hidden_fields = self.generate_test_case()
            
            self.log(f"\n【🎭 测试题材】: {genre_key}")
            if hidden_fields:
                self.log(f"【🙈 故意隐藏的字段 (测试AI推断)】: {', '.join(hidden_fields)}")
            else:
                self.log(f"【🙈 故意隐藏的字段】: 无 (全量提供)")
                
            self.log("\n【📥 注入的混乱输入 (已打乱)】")
            self.log(user_input)
                
            self.log("\n【🎯 期望提取的核心设定 (Ground Truth)】")
            for k, v in expected.items():
                if k not in hidden_fields: self.log(f"  - {k}: {v}")

            self.log("\n⏳ 正在调用 DialogAnalyzer.extract_spec 进行提取...")
            start_time = time.time()
            ai_result = self.run_extraction(user_input)
            elapsed = time.time() - start_time
            self.log(f"✅ 提取完成 (耗时: {elapsed:.2f}秒)")

            eval_res = self.evaluate(ai_result, expected, noises, hidden_fields)
            total_score += eval_res["score"]

            self.log("\n【🤖 业务代码实际返回结果 (含来源追踪)】")
            raw_result = eval_res.get("raw_ai_result", ai_result)
            tracking = raw_result.pop("_source_tracking", {})
            self.log(json.dumps(raw_result, ensure_ascii=False, indent=2))
            
            self.log("\n【🔍 字段来源追踪 (_source_tracking)】")
            for k, v in tracking.items():
                self.log(f"  - {k}: [{v}]")

            self.log("\n【📊 对比分析与评分】")
            if "error" in eval_res:
                self.log(f"  ❌ 致命错误: {eval_res['error']}")
                self.log(f"  🏆 本轮得分: 0 / 100")
            else:
                self.log(f"  ✅ 成功召回字段: {', '.join(eval_res['recalled']) if eval_res['recalled'] else '无'}")
                self.log(f"  ❌ 遗漏设定字段: {', '.join(eval_res['missing']) if eval_res['missing'] else '无'}")
                if hidden_fields:
                    self.log(f"  💡 成功推断字段: {', '.join(eval_res['inferred_success']) if eval_res['inferred_success'] else '无 (AI摆烂使用了默认值)'}")
                    self.log(f"  ❌ 推断失败字段: {', '.join(eval_res['inferred_fail']) if eval_res['inferred_fail'] else '无'}")
                self.log(f"  ⚠️ 幻觉/噪音污染: {', '.join(eval_res['hallucinations']) if eval_res['hallucinations'] else '无 (完美抗干扰)'}")
                self.log(f"  📈 召回率: {eval_res['recall_rate']} | 幻觉率: {eval_res['hallucination_rate']} | 推断成功率: {eval_res['inference_rate']}")
                self.log(f"  🏆 本轮得分: {eval_res['score']} / 100")

        avg_score = total_score / self.test_rounds if self.test_rounds > 0 else 0
        self.log(f"\n{'='*70}")
        self.log(f"🎉 测试完成！DialogAnalyzer 综合平均得分: {avg_score:.1f} / 100")
        if avg_score >= 85: self.log("🌟 评价：提取与推断能力极佳，具备真正的网文策划直觉！")
        elif avg_score >= 70: self.log("⚠️ 评价：能力优秀，但在极复杂噪音下偶有遗漏或推断保守。")
        else: self.log("❌ 评价：能力较弱，容易被噪音带偏或缺乏自主推断能力。")
        self.log(f"{'='*70}")

        try:
            with open(self.report_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.report_lines))
            print(f"\n✅ 详细诊断报告已保存至: {self.report_path}")
        except Exception as e:
            print(f"\n❌ 报告写入失败: {e}")

if __name__ == "__main__":
    tester = CoreExtractionTester(test_rounds=5)
    tester.run_tests()
