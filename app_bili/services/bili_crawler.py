"""
B站舆情分析爬虫 - 异步版本
使用 bilibili-api-python 原生异步 API
所有数据库操作均通过 sync_to_async 包装，避免异步上下文错误
API文档: https://nemo2011.github.io/bilibili-api/#/
"""
import asyncio
import hashlib
import datetime
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
import requests
from asgiref.sync import sync_to_async
from bilibili_api import Credential
from bilibili_api.video import Video
from bilibili_api.user import User
from bilibili_api.comment import CommentResourceType, OrderType, get_comments_lazy
from django.conf import settings
from django.db.models import Max
from ..models import CommentInfo, VideoAnalysis, VideoInfo
# from clean_comments import clean_text  # 导入数据清洗函数，去除全角符号
from .clean_comments import clean_text
from .predict_comments import predict_batch


# 辅助函数：异步指数退避重试
async def retry_async(func, max_retries=3, base_delay=1.0, backoff=2, exceptions=(Exception,)):
    """异步函数指数退避重试"""
    for attempt in range(max_retries):
        try:
            return await func()
        except exceptions as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (backoff ** attempt) + random.uniform(0, 0.5)
            print(f"请求失败，{delay:.2f}秒后重试 (尝试 {attempt+1}/{max_retries})... 错误: {e}")
            await asyncio.sleep(delay)
    raise RuntimeError("Unreachable")


# 同步 ORM 操作包装为异步
@sync_to_async
def get_existing_max_timestamp(bvid: str) -> int:
    max_ts = CommentInfo.objects.filter(bvid=bvid).aggregate(max_ts=Max('timestamp'))['max_ts']
    return max_ts or 0


@sync_to_async
def bulk_create_comments(comment_objs):
    """批量创建评论对象"""
    if comment_objs:
        CommentInfo.objects.bulk_create(comment_objs, ignore_conflicts=True)


@sync_to_async
def get_video_comment_count(video) -> int:
    return CommentInfo.objects.filter(video=video).count()


# 封面下载器
class CoverDownloader:
    """封面下载器（同步，但可以在异步中调用）"""
    def __init__(self):
        self.save_dir = Path(settings.MEDIA_ROOT) / 'covers'
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/"
        })

    def download(self, url: str, bvid: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=10, stream=True)
            resp.raise_for_status()
            ext = self._get_ext(url) or 'jpg'
            filename = f"{bvid}.{ext}"
            filepath = self.save_dir / filename
            if not filepath.exists():
                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
            return str(filepath.relative_to(settings.MEDIA_ROOT))
        except Exception as e:
            print(f"封面下载失败 {bvid}: {e}")
            return None

    @staticmethod
    def _get_ext(url: str) -> str:
        path = urlparse(url).path.lower()
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            if path.endswith(ext):
                return ext[1:]
        return 'jpg'


# 评论图片下载器
class ImageDownloader:
    def __init__(self, max_retries: int = 3, delay: float = 0.5):
        self.base_dir = Path(settings.MEDIA_ROOT) / 'images'
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/"
        })
        self.downloaded_md5: Set[str] = set()
        self.stats = {"total": 0, "duplicate": 0, "error": 0}

    def _get_file_ext(self, url: str) -> str:
        path = urlparse(url).path.lower()
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
            if path.endswith(ext):
                return ext[1:] if ext.startswith('.') else ext
        return 'jpg'

    def _save_by_md5(self, data: bytes, md5_hash: str, ext: str) -> str:
        dir1 = md5_hash[:2]
        dir2 = md5_hash[2:4]
        save_dir = self.base_dir / dir1 / dir2
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{md5_hash}.{ext}"
        filepath = save_dir / filename
        if not filepath.exists():
            filepath.write_bytes(data)
        return str(filepath.relative_to(settings.MEDIA_ROOT))

    def download(self, url: str, timeout: int = 10) -> Tuple[Optional[str], Optional[str]]:
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, timeout=timeout, stream=True)
                resp.raise_for_status()
                ctype = resp.headers.get('content-type', '').lower()
                if not any(t in ctype for t in ['image', 'octet-stream']):
                    self.stats["error"] += 1
                    return None, None
                data = resp.content
                md5 = hashlib.md5(data).hexdigest()
                if md5 in self.downloaded_md5:
                    self.stats["duplicate"] += 1
                    return md5, None
                ext = self._get_file_ext(url)
                rel_path = self._save_by_md5(data, md5, ext)
                self.downloaded_md5.add(md5)
                self.stats["total"] += 1
                return md5, rel_path
            except Exception as e:
                if attempt == self.max_retries - 1:
                    self.stats["error"] += 1
                    print(f"图片下载失败 {url}: {e}")
                    return None, None
                time.sleep(self.delay * (2 ** attempt))
        return None, None

    def get_stats(self) -> Dict:
        return self.stats.copy()


