# stats.py
import json
import os
from datetime import datetime, timedelta

USERS_FILE = "users.json"

def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_users(users: dict):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def get_plan_limit(plan: str) -> int:
    limits = {'free': 3, 'premium': 20, 'professional': 999}
    return limits.get(plan, 3)

def reset_if_needed(user_id: int):
    users = load_users()
    user_key = str(user_id)
    if user_key not in users:
        return

    user = users[user_key]
    last_reset_str = user.get('last_reset')
    if not last_reset_str:
        user['last_reset'] = datetime.now().isoformat()
        user['downloads_today'] = 0
        save_users(users)
        return

    last_reset = datetime.fromisoformat(last_reset_str)
    now = datetime.now()

    if (now - last_reset) >= timedelta(days=1):
        user['downloads_today'] = 0
        user['last_reset'] = now.isoformat()
        save_users(users)

def get_user_stats(user_id: int) -> dict:
    """
    منبع حقیقت واحد برای آمار کاربر
    """
    reset_if_needed(user_id)
    users = load_users()
    user_key = str(user_id)
    if user_key not in users:
        return {
            'plan': 'free',
            'downloads_today': 0,
            'remaining_today': 3,
            'downloads_total': 0,
            'username': f"User_{user_id}"
        }

    user = users[user_key]
    plan = user.get('plan', 'free')
    limit = get_plan_limit(plan)
    # اطمینان از اینکه downloads_today منفی نشود
    downloads_today = max(0, user.get('downloads_today', 0))
    remaining = max(0, limit - downloads_today)

    return {
        'plan': plan,
        'downloads_today': downloads_today,
        'remaining_today': remaining,
        'downloads_total': user.get('downloads_total', 0),
        'username': user.get('username', f"User_{user_id}")
    }

def increment_daily_download(user_id: int):
    """
    فقط بعد از دانلود موفق فراخوانی شود
    """
    reset_if_needed(user_id)
    users = load_users()
    user_key = str(user_id)

    # اگر کاربر وجود نداشت، یک رکورد اولیه می‌سازیم
    if user_key not in users:
        now_iso = datetime.now().isoformat()
        users[user_key] = {
            'plan': 'free',
            'downloads_today': 0,
            'downloads_total': 0,
            'last_reset': now_iso,
            'username': f"User_{user_id}",
            'ai_used_count': 0,
            'ai_window_start_time': now_iso,
        }

    user = users[user_key]
    plan = user.get('plan', 'free')
    limit = get_plan_limit(plan)

    # نرمال‌سازی مقدار فعلی
    current = max(0, user.get('downloads_today', 0))

    # اگر همین حالا هم به سقف رسیده‌ایم، دیگر افزایش نمی‌دهیم
    if current >= limit:
        user['downloads_today'] = current
    else:
        user['downloads_today'] = current + 1
        user['downloads_total'] = user.get('downloads_total', 0) + 1

    users[user_key] = user
    save_users(users)

def can_user_download(user_id: int) -> tuple[bool, int, int]:
    """
    بررسی امکان دانلود
    بازگشت: (can_download: bool, current: int, limit: int)
    """
    stats = get_user_stats(user_id)
    limit = get_plan_limit(stats['plan'])
    current = max(0, stats['downloads_today'])
    # اگر current >= limit باشد، اجازه دانلود نداریم و current را همواره در بازه [0, limit] می‌بینیم
    return (
        current < limit,
        current,
        limit
    )

def reset_ai_limit_if_needed(user_id: int):
    """
    بررسی و بازنشانی محدودیت AI در صورت نیاز (هر 5 ساعت)
    """
    users = load_users()
    user_key = str(user_id)
    if user_key not in users:
        return
    
    user = users[user_key]
    ai_window_start_str = user.get('ai_window_start_time')
    
    # اگر فیلد وجود نداشت، آن را ایجاد می‌کنیم
    if not ai_window_start_str:
        now = datetime.now()
        user['ai_window_start_time'] = now.isoformat()
        user['ai_used_count'] = 0
        save_users(users)
        return
    
    ai_window_start = datetime.fromisoformat(ai_window_start_str)
    now = datetime.now()
    
    # اگر 5 ساعت گذشته باشد، بازنشانی می‌کنیم
    if (now - ai_window_start) >= timedelta(hours=5):
        user['ai_used_count'] = 0
        user['ai_window_start_time'] = now.isoformat()
        save_users(users)

def check_ai_support_limit(user_id: int) -> tuple[bool, int, int, str | None]:
    """
    بررسی محدودیت پشتیبانی هوشمند برای کاربر
    فقط برای کاربران FREE اعمال می‌شود
    بازگشت:
        can_use: آیا کاربر می‌تواند از پشتیبانی هوشمند استفاده کند؟
        current_count: تعداد استفاده‌های فعلی در بازه ۵ ساعته
        limit: سقف مجاز استفاده در هر بازه
        reset_time_iso: زمان ریست محدودیت به صورت ISO (رشته) یا None
    """
    users = load_users()
    user_key = str(user_id)
    
    # اگر کاربر وجود نداشت، اجازه می‌دهیم (کاربر جدید)
    if user_key not in users:
        return (True, 0, 10, None)
    
    user = users[user_key]
    plan = user.get('plan', 'free').lower()
    
    # اگر پلن FREE نیست، محدودیتی نداریم
    if plan != 'free':
        return (True, 0, 999999, None)
    
    # برای کاربران FREE، محدودیت را بررسی می‌کنیم
    reset_ai_limit_if_needed(user_id)
    
    # دوباره بارگذاری می‌کنیم چون ممکن است reset شده باشد
    users = load_users()
    user = users[user_key]
    
    ai_used_count = user.get('ai_used_count', 0)
    limit = 10

    # محاسبه زمان ریست بعدی بر اساس زمان شروع پنجره ۵ ساعته
    ai_window_start_str = user.get('ai_window_start_time')
    reset_time_iso: str | None = None
    if ai_window_start_str:
        try:
            ai_window_start = datetime.fromisoformat(ai_window_start_str)
            reset_time = ai_window_start + timedelta(hours=5)
            reset_time_iso = reset_time.isoformat()
        except Exception:
            # در صورت خطای پارس، مقدار None برمی‌گردانیم
            reset_time_iso = None
    
    return (ai_used_count < limit, ai_used_count, limit, reset_time_iso)

def increment_ai_support_usage(user_id: int):
    """
    افزایش تعداد استفاده از پشتیبانی هوشمند
    فقط برای کاربران FREE اعمال می‌شود
    """
    users = load_users()
    user_key = str(user_id)
    
    if user_key not in users:
        return
    
    user = users[user_key]
    plan = user.get('plan', 'free').lower()
    
    # اگر پلن FREE نیست، نیازی به ثبت استفاده نیست
    if plan != 'free':
        return
    
    # بررسی و بازنشانی در صورت نیاز
    reset_ai_limit_if_needed(user_id)
    
    # دوباره بارگذاری می‌کنیم
    users = load_users()
    user = users[user_key]
    
    # افزایش تعداد استفاده
    current_count = user.get('ai_used_count', 0)
    user['ai_used_count'] = current_count + 1
    
    # اگر اولین استفاده است، زمان شروع را تنظیم می‌کنیم
    if not user.get('ai_window_start_time'):
        user['ai_window_start_time'] = datetime.now().isoformat()
    
    save_users(users)