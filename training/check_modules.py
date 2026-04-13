"""
检查Chinese-CLIP模型的模块名称，用于定位正确的LoRA目标模块
请将这个脚本放在你的 training/ 目录下运行
"""
import torch
from pathlib import Path
from cn_clip.clip import load_from_name

# 路径配置（和train.py保持一致）
TRAINING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TRAINING_DIR.parent
PRETRAINED_MODEL_PATH = PROJECT_ROOT / "models" / "clip_cn_vit-b-16.pt"
device = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "ViT-B-16"

print("正在加载模型，请稍候...")
try:
    model, preprocess = load_from_name(
        name=MODEL_NAME,
        device=device,
        download_root=str(PRETRAINED_MODEL_PATH.parent)
    )
    print("模型加载完成！")

    print("\n===== 模型中的所有线性层名称 =====")
    print("（请从下面的列表中找到注意力层的模块名，用于配置LoRA的target_modules）")
    linear_layers = []
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Linear):
            linear_layers.append(name)
            print(f"线性层: {name}")

    print(f"\n共找到 {len(linear_layers)} 个线性层")
    print("\n提示：注意力层的线性层通常包含 'attn' 关键词，你可以从上面的列表中找到对应的后缀名")

except Exception as e:
    print(f"加载模型出错: {e}")
    import traceback

    traceback.print_exc()