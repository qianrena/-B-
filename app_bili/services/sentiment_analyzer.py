"""
单视频评论情感分析服务（pyecharts 优化版）
- 保留图表：1.1, 1.2, 粗粒度累计数量堆叠面积图×2, 细粒度累计数量堆叠面积图×2, 2.3, 3.2, 3.3, 4.1, 4.2, 4.3, 5.2(全评论词云)
- 移除图表：1.3, 1.4, 2.1, 2.2, 2.4, 3.1, 3.4, 4.4, 5.1
"""

import re
import jieba
import pandas as pd
import numpy as np
from pathlib import Path
from collections import Counter
from typing import Optional, Dict, Any

from django.conf import settings
from ..models import CommentInfo, VideoInfo, VideoAnalysis

from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Pie, WordCloud
from pyecharts.globals import ThemeType, SymbolType


class SingleVideoAnalyzer:
    COARSE_ORDER = ['positive', 'neutral', 'negative']
    COARSE_NAMES = ['正面', '中性', '负面']
    FINE_ORDER = ['happiness', 'anger', 'sadness', 'fear', 'surprise', 'neutral']
    FINE_NAMES = ['开心', '愤怒', '悲伤', '恐惧', '惊讶', '中性']
    COLORS = {
        'positive': '#2ecc71', 'neutral': '#95a5a6', 'negative': '#e74c3c',
        'happiness': '#2ecc71', 'anger': '#e74c3c', 'sadness': '#3498db',
        'fear': '#9b59b6', 'surprise': '#f1c40f', 'neutral': '#95a5a6'
    }

    def __init__(self, bvid: str, output_dir: Optional[str] = None):
        self.bvid = bvid
        self.df = None
        self.video_info = None
        self.output_dir = Path(output_dir) if output_dir else Path(settings.MEDIA_ROOT) / 'analysis' / bvid
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 数据加载 ----------
    def load_data(self) -> pd.DataFrame:
        try:
            self.video_info = VideoInfo.objects.get(bvid=self.bvid)
        except VideoInfo.DoesNotExist:
            self.video_info = None

        comments = CommentInfo.objects.filter(bvid=self.bvid).values(
            'rpid', 'user_name', 'user_id', 'user_level',
            'content', 'publish_time', 'like_count',
            'ip_location', 'image_count',
            'coarse_sentiment', 'fine_sentiment', 'sentiment_score'
        )
        if not comments:
            raise ValueError(f"视频 {self.bvid} 没有评论数据")

        df = pd.DataFrame.from_records(comments)

        df['comment_time'] = pd.to_datetime(df['publish_time'])
        video_publish_time = self.video_info.pubdate if (self.video_info and self.video_info.pubdate) else df['comment_time'].min()
        df['hours_since_publish'] = (df['comment_time'] - video_publish_time).dt.total_seconds() / 3600
        df['hours_bin'] = df['hours_since_publish'].apply(lambda x: min(int(x), 72))

        df['like_count'] = pd.to_numeric(df['like_count'], errors='coerce').fillna(0).astype(int)
        df['has_image'] = df['image_count'] > 0
        df['user_region'] = df['ip_location'].fillna('未知').astype(str)

        self.df = df
        print(f"已加载 {len(df)} 条评论数据")
        return df

    def _save_chart(self, chart, filename: str):
        chart.render(str(self.output_dir / filename))

    # ---------- 通用堆叠面积图生成函数（数值版） ----------
    def _make_stacked_area(self, df_hourly: pd.DataFrame, columns: list, names: list, colors: list,
                           title: str, yaxis_name: str = "累计评论数") -> Line:
        hours = df_hourly.index.tolist()
        hours_label = [f"{h}h" for h in hours]

        line = Line(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
        line.add_xaxis(hours_label)
        for col, name, color in zip(columns, names, colors):
            line.add_yaxis(
                series_name=name,
                y_axis=df_hourly[col].tolist(),
                stack="stack1",
                areastyle_opts=opts.AreaStyleOpts(opacity=0.6),
                linestyle_opts=opts.LineStyleOpts(width=1),
                label_opts=opts.LabelOpts(is_show=False),
                color=color,
            )
        line.set_global_opts(
            title_opts=opts.TitleOpts(title=title, pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            xaxis_opts=opts.AxisOpts(name="视频发布后小时数", axislabel_opts=opts.LabelOpts(rotate=30)),
            yaxis_opts=opts.AxisOpts(name=yaxis_name),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="right", item_width=20),
            tooltip_opts=opts.TooltipOpts(trigger="axis"),
        )
        return line

    # ==================== 1. 整体情感画像 ====================
    def plot_overall_sentiment(self):
        df = self.df

        # 1.1 粗粒度环形图
        coarse_counts = df['coarse_sentiment'].value_counts().reindex(self.COARSE_ORDER, fill_value=0)
        pie_data = [(self.COARSE_NAMES[i], int(coarse_counts.iloc[i])) for i in range(len(self.COARSE_ORDER))]
        pie = (
            Pie(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add("情感占比", pie_data, radius=["40%", "65%"],
                 label_opts=opts.LabelOpts(formatter="{b}\n{d}%", font_size=12))
            .set_colors([self.COLORS[c] for c in self.COARSE_ORDER])
            .set_global_opts(
                title_opts=opts.TitleOpts(title=f"{self.bvid} 粗粒度情感占比", pos_left="center", pos_top="5%",
                                          title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
                legend_opts=opts.LegendOpts(orient="vertical", pos_top="15%", pos_left="5%"),
            )
        )
        self._save_chart(pie, "1.1_coarse_pie.html")

        # 1.2 细粒度横向柱状图
        fine_counts = df['fine_sentiment'].value_counts().reindex(self.FINE_ORDER, fill_value=0)
        bar = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(self.FINE_NAMES)
            .add_yaxis("评论数量", [int(fine_counts[c]) for c in self.FINE_ORDER],
                       label_opts=opts.LabelOpts(position="right", font_size=11))
            .set_colors([self.COLORS[c] for c in self.FINE_ORDER])
            .reversal_axis()
            .set_global_opts(
                title_opts=opts.TitleOpts(title=f"{self.bvid} 细粒度情感分布", pos_left="center", pos_top="5%",
                                          title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
                xaxis_opts=opts.AxisOpts(name="评论数量"),
                yaxis_opts=opts.AxisOpts(name="情感类别"),
            )
        )
        self._save_chart(bar, "1.2_fine_bar.html")

    # ==================== 2. 时间演化分析（累计数量堆叠面积图 + 双轴图） ====================
    def plot_time_evolution(self):
        df = self.df

        # 按小时聚合各情感评论数和点赞数（累计之前的小时总量）
        hourly_counts = df.groupby('hours_bin').agg(
            total=('coarse_sentiment', 'count'),
            total_like=('like_count', 'sum')
        )
        for coarse in self.COARSE_ORDER:
            hourly_counts[f'{coarse}_count'] = df[df['coarse_sentiment'] == coarse].groupby('hours_bin').size()
            hourly_counts[f'{coarse}_like'] = df[df['coarse_sentiment'] == coarse].groupby('hours_bin')['like_count'].sum()
        for fine in self.FINE_ORDER:
            hourly_counts[f'{fine}_count'] = df[df['fine_sentiment'] == fine].groupby('hours_bin').size()
            hourly_counts[f'{fine}_like'] = df[df['fine_sentiment'] == fine].groupby('hours_bin')['like_count'].sum()
        hourly_counts = hourly_counts.fillna(0)

        # 计算累计值（cumsum）
        for coarse in self.COARSE_ORDER:
            hourly_counts[f'{coarse}_cum'] = hourly_counts[f'{coarse}_count'].cumsum()
            hourly_counts[f'{coarse}_like_cum'] = hourly_counts[f'{coarse}_like'].cumsum()
        for fine in self.FINE_ORDER:
            hourly_counts[f'{fine}_cum'] = hourly_counts[f'{fine}_count'].cumsum()
            hourly_counts[f'{fine}_like_cum'] = hourly_counts[f'{fine}_like'].cumsum()

        # 补充缺失的小时（保证X轴连续）
        all_hours = list(range(0, max(hourly_counts.index) + 1))
        hourly_counts = hourly_counts.reindex(all_hours, fill_value=0)

        # 计算每个小时的瞬时负面率
        hourly_counts['neg_rate_instant'] = hourly_counts['negative_count'] / hourly_counts['total']
        hourly_counts['neg_rate_instant_w'] = hourly_counts['negative_like'] / hourly_counts['total_like']
        # 处理除零
        hourly_counts['neg_rate_instant'] = hourly_counts['neg_rate_instant'].fillna(0)
        hourly_counts['neg_rate_instant_w'] = hourly_counts['neg_rate_instant_w'].fillna(0)


        # 对于填充的小时，累计值应与上一小时相同（前向填充）
        for col in hourly_counts.columns:
            if col.endswith('_cum') or col.endswith('_like_cum'):
                hourly_counts[col] = hourly_counts[col].replace(0, np.nan).ffill().fillna(0)

        # ----- 粗粒度累计数量堆叠面积图（评论数版）-----
        coarse_cum_cols = [f'{c}_cum' for c in self.COARSE_ORDER]
        chart_coarse_cum = self._make_stacked_area(
            hourly_counts, coarse_cum_cols, self.COARSE_NAMES,
            [self.COLORS[c] for c in self.COARSE_ORDER],
            title=f"{self.bvid} 粗粒度情感累计评论数时间演化",
            yaxis_name="累计评论数"
        )
        self._save_chart(chart_coarse_cum, "2.0_coarse_cum_count.html")

        # ----- 粗粒度累计点赞数堆叠面积图（点赞加权版）-----
        coarse_like_cum_cols = [f'{c}_like_cum' for c in self.COARSE_ORDER]
        chart_coarse_like_cum = self._make_stacked_area(
            hourly_counts, coarse_like_cum_cols, self.COARSE_NAMES,
            [self.COLORS[c] for c in self.COARSE_ORDER],
            title=f"{self.bvid} 粗粒度情感累计点赞数时间演化",
            yaxis_name="累计点赞数"
        )
        self._save_chart(chart_coarse_like_cum, "2.1_coarse_cum_like.html")

        # ----- 细粒度累计数量堆叠面积图（评论数版）-----
        fine_cum_cols = [f'{f}_cum' for f in self.FINE_ORDER]
        chart_fine_cum = self._make_stacked_area(
            hourly_counts, fine_cum_cols, self.FINE_NAMES,
            [self.COLORS[f] for f in self.FINE_ORDER],
            title=f"{self.bvid} 细粒度情感累计评论数时间演化",
            yaxis_name="累计评论数"
        )
        self._save_chart(chart_fine_cum, "2.2_fine_cum_count.html")

        # ----- 细粒度累计点赞数堆叠面积图（点赞加权版）-----
        fine_like_cum_cols = [f'{f}_like_cum' for f in self.FINE_ORDER]
        chart_fine_like_cum = self._make_stacked_area(
            hourly_counts, fine_like_cum_cols, self.FINE_NAMES,
            [self.COLORS[f] for f in self.FINE_ORDER],
            title=f"{self.bvid} 细粒度情感累计点赞数时间演化",
            yaxis_name="累计点赞数"
        )
        self._save_chart(chart_fine_like_cum, "2.3_fine_cum_like.html")

        # ----- 2.3 评论量+负面率双轴图（负面率为占比，用于舆情爆发点分析）-----
        hourly_counts['total_cum'] = hourly_counts['total'].cumsum()
        hourly_counts['neg_rate'] = hourly_counts['negative_cum'] / hourly_counts['total_cum']
        hourly_counts['neg_rate_w'] = hourly_counts['negative_like_cum'] / hourly_counts['total_like'].cumsum()
        # 处理除零
        hourly_counts['neg_rate'] = hourly_counts['neg_rate'].fillna(0)
        hourly_counts['neg_rate_w'] = hourly_counts['neg_rate_w'].fillna(0)

        hours = hourly_counts.index.tolist()
        hours_label = [f"{h}h" for h in hours]

        bar = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(hours_label)
            .add_yaxis("评论量", hourly_counts['total'].tolist(), yaxis_index=0, color="#3498db",
                       label_opts=opts.LabelOpts(is_show=False))
            .extend_axis(yaxis=opts.AxisOpts(name="负面评论占比 (%)", position="right", min_=0, max_=100))
        )
        line_neg = (
            Line()
            .add_xaxis(hours_label)
            .add_yaxis("负面率(原始)", (hourly_counts['neg_rate'] * 100).round(1).tolist(), yaxis_index=1,
                       color=self.COLORS['negative'], linestyle_opts=opts.LineStyleOpts(width=2.5),
                       label_opts=opts.LabelOpts(is_show=False))
            .add_yaxis("负面率(加权)", (hourly_counts['neg_rate_w'] * 100).round(1).tolist(), yaxis_index=1,
                       color=self.COLORS['negative'], linestyle_opts=opts.LineStyleOpts(width=2, type_="dashed"),
                       label_opts=opts.LabelOpts(is_show=False))
            .add_yaxis("负面率(瞬时)", (hourly_counts['neg_rate_instant'] * 100).round(1).tolist(), yaxis_index=1,
                       color=self.COLORS['negative'], linestyle_opts=opts.LineStyleOpts(width=3),
                       label_opts=opts.LabelOpts(is_show=False),
                       # 自动标记突变点
                       markpoint_opts=opts.MarkPointOpts(
                           data=[opts.MarkPointItem(type_="max", name="最大值")],
                           symbol_size=60
                       )
                       )
            .add_yaxis("负面率(瞬时加权)", (hourly_counts['neg_rate_instant_w'] * 100).round(1).tolist(), yaxis_index=1,
                       color="#c0392b", linestyle_opts=opts.LineStyleOpts(width=2.5, type_="dashed"),
                       label_opts=opts.LabelOpts(is_show=False))
        )
        bar.overlap(line_neg)
        bar.set_global_opts(
            title_opts=opts.TitleOpts(title="评论量与负面率时序变化", pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            xaxis_opts=opts.AxisOpts(name="视频发布后小时数", axislabel_opts=opts.LabelOpts(rotate=30)),
            yaxis_opts=opts.AxisOpts(name="评论量"),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="center", item_width=20),
        )
        self._save_chart(bar, "2.4_comment_neg_rate.html")

    # ==================== 3. 互动关联分析 ====================
    def plot_interaction_correlation(self):
        df = self.df

        # 3.2 细粒度平均点赞柱状图
        fine_like = df.groupby('fine_sentiment')['like_count'].mean().reindex(self.FINE_ORDER, fill_value=0)
        bar = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(self.FINE_NAMES)
            .add_yaxis("平均点赞数", [round(v, 1) for v in fine_like.values],
                       label_opts=opts.LabelOpts(position="top", font_size=11))
            .set_colors([self.COLORS[c] for c in self.FINE_ORDER])
            .set_global_opts(
                title_opts=opts.TitleOpts(title="不同细粒度情感的平均点赞数", pos_left="center", pos_top="5%",
                                          title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
                xaxis_opts=opts.AxisOpts(name="情感类别", axislabel_opts=opts.LabelOpts(rotate=30)),
                yaxis_opts=opts.AxisOpts(name="平均点赞数"),
            )
        )
        self._save_chart(bar, "3.2_fine_like_bar.html")

        # 3.3 热评vs普通评论情感对比
        threshold = df['like_count'].quantile(0.9)
        df['is_hot'] = df['like_count'] >= threshold
        hot_coarse = df.groupby('is_hot')['coarse_sentiment'].value_counts(normalize=True).unstack(fill_value=0)
        hot_coarse = hot_coarse.reindex(columns=self.COARSE_ORDER, fill_value=0)
        categories = ['普通评论', '热评(前10%)']
        bar_stack = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(categories)
        )
        for i, coarse in enumerate(self.COARSE_ORDER):
            bar_stack.add_yaxis(
                self.COARSE_NAMES[i],
                [(hot_coarse.loc[False, coarse] * 100).round(1), (hot_coarse.loc[True, coarse] * 100).round(1)],
                stack="total",
                label_opts=opts.LabelOpts(formatter="{c}%", position="inside", font_size=11),
            )
        bar_stack.set_colors([self.COLORS[c] for c in self.COARSE_ORDER])
        bar_stack.set_global_opts(
            title_opts=opts.TitleOpts(title="热评 vs 普通评论情感对比", pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            yaxis_opts=opts.AxisOpts(name="占比 (%)", max_=100),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="right"),
        )
        self._save_chart(bar_stack, "3.3_hot_vs_normal.html")

    # ==================== 4. 用户特征交叉 ====================
    def plot_user_feature_cross(self):
        df = self.df

        # 4.1 地区情感占比（Top10）
        top_regions = df['user_region'].value_counts().nlargest(10).index
        region_df = df[df['user_region'].isin(top_regions)]
        region_coarse = region_df.groupby('user_region')['coarse_sentiment'].value_counts(normalize=True).unstack(fill_value=0)
        region_coarse = region_coarse.reindex(columns=self.COARSE_ORDER, fill_value=0)
        regions = region_coarse.index.tolist()
        bar_stack = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(regions)
        )
        for i, coarse in enumerate(self.COARSE_ORDER):
            bar_stack.add_yaxis(
                self.COARSE_NAMES[i],
                [(region_coarse.loc[r, coarse] * 100).round(1) for r in regions],
                stack="region",
                label_opts=opts.LabelOpts(is_show=False),
            )
        bar_stack.set_colors([self.COLORS[c] for c in self.COARSE_ORDER])
        bar_stack.set_global_opts(
            title_opts=opts.TitleOpts(title="不同地区用户情感占比（Top10）", pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            xaxis_opts=opts.AxisOpts(axislabel_opts=opts.LabelOpts(rotate=45, font_size=10)),
            yaxis_opts=opts.AxisOpts(name="占比 (%)", max_=100),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="right"),
        )
        self._save_chart(bar_stack, "4.1_region_sentiment.html")

        # 4.2 带图 vs 纯文本
        img_coarse = df.groupby('has_image')['coarse_sentiment'].value_counts(normalize=True).unstack(fill_value=0)
        img_coarse = img_coarse.reindex(columns=self.COARSE_ORDER, fill_value=0)
        categories = ['纯文本', '带图片']
        bar_stack = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(categories)
        )
        for i, coarse in enumerate(self.COARSE_ORDER):
            bar_stack.add_yaxis(
                self.COARSE_NAMES[i],
                [(img_coarse.loc[False, coarse] * 100).round(1), (img_coarse.loc[True, coarse] * 100).round(1)],
                stack="img",
                label_opts=opts.LabelOpts(formatter="{c}%", position="inside", font_size=11),
            )
        bar_stack.set_colors([self.COLORS[c] for c in self.COARSE_ORDER])
        bar_stack.set_global_opts(
            title_opts=opts.TitleOpts(title="带图 vs 纯文本评论情感对比", pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            yaxis_opts=opts.AxisOpts(name="占比 (%)", max_=100),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="right"),
        )
        self._save_chart(bar_stack, "4.2_image_vs_text.html")

        # 4.3 用户等级情感占比
        top_levels = df['user_level'].value_counts().nlargest(6).index
        level_df = df[df['user_level'].isin(top_levels)]
        level_coarse = level_df.groupby('user_level')['coarse_sentiment'].value_counts(normalize=True).unstack(fill_value=0)
        level_coarse = level_coarse.reindex(columns=self.COARSE_ORDER, fill_value=0)
        levels = [str(lv) for lv in level_coarse.index]
        bar_stack = (
            Bar(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="400px"))
            .add_xaxis(levels)
        )
        for i, coarse in enumerate(self.COARSE_ORDER):
            bar_stack.add_yaxis(
                self.COARSE_NAMES[i],
                [(level_coarse.loc[int(lv), coarse] * 100).round(1) for lv in levels],
                stack="level",
                label_opts=opts.LabelOpts(is_show=False),
            )
        bar_stack.set_colors([self.COLORS[c] for c in self.COARSE_ORDER])
        bar_stack.set_global_opts(
            title_opts=opts.TitleOpts(title="不同用户等级情感占比", pos_left="center", pos_top="5%",
                                      title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            xaxis_opts=opts.AxisOpts(name="用户等级"),
            yaxis_opts=opts.AxisOpts(name="占比 (%)", max_=100),
            legend_opts=opts.LegendOpts(pos_top="12%", pos_left="right"),
        )
        self._save_chart(bar_stack, "4.3_level_sentiment.html")

    # ==================== 5. 词云 ====================
    def plot_wordcloud(self):
        """全评论词云"""
        df = self.df
        text = ' '.join(df['content'].astype(str).tolist())
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\d+', '', text)
        words = list(jieba.cut(text))
        stopwords = set(['的', '了', '是', '我', '都', '就', '也', '在', '有', '和', '这', '那', '你', '他', '她', '它', '们', '个', '么'])
        words = [w for w in words if len(w) > 1 and w not in stopwords]
        word_freq = Counter(words).most_common(100)

        wc = (
            WordCloud(init_opts=opts.InitOpts(theme=ThemeType.LIGHT, width="100%", height="500px"))
            .add("", word_freq, word_size_range=[20, 80], shape=SymbolType.DIAMOND)
            .set_global_opts(
                title_opts=opts.TitleOpts(title="全评论高频词云", pos_left="center", pos_top="5%",
                                          title_textstyle_opts=opts.TextStyleOpts(font_size=16, font_weight="bold")),
            )
        )
        self._save_chart(wc, "5.2_all_wordcloud.html")

    # ---------- 主入口 ----------
    def run_analysis(self) -> Dict[str, Any]:
        self.load_data()
        self.plot_overall_sentiment()
        self.plot_time_evolution()
        self.plot_interaction_correlation()
        self.plot_user_feature_cross()
        self.plot_wordcloud()
        self._save_analysis_record()
        print(f"\n分析完成！图表已保存至 {self.output_dir}")
        return self.get_summary()

    def _save_analysis_record(self):
        summary = self.get_summary()
        coarse_pct = summary['coarse_distribution']
        rel_dir = str(self.output_dir.relative_to(settings.MEDIA_ROOT))
        VideoAnalysis.objects.update_or_create(
            video=self.video_info,
            defaults={
                'status': 'completed',
                'total_comments': summary['total_comments'],
                'pos_rate': coarse_pct.get('positive', 0),
                'neu_rate': coarse_pct.get('neutral', 0),
                'neg_rate': coarse_pct.get('negative', 0),
                'avg_like': summary['avg_like'],
                'fine_distribution': summary['fine_distribution'],
                'output_dir': rel_dir,
                'chart_coarse_pie': f"{rel_dir}/1.1_coarse_pie.html",
                'chart_fine_bar': f"{rel_dir}/1.2_fine_bar.html",
                'chart_coarse_cum_count': f"{rel_dir}/2.0_coarse_cum_count.html",
                'chart_coarse_cum_like': f"{rel_dir}/2.1_coarse_cum_like.html",
                'chart_fine_cum_count': f"{rel_dir}/2.2_fine_cum_count.html",
                'chart_fine_cum_like': f"{rel_dir}/2.3_fine_cum_like.html",
                'chart_dual_axis': f"{rel_dir}/2.4_comment_neg_rate.html",
                'chart_wordcloud': f"{rel_dir}/5.2_all_wordcloud.html",
                'error_message': ''
            }
        )

    def get_summary(self) -> Dict[str, Any]:
        df = self.df
        total = len(df)
        coarse_pct = (df['coarse_sentiment'].value_counts(normalize=True) * 100).to_dict()
        fine_pct = (df['fine_sentiment'].value_counts(normalize=True) * 100).to_dict()
        return {
            'bvid': self.bvid,
            'total_comments': total,
            'coarse_distribution': coarse_pct,
            'fine_distribution': fine_pct,
            'avg_like': df['like_count'].mean(),
            'neg_rate': coarse_pct.get('negative', 0),
            'output_dir': str(self.output_dir)
        }