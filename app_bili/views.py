import os
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth import update_session_auth_hash  # 修改密码后保持登录
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator

from .models import VideoInfo, CommentInfo, UserCredential, VideoAnalysis, UserProfile, SystemErrorLog
from .services.crawler_service import sync_crawl_video, sync_recrawl_comments, CrawlerService
from .services.sentiment_service import SentimentAdminService,SentimentAdminService
import threading
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import get_user_model




@login_required
def home(request):
    """
    首页：展示最近爬取的8个视频（按最后爬取时间倒序）
    """
    # 获取最新的8个视频（按 last_crawl_time 降序，排除为空的记录）
    latest_videos = VideoInfo.objects.exclude(last_crawl_time__isnull=True) \
        .order_by('-last_crawl_time')[:8]
    # 把逗号分隔的tags字符串转成列表，让前端模板可以正常循环
    for video in latest_videos:
        if video.tags:
            # 按逗号分割标签，并且去掉每个标签的前后空格
            video.tags = [tag.strip() for tag in video.tags.split(',')]
    context = {
        'video_list': latest_videos,
    }
    return render(request, 'home.html', context)


@login_required
@require_http_methods(['GET', 'POST'])
def profile(request):
    """
    用户个人信息管理（包括用户名、邮箱、密码、B站凭证等）
    GET: 显示包含所有字段的表单
    POST: 处理更新请求
    """
    user = request.user
    # 获取或创建凭证对象（方便模板使用）
    try:
        credential = UserCredential.objects.get(user=user, is_active=True)
        has_credential = True
    except UserCredential.DoesNotExist:
        credential = None
        has_credential = False
    if request.method == 'POST':
        # 1. 处理基础信息（用户名、邮箱）
        new_username = request.POST.get('username', '').strip()
        new_email = request.POST.get('email', '').strip()
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        # 验证当前密码（任何修改都需要先验证当前密码）
        if not user.check_password(current_password):
            messages.error(request, '当前密码错误，无法修改信息')
            return redirect('profile')
        # 修改用户名（需要检查唯一性）
        if new_username and new_username != user.username:
            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                messages.error(request, '用户名已被占用')
            else:
                user.username = new_username
                messages.success(request, '用户名已更新')
        # 修改邮箱（格式校验 + 唯一性）
        if new_email != user.email:
            if new_email:
                try:
                    validate_email(new_email)
                except ValidationError:
                    messages.error(request, '邮箱格式不正确')
                else:
                    if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                        messages.error(request, '邮箱已被其他账号使用')
                    else:
                        user.email = new_email
                        messages.success(request, '邮箱已更新')
            else:
                user.email = ''
                messages.success(request, '邮箱已清空')
        # 修改密码
        if new_password:
            if len(new_password) < 6:
                messages.error(request, '新密码长度至少为6位')
            elif new_password != confirm_password:
                messages.error(request, '两次输入的新密码不一致')
            else:
                user.set_password(new_password)
                messages.success(request, '密码已更新，请重新登录')
                # 更新 session 中的密码哈希，避免被登出
                update_session_auth_hash(request, user)
        # 保存用户基础信息
        user.save()
        # 2. 处理 B站凭证（可选）
        sessdata = request.POST.get('sessdata', '').strip()
        bili_jct = request.POST.get('bili_jct', '').strip()
        dedeuserid = request.POST.get('dedeuserid', '')
        ac_time_value = request.POST.get('ac_time_value', '')
        if sessdata and bili_jct:
            # 有新的凭证则保存/更新
            try:
                cred_obj, created = UserCredential.objects.update_or_create(
                    user=user,
                    defaults={
                        'sessdata': sessdata,
                        'bili_jct': bili_jct,
                        'dedeuserid': dedeuserid,
                        'ac_time_value': ac_time_value,
                        'is_active': True,
                    }
                )
                cred_obj.save()
                messages.success(request, 'B站凭证已更新')
            except Exception as e:
                messages.error(request, f'凭证保存失败: {str(e)}')
        elif sessdata or bili_jct:
            messages.warning(request, '请同时填写 SESSDATA 和 bili_jct，否则凭证不会更新')
        # 如果两者都为空，则不修改凭证（保留原有凭证）
        return redirect('profile')
    # GET 请求：准备现有数据填充表单
    context = {
        'user': user,
        'has_credential': has_credential,
        'credential': credential,
        # 脱敏显示 bili_jct
        'bili_jct_display': credential.bili_jct[:10] + '...' if credential and credential.bili_jct else '',
        'dedeuserid_display': credential.dedeuserid if credential else '',
        'credential_created_at': credential.created_at if credential else None,
    }
    return render(request, 'profile.html', context)


