"""
管理员情感分析管理服务
提供：补全未预测数据、全量重预测、图表重生成等功能
"""
from typing import Optional, Dict, Any
from django.db.models import Q
from asgiref.sync import sync_to_async
# 导入预测和分析的核心逻辑
from .predict_comments import predict_batch, BATCH_SIZE
from .sentiment_analyzer import SingleVideoAnalyzer
from ..models import CommentInfo, VideoInfo, VideoAnalysis, SystemErrorLog


class SentimentAdminService:
    """情感分析管理员服务，所有方法都支持异步调用，可直接在views中使用"""

    @staticmethod
    async def predict_unpredicted(bvid: Optional[str] = None) -> Dict[str, Any]:
        """
        只对还没有预测过的评论进行预测（自动补全旧数据）
        :param bvid: 可选，指定BV号，不指定则处理全库
        :return: 处理结果统计
        """
        try:
            # 筛选未预测的评论
            query = CommentInfo.objects.filter(Q(coarse_sentiment__isnull=True) | Q(coarse_sentiment=''))
            if bvid:
                query = query.filter(bvid=bvid)

            total = await sync_to_async(query.count)()
            if total == 0:
                return {"success": True, "processed": 0, "message": "没有未预测的评论"}

            # 批量处理
            comments = await sync_to_async(list)(query.all())
            processed = 0

            for start in range(0, total, BATCH_SIZE):
                batch = comments[start:start + BATCH_SIZE]
                # 批量预测
                predictions = await sync_to_async(predict_batch)(batch)
                # 更新数据库
                for comment, pred in zip(batch, predictions):
                    comment.coarse_sentiment = pred['coarse_sentiment']
                    comment.fine_sentiment = pred['fine_sentiment']
                    comment.sentiment_score = pred['sentiment_score']
                await sync_to_async(
                    lambda: CommentInfo.objects.bulk_update(
                        batch,
                        ['coarse_sentiment', 'fine_sentiment', 'sentiment_score']
                    )
                )()
                processed += len(batch)

            return {
                "success": True,
                "processed": processed,
                "message": f"成功补全 {processed} 条未预测的评论"
            }
        except Exception as e:
            # 记录错误日志
            await sync_to_async(SystemErrorLog.objects.create)(
                error_type="sentiment_predict_error",
                error_message=f"补全未预测评论失败: {str(e)}",
                bvid=bvid
            )
            return {"success": False, "error": str(e), "message": "补全未预测评论失败"}

    @staticmethod
    async def re_predict_all(bvid: Optional[str] = None) -> Dict[str, Any]:
        """
        对所有评论重新预测（覆盖旧的预测结果）
        :param bvid: 可选，指定BV号，不指定则处理全库
        :return: 处理结果统计
        """
        try:
            # 筛选所有评论
            query = CommentInfo.objects.all()
            if bvid:
                query = query.filter(bvid=bvid)

            total = await sync_to_async(query.count)()
            if total == 0:
                return {"success": True, "processed": 0, "message": "没有评论数据"}

            # 批量处理
            comments = await sync_to_async(list)(query.all())
            processed = 0

            for start in range(0, total, BATCH_SIZE):
                batch = comments[start:start + BATCH_SIZE]
                # 批量预测
                predictions = await sync_to_async(predict_batch)(batch)
                # 更新数据库
                for comment, pred in zip(batch, predictions):
                    comment.coarse_sentiment = pred['coarse_sentiment']
                    comment.fine_sentiment = pred['fine_sentiment']
                    comment.sentiment_score = pred['sentiment_score']
                await sync_to_async(
                    lambda: CommentInfo.objects.bulk_update(
                        batch,
                        ['coarse_sentiment', 'fine_sentiment', 'sentiment_score']
                    )
                )()
                processed += len(batch)

            return {
                "success": True,
                "processed": processed,
                "message": f"成功重新预测 {processed} 条评论"
            }
        except Exception as e:
            await sync_to_async(SystemErrorLog.objects.create)(
                error_type="sentiment_repredict_error",
                error_message=f"全量重预测失败: {str(e)}",
                bvid=bvid
            )
            return {"success": False, "error": str(e), "message": "全量重预测失败"}

    @staticmethod
    async def regenerate_charts(bvid: Optional[str] = None) -> Dict[str, Any]:
        """
        重新生成分析图表
        :param bvid: 可选，指定BV号，不指定则处理全库
        :return: 处理结果统计
        """
        try:
            # 筛选要处理的视频
            query = VideoInfo.objects.all()
            if bvid:
                query = query.filter(bvid=bvid)

            videos = await sync_to_async(list)(query.all())
            processed = 0
            failed = 0

            for video in videos:
                try:
                    # 重新生成这个视频的所有图表
                    def run_analysis():
                        analyzer = SingleVideoAnalyzer(bvid=video.bvid)
                        analyzer.load_data()
                        analyzer.plot_overall_sentiment()
                        analyzer.plot_time_evolution()
                        analyzer.plot_interaction_correlation()
                        analyzer.plot_user_feature_cross()
                        analyzer.plot_wordcloud()
                        return analyzer

                    await sync_to_async(run_analysis)()

                    # 更新分析记录
                    analysis, created = await sync_to_async(VideoAnalysis.objects.get_or_create)(video=video)
                    # 更新图表路径
                    analysis.chart_coarse_pie = f"analysis/{video.bvid}/coarse_pie.html"
                    analysis.chart_fine_bar = f"analysis/{video.bvid}/fine_bar.html"
                    analysis.chart_coarse_cum_count = f"analysis/{video.bvid}/coarse_cum_count.html"
                    analysis.chart_coarse_cum_like = f"analysis/{video.bvid}/coarse_cum_like.html"
                    analysis.chart_fine_cum_count = f"analysis/{video.bvid}/fine_cum_count.html"
                    analysis.chart_fine_cum_like = f"analysis/{video.bvid}/fine_cum_like.html"
                    analysis.chart_dual_axis = f"analysis/{video.bvid}/comment_neg_rate.html"
                    analysis.chart_wordcloud = f"analysis/{video.bvid}/wordcloud.html"
                    analysis.chart_fine_like_bar = f"analysis/{video.bvid}/fine_like_bar.html"
                    analysis.chart_hot_vs_normal = f"analysis/{video.bvid}/hot_vs_normal.html"
                    analysis.chart_region_sentiment = f"analysis/{video.bvid}/region_sentiment.html"
                    analysis.chart_image_vs_text = f"analysis/{video.bvid}/image_vs_text.html"
                    analysis.chart_level_sentiment = f"analysis/{video.bvid}/level_sentiment.html"
                    analysis.status = 'completed'
                    analysis.total_comments = await sync_to_async(
                        lambda: CommentInfo.objects.filter(video=video).count()
                    )()
                    await sync_to_async(analysis.save)()

                    processed += 1
                except Exception as e:
                    # 单个视频失败不影响其他的
                    await sync_to_async(SystemErrorLog.objects.create)(
                        error_type="chart_regenerate_error",
                        error_message=f"视频 {video.bvid} 图表生成失败: {str(e)}",
                        bvid=video.bvid
                    )
                    failed += 1
                    continue

            return {
                "success": True,
                "processed": processed,
                "failed": failed,
                "message": f"成功重新生成 {processed} 个视频的图表，失败 {failed} 个"
            }
        except Exception as e:
            await sync_to_async(SystemErrorLog.objects.create)(
                error_type="chart_regenerate_error",
                error_message=f"批量重生成图表失败: {str(e)}",
                bvid=bvid
            )
            return {"success": False, "error": str(e), "message": "批量重生成图表失败"}