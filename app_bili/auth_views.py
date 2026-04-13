from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_protect


@csrf_protect
def user_register(request):
    """用户注册页面及处理"""
    if request.user.is_authenticated:
        return redirect('home')  # 已登录则跳转

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')
        email = request.POST.get('email', '').strip()

        # 校验
        if not username or not password:
            messages.error(request, '用户名和密码不能为空')
        elif len(username) < 3:
            messages.error(request, '用户名长度至少为3个字符')
        elif len(password) < 6:
            messages.error(request, '密码长度至少为6个字符')
        elif password != confirm_password:
            messages.error(request, '两次输入的密码不一致')
        elif email:
            try:
                validate_email(email)
            except ValidationError:
                messages.error(request, '邮箱格式不正确')
        elif User.objects.filter(username=username).exists():
            messages.error(request, '用户名已存在')
        elif email and User.objects.filter(email=email).exists():
            messages.error(request, '邮箱已被注册')
        else:
            # 创建用户
            user = User.objects.create_user(username=username, password=password, email=email)
            # 自动登录
            login(request, user)
            messages.success(request, '注册成功，已自动登录')
            return redirect('home')

        # 校验失败返回注册页
        return render(request, 'register.html', {
            'username': username,
            'email': email,
        })

    # GET 请求显示注册表单
    return render(request, 'register.html')


@csrf_protect
def user_login(request):
    """用户登录页面及处理"""
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f'欢迎回来，{username}！')
            # 获取 next 参数，实现登录后跳转回原页面
            next_url = request.GET.get('next', 'home')
            return redirect(next_url)
        else:
            messages.error(request, '用户名或密码错误')
            return render(request, 'login.html', {'username': username})

    return render(request, 'login.html')


def user_logout(request):
    """用户登出"""
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, '已成功登出')
    return redirect('user_login')