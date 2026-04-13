import os


def print_tree(root, prefix='', ignore_dirs=None):
    if ignore_dirs is None:
        # 这里写你要彻底排除的文件夹
        ignore_dirs = {"data",'analysis','out_label','images', 'covers', '__pycache__', 'venv', '.git'}

    items = sorted(os.listdir(root))
    for i, item in enumerate(items):
        path = os.path.join(root, item)

        # 排除文件夹
        if os.path.isdir(path) and item in ignore_dirs:
            continue

        # 最后一个
        is_last = (i == len(items) - 1)
        connector = '└── ' if is_last else '├── '

        print(prefix + connector + item)

        # 如果是目录，递归
        if os.path.isdir(path):
            new_prefix = prefix + ('    ' if is_last else '│   ')
            print_tree(path, new_prefix, ignore_dirs)


if __name__ == '__main__':
    print("项目结构（已排除 images / covers）")
    print(".")
    print_tree('.')