@login_required
def crawler_video(request):
    if request.method == 'POST':
        bvid = request.POST.get('bvid')
        if not bvid:
            messages.error(request, '请提供 BV 号')
            return redirect('crawler_video')
        # 自动判断爬取模式：视频已存在则增量，不存在则全量
        video = CrawlerService.get_video_sync(bvid)
        # 凭证预检查（同步），避免启动线程后才发现凭证缺失
        credential_dict = CrawlerService.get_credential_sync(request.user)
        if not credential_dict:
            messages.error(request, '未配置B站凭证，请先在个人信息页面填写 SESSDATA 和 bili_jct')
            return redirect('profile')
        # 创建或更新记录，标记为处理中
        if not video:
            video = CrawlerService.create_video_sync(
                bvid=bvid,
                title='爬取中...',
                owner_name='未知',
                owner_mid=0,
                status='processing',
                last_crawl_time=timezone.now()
            )
        else:
            CrawlerService.update_video_status_sync(video, 'processing')
            video.last_crawl_time = timezone.now()
            video.save(update_fields=['last_crawl_time'])
        # 自动选择爬取模式：视频不存在则全量，已存在则增量更新
        force_full = not video
        # 启动后台线程执行爬虫
        def run_crawler():
            User = get_user_model()
            try:
                user = User.objects.get(id=request.user.id)
                result = sync_crawl_video(user, bvid, force_full=force_full)
                if not result['success']:
                    print(f"爬虫失败: {result['error']}")
                else:
                    print(f"爬虫成功，新增评论 {result['comments_added']} 条")
            except Exception as e:
                print(f"后台爬虫异常: {e}")
        thread = threading.Thread(target=run_crawler)
        thread.daemon = True
        thread.start()
        # 根据模式显示不同的提示
        if force_full:
            messages.success(request, f'已开始全量爬取视频 {bvid}，请稍后查看详情')
        else:
            messages.success(request, f'已开始增量更新视频 {bvid}，请稍后查看详情')
        return redirect('crawler_video')  # 刷新当前页面，显示提示
    # GET 请求：显示表单
    return render(request, 'crawler_video.html')



def admin_required(view_func):
    decorated_view_func = user_passes_test(
        lambda u: u.is_authenticated and u.is_staff,
        login_url='login'
    )(view_func)
    return decorated_view_func

@login_required
@admin_required  # 管理员权限装饰器
async def admin_sentiment_action(request):
    action = request.POST.get('action')
    bvid = request.POST.get('bvid')  # 可选，指定BV

    if action == 'predict_unpredicted':
        result = await SentimentAdminService.predict_unpredicted(bvid)
    elif action == 're_predict_all':
        result = await SentimentAdminService.re_predict_all(bvid)
    elif action == 'regenerate_charts':
        result = await SentimentAdminService.regenerate_charts(bvid)
    else:
        result = {"success": False, "message": "未知操作"}

    # 返回结果给前端
    return JsonResponse(result)


