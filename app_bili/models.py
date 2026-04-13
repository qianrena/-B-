from django.contrib.auth.models import User
from django.db import models
from cryptography.fernet import Fernet
from django.conf import settings


class UserCredential(models.Model):
    """用户B站凭证表"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='关联用户',
        help_text='与Django内置User模型一对一关联'
    )
    sessdata = models.TextField(
        verbose_name='SESSDATA',
        help_text='B站登录凭证SESSDATA'
    )
    bili_jct = models.TextField(
        verbose_name='bili_jct',
        help_text='B站CSRF令牌'
    )
    dedeuserid = models.CharField(
        max_length=50, blank=True,
        verbose_name='DedeUserID',
        help_text='B站用户ID（可选）'
    )
    ac_time_value = models.CharField(
        max_length=100, blank=True,
        verbose_name='ac_time_value',
        help_text='B站时间戳相关凭证（可选）'
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='是否有效',
        help_text='标记该凭证是否可用'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='创建时间',
        help_text='凭证创建时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='凭证最后更新时间',
        help_text='凭证最后更新时间'
    )
    class Meta:
        db_table = 'user_credential'
        verbose_name = '用户B站凭证'
        verbose_name_plural = '用户B站凭证'


class VideoInfo(models.Model):
    """视频信息表"""
    STATUS_CHOICES = [
        ('pending', '待爬取'),
        ('processing', '爬取中'),
        ('completed', '已完成'),
        ('failed', '失败'),
        ('partial', '部分完成'),
    ]
    bvid = models.CharField(
        max_length=20, unique=True, db_index=True,
        verbose_name='视频BV号',
        help_text='B站视频唯一标识符'
    )
    aid = models.BigIntegerField(
        null=True, blank=True,
        verbose_name='视频AV号',
        help_text='视频旧版ID，部分接口使用'
    )
    title = models.CharField(
        max_length=500,
        verbose_name='视频标题',
        help_text='视频完整标题'
    )
    cover_url = models.URLField(
        max_length=500, blank=True,
        verbose_name='封面URL',
        help_text='视频封面图片的网络地址'
    )
    cover_local_path = models.CharField(
        max_length=500, blank=True,
        verbose_name='封面本地路径',
        help_text='封面下载到服务器后的相对路径（相对于MEDIA_ROOT）'
    )
    # UP主信息
    owner_mid = models.BigIntegerField(
        db_index=True,
        verbose_name='UP主UID',
        help_text='视频作者的B站用户ID'
    )
    owner_name = models.CharField(
        max_length=100,
        verbose_name='UP主昵称',
        help_text='视频作者的用户名'
    )
    owner_fans_count = models.IntegerField(
        default=0,
        verbose_name='UP主粉丝数',
        help_text='爬取时作者当前的粉丝数量'
    )
    owner_total_videos = models.IntegerField(
        default=0,
        verbose_name='UP主总投稿数',
        help_text='爬取时作者的总视频数'
    )
    # 统计数据
    view_count = models.BigIntegerField(
        default=0,
        verbose_name='播放量',
        help_text='视频总播放次数'
    )
    like_count = models.IntegerField(
        default=0,
        verbose_name='点赞数',
        help_text='视频点赞总数'
    )
    danmaku_count = models.IntegerField(
        default=0,
        verbose_name='弹幕数',
        help_text='视频弹幕总数'
    )
    reply_count = models.IntegerField(
        default=0,
        verbose_name='评论数',
        help_text='视频评论总数（B站接口返回）'
    )
    coin_count = models.IntegerField(
        default=0,
        verbose_name='投币数',
        help_text='视频获得的总投币数'
    )
    share_count = models.IntegerField(
        default=0,
        verbose_name='分享数',
        help_text='视频被分享的次数'
    )
    favorite_count = models.IntegerField(
        default=0,
        verbose_name='收藏数',
        help_text='视频被收藏的次数'
    )
    # 其他信息
    pubdate = models.DateTimeField(
        null=True, blank=True,
        verbose_name='发布时间',
        help_text='视频发布的日期时间'
    )
    tname = models.CharField(
        max_length=100, blank=True,
        verbose_name='分区名称',
        help_text='视频所属子分区，如“知识”“游戏”等'
    )
    duration = models.IntegerField(
        default=0,
        verbose_name='视频时长（秒）',
        help_text='视频总时长，单位为秒'
    )
    tags = models.TextField(
        blank=True,
        verbose_name='视频标签',
        help_text='视频关联的标签，多个标签以逗号或空格分隔'
    )
    # 爬取状态
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending',
        verbose_name='爬取状态',
        help_text='当前视频数据的爬取进度状态'
    )
    last_crawl_time = models.DateTimeField(
        null=True, blank=True,
        verbose_name='最后爬取时间',
        help_text='最近一次爬取该视频的时间'
    )
    comment_count = models.IntegerField(
        default=0,
        verbose_name='已爬取评论数',
        help_text='已经保存到数据库的评论条数'
    )
    error_message = models.TextField(
        blank=True,
        verbose_name='错误信息',
        help_text='爬取失败时的错误描述'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='记录创建时间',
        help_text='数据库中该条记录的入库时间'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='记录更新时间',
        help_text='数据库中该条记录的最后修改时间'
    )
    class Meta:
        db_table = 'video_info'
        verbose_name = '视频信息'
        verbose_name_plural = '视频信息'
        indexes = [
            models.Index(fields=['bvid']),
            models.Index(fields=['owner_mid']),
            models.Index(fields=['status']),
        ]
    def __str__(self):
        return f"{self.bvid} - {self.title[:50]}"


class CommentInfo(models.Model):
    """评论信息表"""
    rpid = models.CharField(
        max_length=50, db_index=True,
        verbose_name='评论ID',
        help_text='B站评论的唯一标识符'
    )
    bvid = models.CharField(
        max_length=20, db_index=True,
        verbose_name='所属视频BV号',
        help_text='该评论所属的视频BV号'
    )
    video = models.ForeignKey(
        VideoInfo, on_delete=models.CASCADE, related_name='comments',
        verbose_name='关联视频',
        help_text='外键关联到视频信息表'
    )
    # 用户信息
    user_name = models.CharField(
        max_length=100,
        verbose_name='评论者昵称',
        help_text='发表评论的用户名称'
    )
    user_id = models.BigIntegerField(
        db_index=True,
        verbose_name='评论者UID',
        help_text='发表评论的用户B站ID'
    )
    user_level = models.IntegerField(
        default=0,
        verbose_name='用户等级',
        help_text='评论者当前的B站等级（1-6）'
    )
    # 评论内容
    content = models.TextField(
        verbose_name='评论内容',
        help_text='评论的文本内容'
    )
    publish_time = models.DateTimeField(
        verbose_name='发布时间',
        help_text='评论发布的日期时间'
    )
    timestamp = models.IntegerField(
        db_index=True,
        verbose_name='原始时间戳',
        help_text='评论发布的Unix时间戳，用于增量判断'
    )
    # 互动数据
    like_count = models.IntegerField(
        default=0,
        verbose_name='点赞数',
        help_text='该评论获得的点赞数量'
    )
    reply_count = models.IntegerField(
        default=0,
        verbose_name='回复数',
        help_text='该评论下的回复数量'
    )
    ip_location = models.CharField(
        max_length=50, blank=True,
        verbose_name='IP属地',
        help_text='评论者的IP归属地（如“广西”）'
    )
    # 图片相关
    image_urls = models.TextField(
        blank=True,
        verbose_name='图片URL列表',
        help_text='评论中包含的图片URL，多个以“|”分隔'
    )
    image_count = models.IntegerField(
        default=0,
        verbose_name='图片数量',
        help_text='评论中包含的图片张数'
    )
    local_image_paths = models.TextField(
        blank=True,
        verbose_name='本地图片路径',
        help_text='下载到本地的图片路径，多个以“|”分隔'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='记录创建时间',
        help_text='数据库中该条评论记录的入库时间'
    )
    # 粗粒度情感三分类
    coarse_sentiment = models.CharField(
        max_length=20, blank=True,
        verbose_name='粗粒度情感',
        choices=[('positive', '积极'), ('neutral', '中性'), ('negative', '消极')]
    )
    # 细粒度情感六分类
    fine_sentiment = models.CharField(
        max_length=20, blank=True,
        verbose_name='细粒度情感',
        choices=[
            ('happiness', '喜悦'), ('anger', '愤怒'), ('sadness', '悲伤'),
            ('fear', '恐惧'), ('surprise', '惊讶'), ('neutral', '中性')
        ]
    )
    # 预测置信度
    sentiment_score = models.FloatField(null=True, blank=True, verbose_name='预测置信度')
    class Meta:
        db_table = 'comment_info'
        verbose_name = '评论信息'
        verbose_name_plural = '评论信息'
        unique_together = [['rpid', 'bvid']]
        indexes = [
            models.Index(fields=['bvid', 'timestamp']),
            models.Index(fields=['user_id']),
        ]
    def __str__(self):
        return f"{self.rpid} - {self.user_name}: {self.content[:50]}"


class VideoAnalysis(models.Model):
    video = models.OneToOneField(
        VideoInfo,
        on_delete=models.CASCADE,
        related_name='analysis',
        verbose_name='关联视频'
    )
    status = models.CharField(
        max_length=20,
        choices=[('pending', '待分析'), ('processing', '分析中'), ('completed', '已完成'), ('failed', '失败')],
        default='pending',
        verbose_name='分析状态'
    )
    # 基础统计
    total_comments = models.IntegerField(default=0, verbose_name='总评论数')
    pos_rate = models.FloatField(default=0.0, verbose_name='正面占比')
    neu_rate = models.FloatField(default=0.0, verbose_name='中性占比')
    neg_rate = models.FloatField(default=0.0, verbose_name='负面占比')
    avg_like = models.FloatField(default=0.0, verbose_name='平均点赞数')
    fine_distribution = models.JSONField(default=dict,blank=True, verbose_name='细粒度情感分布')
    # 输出目录
    output_dir = models.CharField(max_length=255, verbose_name='图表输出目录')
    # 各图表路径（相对于 MEDIA_ROOT）
    chart_coarse_pie = models.CharField(max_length=255, blank=True, verbose_name='粗粒度环形图')
    chart_fine_bar = models.CharField(max_length=255, blank=True, verbose_name='细粒度柱状图')
    chart_coarse_cum_count = models.CharField(max_length=255, blank=True, verbose_name='粗粒度累计评论数面积图')
    chart_coarse_cum_like = models.CharField(max_length=255, blank=True, verbose_name='粗粒度累计点赞数面积图')
    chart_fine_cum_count = models.CharField(max_length=255, blank=True, verbose_name='细粒度累计评论数面积图')
    chart_fine_cum_like = models.CharField(max_length=255, blank=True, verbose_name='细粒度累计点赞数面积图')
    chart_dual_axis = models.CharField(max_length=255, blank=True, verbose_name='评论量与负面率双轴图')
    chart_wordcloud = models.CharField(max_length=255, blank=True, verbose_name='全评论词云')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='分析时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='最后更新时间')
    error_message = models.TextField(blank=True, verbose_name='错误信息')
    class Meta:
        db_table = 'video_analysis'
        verbose_name = '视频分析结果'
        verbose_name_plural = '视频分析结果'
    def __str__(self):
        return f"{self.video.bvid} 分析结果"


# 新增：用户资料与收藏模型
class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='关联用户'
    )
    # 多对多关联视频，一个用户可以收藏多个视频，一个视频可以被多个用户收藏
    favorite_videos = models.ManyToManyField(
        VideoInfo,
        blank=True,
        related_name='favorited_by',
        verbose_name='收藏的视频'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    class Meta:
        db_table = 'user_profile'
        verbose_name = '用户资料'
        verbose_name_plural = '用户资料'


class SystemErrorLog(models.Model):
    """系统管理员错误信息表，用于存储爬虫等系统错误信息
    参考API文档: https://nemo2011.github.io/bilibili-api/#/
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name='关联用户',
        help_text='触发错误的用户，可选'
    )
    bvid = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        db_index=True,
        verbose_name='相关视频BV号',
        help_text='错误发生时处理的视频BV号，可选'
    )
    error_type = models.CharField(
        max_length=50,
        default='crawler_error',
        verbose_name='错误类型',
        help_text='错误的类型，如crawler_error, analysis_error等'
    )
    error_message = models.TextField(
        verbose_name='错误信息',
        help_text='详细的错误描述信息'
    )
    error_time = models.DateTimeField(
        auto_now_add=True,
        verbose_name='错误发生时间',
        help_text='错误发生的时间'
    )
    is_read = models.BooleanField(
        default=False,
        verbose_name='是否已读',
        help_text='管理员是否已读该错误信息'
    )
    class Meta:
        db_table = 'system_error_log'
        verbose_name = '系统错误日志'
        verbose_name_plural = '系统错误日志'
        indexes = [
            models.Index(fields=['error_time']),
            models.Index(fields=['is_read']),
        ]
    def __str__(self):
        return f"{self.error_time} - {self.error_type}: {self.error_message[:50]}"