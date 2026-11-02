"""
基于 Chinese-CLIP + LoRA 的多模态双粒度情感分类微调脚本
- 自动划分训练/验证/测试集
- 早停机制，保存最佳模型
- 绘制训练曲线和混淆矩阵
- 最终测试并输出详细报告
"""

import os
import torch
import pandas as pd
import numpy as np
from PIL import Image
from pathlib import Path
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm
from peft import LoraConfig, get_peft_model
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
import matplotlib.pyplot as plt
import seaborn as sns

import cn_clip.clip as clip
from cn_clip.clip import load_from_name

# 路径配置
TRAINING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TRAINING_DIR.parent

PRETRAINED_MODEL_PATH = PROJECT_ROOT / "models" / "clip_cn_vit-b-16.pt"
CSV_PATH = PROJECT_ROOT / "llm_label" / "finetune_data" / "all_labeled_data.csv"
MEDIA_ROOT = PROJECT_ROOT / "media"

SAVE_MODEL_DIR = TRAINING_DIR / "models"
SAVE_MODEL_DIR.mkdir(exist_ok=True)
SAVE_MODEL_PATH = SAVE_MODEL_DIR / "sentiment_model_lora_best.pth"

PLOTS_DIR = TRAINING_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

# 超参数
device = "cuda" if torch.cuda.is_available() else "cpu"
BATCH_SIZE = 4
GRADIENT_ACCUMULATION = 2
EPOCHS = 100  # 总轮次，早停会提前终止
LEARNING_RATE = 1e-4
MODEL_NAME = "ViT-B-16"
EARLY_STOP_PATIENCE = 10  # 连续10轮无提升则停止
RANDOM_SEED = 42

torch.manual_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)

print(f"设备: {device}")
print(f"预训练模型: {PRETRAINED_MODEL_PATH}")
print(f"数据文件: {CSV_PATH}")
print(f"图片根目录: {MEDIA_ROOT}")

# 加载 Chinese-CLIP
model, preprocess = load_from_name(
    name=MODEL_NAME,
    device=device,
    download_root=str(PRETRAINED_MODEL_PATH.parent)
)

# LoRA 配置
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["out_proj", "query", "value"],
    lora_dropout=0.1,
    bias="none"
)
model = get_peft_model(model, lora_config)
print("\n可训练参数统计:")
model.print_trainable_parameters()


# 双粒度分类头
class MultiHeadSentimentModel(torch.nn.Module):
    def __init__(self, clip_model):
        super().__init__()
        self.clip_model = clip_model
        self.coarse_head = torch.nn.Linear(512, 3)
        self.fine_head = torch.nn.Linear(512, 6)

    def forward(self, images_list, texts):
        text_features = self.clip_model.encode_text(texts)
        B = texts.shape[0]  # batch size

        # 批量处理所有图片，不管每个样本有多少张
        all_images = torch.cat(images_list, dim=0)  # 拉平所有图片，批量编码提速
        all_image_features = self.clip_model.encode_image(all_images)

        # 按样本拆分，对同一个样本的多张图片特征做平均融合
        counts = [img.shape[0] for img in images_list]  # 每个样本的图片数量
        split_features = torch.split(all_image_features, counts)
        image_features = torch.stack([f.mean(dim=0) for f in split_features])

        image_sum = torch.tensor([img.sum() for img in images_list]).to(texts.device)
        has_image = image_sum != 0
        features = text_features.clone()
        if has_image.any():
            features[has_image] = (text_features[has_image] + image_features[has_image]) / 2.0
        return self.coarse_head(features), self.fine_head(features)


sentiment_model = MultiHeadSentimentModel(model).to(device)


