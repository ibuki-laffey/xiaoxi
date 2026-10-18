import os
import shutil

print("="*50)
print("🔥 步骤 1：物理超度所有 __pycache__ 缓存...")
print("="*50)
# 暴力删除所有 __pycache__ 目录
deleted_count = 0
for root, dirs, files in os.walk('.'):
    for d in dirs:
        if d == '__pycache__':
            target = os.path.join(root, d)
            shutil.rmtree(target)
            print(f"   🗑️ 已删除: {target}")
            deleted_count += 1
print(f"✅ 共清理了 {deleted_count} 个缓存目录！\n")

print("="*50)
print("🔧 步骤 2：强制覆写 chapter_generator.py...")
print("="*50)
file_path = os.path.join('core', 'chapter_generator.py')

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 强制替换类型提示（加双引号）
if '-> ChapterGenerator:' in content:
    content = content.replace('-> ChapterGenerator:', '-> "ChapterGenerator":')
    print("   ✅ 已将 -> ChapterGenerator: 替换为 -> \"ChapterGenerator\":")

# 2. 强制在文件第一行插入 future 导入
if 'from __future__ import annotations' not in content:
    content = 'from __future__ import annotations\n' + content
    print("   ✅ 已在文件首行插入 from __future__ import annotations")

# 3. 强制修正致命缩进（确保 def 顶格）
# 将带有缩进的 def create_chapter_generator 强制替换为顶格
content = content.replace('    def create_chapter_generator(', 'def create_chapter_generator(')
content = content.replace('\tdef create_chapter_generator(', 'def create_chapter_generator(')
print("   ✅ 已强制确保 def create_chapter_generator 顶格（无缩进）")

# 覆盖保存
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("   💾 文件已强制覆盖保存！\n")

print("="*50)
print("🔍 步骤 3：灵魂拷问（验证文件真实内容）...")
print("="*50)
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到 def create_chapter_generator 所在的行并打印上下文
for i, line in enumerate(lines):
    if 'def create_chapter_generator' in line:
        print(f"   🎯 找到目标函数在第 {i+1} 行：")
        # 打印前后 5 行
        start = max(0, i - 2)
        end = min(len(lines), i + 8)
        for j in range(start, end):
            prefix = ">>>" if j == i else "   "
            print(f"   {prefix} 行 {j+1}: {lines[j].rstrip()}")
        break

print("\n" + "="*50)
print("🚀 修复完成！现在请重新运行您的 full_diagnosis_combined.py 脚本！")
print("="*50)
