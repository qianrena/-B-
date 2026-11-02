"""
从 out_label 目录提取情感分类数据，用于模型微调。
输出格式：content, image_path(相对路径), coarse_sentiment, fine_sentiment
同时统计并打印各情感类别的数量。
"""

import os
import sys
import csv
from pathlib import Path
from collections import Counter

# Django 环境初始化
DJANGO_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # Bili_web 目录
sys.path.append(str(DJANGO_PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Bili_web.settings')

import django
django.setup()
from django.conf import settings

# 配置
OUT_LABEL_DIR = Path(__file__).parent / "out_label"
OUTPUT_DIR = Path(__file__).parent / "finetune_data"
OUTPUT_DIR.mkdir(exist_ok=True)

OUTPUT_CSV = OUTPUT_DIR / "all_labeled_data.csv"
KEEP_COLUMNS = ["content", "image_path", "coarse_sentiment", "fine_sentiment"]


# 路径转换辅助函数
def absolute_to_relative(abs_path: str) -> str:
    if not abs_path:
        return ""
    if abs_path.startswith("file://"):
        abs_path = abs_path[7:]
    try:
        p = Path(abs_path)
        if p.is_absolute():
            media_root = Path(settings.MEDIA_ROOT)
            try:
                rel = p.relative_to(media_root)
                return str(rel).replace('\\', '/')
            except ValueError:
                return abs_path
        else:
            return abs_path
    except Exception:
        return abs_path


def process_file(csv_path: Path, writer: csv.DictWriter, coarse_counter: Counter, fine_counter: Counter):
    """处理单个 CSV 文件，提取所需字段并写入输出，同时统计类别计数"""
    print(f"处理: {csv_path.name}")
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        missing = [col for col in KEEP_COLUMNS if col not in reader.fieldnames]
        if missing:
            print(f"  警告: 缺少列 {missing}，跳过该文件")
            return
        for row in reader:
            new_row = {col: row[col] for col in KEEP_COLUMNS}
            new_row['image_path'] = absolute_to_relative(new_row['image_path'])
            writer.writerow(new_row)

            # 统计
            coarse_sent = new_row.get('coarse_sentiment', '').strip()
            fine_sent = new_row.get('fine_sentiment', '').strip()
            if coarse_sent:
                coarse_counter[coarse_sent] += 1
            if fine_sent:
                fine_counter[fine_sent] += 1


def main():
    labeled_files = list(OUT_LABEL_DIR.glob("*_labeled.csv"))
    if not labeled_files:
        print(f"在 {OUT_LABEL_DIR} 中未找到任何 *_labeled.csv 文件")
        return

    print(f"找到 {len(labeled_files)} 个文件")

    coarse_counter = Counter()
    fine_counter = Counter()

    with open(OUTPUT_CSV, 'w', encoding='utf-8-sig', newline='') as f_out:
        writer = csv.DictWriter(f_out, fieldnames=KEEP_COLUMNS)
        writer.writeheader()

        for csv_file in labeled_files:
            process_file(csv_file, writer, coarse_counter, fine_counter)

    print(f"\n合并完成，输出文件: {OUTPUT_CSV}")

    # 打印统计信息
    print("\n" + "=" * 40)
    print("粗粒度情感 (coarse_sentiment) 统计:")
    for label, count in sorted(coarse_counter.items()):
        print(f"  {label}: {count}")

    print("\n细粒度情感 (fine_sentiment) 统计:")
    for label, count in sorted(fine_counter.items()):
        print(f"  {label}: {count}")
    print("=" * 40)


if __name__ == "__main__":
    main()