# 数据集类
class SentimentDataset(Dataset):
    def __init__(self, df, media_root, preprocess):
        self.df = df.reset_index(drop=True)
        self.media_root = Path(media_root)
        self.preprocess = preprocess
        self.coarse_map = {'positive': 0, 'neutral': 1, 'negative': 2}
        self.fine_map = {'happiness': 0, 'anger': 1, 'sadness': 2,
                         'fear': 3, 'surprise': 4, 'neutral': 5}

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        content = row['content']
        image_rel_path = row['image_path']
        text = clip.tokenize([content])[0].to(device)

        # 处理多张图片
        if pd.notna(image_rel_path) and image_rel_path.strip() != "":
            path_list = [p.strip() for p in image_rel_path.replace(';', ',').split(',') if p.strip()]
            image_tensors = []
            for p in path_list:
                img_full_path = self.media_root / p
                if img_full_path.exists():
                    img = Image.open(img_full_path).convert('RGB')
                    img_tensor = self.preprocess(img)
                    image_tensors.append(img_tensor)
            if len(image_tensors) > 0:
                image = torch.stack(image_tensors).to(device)
            else:
                image = torch.zeros(1, 3, 224, 224).to(device)
        else:
            image = torch.zeros(1, 3, 224, 224).to(device)

        coarse_label = self.coarse_map[row['coarse_sentiment']]
        fine_label = self.fine_map[row['fine_sentiment']]
        coarse_y = torch.tensor(coarse_label).to(device)
        fine_y = torch.tensor(fine_label).to(device)

        return image, text, coarse_y, fine_y


def custom_collate_fn(batch):
    # 处理不同图片数量的样本：把图片存为列表，剩下的正常stack
    images_list = [item[0] for item in batch]
    texts = torch.stack([item[1] for item in batch])
    coarse_y = torch.stack([item[2] for item in batch])
    fine_y = torch.stack([item[3] for item in batch])
    return images_list, texts, coarse_y, fine_y


# 数据划分
df_all = pd.read_csv(CSV_PATH)
# 按细粒度分层，确保每个集合包含所有细粒度类别
train_val_df, test_df = train_test_split(
    df_all, test_size=0.2, random_state=RANDOM_SEED,
    stratify=df_all['fine_sentiment']  # 只分层细粒度
)
train_df, val_df = train_test_split(
    train_val_df, test_size=0.125, random_state=RANDOM_SEED,
    stratify=train_val_df['fine_sentiment']  # 只分层细粒度
)

print(f"\n数据划分: 训练集 {len(train_df)} 条, 验证集 {len(val_df)} 条, 测试集 {len(test_df)} 条")

train_dataset = SentimentDataset(df_all, MEDIA_ROOT, preprocess)
val_dataset = SentimentDataset(val_df, MEDIA_ROOT, preprocess)
test_dataset = SentimentDataset(test_df, MEDIA_ROOT, preprocess)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, collate_fn=custom_collate_fn)
val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, collate_fn=custom_collate_fn)
test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False, collate_fn=custom_collate_fn)


# 类别权重计算
def compute_class_weights(df, label_column, class_list):
    counts = df[label_column].value_counts().to_dict()
    total = len(df)
    num_classes = len(class_list)
    weights = []
    for cls in class_list:
        cnt = counts.get(cls, 0)
        weight = total / (num_classes * cnt) if cnt > 0 else 0.0
        weights.append(weight)
    return torch.tensor(weights, dtype=torch.float32).to(device)


coarse_classes = ['positive', 'neutral', 'negative']
fine_classes = ['happiness', 'anger', 'sadness', 'fear', 'surprise', 'neutral']

coarse_weights = compute_class_weights(train_df, 'coarse_sentiment', coarse_classes)
fine_weights = compute_class_weights(train_df, 'fine_sentiment', fine_classes)

print(f"\n粗粒度类别权重: {dict(zip(coarse_classes, coarse_weights.cpu().numpy()))}")
print(f"细粒度类别权重: {dict(zip(fine_classes, fine_weights.cpu().numpy()))}")