@login_required
def video_detail(request, bvid):
    """获取视频详情与舆情分析图表"""
    video = get_object_or_404(VideoInfo, bvid=bvid)
    # 获取该视频的舆情分析结果
    analysis = None
    try:
        analysis = VideoAnalysis.objects.get(video=video)
        # 读取图表文件内容的工具函数
        def read_chart_content(chart_path):
            if not chart_path:
                return None
            # 把Windows反斜杠转成系统路径
            chart_path = chart_path.replace('\\', os.sep)
            # 拼接完整的本地文件路径
            full_file_path = os.path.join(settings.MEDIA_ROOT, chart_path)
            try:
                # 读取图表文件的内容
                with open(full_file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                print(f"读取图表文件失败: {full_file_path}, 错误: {e}")
                return None
        # 读取所有图表的内容，传给前端
        if analysis:
            analysis.chart_coarse_pie_content = read_chart_content(analysis.chart_coarse_pie)
            analysis.chart_fine_bar_content = read_chart_content(analysis.chart_fine_bar)
            analysis.chart_coarse_cum_count_content = read_chart_content(analysis.chart_coarse_cum_count)
            analysis.chart_coarse_cum_like_content = read_chart_content(analysis.chart_coarse_cum_like)
            analysis.chart_fine_cum_count_content = read_chart_content(analysis.chart_fine_cum_count)
            analysis.chart_fine_cum_like_content = read_chart_content(analysis.chart_fine_cum_like)
            analysis.chart_dual_axis_content = read_chart_content(analysis.chart_dual_axis)
            analysis.chart_wordcloud_content = read_chart_content(analysis.chart_wordcloud)
    except VideoAnalysis.DoesNotExist:
        pass
    # 把tags转成列表，避免前端单个字符的问题
    if video.tags:
        video.tags = [tag.strip() for tag in video.tags.split(',')]
    # 检查用户是否已经收藏了这个视频
    is_favorited = False
    try:
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        is_favorited = profile.favorite_videos.filter(id=video.id).exists()
    except Exception as e:
        print(f"获取收藏状态失败: {e}")
    context = {
        'video': video,
        'analysis': analysis,
        'is_favorited': is_favorited,
    }
    return render(request, 'video_detail.html', context)


# 新增：切换收藏状态的接口
@login_required
@require_http_methods(['POST'])
def toggle_favorite(request):
    """切换视频的收藏状态：已收藏则取消，未收藏则添加"""
    bvid = request.POST.get('bvid')
    if not bvid:
        return JsonResponse({'success': False, 'error': '参数错误'})
    # 找到对应的视频
    try:
        video = VideoInfo.objects.get(bvid=bvid)
    except VideoInfo.DoesNotExist:
        return JsonResponse({'success': False, 'error': '视频不存在'})
    # 找到用户的资料
    try:
        profile, created = UserProfile.objects.get_or_create(user=request.user)
    except Exception as e:
        return JsonResponse({'success': False, 'error': '用户资料错误'})
    # 切换收藏状态
    is_favorited = profile.favorite_videos.filter(id=video.id).exists()
    if is_favorited:
        # 已收藏，取消收藏
        profile.favorite_videos.remove(video)
        is_favorited = False
        message = '已取消收藏'
    else:
        # 未收藏，添加收藏
        profile.favorite_videos.add(video)
        is_favorited = True
        message = '已添加到收藏'
    profile.save()
    return JsonResponse({
        'success': True,
        'is_favorited': is_favorited,
        'message': message
    })


# 新增：我的收藏页面
@login_required
def my_favorites(request):
    """我的收藏页面，展示用户收藏的所有视频，分页展示，一页8个"""
    # 找到用户的资料
    try:
        profile = UserProfile.objects.get(user=request.user)
        # 获取收藏的视频，按最后爬取时间倒序，最新的在最前面
        favorite_videos = profile.favorite_videos.order_by('-last_crawl_time')
    except UserProfile.DoesNotExist:
        # 没有资料，说明没有收藏
        favorite_videos = []
    # Django自带的分页，一页8个
    paginator = Paginator(favorite_videos, 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    # 把tags转成列表，和首页一样，避免前端单个字符的问题
    for video in page_obj:
        if video.tags:
            video.tags = [tag.strip() for tag in video.tags.split(',')]
    context = {
        'page_obj': page_obj,
        'video_list': page_obj.object_list,
    }
    return render(request, 'my_favorites.html', context)


# 新增：已分析视频页面
@login_required
def all_videos(request):
    """已分析视频页面，展示所有的视频，一页8个，一行4个"""
    # 获取所有的视频，按最后爬取时间倒序
    all_videos = VideoInfo.objects.exclude(last_crawl_time__isnull=True) \
        .order_by('-last_crawl_time')
    # 分页，一页8个
    paginator = Paginator(all_videos, 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    # 把tags转成列表
    for video in page_obj:
        if video.tags:
            video.tags = [tag.strip() for tag in video.tags.split(',')]
    context = {
        'page_obj': page_obj,
        'video_list': page_obj.object_list,
    }
    return render(request, 'all_videos.html', context)


# ====================== 管理员增删改查功能 ======================
@login_required
@admin_required
def admin_dashboard(request):
    """管理员总览页面，展示系统统计数据"""
    # 统计各项数据
    total_users = User.objects.count()
    total_videos = VideoInfo.objects.count()
    total_comments = CommentInfo.objects.count()
    unread_errors = SystemErrorLog.objects.filter(is_read=False).count()
    total_analysis = VideoAnalysis.objects.filter(status='completed').count()

    context = {
        'total_users': total_users,
        'total_videos': total_videos,
        'total_comments': total_comments,
        'unread_errors': unread_errors,
        'total_analysis': total_analysis,
    }
    return render(request, 'admin_dashboard.html', context)


# 用户管理
@login_required
@admin_required
def admin_user_list(request):
    """用户列表页面，展示所有系统用户，支持分页"""
    users = User.objects.all().order_by('-date_joined')
    paginator = Paginator(users, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'users': page_obj.object_list,
    }
    return render(request, 'admin_user_list.html', context)


@login_required
@admin_required
@require_http_methods(['GET', 'POST'])
def admin_user_edit(request, user_id=None):
    """用户编辑/新增页面"""
    user_obj = None
    if user_id:
        user_obj = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        is_staff = request.POST.get('is_staff') == 'on'
        new_password = request.POST.get('new_password', '')

        # 构建上下文，用于验证失败时重新渲染
        context = {
            'user_obj': user_obj,
            'is_edit': user_obj is not None,
            'form_data': {  # 保留用户输入
                'username': username,
                'email': email,
                'is_staff': is_staff,
            }
        }

        # 基础验证
        if not username:
            messages.error(request, '用户名不能为空')
            return render(request, 'admin_user_edit.html', context)

        # 用户名唯一性检查
        if User.objects.filter(username=username).exclude(id=user_obj.id if user_obj else None).exists():
            messages.error(request, '用户名已被占用')
            return render(request, 'admin_user_edit.html', context)

        # 邮箱验证
        if email:
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, '邮箱格式不正确')
                return render(request, 'admin_user_edit.html', context)
            if User.objects.filter(email=email).exclude(id=user_obj.id if user_obj else None).exists():
                messages.error(request, '邮箱已被其他账号使用')
                return render(request, 'admin_user_edit.html', context)

        # 密码验证（仅新增时必填）
        if not user_obj and not new_password:
            messages.error(request, '新建用户必须设置初始密码')
            return render(request, 'admin_user_edit.html', context)
        if new_password and len(new_password) < 6:
            messages.error(request, '密码长度至少为6位')
            return render(request, 'admin_user_edit.html', context)

        # 处理新增/更新
        if not user_obj:
            user_obj = User(username=username, email=email, is_staff=is_staff)
            user_obj.set_password(new_password)
            user_obj.save()
            messages.success(request, '用户创建成功')
        else:
            user_obj.username = username
            user_obj.email = email
            user_obj.is_staff = is_staff
            if new_password:
                user_obj.set_password(new_password)
                messages.success(request, '用户信息已更新，密码已修改')
            else:
                messages.success(request, '用户信息已更新')
            user_obj.save()

        return redirect('admin_user_list')

    # GET 请求
    context = {
        'user_obj': user_obj,
        'is_edit': user_obj is not None,
    }
    return render(request, 'admin_user_edit.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_user_delete(request, user_id):
    """删除用户，级联删除关联数据"""
    user = get_object_or_404(User, id=user_id)
    try:
        # 删除关联的用户资料
        try:
            profile = UserProfile.objects.get(user=user)
            profile.delete()
        except UserProfile.DoesNotExist:
            pass
        # 删除关联的B站凭证
        try:
            cred = UserCredential.objects.get(user=user)
            cred.delete()
        except UserCredential.DoesNotExist:
            pass
        # 删除用户本身
        user.delete()
        messages.success(request, '用户及关联数据已删除')
    except Exception as e:
        messages.error(request, f'删除失败: {str(e)}')
    return redirect('admin_user_list')


# 视频管理
@login_required
@admin_required
def admin_video_list(request):
    """视频列表页面，展示所有系统中的视频，支持分页"""
    videos = VideoInfo.objects.all().order_by('-last_crawl_time')
    paginator = Paginator(videos, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'videos': page_obj.object_list,
    }
    return render(request, 'admin_video_list.html', context)


@login_required
@admin_required
@require_http_methods(['GET', 'POST'])
def admin_video_edit(request, bvid):
    """视频信息编辑页面"""
    video = get_object_or_404(VideoInfo, bvid=bvid)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        owner_name = request.POST.get('owner_name', '').strip()
        status = request.POST.get('status', '').strip()
        tname = request.POST.get('tname', '').strip()
        tags = request.POST.get('tags', '').strip()

        if not title:
            messages.error(request, '视频标题不能为空')
            return redirect('admin_video_edit', bvid=bvid)

        video.title = title
        video.owner_name = owner_name
        video.status = status
        video.tname = tname
        video.tags = tags
        video.save()

        messages.success(request, '视频信息已更新')
        return redirect('admin_video_list')

    context = {
        'video': video,
        'status_choices': VideoInfo.STATUS_CHOICES,
    }
    return render(request, 'admin_video_edit.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_video_delete(request, bvid):
    """删除视频，级联删除评论、分析结果等关联数据"""
    video = get_object_or_404(VideoInfo, bvid=bvid)
    try:
        # Django外键默认级联删除，会自动删除评论、分析结果等
        video.delete()
        messages.success(request, '视频及所有关联数据已删除')
    except Exception as e:
        messages.error(request, f'删除失败: {str(e)}')
    return redirect('admin_video_list')


# 评论管理
@login_required
@admin_required
def admin_comment_list(request):
    """评论列表页面，展示所有系统中的评论，支持分页"""
    comments = CommentInfo.objects.all().order_by('-publish_time')
    paginator = Paginator(comments, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'comments': page_obj.object_list,
    }
    return render(request, 'admin_comment_list.html', context)


@login_required
@admin_required
@require_http_methods(['GET', 'POST'])
def admin_comment_edit(request, comment_id):
    """评论信息编辑页面"""
    comment = get_object_or_404(CommentInfo, id=comment_id)

    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        user_name = request.POST.get('user_name', '').strip()
        coarse_sentiment = request.POST.get('coarse_sentiment', '').strip()
        fine_sentiment = request.POST.get('fine_sentiment', '').strip()

        if not content:
            messages.error(request, '评论内容不能为空')
            return redirect('admin_comment_edit', comment_id=comment_id)

        comment.content = content
        comment.user_name = user_name
        comment.coarse_sentiment = coarse_sentiment
        comment.fine_sentiment = fine_sentiment
        comment.save()

        messages.success(request, '评论信息已更新')
        return redirect('admin_comment_list')

    context = {
        'comment': comment,
        'coarse_choices': [('positive', '积极'), ('neutral', '中性'), ('negative', '消极')],
        'fine_choices': [
            ('happiness', '喜悦'), ('anger', '愤怒'), ('sadness', '悲伤'),
            ('fear', '恐惧'), ('surprise', '惊讶'), ('neutral', '中性')
        ],
    }
    return render(request, 'admin_comment_edit.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_comment_delete(request, comment_id):
    """删除评论"""
    comment = get_object_or_404(CommentInfo, id=comment_id)
    try:
        comment.delete()
        messages.success(request, '评论已删除')
    except Exception as e:
        messages.error(request, f'删除失败: {str(e)}')
    return redirect('admin_comment_list')


# 系统错误日志管理
@login_required
@admin_required
def admin_error_log_list(request):
    """系统错误日志列表页面"""
    logs = SystemErrorLog.objects.all().order_by('-error_time')
    paginator = Paginator(logs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'logs': page_obj.object_list,
    }
    return render(request, 'admin_error_log_list.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_error_log_mark_read(request, log_id):
    """标记错误日志为已读"""
    log = get_object_or_404(SystemErrorLog, id=log_id)
    log.is_read = True
    log.save()
    messages.success(request, '已标记为已读')
    return redirect('admin_error_log_list')


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_error_log_delete(request, log_id):
    """删除错误日志"""
    log = get_object_or_404(SystemErrorLog, id=log_id)
    try:
        log.delete()
        messages.success(request, '日志已删除')
    except Exception as e:
        messages.error(request, f'删除失败: {str(e)}')
    return redirect('admin_error_log_list')


# ====================== 爬虫与分析管理员功能 ======================
@login_required
@admin_required
def admin_crawler_manage(request):
    """爬虫与分析管理页面，管理员可以手动触发各种后台操作"""
    context = {}
    # 如果有POST的结果，传递给模板
    if request.session.get('crawler_action_result'):
        context['action_result'] = request.session.pop('crawler_action_result')
    return render(request, 'admin_crawler_manage.html', context)


@login_required
@admin_required
@require_http_methods(['POST'])
def admin_crawler_action(request):
    """处理爬虫/分析的操作请求"""
    bvid = request.POST.get('bvid', '').strip() or None
    action = request.POST.get('action', '').strip()

    try:
        if action == 'force_full_crawl':
            # 强制全量重新爬取
            if not bvid:
                result = {'success': False, 'error': '全量爬取必须指定BV号'}
            else:
                # 调用同步的爬取方法
                result = sync_crawl_video(request.user, bvid, force_full=True)
                if result['success']:
                    result['message'] = f'全量爬取完成，新增评论 {result["comments_added"]} 条，视频信息已更新'

        elif action == 'incremental_crawl':
            # 增量重新爬取评论
            if not bvid:
                result = {'success': False, 'error': '增量爬取必须指定BV号'}
            else:
                result = sync_recrawl_comments(request.user, bvid)
                if result['success']:
                    result['message'] = f'增量爬取完成，新增评论 {result["comments_added"]} 条'

        elif action == 'predict_unpredicted':
            # 补全未预测的评论
            import asyncio
            result = asyncio.run(SentimentAdminService.predict_unpredicted(bvid))

        elif action == 're_predict_all':
            # 全量重新预测
            import asyncio
            result = asyncio.run(SentimentAdminService.re_predict_all(bvid))

        elif action == 'regenerate_charts':
            # 重新生成图表
            import asyncio
            result = asyncio.run(SentimentAdminService.regenerate_charts(bvid))

        else:
            result = {'success': False, 'error': '未知的操作类型'}

    except Exception as e:
        result = {'success': False, 'error': str(e), 'message': f'操作执行失败: {str(e)}'}

    # 把结果存到session，跳转到管理页面显示
    request.session['crawler_action_result'] = result
    return redirect('admin_crawler_manage')