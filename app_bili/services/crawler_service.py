# crawler_service.py
import datetime
import asyncio
from typing import Optional
from asgiref.sync import sync_to_async
from django.utils import timezone
from bilibili_api import Credential
from ..models import VideoInfo, CommentInfo, UserCredential, SystemErrorLog
from .bili_crawler import BiliCrawler


class CrawlerService:
    """爬虫服务管理类（同时提供同步和异步接口）
    参考API文档: https://nemo2011.github.io/bilibili-api/#/
    """

    # ========== 同步数据库方法（供视图直接调用） ==========
    @staticmethod
    def get_video_sync(bvid: str) -> Optional[VideoInfo]:
        try:
            return VideoInfo.objects.get(bvid=bvid)
        except VideoInfo.DoesNotExist:
            return None

    @staticmethod
    def video_exists_sync(bvid: str) -> bool:
        return VideoInfo.objects.filter(bvid=bvid).exists()

    @staticmethod
    def create_video_sync(bvid: str, **defaults) -> VideoInfo:
        return VideoInfo.objects.create(bvid=bvid, **defaults)

    @staticmethod
    def update_video_status_sync(video: VideoInfo, status: str, error_message: str = None):
        video.status = status
        if error_message:
            video.error_message = error_message
        video.last_crawl_time = timezone.now()
        video.save()

    @staticmethod
    def update_video_comment_count_sync(video: VideoInfo):
        video.comment_count = CommentInfo.objects.filter(video=video).count()
        video.save(update_fields=['comment_count'])

    # 同步获取用户凭证（明文）
    @staticmethod
    def get_credential_sync(user) -> Optional[dict]:
        try:
            credential_obj = UserCredential.objects.get(user=user, is_active=True)
            sessdata = credential_obj.sessdata
            bili_jct = credential_obj.bili_jct
            # 关键：必须同时有 sessdata 和 bili_jct 才返回
            if not sessdata or not bili_jct:
                return None
            return {
                'sessdata': sessdata,
                'bili_jct': bili_jct,
                'dedeuserid': credential_obj.dedeuserid,
                'ac_time_value': credential_obj.ac_time_value,
            }
        except UserCredential.DoesNotExist:
            return None

    @staticmethod
    def update_credential_sync(user, new_plain: dict):
        credential_obj = UserCredential.objects.get(user=user, is_active=True)
        credential_obj.sessdata = new_plain['sessdata']
        credential_obj.bili_jct = new_plain['bili_jct']
        credential_obj.dedeuserid = new_plain['dedeuserid']
        credential_obj.ac_time_value = new_plain['ac_time_value']
        credential_obj.save()

    # ========== 异步凭证刷新 ==========
    @staticmethod
    async def refresh_and_update_credential_async(user):
        """异步刷新凭证（使用同步方法包装）"""
        try:
            current = await sync_to_async(CrawlerService.get_credential_sync)(user)
            if not current:
                return None
            temp_cred = Credential(sessdata=current['sessdata'], bili_jct=current['bili_jct'])
            if await temp_cred.check_refresh():
                print("Cookie需要刷新，正在刷新...")
                await temp_cred.refresh()
                print("Cookie已自动刷新，更新数据库...")
                new_plain = {
                    'sessdata': temp_cred.sessdata,
                    'bili_jct': temp_cred.bili_jct,
                    'dedeuserid': temp_cred.dedeuserid,
                    'ac_time_value': temp_cred.ac_time_value,
                }
                await sync_to_async(CrawlerService.update_credential_sync)(user, new_plain)
                return new_plain
            else:
                return current
        except Exception as e:
            print(f"凭证刷新失败: {e}")
            return None

    # ========== 异步爬虫核心 ==========
    @classmethod
    async def crawl_video_async(cls, user, bvid: str, force_full: bool = False) -> dict:
        result = {'success': False, 'video_info_updated': False, 'comments_added': 0, 'error': None}
        # 先获取视频对象（全量模式也需要获取，避免重复创建触发唯一约束）
        existing_video = await sync_to_async(cls.get_video_sync)(bvid)
        # 凭证检查
        credential_dict = await cls.refresh_and_update_credential_async(user)
        if not credential_dict:
            error_msg = '未找到有效的B站凭证，或刷新失败，请重新配置'
            result['error'] = error_msg
            # 保存错误日志
            try:
                await sync_to_async(
                    lambda: SystemErrorLog.objects.create(
                        user=user,
                        bvid=bvid,
                        error_type='crawler_error',
                        error_message=error_msg
                    )
                )()
            except Exception as log_e:
                print(f"保存错误日志失败: {log_e}")
            # 更新视频状态为 failed
            if existing_video:
                existing_video.status = 'failed'
                existing_video.error_message = error_msg
                await sync_to_async(existing_video.save)()
            else:
                # 创建一个失败记录
                await sync_to_async(VideoInfo.objects.create)(
                    bvid=bvid, title='爬取失败', owner_name='未知', owner_mid=0,
                    status='failed', error_message=error_msg
                )
            return result
        try:
            crawler = BiliCrawler(credential_dict)
            # 获取视频信息
            video_info_data = await crawler.get_video_info(bvid)
            if not video_info_data:
                error_msg = '获取视频信息失败'
                result['error'] = error_msg
                # 保存错误日志
                try:
                    await sync_to_async(
                        lambda: SystemErrorLog.objects.create(
                            user=user,
                            bvid=bvid,
                            error_type='crawler_error',
                            error_message=error_msg
                        )
                    )()
                except Exception as log_e:
                    print(f"保存错误日志失败: {log_e}")
                return result
            # 转换 pubdate 时间戳为 上海时区的datetime 对象
            if video_info_data.get('pubdate'):
                video_info_data['pubdate'] = datetime.datetime.fromtimestamp(
                    video_info_data['pubdate']
                )
            # 获取UP主信息
            up_info = await crawler.get_up_user_info(video_info_data['owner_mid'])
            if up_info:
                video_info_data['owner_fans_count'] = up_info['fans_count']
                video_info_data['owner_total_videos'] = up_info['total_videos']
                video_info_data['owner_name'] = up_info.get('name') or video_info_data['owner_name']

            # 保存/更新视频信息（此时还未下载封面，cover_local_path暂为空）
            if existing_video:
                for key, value in video_info_data.items():
                    if hasattr(existing_video, key) and value is not None:
                        setattr(existing_video, key, value)
                existing_video.status = 'processing'
                existing_video.last_crawl_time = timezone.now()
                await sync_to_async(existing_video.save)()
                video = existing_video
                result['video_info_updated'] = True
            else:
                video = await sync_to_async(VideoInfo.objects.create)(
                    **video_info_data, status='processing', last_crawl_time=timezone.now()
                )
            # 爬取评论，根据force_full参数决定是增量还是全量
            comments = await crawler.fetch_comments_incremental(bvid, force_full=force_full)
            await crawler.save_comments_to_db(comments, video)

            # ==================== 所有元数据采集完成，现在下载静态资源 ====================
            # 1. 下载封面（所有数据采集完成后才下载，避免爬取失败残留封面文件）
            if video_info_data.get('cover_url'):
                local_cover = await asyncio.to_thread(
                    crawler.cover_downloader.download,
                    video_info_data['cover_url'], bvid
                )
                if local_cover:
                    video.cover_local_path = local_cover
            # 2. 批量下载评论中的图片（所有评论保存后才下载，避免爬取失败残留图片文件）
            if comments:
                await crawler.batch_download_comment_images(comments, video)

            # 更新最终状态
            video.status = 'completed'
            # 修复：正确包装 filter().count() 整个同步操作，移除多余的调用，避免coroutine调用错误
            video.comment_count = await sync_to_async(lambda: CommentInfo.objects.filter(video=video).count())()
            await sync_to_async(video.save)()
            result['success'] = True
            result['comments_added'] = len(comments)
            crawler.print_stats()
        except Exception as e:
            error_msg = str(e)
            result['error'] = error_msg
            # 保存错误信息到系统管理员日志表
            try:
                await sync_to_async(
                    lambda: SystemErrorLog.objects.create(
                        user=user,
                        bvid=bvid,
                        error_type='crawler_error',
                        error_message=error_msg
                    )
                )()
            except Exception as log_e:
                print(f"保存错误日志失败: {log_e}")
            # 更新视频状态
            if existing_video:
                existing_video.status = 'failed'
                existing_video.error_message = error_msg
                await sync_to_async(existing_video.save)()
            else:
                try:
                    await sync_to_async(VideoInfo.objects.create)(
                        bvid=bvid, title='爬取失败', owner_name='未知', owner_mid=0,
                        status='failed', error_message=error_msg
                    )
                except:
                    pass
        return result

    @classmethod
    async def recrawl_comments_async(cls, user, bvid: str) -> dict:
        result = {'success': False, 'comments_added': 0, 'error': None}
        video = await sync_to_async(cls.get_video_sync)(bvid)
        if not video:
            error_msg = f'视频 {bvid} 不存在，请先爬取基础信息'
            result['error'] = error_msg
            # 保存错误日志
            try:
                await sync_to_async(
                    lambda: SystemErrorLog.objects.create(
                        user=user,
                        bvid=bvid,
                        error_type='crawler_error',
                        error_message=error_msg
                    )
                )()
            except Exception as log_e:
                print(f"保存错误日志失败: {log_e}")
            return result
        credential_dict = await cls.refresh_and_update_credential_async(user)
        if not credential_dict:
            error_msg = '凭证刷新失败，请重新配置'
            result['error'] = error_msg
            # 保存错误日志
            try:
                await sync_to_async(
                    lambda: SystemErrorLog.objects.create(
                        user=user,
                        bvid=bvid,
                        error_type='crawler_error',
                        error_message=error_msg
                    )
                )()
            except Exception as log_e:
                print(f"保存错误日志失败: {log_e}")
            return result
        try:
            crawler = BiliCrawler(credential_dict)
            video.status = 'processing'
            await sync_to_async(video.save)(update_fields=['status'])
            comments = await crawler.fetch_comments_incremental(bvid)
            await crawler.save_comments_to_db(comments, video)

            # 批量下载评论图片
            if comments:
                await crawler.batch_download_comment_images(comments, video)

            video.status = 'completed'
            # 修复：正确包装 filter().count() 整个同步操作，移除多余的调用，避免coroutine调用错误
            video.comment_count = await sync_to_async(lambda: CommentInfo.objects.filter(video=video).count())()
            video.last_crawl_time = timezone.now()
            await sync_to_async(video.save)()
            result['success'] = True
            result['comments_added'] = len(comments)
            crawler.print_stats()
        except Exception as e:
            error_msg = str(e)
            result['error'] = error_msg
            # 保存错误日志
            try:
                await sync_to_async(
                    lambda: SystemErrorLog.objects.create(
                        user=user,
                        bvid=bvid,
                        error_type='crawler_error',
                        error_message=error_msg
                    )
                )()
            except Exception as log_e:
                print(f"保存错误日志失败: {log_e}")
            video.status = 'failed'
            video.error_message = error_msg
            await sync_to_async(video.save)()
        return result


# 同步入口（供后台线程调用）
def sync_crawl_video(user, bvid: str, force_full: bool = False) -> dict:
    return asyncio.run(CrawlerService.crawl_video_async(user, bvid, force_full))


def sync_recrawl_comments(user, bvid: str) -> dict:
    return asyncio.run(CrawlerService.recrawl_comments_async(user, bvid))