"""
使用微调后的 Chinese-CLIP + LoRA 模型对数据库中评论进行情感分类并更新字段。
支持多张图片（特征平均融合），纯文本评论自动走纯文本分支。
懒加载优化：Django启动时不会自动加载模型，第一次用预测功能才会加载
"""
import os
import sys
import django
from pathlib import Path
# Django 环境初始化
DJANGO_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(DJANGO_PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Bili_web.settings')
django.setup()
from ..models import CommentInfo
from django.conf import settings
import torch
from PIL import Image
from tqdm import tqdm
from peft import LoraConfig, get_peft_model
import cn_clip.clip as clip
from cn_clip.clip import load_from_name

# 配置
device = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "ViT-B-16"
PRETRAINED_MODEL_PATH = DJANGO_PROJECT_ROOT / "models" / "clip_cn_vit-b-16.pt"
FINETUNED_MODEL_PATH = DJANGO_PROJECT_ROOT / "training" / "models" / "sentiment_model_lora_best.pth"
BATCH_SIZE = 8
# 标签映射
COARSE_IDX_TO_LABEL = {0: 'positive', 1: 'neutral', 2: 'negative'}
FINE_IDX_TO_LABEL = {0: 'happiness', 1: 'anger', 2: 'sadness', 3: 'fear', 4: 'surprise', 5: 'neutral'}

# 懒加载：模型初始化为None，第一次用的时候才加载
model = None
preprocess = None

def load_model_once():
    """只加载一次模型，懒加载，Django启动时不会执行"""
    global model, preprocess
    if model is not None:
        return  # 已经加载过了，直接返回

    print("开始加载情感分析模型...")
    # 加载预训练 Chinese-CLIP
    base_model, preprocess = load_from_name(
        name=MODEL_NAME,
        device=device,
        download_root=str(PRETRAINED_MODEL_PATH.parent)
    )
    # LoRA 配置（与训练时一致）
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["out_proj", "query", "value"],
        lora_dropout=0.1,
        bias="none"
    )
    base_model = get_peft_model(base_model, lora_config)

    # 分类头（与训练时一致）
    class MultiHeadSentimentModel(torch.nn.Module):
        def __init__(self, clip_model):
            super().__init__()
            self.clip_model = clip_model
            self.coarse_head = torch.nn.Linear(512, 3)
            self.fine_head = torch.nn.Linear(512, 6)
        def forward(self, images_list, texts):
            """
            images_list: List[Tensor]，每个元素是 [num_imgs, 3, 224, 224]
            texts: [batch_size, context_length]
            """
            text_features = self.clip_model.encode_text(texts)
            # B = texts.shape[0]
            # 将所有图片拉平为一个大 batch 进行编码
            all_images = torch.cat(images_list, dim=0)
            all_image_features = self.clip_model.encode_image(all_images)
            # 按样本拆分，对同一评论的多张图片特征取平均
            counts = [img.shape[0] for img in images_list]
            split_features = torch.split(all_image_features, counts)
            image_features = torch.stack([f.mean(dim=0) for f in split_features])
            # 判断是否有真实图片（非全零占位图）
            image_sum = torch.tensor([img.sum() for img in images_list]).to(device)
            has_image = image_sum != 0
            features = text_features.clone()
            if has_image.any():
                features[has_image] = (text_features[has_image] + image_features[has_image]) / 2.0
            return self.coarse_head(features), self.fine_head(features)

    model = MultiHeadSentimentModel(base_model).to(device)
    model.load_state_dict(torch.load(FINETUNED_MODEL_PATH, map_location=device))
    model.eval()
    print("情感分析模型加载完成！")

# 批量预测函数
def predict_batch(comments):
    # 懒加载：第一次调用的时候才加载模型
    load_model_once()

    texts = []
    images_list = []
    for comment in comments:
        # 文本处理
        text = comment.content if comment.content else ""
        texts.append(clip.tokenize([text])[0])
        # 图片处理：支持多张图片
        img_tensors = []
        if comment.local_image_paths:
            paths = [p.strip() for p in comment.local_image_paths.split('|') if p.strip()]
            for path in paths:
                img_full = Path(settings.MEDIA_ROOT) / path
                if img_full.exists():
                    try:
                        img = Image.open(img_full).convert('RGB')
                        img_tensor = preprocess(img).to(device)   # 关键：移动到 GPU
                        img_tensors.append(img_tensor)
                    except Exception:
                        continue
        if img_tensors:
            images_list.append(torch.stack(img_tensors))
        else:
            # 占位图也要放到 GPU
            images_list.append(torch.zeros(1, 3, 224, 224).to(device))
    # 文本堆叠并移到 GPU
    text_batch = torch.stack(texts).to(device)
    with torch.no_grad():
        with torch.amp.autocast('cuda'):
            coarse_logits, fine_logits = model(images_list, text_batch)
            coarse_probs = coarse_logits.softmax(dim=-1)
            fine_probs = fine_logits.softmax(dim=-1)
    results = []
    for i in range(len(comments)):
        coarse_pred = coarse_probs[i].argmax().item()
        fine_pred = fine_probs[i].argmax().item()
        confidence = (coarse_probs[i].max().item() + fine_probs[i].max().item()) / 2.0
        results.append({
            'coarse_sentiment': COARSE_IDX_TO_LABEL[coarse_pred],
            'fine_sentiment': FINE_IDX_TO_LABEL[fine_pred],
            'sentiment_score': confidence
        })
    return results

# 主处理函数
def process_all_comments(bvid=None):
    queryset = CommentInfo.objects.all()
    if bvid:
        queryset = queryset.filter(bvid=bvid)
    total = queryset.count()
    if total == 0:
        print("没有需要处理的评论。")
        return
    print(f"待处理评论数: {total}")
    comments = list(queryset)
    for start in tqdm(range(0, total, BATCH_SIZE), desc="预测进度"):
        batch = comments[start:start + BATCH_SIZE]
        predictions = predict_batch(batch)
        for comment, pred in zip(batch, predictions):
            comment.coarse_sentiment = pred['coarse_sentiment']
            comment.fine_sentiment = pred['fine_sentiment']
            comment.sentiment_score = pred['sentiment_score']
        CommentInfo.objects.bulk_update(
            batch,
            ['coarse_sentiment', 'fine_sentiment', 'sentiment_score']
        )
    print("所有评论处理完成并已更新数据库。")

# 命令行入口
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="对数据库中评论进行情感预测")
    parser.add_argument("--bvid", type=str, help="指定视频 BV 号（可选），不指定则处理全部")
    args = parser.parse_args()
    process_all_comments(bvid=args.bvid)