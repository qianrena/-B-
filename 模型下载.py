from cn_clip.clip import load_from_name, available_models

if __name__ == "__main__":
    # 查看所有可用的中文模型
    print("Available models:", available_models())
    # 预期输出: ['ViT-B-16', 'ViT-L-14', 'ViT-L-14-336', 'ViT-H-14', 'RN50']

    # 使用 load_from_name 下载模型，并指定保存路径
    # 对于你的 RTX 3060 6GB，'ViT-B-16' 是性能和显存的最佳平衡点
    model, preprocess = load_from_name("ViT-B-16", device="cpu", download_root="./models")
    print("模型下载成功，保存在 ./models 目录下")