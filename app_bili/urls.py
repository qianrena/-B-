from django.urls import path
from . import views
from . import auth_views

urlpatterns = [
    # 首页
    path('', views.home, name='home'),
    path('video/<str:bvid>/', views.video_detail, name='video_detail'),
    path('all_videos/', views.all_videos, name='all_videos'),

    # 用户相关
    path('register/', auth_views.user_register, name='user_register'),
    path('login/', auth_views.user_login, name='user_login'),
    path('logout/', auth_views.user_logout, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('toggle_favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('my_favorites/', views.my_favorites, name='my_favorites'),

    # 爬虫相关
    path('crawler_video/', views.crawler_video, name='crawler_video'),

    # 管理员相关路由
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin_crawler/', views.admin_crawler_manage, name='admin_crawler_manage'),
    path('admin_crawler/action/', views.admin_crawler_action, name='admin_crawler_action'),
    # 用户管理
    path('admin_users/', views.admin_user_list, name='admin_user_list'),
    path('admin_users/add/', views.admin_user_edit, name='admin_user_add'),
    path('admin_users/<int:user_id>/edit/', views.admin_user_edit, name='admin_user_edit'),
    path('admin_users/<int:user_id>/delete/', views.admin_user_delete, name='admin_user_delete'),
    # 视频管理
    path('admin_videos/', views.admin_video_list, name='admin_video_list'),
    path('admin_videos/<str:bvid>/edit/', views.admin_video_edit, name='admin_video_edit'),
    path('admin_videos/<str:bvid>/delete/', views.admin_video_delete, name='admin_video_delete'),
    # 评论管理
    path('admin_comments/', views.admin_comment_list, name='admin_comment_list'),
    path('admin_comments/<int:comment_id>/edit/', views.admin_comment_edit, name='admin_comment_edit'),
    path('admin_comments/<int:comment_id>/delete/', views.admin_comment_delete, name='admin_comment_delete'),
    # 错误日志
    path('admin_errors/', views.admin_error_log_list, name='admin_error_log_list'),
    path('admin_errors/<int:log_id>/mark_read/', views.admin_error_log_mark_read, name='admin_error_log_mark_read'),
    path('admin_errors/<int:log_id>/delete/', views.admin_error_log_delete, name='admin_error_log_delete'),

]
