# core/quality_scoring_rules.py
# ==============================================================================
# 🌟 题材自适应多维度小说质量打分系统 V3.0 (全角色死锁 & 绝对红线版)
# ==============================================================================
# 核心升级：
# 1. 全角色代词与设定死锁：不再只锁主角，强制锁定所有出场角色的代词与核心设定。
# 2. 引入“绝对红线 (Absolute Red Lines)”：代词错乱、凭空刷人、AI套话泛滥直接重扣。
# 3. 反“剧情鬼打墙”：严厉审查章节间的套路化重复（如连续5章都是“发现物品-被追-逃跑”）。
# 4. 题材自适应优化：重构权重归一化算法，确保多维度叠加时数学严谨。
# ==============================================================================

import json
import re
from typing import Dict, Any, List, Optional

# ==============================================================================
# 1. 绝对红线定义 (Absolute Red Lines - 凌驾于所有维度之上的铁律)
# ==============================================================================
# ==============================================================================
# 1. 绝对红线定义 (Absolute Red Lines - 凌驾于所有维度之上的铁律)
# ==============================================================================
# 扣分梯队：致命级(-30) > 严重级(-20) > 恶劣级(-15)
ABSOLUTE_RED_LINES = [
    # 🚨 致命级 (直接导致读者弃书的毒点，扣 30 分)
    {
        "id": "pronoun_schizophrenia",
        "name": "代词精神分裂",
        "description": "同一角色的代词（他/她/它）在前后文出现混用、漂移，或不符合其生理性别设定。",
        "penalty": -30
    },
    {
        "id": "phantom_characters",
        "name": "凭空刷出角色/队友",
        "description": "前文毫无铺垫，突然冒出‘身边的人’、‘同伴’、‘他忽略了身旁的低语’等幻觉角色。",
        "penalty": -30
    },
    {
        "id": "pov_violation",
        "name": "视角严重越界 (POV混乱)",
        "description": "在主角限知视角下，突然写出了反派/配角内心的想法，或者描写了主角视线死角（如背后、远处密室）正在发生的事。",
        "penalty": -30
    },

    # ⚠️ 严重级 (严重影响阅读体验的 AI 通病，扣 20 分)
    {
        "id": "plot_groundhog_day",
        "name": "剧情鬼打墙 (同质化重复)",
        "description": "本章的核心事件、场景转换、危机应对与上一章几乎一模一样（如连续多次‘发现发光物->被怪物追->跑酷’）。",
        "penalty": -20
    },
    {
        "id": "severe_ai_cliches",
        "name": "严重 AI 套路与机械味",
        "description": "密集出现 AI 标志性句式（如‘不是A，而是B’、‘带着一种...’）、滥用程度副词（极度/纯粹/瞬间/猛然）、或出现明显的翻译腔。",
        "penalty": -20
    },
    {
        "id": "spatial_physics_collapse",
        "name": "空间瞬移与物理法则崩坏",
        "description": "场景空间感混乱（如：上一秒在室内，下一秒在室外；两人隔着十米却突然抓住了对方的手；无视重力或物理惯性）。",
        "penalty": -20
    },

    # ❌ 恶劣级 (破坏网文节奏和沉浸感的毛病，扣 15 分)
    {
        "id": "ai_forced_summary",
        "name": "AI 式强行升华与总结陈词",
        "description": "在段落末尾或章节末尾，AI 习惯性地添加毫无必要的哲理总结、抒情升华、或‘他知道，这只是一个开始/未来的路还很长’等废话套话。",
        "penalty": -15
    },
    {
        "id": "emotion_mutation",
        "name": "情绪突变与无铺垫发情/暴怒",
        "description": "角色在没有受到足够外部刺激的情况下，突然大笑、大哭、暴怒，或者突然对配角产生强烈的爱意/恨意，缺乏心理过渡。",
        "penalty": -15
    },
    {
        "id": "redundant_padding",
        "name": "同义反复与废话水字数",
        "description": "一句话换着法子说三遍，或者用大量华丽但空洞的辞藻堆砌一个毫无信息量的动作，严重拖慢叙事节奏。",
        "penalty": -15
    },

    {
        "id": "reader_fatal_flaw",
        "name": "读者弃书级毒点 (主观体验崩盘)",
        "description": "以读者视角阅读时，产生强烈的‘弃书冲动’。包括但不限于：主角极度憋屈/圣母/降智导致读者愤怒；剧情极度无聊/水字数导致读者想跳过；毒点爆发且未给出情绪补偿。",
        "penalty": -20  # 触发直接扣 20 分
    }

]


