"""
导出评论数据用于人工标注 / 模型训练（全量版，每类最多25条）
按视频分组，分别取文本和图片评论中点赞量最高的前25条（不限制前50%）
生成 CSV 文件，列：content, image_path（绝对路径，多图以逗号分隔）
"""

import os
import sys
import csv
import django

# ===== 动态添加项目根目录到 sys.path =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Bili_web.settings')
django.setup()

from django.conf import settings
from django.db import models
from app_bili.models import CommentInfo

# ===== 配置输出目录 =====
OUTPUT_DIR = os.path.join(settings.BASE_DIR, 'llm_label', 'data')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===== 统计变量 =====
total_videos = 0
total_text_comments = 0
total_image_comments = 0
total_exported_files = 0

# ===== 获取所有视频 bvid =====
all_bvids = CommentInfo.objects.values_list('bvid', flat=True).distinct()

for bvid in all_bvids:
    if not bvid:
        continue

    # 分离文本评论和图片评论
    text_comments = CommentInfo.objects.filter(
        bvid=bvid
    ).filter(
        models.Q(local_image_paths='') | models.Q(local_image_paths__isnull=True)
    )

    image_comments = CommentInfo.objects.filter(
        bvid=bvid
    ).exclude(
        models.Q(local_image_paths='') | models.Q(local_image_paths__isnull=True)
    )

    # 定义筛选函数：直接取点赞量最高的前 max_limit 条
    def select_top_comments(queryset, max_limit=25):
        if not queryset.exists():
            return []
        # 按点赞量降序，取前 max_limit 条
        return list(queryset.order_by('-like_count')[:max_limit])

    selected_text = select_top_comments(text_comments, max_limit=25)
    selected_image = select_top_comments(image_comments, max_limit=25)

    # 如果没有数据则跳过
    if not selected_text and not selected_image:
        continue

    # 生成 CSV 行
    rows = []
    for comment in selected_text:
        rows.append({
            'content': comment.content,
            'image_path': ''
        })

    for comment in selected_image:
        raw_paths = comment.local_image_paths
        if raw_paths:
            paths = [p.strip() for p in raw_paths.split('|') if p.strip()]
            abs_paths = []
            for p in paths:
                abs_path = os.path.join(settings.MEDIA_ROOT, p)
                abs_paths.append(os.path.normpath(abs_path))
            final_image_path = ','.join(abs_paths)
        else:
            final_image_path = ''

        rows.append({
            'content': comment.content,
            'image_path': final_image_path
        })

    # 写入 CSV
    csv_filename = f"{bvid}.csv"
    csv_path = os.path.join(OUTPUT_DIR, csv_filename)

    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['content', 'image_path'])
        writer.writeheader()
        writer.writerows(rows)

    # 更新统计
    total_videos += 1
    total_text_comments += len(selected_text)
    total_image_comments += len(selected_image)
    total_exported_files += 1

    print(f"视频 {bvid} 导出完成，文本 {len(selected_text)} 条，图片 {len(selected_image)} 条 -> {csv_filename}")

# ===== 最终统计输出 =====
print("\n" + "=" * 60)
print("导出任务完成统计")
print("=" * 60)
print(f"处理视频总数：{total_videos} 个")
print(f"导出文本评论总数：{total_text_comments} 条")
print(f"导出图片评论总数：{total_image_comments} 条")
print(f"生成 CSV 文件数：{total_exported_files} 个")
print(f"输出目录：{OUTPUT_DIR}")
print("=" * 60)