# 评估函数
@torch.no_grad()
def evaluate(model, dataloader, loss_fn_coarse, loss_fn_fine):
    model.eval()
    total_loss = 0
    all_coarse_preds, all_coarse_labels = [], []
    all_fine_preds, all_fine_labels = [], []

    for images, texts, coarse_y, fine_y in dataloader:
        coarse_y, fine_y = coarse_y.to(device), fine_y.to(device)

        with torch.amp.autocast('cuda'):
            coarse_logits, fine_logits = model(images, texts)
            loss = loss_fn_coarse(coarse_logits, coarse_y) + loss_fn_fine(fine_logits, fine_y)

        total_loss += loss.item()
        all_coarse_preds.extend(coarse_logits.argmax(1).cpu().numpy())
        all_coarse_labels.extend(coarse_y.cpu().numpy())
        all_fine_preds.extend(fine_logits.argmax(1).cpu().numpy())
        all_fine_labels.extend(fine_y.cpu().numpy())

    avg_loss = total_loss / len(dataloader)
    coarse_acc = accuracy_score(all_coarse_labels, all_coarse_preds)
    fine_acc = accuracy_score(all_fine_labels, all_fine_preds)
    coarse_f1 = f1_score(all_coarse_labels, all_coarse_preds, average='macro')
    fine_f1 = f1_score(all_fine_labels, all_fine_preds, average='macro')

    return {
        'loss': avg_loss,
        'coarse_acc': coarse_acc,
        'fine_acc': fine_acc,
        'coarse_f1': coarse_f1,
        'fine_f1': fine_f1,
        'coarse_preds': all_coarse_preds,
        'coarse_labels': all_coarse_labels,
        'fine_preds': all_fine_preds,
        'fine_labels': all_fine_labels,
    }


# 训练准备
optimizer = torch.optim.AdamW(sentiment_model.parameters(), lr=LEARNING_RATE)
loss_fn_coarse = torch.nn.CrossEntropyLoss(weight=coarse_weights)
loss_fn_fine = torch.nn.CrossEntropyLoss(weight=fine_weights)
scaler = torch.amp.GradScaler('cuda')

# 记录历史
history = {
    'train_loss': [], 'val_loss': [],
    'val_coarse_acc': [], 'val_fine_acc': [],
    'val_coarse_f1': [], 'val_fine_f1': []
}

best_f1 = 0.0
patience_counter = 0

# 训练循环
for epoch in range(EPOCHS):
    sentiment_model.train()
    total_train_loss = 0
    pbar = tqdm(train_loader, desc=f"Epoch {epoch + 1}/{EPOCHS}")
    for step, (images, texts, coarse_y, fine_y) in enumerate(pbar):
        coarse_y, fine_y = coarse_y.to(device), fine_y.to(device)

        with torch.amp.autocast('cuda'):
            coarse_logits, fine_logits = sentiment_model(images, texts)
            loss = loss_fn_coarse(coarse_logits, coarse_y) + loss_fn_fine(fine_logits, fine_y)
            loss = loss / GRADIENT_ACCUMULATION

        scaler.scale(loss).backward()

        if (step + 1) % GRADIENT_ACCUMULATION == 0:
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()

        total_train_loss += loss.item() * GRADIENT_ACCUMULATION
        pbar.set_postfix({'loss': f"{loss.item() * GRADIENT_ACCUMULATION:.4f}"})

    avg_train_loss = total_train_loss / len(train_loader)
    history['train_loss'].append(avg_train_loss)

    # 验证
    val_metrics = evaluate(sentiment_model, val_loader, loss_fn_coarse, loss_fn_fine)
    history['val_loss'].append(val_metrics['loss'])
    history['val_coarse_acc'].append(val_metrics['coarse_acc'])
    history['val_fine_acc'].append(val_metrics['fine_acc'])
    history['val_coarse_f1'].append(val_metrics['coarse_f1'])
    history['val_fine_f1'].append(val_metrics['fine_f1'])

    print(f"Epoch {epoch + 1} | Train Loss: {avg_train_loss:.4f} | Val Loss: {val_metrics['loss']:.4f}")
    print(f"  粗粒度 Acc: {val_metrics['coarse_acc']:.4f}, F1: {val_metrics['coarse_f1']:.4f}")
    print(f"  细粒度 Acc: {val_metrics['fine_acc']:.4f}, F1: {val_metrics['fine_f1']:.4f}")

    # 早停与保存最佳模型
    current_f1 = val_metrics['fine_f1']  # 以细粒度 Macro F1 为监控指标
    if current_f1 > best_f1:
        best_f1 = current_f1
        patience_counter = 0
        torch.save(sentiment_model.state_dict(), SAVE_MODEL_PATH)
        print(f"  保存最佳模型 (F1: {best_f1:.4f})")
    else:
        patience_counter += 1
        print(f"  早停计数: {patience_counter}/{EARLY_STOP_PATIENCE}")
        if patience_counter >= EARLY_STOP_PATIENCE:
            print(f"早停触发，训练结束。")
            break