# ==============================================================================
# 2. 基础打分维度 (Base Dimensions - 基准权重与通用检查点)
# ==============================================================================
# ==============================================================================
# 2. 基础打分维度 (Base Dimensions - 基准权重与通用检查点)
# ==============================================================================
# 总计 10 个维度，基准权重总和为 1.0，系统会自动进行题材修正和归一化。
BASE_DIMENSIONS = [
    {
        "id": "character_consistency",
        "name": "全角色一致性与代词死锁",
        "base_weight": 0.15,
        "description": "审查所有出场角色的行为逻辑、性格是否符合设定，以及代词是否绝对锁定。",
        "checkpoints": [
            "主角的决策是否符合其核心动机与隐藏创伤？",
            "配角的言行是否符合其身份，有无沦为降智工具人？",
            "【核心】所有角色的代词（他/她）是否在首次出场后保持绝对一致，无一错漏？"
        ],
        "deduction_triggers": ["主角性格 OOC (脱离人设)", "配角毫无逻辑地降智", "代词使用犹豫或前后矛盾"],
        "bonus_triggers": ["通过极具张力的对话和潜台词展现角色性格，而非旁白告知"]
    },
    {
        "id": "plot_pacing_and_structure",
        "name": "剧情推动与结构张力",
        "base_weight": 0.15,
        "description": "评估剧情是否有效推进，节奏是否张弛有度，是否打破了套路。",
        "checkpoints": [
            "本章是否解决了悬念或抛出了新冲突？",
            "信息密度是否合理？有无水字数？",
            "剧情发展是否打破了读者的常规预期（反套路）？"
        ],
        "deduction_triggers": ["大段复制前文设定水字数", "剧情原地踏步", "强行制造巧合推动剧情"],
        "bonus_triggers": ["意料之外情理之中的剧情反转", "极其精妙的‘欲扬先抑’情绪拉扯"]
    },
    {
        "id": "prose_and_immersion",
        "name": "文笔、画面感与沉浸感",
        "base_weight": 0.12,
        "description": "评估文字的感染力、感官调动、修辞运用以及去 AI 味程度。",
        "checkpoints": [
            "是否使用了丰富的感官描写（视觉、听觉、嗅觉、触觉）？",
            "动作描写是否连贯且具有张力？",
            "是否做到了 'Show, don't tell'（展示而非告知）？"
        ],
        "deduction_triggers": ["干瘪的流水账叙述", "成语或修辞滥用导致语意不通", "强行煽情"],
        "bonus_triggers": ["极具电影画面感的长镜头描写", "使用了极具个人风格的独特比喻"]
    },
    {
        "id": "worldbuilding_and_lore",
        "name": "世界观、设定与时空因果",
        "base_weight": 0.10,
        "description": "评估设定一致性、物理/魔法规则、分层禁忌以及时空连续性。",
        "checkpoints": [
            "力量体系/科技树是否崩坏？专有名词是否前后一致？",
            "场景的物理状态是否与历史记录一致？（如毁坏的桥不能复原）",
            "【核心】是否严格遵守了【世界观分层禁忌】？有无提前剧透‘里层秘密’？"
        ],
        "deduction_triggers": [
            "战力崩坏/数值膨胀", "场景状态前后矛盾",
            "【重扣】像百科全书一样大段旁白解说设定（Info-dumping）",
            "【重扣】在剧情未触及时，强行抛出核心秘密（提前剧透）"
        ],
        "bonus_triggers": ["巧妙利用世界规则的漏洞破局", "将设定完美融入剧情冲突"]
    },
    {
        "id": "commercial_hooks",
        "name": "商业性、网文爽点与悬念",
        "base_weight": 0.10,
        "description": "评估期待感建立、情绪拉扯、金手指运用及章末悬念（钩子）。",
        "checkpoints": [
            "是否有压抑与释放（打脸/收获）的情绪循环？",
            "章末是否留有强有力的“钩子（Hook）”？"
        ],
        "deduction_triggers": ["主角极度憋屈且章末未给希望（毒点）", "平铺直叙毫无悬念"],
        "bonus_triggers": ["完美的网文断章艺术", "金手指/外挂的使用带来了强烈的爽感"]
    },
    {
        "id": "dialogue_and_interaction",
        "name": "对话质量与互动张力",
        "base_weight": 0.10,
        "description": "评估对话是否自然、符合身份，有无播音腔或全员理智怪。",
        "checkpoints": [
            "不同角色的说话语气、口癖是否有明显区分度？",
            "对话是否充满潜台词，角色通过‘不说什么’来表达情绪？"
        ],
        "deduction_triggers": ["大段说教式对话", "全员播音腔/书面语", "对话沦为单纯的剧情解说工具"],
        "bonus_triggers": ["极具张力、暗流涌动的双人对话博弈"]
    },
    {
        "id": "anti_cliche_execution",
        "name": "反套路与去 AI 味执行",
        "base_weight": 0.08,
        "description": "严厉审查文本是否陷入了 AI 生成的陈词滥调、俗套剧情和机械化表达。",
        "checkpoints": [
            "是否大量使用了 AI 高频词汇（如：不禁、眼眸中闪过、嘴角勾起、倒吸一口凉气、深邃）？",
            "是否避免了‘他深吸了一口气，平复了心情’这种机械过渡？"
        ],
        "deduction_triggers": [
            "单段内出现 2 个以上 AI 高频词",
            "使用‘据说’、‘传说中’等词汇进行大段设定解说"
        ],
        "bonus_triggers": [
            "剧情走向完全打破 AI 常见的套路预测",
            "行文干练，多用动词和名词，少用无意义的形容词和副词"
        ]
    },
    {
        "id": "ai_translation_and_broadcast_tone",
        "name": "AI 翻译腔、播音腔与机械情绪",
        "base_weight": 0.08,
        "description": "专门针对大语言模型生成文本时特有的‘翻译腔’、‘播音腔’、机械的情绪描写进行精准打击。",
        "checkpoints": [
            "是否存在明显的‘翻译腔’（如：过长且结构复杂的定语、滥用‘关于/对于’、被动语态泛滥）？",
            "是否存在‘播音腔’（如：角色在日常对话或危急时刻，使用过于正式、书面化、毫无口语瑕疵的词汇）？",
            "是否滥用了 AI 高频转折/强调词（如：然而、事实上、不可否认、毫无疑问）？"
        ],
        "deduction_triggers": [
            "出现明显的翻译腔句式或西式长句",
            "角色对话脱离日常口语，像在朗读课文或发表演讲",
            "单段内出现 2 个以上‘然而/事实上/毫无疑问’等 AI 连接词"
        ],
        "bonus_triggers": [
            "对话充满口语化的瑕疵（如结巴、打断、省略语），极具真实生活气息"
        ]
    },
    {
        "id": "spatial_and_pov_logic",
        "name": "空间连贯性与视角限制",
        "base_weight": 0.06,
        "description": "审查场景的空间物理逻辑是否严密，以及叙事视角（POV）是否严格遵守限制。",
        "checkpoints": [
            "角色的移动、物品的位置是否符合物理空间逻辑？（有无瞬移或穿模）",
            "如果采用主角限知视角，是否严格屏蔽了主角视线死角（背后、远处）的信息？",
            "是否避免了随意切入其他配角/反派的内心独白（除非明确切换了POV）？"
        ],
        "deduction_triggers": [
            "空间位置前后矛盾（如：刚锁上的门突然开了，隔着墙看到对方表情）",
            "【重扣】视角越界：在主角视角下，直接描写了反派心里的阴谋或背后的动作"
        ],
        "bonus_triggers": [
            "巧妙利用视角的局限性（如主角看不到的盲区）来制造悬疑感和信息差"
        ]
    },
    {
        "id": "emotional_transition",
        "name": "情感过渡与心理逻辑",
        "base_weight": 0.06,
        "description": "审查角色的情绪变化是否有合理的外部刺激和内部心理铺垫，拒绝突兀的情绪爆发。",
        "checkpoints": [
            "角色从一种情绪切换到另一种情绪（如从冷静到暴怒，从警惕到放松）是否有合理的触发事件？",
            "心理活动是否自然流露，而非生硬地贴标签（如直接写‘他感到非常悲伤’）？"
        ],
        "deduction_triggers": [
            "【重扣】情绪突变：没有受到刺激突然大笑、大哭、暴怒或发情",
            "情绪转换缺乏过渡，像开关一样瞬间切换"
        ],
        "bonus_triggers": [
            "通过细微的生理反应（如呼吸频率、肌肉紧绷度）和潜意识动作来展现复杂的情绪渐变"
        ]
    }
]


