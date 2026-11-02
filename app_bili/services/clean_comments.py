import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Bili_web.settings')
django.setup()

from app_bili.models import CommentInfo


def clean_text(text):
    """清理文本里的全角字符、零宽字符等特殊符号"""
    if not text:
        return text

    # 全角字符转半角（把全角的数字、字母、符号都转成正常的半角）
    full_width_chars = '０１２３４５６７８９　ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～'
    half_width_chars = '0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
    text = text.translate(str.maketrans(full_width_chars, half_width_chars))

    # 去掉零宽字符、非断空格这些Tokenizer处理不了的特殊字符
    text = text.replace('\u200b', '')  # 零宽空格
    text = text.replace('\u200c', '')  # 零宽非连接符
    text = text.replace('\u200d', '')  # 零宽连接符
    text = text.replace('\uFEFF', '')  # 字节顺序标记
    text = text.replace('\xa0', ' ')  # 非断空格

    # 过滤掉其他不可打印的控制字符
    text = ''.join(c for c in text if c >= ' ' or c in '\n\t')

    return text.strip()


def clean_all_comments():
    """批量清理数据库里所有评论的content字段"""
    cleaned_count = 0
    # 用iterator()批量处理
    for comment in CommentInfo.objects.iterator():
        old_content = comment.content
        if not old_content:
            continue

        new_content = clean_text(old_content)
        if new_content != old_content:
            comment.content = new_content
            # 只更新content字段，不会影响其他字段
            comment.save(update_fields=['content'])
            cleaned_count += 1

    print(f"清理完成！共清理了 {cleaned_count} 条评论的特殊字符")


if __name__ == '__main__':
    clean_all_comments()