# 绘制训练曲线
def plot_training_curves(history, save_dir):
    epochs = range(1, len(history['train_loss']) + 1)

    plt.figure(figsize=(12, 5))

    plt.subplot(1, 2, 1)
    plt.plot(epochs, history['train_loss'], 'b-', label='Train Loss')
    plt.plot(epochs, history['val_loss'], 'r-', label='Val Loss')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.title('Training and Validation Loss')
    plt.legend()
    plt.grid(True)

    plt.subplot(1, 2, 2)
    plt.plot(epochs, history['val_coarse_f1'], 'g-', label='Coarse F1')
    plt.plot(epochs, history['val_fine_f1'], 'm-', label='Fine F1')
    plt.xlabel('Epoch')
    plt.ylabel('Macro F1 Score')
    plt.title('Validation Macro F1')
    plt.legend()
    plt.grid(True)

    plt.tight_layout()
    plt.savefig(save_dir / 'training_curves.png', dpi=300)
    plt.close()
    print(f"训练曲线已保存至 {save_dir / 'training_curves.png'}")


plot_training_curves(history, PLOTS_DIR)

# 最终测试
print("\n加载最佳模型进行测试...")
sentiment_model.load_state_dict(torch.load(SAVE_MODEL_PATH))
test_metrics = evaluate(sentiment_model, test_loader, loss_fn_coarse, loss_fn_fine)

print("\n========== 测试集结果 ==========")
print(f"粗粒度 Accuracy: {test_metrics['coarse_acc']:.4f}, Macro F1: {test_metrics['coarse_f1']:.4f}")
print(f"细粒度 Accuracy: {test_metrics['fine_acc']:.4f}, Macro F1: {test_metrics['fine_f1']:.4f}")

print("\n粗粒度分类报告:")
print(classification_report(test_metrics['coarse_labels'], test_metrics['coarse_preds'],
                            target_names=coarse_classes, digits=4))

print("\n细粒度分类报告:")
print(classification_report(test_metrics['fine_labels'], test_metrics['fine_preds'],
                            target_names=fine_classes, digits=4))


# 绘制混淆矩阵
def plot_confusion_matrix(y_true, y_pred, classes, title, save_path):
    cm = confusion_matrix(y_true, y_pred)
    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                xticklabels=classes, yticklabels=classes)
    plt.xlabel('Predicted')
    plt.ylabel('True')
    plt.title(title)
    plt.tight_layout()
    plt.savefig(save_path, dpi=300)
    plt.close()
    print(f"混淆矩阵已保存至 {save_path}")


plot_confusion_matrix(test_metrics['coarse_labels'], test_metrics['coarse_preds'],
                      coarse_classes, 'Coarse-grained Sentiment Confusion Matrix',
                      PLOTS_DIR / 'coarse_cm.png')

plot_confusion_matrix(test_metrics['fine_labels'], test_metrics['fine_preds'],
                      fine_classes, 'Fine-grained Sentiment Confusion Matrix',
                      PLOTS_DIR / 'fine_cm.png')

print(f"\n所有任务完成！最佳模型 F1: {best_f1:.4f}")