# ==============================================================================
# 3. 题材特征修正器 (Genre Modifiers)
# ==============================================================================
GENRE_MODIFIERS = {
    "xianxia_fantasy": {
        "keywords": ["修仙", "玄幻", "仙侠", "修真", "爽文", "系统", "升级"],
        "weight_deltas": {
            "commercial_hooks": +0.10,
            "worldbuilding_and_lore": +0.05,
            "prose_and_immersion": -0.05,
            "dialogue_and_interaction": -0.05
        },
        "extra_checkpoints": {
            "commercial_hooks": ["升级/收获的过程是否清晰且令人满足？"],
            "worldbuilding_and_lore": ["境界压制是否绝对有效？功法/法宝品阶是否严谨？"]
        }
    },
    "horror_mystery": {
        "keywords": ["克苏鲁", "悬疑", "恐怖", "惊悚", "规则怪谈", "诡异"],
        "weight_deltas": {
            "prose_and_immersion": +0.15,
            "plot_pacing_and_structure": +0.05,
            "commercial_hooks": -0.15,
            "dialogue_and_interaction": -0.05
        },
        "extra_checkpoints": {
            "prose_and_immersion": ["是否成功营造了‘未知’与‘不可名状’的恐惧感？"],
            "plot_pacing_and_structure": ["线索抛出是否克制？是否避免了过早揭示恐怖源头？"]
        }
    },
    "scifi_cyber": {
        "keywords": ["科幻", "赛博朋克", "星际", "机甲", "废土", "末日"],
        "weight_deltas": {
            "worldbuilding_and_lore": +0.15,
            "prose_and_immersion": +0.05,
            "commercial_hooks": -0.10,
            "dialogue_and_interaction": -0.10
        },
        "extra_checkpoints": {
            "worldbuilding_and_lore": ["科技设定/义体改造/物理法则是否自洽？"],
            "prose_and_immersion": ["对机械、霓虹、废土等视觉奇观的描写是否具有冲击力？"]
        }
    },
    "romance_daily": {
        "keywords": ["恋爱", "日常", "言情", "青春", "轻小说"],
        "weight_deltas": {
            "dialogue_and_interaction": +0.20,
            "prose_and_immersion": +0.10,
            "worldbuilding_and_lore": -0.15,
            "commercial_hooks": -0.15
        },
        "extra_checkpoints": {
            "dialogue_and_interaction": ["角色间的互动是否充满张力（CP感）？微表情是否细腻？"],
            "prose_and_immersion": ["情感推进是否自然？是否避免了工业糖精？"]
        }
    }
}

