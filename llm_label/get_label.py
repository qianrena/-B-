"""
基于 DashScope 千问模型的多模态情感分类批量处理脚本
- 自动处理 data 目录下的所有 CSV 文件（文件名需包含 BV 号，如 BV1bFbhzREFv.csv）
- 从 Django 数据库获取对应视频的标题和标签，作为情感分析上下文
- 支持多张图片（本地绝对路径，使用 file:// 协议）
- 输出完整结果、低置信度结果、调用失败结果到 out_label 目录
"""

import os
import sys
import csv
import asyncio
import json
from typing import List, Dict, Any
from pathlib import Path

# ========== 初始化 Django 环境 ==========
# 请根据你的实际项目结构调整 sys.path 和 settings 模块名
DJANGO_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # 假设脚本位于 Bili_web/llm_label/
sys.path.append(str(DJANGO_PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Bili_web.settings')  # 替换为你的 settings 模块名

import django

django.setup()

from app_bili.models import VideoInfo

import dashscope
from dashscope import MultiModalConversation

# ================== 配置 ==================
dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

# 输入输出目录
DATA_DIR = Path(__file__).parent / "data"  # 存放待标注 CSV 的目录
OUT_LABEL_DIR = Path(__file__).parent / "out_label"  # 输出目录
OUT_LABEL_DIR.mkdir(parents=True, exist_ok=True)

# 并发控制
DEFAULT_CONCURRENCY = 10
CONFIDENCE_THRESHOLD = 0.7
DEFAULT_MODEL = "qwen3.5-122b-a10b"


# ================== 视频信息辅助函数 ==================
def get_video_context(bvid: str) -> Dict[str, str]:
    """
    根据 BV 号从数据库获取视频标题和标签
    返回 {'title': str, 'tags': str}，若未查到则返回空字符串
    """
    try:
        video = VideoInfo.objects.get(bvid=bvid)
        tags = video.tags.replace(',', '、') if video.tags else '无'
        return {'title': video.title, 'tags': tags}
    except VideoInfo.DoesNotExist:
        return {'title': '', 'tags': ''}


# ================== 提示词构建 ==================
def build_prompt(text: str, has_image: bool, video_context: Dict[str, str] = None) -> str:
    """构建发送给大模型的提示词，包含视频标题和标签（如有）"""
    video_info = ""
    if video_context and video_context.get('title'):
        video_info = f"\n该评论出自视频：《{video_context['title']}》"
        if video_context.get('tags'):
            video_info += f"  视频标签：{video_context['tags']}"

    return f"""
你是一个专业的社交媒体情感分析专家，擅长理解B站弹幕文化、谐音梗、抽象话的中文网络语境。
请根据以下评论的文字内容{'和所附的图片' if has_image else ''}，综合判断用户表达的情感倾向。
{video_info}
输出必须严格按照 JSON 格式，不要包含任何多余的解释或文字。

评论文本：
{text}

输出格式：
{{
  "coarse_sentiment": "positive" | "neutral" | "negative",
  "fine_sentiment": "happiness" | "anger" | "sadness" | "fear" | "surprise" | "neutral",
  "confidence": 0.0 ~ 1.0,
  "reason": "简短理由（一句话）"
}}

标签定义：
- coarse_sentiment（粗粒度三分类）：
  * positive：积极、赞赏、喜爱、开心、期待等正面情绪。
  * neutral：客观陈述、询问、无明显情绪，或情绪模糊无法判断。
  * negative：消极、愤怒、悲伤、恐惧、厌恶、讽刺、失望等负面情绪。
- fine_sentiment（细粒度六分类）：
  * happiness：喜悦、开心、觉得有趣、好笑、感动、满足。
  * anger：愤怒、生气、不满、指责、暴躁、攻击性言辞。
  * sadness：悲伤、难过、遗憾、失望、沮丧、惋惜。
  * fear：恐惧、害怕、惊吓、担忧、不安、紧张。
  * surprise：惊讶、震惊、意外、不可思议（偏中性或略带积极）。
  * neutral：无明显情绪，或无法归入以上五类。

判断规则：
1. 注意反讽、阴阳怪气、谐音梗（例如"蚌埠住了"="绷不住了"=消极愤怒/无奈；"2333"=大笑=积极喜悦）。
2. 如果评论包含图片，且图片内容与文字情绪冲突，以图片传达的情绪为准。
3. confidence 取值范围 0.0~1.0，表示你对本次判断的把握程度。若评论过于模糊、无上下文，请给出低于 0.7 的分数。
"""


def build_messages(text: str, image_paths: List[str], video_context: Dict[str, str] = None) -> List[Dict[str, Any]]:
    """构建 DashScope 原生 messages 格式，包含图片和文本"""
    content = []
    for img_path in image_paths:
        # 手动构造符合 DashScope 规范的 file:// 格式
        normalized_path = img_path.replace('\\', '/')
        file_url = f"file://{normalized_path}"
        content.append({"image": file_url})
    content.append({"text": build_prompt(text, bool(image_paths), video_context)})
    return [{"role": "user", "content": content}]


def parse_model_response(raw_json: str) -> Dict[str, Any]:
    """解析模型返回的 JSON 字符串，校验必要字段"""
    try:
        result = json.loads(raw_json)
    except json.JSONDecodeError:
        raise ValueError("模型返回非 JSON 格式")

    required = ["coarse_sentiment", "fine_sentiment", "confidence", "reason"]
    for key in required:
        if key not in result:
            result[key] = "neutral" if "sentiment" in key else (0.0 if key == "confidence" else "")
    return result


# ================== API 调用核心 ==================
def classify_single_sync(
        text: str,
        image_paths: List[str],
        video_context: Dict[str, str] = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 3
) -> Dict[str, Any]:
    """同步调用 DashScope 多模态接口"""
    import time

    messages = build_messages(text, image_paths, video_context)

    for attempt in range(max_retries):
        try:
            response = MultiModalConversation.call(
                model=model,
                messages=messages,
                api_key=DASHSCOPE_API_KEY,
                response_format={'type': 'json_object'},
                result_format='message',
            )

            if response.status_code != 200:
                error_code = getattr(response, 'code', 'Unknown')
                error_msg = getattr(response, 'message', 'Unknown error')
                print(
                    f"  [尝试 {attempt + 1}/{max_retries}] 请求失败，状态码: {response.status_code}，错误: {error_code} - {error_msg}")
                if attempt == max_retries - 1:
                    raise RuntimeError(f"API错误 (状态码 {response.status_code}): {error_msg}")
                time.sleep(2 ** attempt)
                continue

            output_text = response.output.choices[0].message.content[0]["text"]
            return parse_model_response(output_text)

        except Exception as e:
            print(f"  [尝试 {attempt + 1}/{max_retries}] 异常: {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)


async def classify_single_async(
        text: str,
        image_paths: List[str],
        video_context: Dict[str, str] = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 3
) -> Dict[str, Any]:
    """异步包装器，在线程池中运行同步调用"""
    return await asyncio.to_thread(
        classify_single_sync, text, image_paths, video_context, model, max_retries
    )


async def batch_classify(
        rows: List[Dict[str, Any]],
        video_context: Dict[str, str],
        concurrency: int = DEFAULT_CONCURRENCY
) -> List[Dict[str, Any]]:
    """批量异步处理，返回带标签的行"""
    semaphore = asyncio.Semaphore(concurrency)

    async def process_one(row):
        async with semaphore:
            text = row.get("content", "")
            img_str = row.get("image_path", "")
            image_paths = []
            if img_str:
                image_paths = [p.strip() for p in img_str.split(",") if p.strip()]
            try:
                result = await classify_single_async(text, image_paths, video_context)
                return {**row, **result}
            except Exception as e:
                print(f"  处理失败，错误: {e}")
                return {
                    **row,
                    "coarse_sentiment": "neutral",
                    "fine_sentiment": "neutral",
                    "confidence": 0.0,
                    "reason": f"处理失败: {str(e)}"
                }

    tasks = [process_one(row) for row in rows]
    return await asyncio.gather(*tasks)


# ================== CSV 处理 ==================
def process_csv_file(csv_path: Path) -> None:
    """处理单个 CSV 文件：读取、分类、保存结果"""
    print(f"\n处理文件: {csv_path.name}")

    # 从文件名提取 BV 号（假设格式为 BVxxxx.csv 或 BVxxxx_xxx.csv）
    bvid = csv_path.stem.split('_')[0]
    video_context = get_video_context(bvid)
    if video_context['title']:
        print(f"  视频信息：{video_context['title'][:30]}...  标签：{video_context['tags']}")
    else:
        print("  警告：数据库中未找到该 BV 号，将仅基于评论内容判断")

    rows = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return

    if not rows:
        print("文件无数据，跳过")
        return

    print(f"共 {len(rows)} 条待处理，并发数 {DEFAULT_CONCURRENCY}...")

    loop = asyncio.get_event_loop()
    labeled_rows = loop.run_until_complete(
        batch_classify(rows, video_context, DEFAULT_CONCURRENCY)
    )

    # 按置信度分类
    high_conf = [r for r in labeled_rows if r.get("confidence", 0) >= CONFIDENCE_THRESHOLD]
    low_conf = [r for r in labeled_rows if 0.0 < r.get("confidence", 0) < CONFIDENCE_THRESHOLD]
    failed = [r for r in labeled_rows if r.get("confidence", 0) == 0.0]

    base_name = csv_path.stem
    out_full = OUT_LABEL_DIR / f"{base_name}_labeled.csv"
    out_low = OUT_LABEL_DIR / f"{base_name}_low_confidence.csv"
    out_failed = OUT_LABEL_DIR / f"{base_name}_failed_requests.csv"

    fieldnames = ["content", "image_path", "coarse_sentiment", "fine_sentiment", "confidence", "reason"]

    with open(out_full, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(labeled_rows)

    if low_conf:
        with open(out_low, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(low_conf)

    if failed:
        with open(out_failed, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(failed)

    print(f"处理完成：全部 {len(labeled_rows)} 条，低置信度 {len(low_conf)} 条，失败请求 {len(failed)} 条")
    print(f"  全部结果保存至: {out_full}")
    if low_conf:
        print(f"  低置信度保存至: {out_low}")
    if failed:
        print(f"  失败请求保存至: {out_failed}")


# ================== 主函数 ==================
def main():
    """主函数：自动处理 data 目录下的所有 CSV 文件"""
    if not DASHSCOPE_API_KEY:
        print("请先设置环境变量 DASHSCOPE_API_KEY")
        sys.exit(1)

    # 确保 data 目录存在
    if not DATA_DIR.exists():
        print(f"错误：data 目录不存在: {DATA_DIR}")
        sys.exit(1)

    # 定义要排除的 BV 号列表
    exclude_bvids = ['BV14iXpBrEcD', 'BV152DeB2EGK', 'BV1bFbhzREFv', 'BV1BFX5BPEx5', 'BV1CR9WB3EFH', 'BV1d49WB8EXP',
                     'BV1eRXrB3EQ7', 'BV1GsHfzoEzE', 'BV1HfX6BSEZr', 'BV1jiAJz3EkR', 'BV1KDhVzyEDc', 'BV1MKXhBNEvr',
                     'BV1MQ9jBcEnu', 'BV1PjcqzcEQw', 'BV1qq9EBEEJa','BV1QYXmBSEE8','BV1t5DCB8Eo1','BV1ViX6BvEmo',
                     'BV1wiXNBUEKC','BV1xpX5BEEE7','BV1zkXzBrEYr','BV1ZmDLBAELM']

    csv_files_all = list(DATA_DIR.glob("*.csv"))
    csv_files = [
        f for f in csv_files_all
        if f.stem.split('_')[0] not in exclude_bvids
    ]

    if not csv_files:
        print(f"警告：在 {DATA_DIR} 中未找到 CSV 文件")
        return

    print(f"发现 {len(csv_files)} 个 CSV 文件")
    for csv_file in csv_files:
        process_csv_file(csv_file)

    print("\n所有任务完成！")


if __name__ == "__main__":
    main()