# 主爬虫类（异步）
class BiliCrawler:
    def __init__(self, credential_dict: dict):
        sessdata = credential_dict.get('sessdata', '')
        bili_jct = credential_dict.get('bili_jct', '')
        dedeuserid = credential_dict.get('dedeuserid', '')
        ac_time_value = credential_dict.get('ac_time_value', '')
        self.credential = Credential(sessdata=sessdata,bili_jct=bili_jct,dedeuserid=dedeuserid,ac_time_value=ac_time_value)
        self.cover_downloader = CoverDownloader()
        self.image_downloader = ImageDownloader()
        self.stats = {"comments": 0, "pages": 0, "images": 0}

    async def get_video_info(self, bvid: str) -> Optional[Dict]:
        v = Video(bvid=bvid, credential=self.credential)
        try:
            info = await v.get_info()
            tag_names = []
            try:
                tags = await v.get_tags()
                tag_names = [tag.get('tag_name', '') for tag in tags]
            except Exception as e:
                print(f"获取视频标签失败 {bvid}: {e}")

            # 清洗视频文本数据，去除全角符号
            raw_title = info.get('title', '')
            cleaned_title = clean_text(raw_title)
            cleaned_tags = ",".join([clean_text(tag) for tag in tag_names])

            return {
                'bvid': bvid,
                'aid': info.get('aid'),
                'title': cleaned_title,
                'cover_url': info.get('pic'),
                'owner_mid': info.get('owner', {}).get('mid'),
                'owner_name': info.get('owner', {}).get('name'),
                'view_count': info.get('stat', {}).get('view', 0),
                'like_count': info.get('stat', {}).get('like', 0),
                'danmaku_count': info.get('stat', {}).get('danmaku', 0),
                'reply_count': info.get('stat', {}).get('reply', 0),
                'coin_count': info.get('stat', {}).get('coin', 0),
                'share_count': info.get('stat', {}).get('share', 0),
                'favorite_count': info.get('stat', {}).get('favorite', 0),
                'pubdate': info.get('pubdate'),
                'tname': info.get('tname', ''),
                'duration': info.get('duration', 0),
                'tags': cleaned_tags,
            }
        except Exception as e:
            print(f"获取视频信息失败 {bvid}: {e}")
            return None

    async def get_up_user_info(self, uid: int) -> Optional[Dict]:
        u = User(uid=uid, credential=self.credential)
        try:
            user_info = await u.get_user_info()
            stat = user_info.get('stat', {})
            fans_count = stat.get('follower', 0)
            videos_result = await u.get_videos(ps=1)
            total_videos = videos_result.get('page', {}).get('count', 0)
            return {
                'uid': uid,
                'name': user_info.get('name'),
                'fans_count': fans_count,
                'total_videos': total_videos,
                'like_count': stat.get('likes', 0),
            }
        except Exception as e:
            print(f"获取UP主信息失败 {uid}: {e}")
            return None

    def _parse_comment(self, reply_obj, bvid: str) -> Dict:
        """解析单条评论（同步方法，因为不涉及网络，暂不下载图片）
        """
        member = reply_obj.get('member', {})
        content = reply_obj.get('content', {})
        control = reply_obj.get('reply_control', {})
        ctime = reply_obj.get('ctime', 0)
        if ctime:
            publish_time = datetime.datetime.fromtimestamp(ctime)
        else:
            publish_time = None
        pictures = content.get('pictures', [])
        image_urls = []
        for pic in pictures:
            img_url = pic.get('img_src') or pic.get('img_url')
            if img_url:
                image_urls.append(img_url)
        # 清洗评论内容，去除全角符号
        raw_content = content.get('message', '')
        cleaned_content = clean_text(raw_content)
        return {
            "rpid": str(reply_obj.get('rpid', '')),
            "bvid": bvid,
            "user_name": member.get('uname', ''),
            "user_id": member.get('mid', 0),
            "user_level": member.get('level_info', {}).get('current_level', 0),
            "content": cleaned_content,  # 使用清洗后的内容
            "publish_time": publish_time,
            "timestamp": ctime,
            "like_count": reply_obj.get('like', 0),
            "reply_count": reply_obj.get('count', 0),
            "ip_location": control.get('location', '').replace('IP属地：', ''),
            "image_urls": " | ".join(image_urls),
            "image_count": len(image_urls),
            "local_image_paths": "",  # 暂不下载，后续批量下载
        }

    async def batch_download_comment_images(self, comments: List[Dict], video):
        """批量下载评论中的图片，在所有评论保存到数据库后执行
        """
        # 遍历所有新评论，下载图片
        comment_objs_to_update = []
        for comment_data in comments:
            if not comment_data['image_urls']:
                continue
            image_urls = comment_data['image_urls'].split(' | ')
            local_paths = []
            for img_url in image_urls:
                if not img_url:
                    continue
                md5, path = self.image_downloader.download(img_url)
                if path:
                    local_paths.append(path)
                    self.stats["images"] += 1
            # 更新评论数据的local_image_paths
            comment_data['local_image_paths'] = " | ".join(local_paths)
            # 找到对应的数据库对象，准备更新
            try:
                comment_obj = await sync_to_async(CommentInfo.objects.get)(
                    rpid=comment_data['rpid'], bvid=comment_data['bvid']
                )
                comment_obj.local_image_paths = comment_data['local_image_paths']
                comment_objs_to_update.append(comment_obj)
            except CommentInfo.DoesNotExist:
                continue
        # 批量更新
        if comment_objs_to_update:
            await sync_to_async(CommentInfo.objects.bulk_update)(
                comment_objs_to_update, ['local_image_paths']
            )
        print(f"批量下载完成，共处理 {len(comment_objs_to_update)} 条评论的图片")

    async def fetch_comments_incremental(self, bvid: str, max_loops: int = None, force_full: bool = False) -> List[Dict]:
        """异步增量/全量爬取评论，使用 get_comments_lazy 生成器
        - 全量模式：忽略已有的最大时间戳，从头爬取所有评论
        - 增量模式：读取已有的最大时间戳，只爬新评论，之后更新热门评论的点赞数
        """
        if force_full:
            # 全量模式：忽略已有的最大时间戳，从头爬取所有评论
            existing_max_ts = 0
            is_first = True
        else:
            # 增量模式：读取已有的最大时间戳，只爬新评论
            existing_max_ts = await get_existing_max_timestamp(bvid)
            is_first = (existing_max_ts == 0)
        v = Video(bvid=bvid, credential=self.credential)
        aid = v.get_aid()   # 同步方法，直接调用
        new_comments = []
        offset = ""
        loop_count = 0
        stop_flag = False
        while True:
            if max_loops and loop_count >= max_loops:
                break
            if stop_flag:
                break
            print(f"正在请求评论批次 {loop_count + 1}...")
            try:
                async def fetch():
                    return await get_comments_lazy(
                        oid=aid,
                        type_=CommentResourceType.VIDEO,
                        offset=offset,
                        order=OrderType.TIME,  # 按时间排序，获取最新评论
                        credential=self.credential
                    )
                resp = await retry_async(fetch, max_retries=3, base_delay=1.0, backoff=2)
            except Exception as e:
                print(f"评论请求最终失败: {e}")
                break
            replies = resp.get('replies')
            if not replies:
                print("没有更多评论")
                break
            batch_new = []
            for reply in replies:
                ctime = reply.get('ctime', 0)
                if not is_first and ctime <= existing_max_ts:
                    print(f"遇到已存在的旧评论（时间戳 {ctime} <= {existing_max_ts}），停止爬取")
                    stop_flag = True
                    break
                comment_item = self._parse_comment(reply, bvid)
                batch_new.append(comment_item)
            new_comments.extend(batch_new)
            print(f"本批获取 {len(batch_new)} 条新评论，累计 {len(new_comments)} 条")
            self.stats["comments"] += len(batch_new)
            self.stats["pages"] += 1
            cursor = resp.get('cursor', {})
            is_end = cursor.get('is_end', True)
            if is_end or stop_flag:
                print("已到最后一页或触发停止条件")
                break
            offset = cursor.get('pagination_reply', {}).get('next_offset', '')
            if not offset:
                print("未获取到下一页 offset，停止")
                break
            loop_count += 1
            await asyncio.sleep(random.uniform(1.0, 1.5))

        # 增量模式下，更新热门评论的点赞数
        if not force_full and new_comments:
            # 计算需要更新的热门评论数量：新增数量 = new_comments * 2
            hot_update_count = len(new_comments) * 2
            print(f"增量更新完成，准备更新前 {hot_update_count} 条热门评论的点赞数...")
            try:
                # 按热度排序获取热门评论
                async def fetch_hot():
                    return await get_comments_lazy(
                        oid=aid,
                        type_=CommentResourceType.VIDEO,
                        offset="",
                        order=OrderType.LIKE,  # 按热度排序，获取热门评论
                        credential=self.credential
                    )
                hot_resp = await retry_async(fetch_hot, max_retries=3, base_delay=1.0, backoff=2)
                hot_replies = hot_resp.get('replies', [])
                # 只处理前 hot_update_count 条
                update_count = 0
                for reply in hot_replies[:hot_update_count]:
                    rpid = str(reply.get('rpid', ''))
                    new_like = reply.get('like', 0)
                    # 更新数据库中的点赞数
                    updated = await sync_to_async(
                        lambda: CommentInfo.objects.filter(rpid=rpid, bvid=bvid).update(like_count=new_like)
                    )()
                    if updated > 0:
                        update_count += 1
                print(f"热门评论点赞数更新完成，共更新 {update_count} 条")
            except Exception as e:
                print(f"更新热门评论点赞数失败: {e}，不影响主流程")


        return new_comments

    async def save_comments_to_db(self, comments: List[Dict], video):
        """异步保存评论到数据库"""
        if not comments:
            return
        comment_objs = []
        for comment_data in comments:
            comment_obj = CommentInfo(
                rpid=comment_data['rpid'],
                bvid=comment_data['bvid'],
                video=video,
                user_name=comment_data['user_name'],
                user_id=comment_data['user_id'],
                user_level=comment_data['user_level'],
                content=comment_data['content'],
                publish_time=comment_data['publish_time'],
                timestamp=comment_data['timestamp'],
                like_count=comment_data['like_count'],
                reply_count=comment_data['reply_count'],
                ip_location=comment_data['ip_location'],
                image_urls=comment_data['image_urls'],
                image_count=comment_data['image_count'],
                local_image_paths=comment_data['local_image_paths'],
            )
            comment_objs.append(comment_obj)
        await bulk_create_comments(comment_objs)
        print(f"已保存 {len(comments)} 条新评论到数据库")



    def print_stats(self):
        s = self.image_downloader.get_stats()
        print("\n========== 爬取统计 ==========")
        print(f"评论总数（本次新）: {self.stats['comments']}")
        print(f"请求页数: {self.stats['pages']}")
        print(f"图片下载总数: {s['total']}")
        print(f"图片重复数: {s['duplicate']}")
        print(f"图片失败数: {s['error']}")
        print("==============================\n")