# ==============================================================================
# 4. 三维动态维度生成引擎 (Dynamic Dimension Generator V2.0)
# ==============================================================================
def get_dynamic_dimensions(
    global_genre: str, 
    volume_info: Optional[Dict] = None, 
    chapter_info: Optional[Dict] = None,
    narrative_state: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """
    三维动态计算打分维度：全局题材 + 卷标题材 + 章节节奏状态。
    完美解决“前期低武后期修仙”、“日常章与高潮战权重不同”的问题。
    """
    import copy
    final_dims = copy.deepcopy(BASE_DIMENSIONS)
    
    # 1. 收集所有命中的题材标签 (全局 + 当前卷)
    all_tags = []
    if global_genre:
        all_tags.extend([t.strip() for t in re.split(r'[,，+/\s]+', global_genre.lower()) if t.strip()])
    if volume_info and isinstance(volume_info, dict):
        vol_tags = volume_info.get("genre_tags", [])
        if isinstance(vol_tags, str): vol_tags = [vol_tags]
        all_tags.extend([t.strip().lower() for t in vol_tags if t.strip()])
        
    # 2. 匹配题材修正器 (叠加修正)
    matched_modifiers = []
    for mod_id, mod_data in GENRE_MODIFIERS.items():
        for kw in mod_data["keywords"]:
            if any(kw in tag for tag in all_tags):
                matched_modifiers.append(mod_data)
                break

    # 3. 应用题材权重修正
    if matched_modifiers:
        for dim in final_dims:
            dim_id = dim["id"]
            delta = sum(mod["weight_deltas"].get(dim_id, 0.0) for mod in matched_modifiers)
            dim["weight"] = max(0.05, min(0.50, dim["base_weight"] + delta))
            for mod in matched_modifiers:
                dim["checkpoints"].extend(mod.get("extra_checkpoints", {}).get(dim_id, []))
    else:
        for dim in final_dims:
            dim["weight"] = dim["base_weight"]

    # 4. 🌟 核心创新：根据“章节节奏/功能 (Pacing State)”进行微调
    pacing_state = "normal"
    if chapter_info and isinstance(chapter_info, dict):
        pacing_state = chapter_info.get("pacing_state", "normal").lower()
        
    if pacing_state in ["climax", "battle", "high_tension", "高潮", "决战"]:
        # 高潮战：提高画面感、爽点、剧情推动；降低日常对话、设定解说
        for dim in final_dims:
            if dim["id"] in ["prose_and_immersion", "commercial_hooks", "plot_pacing_and_structure"]:
                dim["weight"] += 0.05
            elif dim["id"] in ["dialogue_and_interaction", "worldbuilding_and_lore"]:
                dim["weight"] -= 0.05
                
    elif pacing_state in ["daily", "transition", "romance", "日常", "过渡", "感情"]:
        # 日常/过渡章：提高角色互动、情感沉浸；降低剧情推动、爽点
        for dim in final_dims:
            if dim["id"] in ["dialogue_and_interaction", "character_consistency", "prose_and_immersion"]:
                dim["weight"] += 0.05
            elif dim["id"] in ["plot_pacing_and_structure", "commercial_hooks"]:
                dim["weight"] -= 0.05

    # 5. 权重归一化 (确保总和严格等于 1.0)
    total_weight = sum(d["weight"] for d in final_dims)
    if total_weight > 0 and len(final_dims) > 0:
        weight_error = 1.0 - total_weight
        final_dims[-1]["weight"] += weight_error
        for dim in final_dims:
            dim["weight"] = max(0.05, min(0.50, dim["weight"]))
        total_weight = sum(d["weight"] for d in final_dims)
        if total_weight > 0:
            for dim in final_dims:
                dim["weight"] = dim["weight"] / total_weight
                
    return final_dims


# ==============================================================================
# 5. Prompt 渲染引擎 (动态裁剪 & 状态注入版)
# ==============================================================================
def render_scoring_prompt(
    content: str, 
    chapter_num: int, 
    task_plan: Dict[str, Any], 
    project_dir: str = "",
    custom_rules: Optional[List[Dict]] = None,
    volume_info: Optional[Dict] = None,      # 🌟 新增：当前卷信息
    chapter_info: Optional[Dict] = None,     # 🌟 新增：当前章大纲信息
    narrative_state: Optional[Dict] = None   # 🌟 新增：当前叙事状态(防设定反转误判)
) -> str:
    """将动态维度、绝对红线与当前剧情状态渲染为严苛的 Prompt。"""
    import os, json, glob, re
    
    global_genre = (task_plan or {}).get("genre", "通用")
    main_char = (task_plan or {}).get("main_character", "主角")
    
    # 🌟 使用三维动态引擎获取维度
    dimensions = custom_rules if custom_rules else get_dynamic_dimensions(
        global_genre, volume_info, chapter_info, narrative_state
    )
    
    # ==================================================================
    # 🌟 核心创新：动态加载“审查基准附件” (带 Token 裁剪保护)
    # ==================================================================
    review_baseline = []
    
    # 1. 注入当前卷的题材与核心关注点 (让 LLM 知道当前处于什么阶段)
    if volume_info:
        vol_text = f"【当前阶段 (第 {volume_info.get('vol', '?')} 卷)】\n"
        vol_text += f"阶段题材: {', '.join(volume_info.get('genre_tags', [global_genre]))}\n"
        vol_text += f"本卷核心目标: {volume_info.get('core_goal', '推进主线')}\n"
        review_baseline.append(vol_text)

    # 2. 注入世界观揭露进度 (防止“合理反转”被误判为 OOC)
    if narrative_state and isinstance(narrative_state, dict):
        lore_level = narrative_state.get("current_lore_level", "表层")
        known_secrets = narrative_state.get("protagonist_known_secrets", [])
        if known_secrets:
            lore_text = f"【主角当前认知边界 (防误判铁律)】\n"
            lore_text += f"主角目前只知道以下秘密，若本章揭示新秘密，属于合理剧情推进，绝非设定冲突：\n"
            lore_text += "\n".join([f"  - {s}" for s in known_secrets[:3]]) + "\n"
            review_baseline.append(lore_text)

    # 3. 注入当前战力基准线 (防止“合理升级”被误判为战力崩坏)
    if narrative_state and isinstance(narrative_state, dict):
        power_level = narrative_state.get("current_power_level", "凡人")
        power_text = f"【当前战力基准线】\n主角当前战力表现为：{power_level}。请以此为基准判断本章战斗是否崩坏，不要用前期的标准衡量后期！\n"
        review_baseline.append(power_text)

    # 4. 动态加载 03_设定 (带长度保护)
    if project_dir and os.path.exists(project_dir):
        lore_dir = os.path.join(project_dir, "03_设定")
        if not os.path.exists(lore_dir):
            _pid = task_plan.get("project_id") if task_plan else None
            if _pid:
                for m in glob.glob(os.path.join("novel_projects", f"*_{_pid}")):
                    candidate = os.path.join(m, "03_设定")
                    if os.path.exists(candidate): lore_dir = candidate; break

        if os.path.exists(lore_dir):
            # 读取主角核心内核 (限制长度)
            char_dir = os.path.join(lore_dir, "角色卡")
            if os.path.exists(char_dir):
                main_card_path = os.path.join(char_dir, f"角色卡_{main_char}.md")
                if os.path.exists(main_card_path):
                    try:
                        with open(main_card_path, 'r', encoding='utf-8') as f: card_content = f.read()
                        motivation_match = re.search(r'## 核心动机\n([\s\S]*?)(?=\n##|\Z)', card_content)
                        if motivation_match:
                            review_baseline.append(f"【主角 {main_char} 核心动机】\n{motivation_match.group(1).strip()[:200]}\n")
                    except Exception: pass

    baseline_str = "\n".join(review_baseline) if review_baseline else "（无特殊阶段状态，基于通用逻辑审查）"

    # 构建维度检查清单
    dim_prompts = []
    for dim in dimensions:
        checkpoints_str = "\n".join([f"  - {cp}" for cp in dim["checkpoints"][:4]]) # 限制检查点数量防 Token 爆炸
        deduc_str = ", ".join(dim["deduction_triggers"][:3])
        dim_prompts.append(f"""
### {dim["name"]} (权重: {int(dim['weight']*100)}%)
**核心检查点**：
{checkpoints_str}
**严重扣分项**：{deduc_str}
""")
    dimensions_text = "\n".join(dim_prompts)

    # 构建绝对红线清单
    red_lines_text = "\n".join([f"🚨 {rl['name']} (扣 {abs(rl['penalty'])} 分)：{rl['description']}" for rl in ABSOLUTE_RED_LINES])

    # 构建 Few-Shot 示例 (加入读者主观体验模块)
    json_example = {
        "red_lines_violations": [
            {"rule_id": "pronoun_schizophrenia", "character": "主角", "issue": "代词混用", "quote": "他/她", "penalty": -30}
        ],
        "dimension_scores": {
            "character_consistency": {"score": 70, "deductions": [{"reason": "代词错乱", "quote": "...", "points": -20}], "bonuses": [], "comment": "需统一"},
            "plot_pacing_and_structure": {"score": 85, "deductions": [], "bonuses": [{"reason": "反转精妙", "points": 5}], "comment": "节奏紧凑"}
        },
        # 🌟 核心新增：读者主观体验模块 (不参与基础权重计算，但影响总分和教鞭)
        "reader_experience": {
            "immersion_score": 6,        # 代入感 (1-10分)
            "excitement_score": 4,       # 期待感/爽感 (1-10分)
            "would_read_next": False,    # 作为读者，是否想看下一章？(boolean)
            "reader_complaints": [       # 读者的真实吐槽/毒点 (用于教鞭重写)
                "主角面对反派的挑衅一直退让，看着太憋屈了，想弃书！",
                "中间那段环境描写太长了，毫无信息量，我想直接跳过。"
            ],
            "reader_praises": [          # 读者的爽点/夸赞 (用于教鞭保留)
                "结尾那个悬念留得很好，如果能改掉前面的憋屈感就完美了。"
            ]
        },
        "overall_score": 55, "passed": False,
        "core_issues_summary": "代词错乱，且主角行为让读者感到极度憋屈。", 
        "modification_suggestions": "1. 统一代词。2. 必须让主角在章末做出反击，释放读者的憋屈情绪！"
    }

    # 🌟 核心修复：将 reader_prompt_block 移到 f-string 外部定义，防止三引号嵌套导致语法崩溃
    reader_prompt_block = """
==================================================
📖 【附加任务：挑剔读者主观体验评价】 (不计入基础权重，但决定生死！)
==================================================
现在，请你忘记“主编”的身份，化身为一个**花钱订阅、极其挑剔的网文读者**。
请凭借你的第一直觉，在 `reader_experience` 字段中给出主观评价：
1. 代入感和爽感打分（1-10分）。
2. 最关键的：`would_read_next`（是否想看下一章）。
3. `reader_complaints`：用读者的口吻，毒舌地吐槽让你觉得“无聊、憋屈、想跳过、毒点”的地方（这将直接作为重写指令！）。
4. `reader_praises`：让你觉得“爽、惊艳、想催更”的地方。
⚠️ 警告：如果 `would_read_next` 为 false，或 `immersion_score` < 5，系统将自动触发【读者弃书级毒点】红线，重扣 20 分！
"""

    # 构建最终的 Prompt (在内部通过 {reader_prompt_block} 引用)
    prompt = f"""你是一位极其严苛的网文主编。请对第 {chapter_num} 章进行【多维度深度质检】。
主角：【{main_char}】

==================================================
📚 【当前剧情阶段与审查基准】 (必须结合当前阶段打分，切忌刻舟求剑！)
{baseline_str}
==================================================

🚨🚨🚨 【绝对红线 (触碰必重扣)】 🚨🚨🚨
{red_lines_text}

🔒 【全角色代词死锁铁律】：主角及所有配角的代词（他/她）必须绝对锁定，严禁混用！

📊 动态打分规则 (总分 100 分，权重已根据当前剧情阶段自动调整)：
{dimensions_text}

{reader_prompt_block}

==================================================
📝 待审查正文 (截取前 3000 字)：
{content[:3000]}
==================================================

【输出指令】：只输出合法 JSON。触碰红线必须列入 `red_lines_violations`。`overall_score` 需减去红线扣分。
【JSON 示例】：
{json.dumps(json_example, ensure_ascii=False, indent=2)}
直接输出纯 JSON："""

    return prompt

# ==============================================================================
# 6. 结果解析与归一化引擎
# ==============================================================================
def parse_scoring_result(raw_llm_output: str, dimensions: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """工业级 JSON 解析器（防 LLM 幻觉捏造维度，并执行红线惩罚）。"""
    if not dimensions or not isinstance(dimensions, list) or len(dimensions) == 0:
        dimensions = BASE_DIMENSIONS
    else:
        valid_dims = [dim for dim in dimensions if isinstance(dim, dict) and "id" in dim]
        dimensions = valid_dims if valid_dims else BASE_DIMENSIONS
    
    valid_dim_ids = {d["id"] for d in dimensions}
    red_line_ids = {rl["id"]: rl["penalty"] for rl in ABSOLUTE_RED_LINES}
    
    default_result = {
        "red_lines_violations": [], "dimension_scores": {}, 
        "overall_score": 75, "passed": True, "score": 75,
        "core_issues_summary": "解析失败，默认放行", "modification_suggestions": "无",
        "issues": [], "suggestions": [], "state_changes": {}, "foreshadowing": [], "new_entities": []
    }

    if not raw_llm_output or not isinstance(raw_llm_output, str): return default_result

    text = re.sub(r'^```json\s*', '', raw_llm_output.strip(), flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    
    parsed_data = None
    try: parsed_data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try: parsed_data = json.loads(match.group())
            except: pass

    if not parsed_data or not isinstance(parsed_data, dict): return default_result

    # 1. 处理绝对红线违规
    red_line_penalty = 0
    violations = parsed_data.get("red_lines_violations", [])
    if isinstance(violations, list):
        for v in violations:
            if isinstance(v, dict):
                rule_id = v.get("rule_id", "")
                if rule_id in red_line_ids:
                    penalty = red_line_ids[rule_id]
                    red_line_penalty += penalty
                    issue_desc = f"🚨[红线:{v.get('character','')}] {v.get('issue', rule_id)}"
                    parsed_data.setdefault("issues", []).append(issue_desc)
                    
    # 2. 处理维度打分
    dim_scores = parsed_data.get("dimension_scores", {})
    if not isinstance(dim_scores, dict): dim_scores = {}
    
    filtered_dim_scores = {k: v for k, v in dim_scores.items() if k in valid_dim_ids}
    
    total_weighted_score = 0
    total_weight = 0
    
    for dim in dimensions:
        dim_id = dim["id"]
        weight = dim.get("weight", dim.get("base_weight", 0.15))
        
        if dim_id not in filtered_dim_scores or not isinstance(filtered_dim_scores[dim_id], dict):
            filtered_dim_scores[dim_id] = {"score": 80, "deductions": [], "bonuses": [], "comment": "未评估"}
            
        dim_data = filtered_dim_scores[dim_id]
        score = max(0, min(100, int(dim_data.get("score", 80))))
        dim_data["score"] = score
        
        total_weighted_score += score * weight
        total_weight += weight
        
        for d in dim_data.get("deductions", []):
            if isinstance(d, dict) and d.get("reason"):
                parsed_data.setdefault("issues", []).append(f"[{dim['name']}] {d['reason']}")
                
    base_score = int(total_weighted_score / total_weight) if total_weight > 0 else parsed_data.get("overall_score", 80)
    
    # ==================================================================
    # 🌟 核心创新：处理“读者主观体验”，对接教鞭程序
    # ==================================================================
    reader_exp_penalty = 0
    reader_exp = parsed_data.get("reader_experience", {})
    
    if isinstance(reader_exp, dict):
        immersion = reader_exp.get("immersion_score", 8)
        would_read = reader_exp.get("would_read_next", True)
        complaints = reader_exp.get("reader_complaints", [])
        praises = reader_exp.get("reader_praises", [])
        
        # 1. 触发“读者弃书”红线扣分
        if not would_read or (isinstance(immersion, (int, float)) and immersion < 5):
            reader_exp_penalty = -20  # 对应 reader_fatal_flaw 的 penalty
            parsed_data.setdefault("issues", []).append("🚨[红线:读者体验] 读者表示不想看下一章/代入感极差，存在弃书级毒点！")
            
        # 2. 将“读者吐槽”转化为教鞭能识别的 issues (重写指令)
        if isinstance(complaints, list) and complaints:
            for comp in complaints[:2]:  # 最多取前2条，防止教鞭 Prompt 过长
                if isinstance(comp, str) and comp.strip():
                    parsed_data.setdefault("issues", []).append(f"[读者吐槽] {comp.strip()}")
                    
        # 3. 将“读者夸赞”转化为教鞭的保留建议
        if isinstance(praises, list) and praises:
            for praise in praises[:2]:
                if isinstance(praise, str) and praise.strip():
                    parsed_data.setdefault("suggestions", []).append(f"[读者爽点-请保留] {praise.strip()}")

    # 计算最终分数 (基础分 + 绝对红线惩罚 + 读者体验惩罚)
    final_score = max(0, base_score + red_line_penalty + reader_exp_penalty)
   

    
    parsed_data["overall_score"] = final_score
    parsed_data["score"] = final_score
    parsed_data["passed"] = final_score >= 60
    parsed_data["dimension_scores"] = filtered_dim_scores
    
    if parsed_data.get("modification_suggestions"):
        parsed_data.setdefault("suggestions", []).append(parsed_data["modification_suggestions"])
    
    return parsed_data
