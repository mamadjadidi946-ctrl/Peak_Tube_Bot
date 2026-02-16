# bot.py - نسخه کامل با اعمال تغییرات منوی خرید اشتراک ویژه (VIP) و مدیریت مالی هوشمند رسیدها
# + پشتیبانی هوشمند با OpenRouter

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
import yt_dlp
import os
import logging
import traceback
import time
import json
from datetime import datetime, timedelta
import glob
import subprocess  # برای هاردساب (فقط برای زیرنویس رسمی حرفه‌ای)
import httpx  # برای پشتیبانی هوشمند
import re  # برای تشخیص user_id و PLAN در کپشن رسید
import asyncio  # برای تأخیر در UX پشتیبانی
import uuid  # برای تولید توکن لینک مستقیم
import secrets  # برای تولید توکن امن

# وارد کردن توابع آمار — منبع حقیقت واحد
from stats import get_user_stats, increment_daily_download, can_user_download as _can_user_download, get_plan_limit, check_ai_support_limit, increment_ai_support_usage

# Wrapper function برای can_user_download که از is_payments_enabled() استفاده می‌کند
def can_user_download(user_id: int) -> tuple[bool, int, int]:
    """
    بررسی امکان دانلود با در نظر گیری وضعیت سیستم پولی
    اگر سیستم پولی غیرفعال باشد، همیشه True برمی‌گرداند
    """
    if not is_payments_enabled():
        # اگر سیستم پولی غیرفعال است، کاربر همیشه می‌تواند دانلود کند
        return (True, 0, 999999)
    return _can_user_download(user_id)

def get_max_quality_allowed(user_plan: str) -> int:
    """
    دریافت حداکثر کیفیت مجاز بر اساس پلن کاربر
    اگر سیستم پولی غیرفعال باشد، همیشه 99999 برمی‌گرداند
    """
    if not is_payments_enabled():
        return 99999
    return {'free': 480, 'premium': 1080, 'professional': 99999}.get(user_plan, 480)

def is_plan_feature_locked(user_plan: str, feature: str = 'subtitle') -> bool:
    """
    بررسی قفل بودن یک قابلیت برای پلن کاربر
    اگر سیستم پولی غیرفعال باشد، همیشه False برمی‌گرداند (قفل نیست)
    """
    if not is_payments_enabled():
        return False
    # در حالت فعال، فقط free plan قفل دارد
    return user_plan == 'free'

# ====================== تنظیمات پیشرفته Logging ======================
class FuncNameFilter(logging.Filter):
    def filter(self, record):
        record.funcName = record.funcName if record.funcName != '<module>' else 'main'
        return True

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] - %(funcName)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addFilter(FuncNameFilter())

class TracebackFilter(logging.Filter):
    def filter(self, record):
        if record.levelno >= logging.ERROR and record.exc_info:
            record.exc_text = ''.join(traceback.format_exception(*record.exc_info))
        else:
            record.exc_text = ''
        return True

logger.addFilter(TracebackFilter())

class DetailedFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        if hasattr(record, 'exc_text') and record.exc_text:
            msg += f"\nTraceback:\n{record.exc_text}"
        return msg

for handler in logging.root.handlers:
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
        handler.setLevel(logging.INFO)
        handler.setFormatter(DetailedFormatter('[%(asctime)s] - %(funcName)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S'))

logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

logging.getLogger(__name__).setLevel(logging.INFO)

# ======================== تنظیمات OpenRouter برای پشتیبانی هوشمند ========================
OPENROUTER_API_KEY = "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"   # ← اینجا کلید واقعی خود را قرار دهید
OPENROUTER_BASE_URL = "https://openrouter.ai"
OPENROUTER_MODEL = "arcee-ai/trinity-large-preview:free"   # می‌توانید مدل دیگری انتخاب کنید

# ===================================================================

TOKEN = "8462120028:AAHMU-qQFrVHn-E0SjZu1gTwXW2-TzrzmfY"
BOT_USERNAME = "PeakTubeBot"
ADMIN_USERBOT_USERNAME = "maaamadd"
DOWNLOADS_FOLDER = "downloads"
ADMIN_IDS = [5754581238]
USERS_FILE = "users.json"
CONFIG_FILE = "config.json"
DIRECT_LINKS_FILE = "direct_links.json"  # فایل ذخیره لینک‌های مستقیم
DIRECT_LINKS_PORT = 8080  # پورت سرور لینک مستقیم

FFMPEG_LOCATION = r'C:\ffmpeg\ffmpeg.exe'

SUPPORT_QUEUE_FILE = "support_queue.json"
RECEIPTS_QUEUE_FILE = "receipts_queue.json"  # فایل جدید برای صف رسیدها

# ======================== آیدی واحد فروش برای دیپ‌لینک ========================
SALES_ADMIN_USERNAME = "maaamadd"  # تغییر دهید اگر آیدی دیگری مد نظر است (بدون @)


# کلاینت HTTP مشترک برای همه درخواست‌ها (به جای ساخت هر بار)
http_client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))

# ======================== دیکشنری فارسی ========================
STRINGS = {
    'welcome': "سلام {username}! 👋 به PeakTube خوش آمدید.\n\nاین ربات برای دانلود ویدیوهای یوتیوب طراحی شده است.\n\n{emoji} <b>پلن شما:</b> {plan}\n📥 <b>دانلود امروز:</b> {current}/{limit}\n⏳ <b>باقی‌مانده:</b> {remaining}\n\n🤝 <b>دعوت دوستان و دریافت پاداش!</b>\nلینک اختصاصی شما:\n{referral_link}\n\nتعداد دعوت‌های موفق: {ref_count}\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
    'settings': "⚙️ تنظیمات",
    'back_to_menu': "🔙 بازگشت به منو",
    'back': "🔙 بازگشت",
    'download': "📥 دانلود",
    'profile': "👤 پروفایل",
    'subscription': "📊 اشتراک",
    'buy_vip': "🚀 خرید اشتراک ویژه (VIP)",
    'referral': "🤝 دعوت دوستان",
    'help': "❓ راهنمایی",
    'about': "ℹ️ درباره",
    'support': "🆘 پشتیبانی",
    'exit': "❌ خروج",
    'about_text': "ℹ️ <b>سلام! ما PeakTube هستیم</b>\n\nحتماً برای شما هم پیش آمده که بخواهید یک ویدیوی آموزشی یا\nموزیک‌ویدیو را ذخیره کنید اما با محدودیت‌های مختلف روبرو شده‌اید.\nPeakTube متولد شد تا تمام این محدودیت‌ها برای جامعه فارسی‌زبان از بین ببرد. \nتوسعه‌دهنده: @PeakTeam\nنسخه: 1.0\n\n",
    'help_title': "❓ <b>راهنمای استفاده از ربات</b>\n\nیکی از موضوعات زیر را انتخاب کنید:",
    'support_welcome_ai': "🤖 <b>سلام {name} عزیز!</b>\n\nشما وارد بخش <b>پشتیبانی هوشمند PeakTube</b> شدید.\nلطفاً سوال خود را بپرسید تا با استفاده از هوش مصنوعی پیشرفته به شما کمک کنم.",
    'support_message_sent': "✅ پیام شما دریافت شد و در صف بررسی قرار گرفت.\nپاسخ پس از بررسی ارسال خواهد شد.",
    'support_reply_sent': "پاسخ شما با موفقیت ارسال گردید. جهت ادامه، یکی از گزینه‌های منوی اصلی را انتخاب فرمایید.",
    'no_message_inbox': "در حال حاضر پیامی از سوی پشتیبانی ثبت نشده است.",
    'inbox_title': "📥 <b>آخرین پیام دریافتی از پشتیبانی</b>\n\n⏰ زمان: {time}\n📩 متن:\n{text}\n\nلطفاً پاسخ خود را در قالب پیام متنی ارسال نمایید.",
    'new_support_message_admin': "📩 پیام جدید در صف پشتیبانی ثبت شد.\n\n👤 کاربر: {username} ({user_id})\n🆔 شناسه پیام: {item_id}\n📊 کل پیام‌های خوانده‌نشده: {unread}",
    'reply_sent_admin': "پاسخ شما با موفقیت ارسال گردید. جهت ادامه، یکی از گزینه‌های منوی اصلی را انتخاب فرمایید.",
    'admin_panel_title': "🔧 <b>منوی مدیریت ادمین:</b>\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
    'admin_stats': "📊 <b>آمار استفاده از بات PeakTube:</b>\n\n👥 کل کاربران: {total_users}\n📥 کل دانلودها: {total_downloads}\n\n<b>توزیع پلن‌ها:</b>\n 🆓 رایگان: {free}\n ⭐ پریمیوم: {premium}\n 👑 حرفه‌ای: {professional}",
    'admin_users': "<b>لیست کاربران ({count} کاربر):</b>\n\n{users_list}",
    'admin_referral_stats': "🤝 <b>آمار سیستم رفرال</b>\n\n📊 کل رفرال‌های موفق: {total}\n\n<b>کاربران برتر (Top 10):</b>\n{top_list}",
    'admin_inbox_title': "📩 <b>پیام‌های خوانده‌نشده پشتیبانی</b>\n\n",
    'admin_inbox_empty': "هیچ پیامی در صف انتظار نیست.",
    'admin_inbox_select': "برای مشاهده، یکی را انتخاب کنید:",
    'admin_view_message': "📨 <b>جزئیات پیام پشتیبانی</b>\n\n🆔 شناسه پیام: {id}\n👤 کاربر: {username} ({user_id})\n⏰ زمان: {created_at}\n📩 متن:\n{text}",
    'admin_reply_prompt': "در حال پاسخ به کاربر {username} ({user_id}).\nلطفاً متن پاسخ را ارسال کنید:",
    'admin_upgrade_prompt': "👤 <b>مدیریت اشتراک کاربر:</b>\n\nلطفا شناسه کاربر (User ID) را وارد کنید:\n\n<i>مثال: 123456789</i>",
    'admin_broadcast_prompt': "📢 <b>ارسال پیام:</b>\n\nلطفا پیام خود را تایپ کنید و آن را ارسال کنید:",
    'admin_broadcast_sent': "✅ <b>ارسال کامل شد!</b>\n\n📤 موفق: {sent}\n❌ ناموفق: {failed}\n👥 کل کاربران: {total}",
    'admin_cleanup': "✅ {count} فایل حذف شد",
    'admin_reset_confirm': "⚠️ آیا از بازنشانی آمار مطمئن هستید؟ (این کار فقط users.json را پاک می‌کند)",
    'admin_reset_done': "✅ آمار کاربران بازنشانی شد",
    'admin_exit': "✅ از پنل مدیریت خارج شدید.\nیکی از گزینه‌ها را انتخاب کنید:",
    'download_limit_reached': "❌ شما به حد دانلود روزانه خود رسیده‌اید!\n\n📊 حد شما: {limit} دانلود در روز\n📥 دانلود‌های امروز: {current}\n\nبرای افزایش حد، به پلن بالاتر ارتقا دهید.",
    'enter_link': "لطفا لینک یوتیوب یا عنوان ویدیو را ارسال کنید:",
    'searching': "🔍 در حال جستجو در یوتیوب...",
    'no_results': "❌ هیچ ویدیویی با این عنوان پیدا نشد!",
    'search_results': "🔍 <b>نتایج جستجو برای:</b> <i>{query}</i>\n\nیکی از ویدیوهای زیر را انتخاب کنید:",
    'video_info': "📹 <b>اطلاعات ویدیو:</b>\n\n<b>عنوان:</b> {title}\n<b>کانال:</b> {channel}\n<b>مدت زمان:</b> {minutes}:{seconds:02d}\n<b>تعداد بازدید:</b> {views:,}",
    'select_quality': "🎥 انتخاب کیفیت ویدیو",
    'audio_only': "🎵 استخراج صدا (MP3)",
    'download_with_subtitle': "💬 دانلود با زیرنویس",
    'download_with_subtitle_locked': "💬 دانلود با زیرنویس (ویژه 🔒)",
    'subtitle_locked_alert': "⚠️ این قابلیت مخصوص اعضای ویژه است. جهت دسترسی، لطفاً از بخش پروفایل حساب خود را ارتقا دهید.",
    'no_main_subtitle_alert': "⚠️ برای این ویدیو هیچ زیرنویس رسمی و اصلی یافت نشد.",
    'select_subtitle_lang': "🌐 لطفاً زبان زیرنویس مورد نظر خود را انتخاب کنید:\n\n> 🔹 نکته: تمامی زیرنویس‌های نمایش داده شده نسخه اصلی و رسمی هستند.",
    'subtitle_guide': "✅ فایل زیرنویس برای شما ارسال گردید.\n\nراهنما: در پلیرهای معتبر مانند VLC یا MX Player، فایل .srt را در کنار ویدیو قرار دهید یا از منوی Subtitles > Load Subtitle آن را انتخاب نمایید.",
    'hard_sub_added': "✅ زیرنویس به صورت دائمی و با کیفیت بالا روی ویدیو حک گردید.",
    'downloading': "⏳ در حال دانلود: {percent}",
    'uploading': "📤 در حال آپلود...",
    'download_success': "✅ دانلود شما با موفقیت انجام شد.\nمی‌توانید دانلود دیگری انجام دهید:",
    'cancelled': "عملیات لغو شد!",
    'error_generic': "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.",
    'quality_locked_message': "⚠️ این کیفیت مخصوص کاربران ویژه است. جهت ارتقا به بخش پروفایل مراجعه کنید.",
    'quality_downgraded_message': "⚠️ کیفیت درخواستی بالاتر از پلن شماست. دانلود به صورت خودکار با بهترین کیفیت مجاز (۴۸۰p) انجام شد.",
    'profile_text': "👤 <b>پروفایل شما</b>\n\n🆔 <b>شناسه:</b> {user_id}\n📛 <b>نام کاربری:</b> @{username}\n{emoji} <b>پلن:</b> {plan_name}\n🗓 <b>تاریخ عضویت:</b> {joined_date}\n📥 <b>دانلود امروز:</b> {downloads_today}\n💾 <b>کل دانلودها:</b> {downloads_total}\n🤝 <b>دعوت‌های موفق:</b> {ref_count}",
    'subscription_text': "📊 <b>وضعیت اشتراک شما</b>\n\n<b>پلن فعلی:</b> {plan_name}\n<b>حد دانلود روزانه:</b> {limit}\n<b>دانلود امروز:</b> {downloads_today}\n<b>باقی‌مانده:</b> {remaining}\n\nبرای ارتقا با ادمین تماس بگیرید: @PeakTeam",
    'referral_text': "🎁 <b>دعوت دوستان</b>\n\nلینک اختصاصی شما:\n{link}\n\nتعداد دعوت‌های موفق: {count}\n\nپاداش: هر دعوت موفق = + دانلود اضافه!",
    'help_download': "🎥 <b>راهنمای دانلود ویدیو</b>\n\n1️⃣ دکمه <b>📥 دانلود</b> را بزنید\n2️⃣ لینک یوتیوب را ارسال کنید یا عنوان ویدیو را بنویسید\n3️⃣ اطلاعات ویدیو نمایش داده می‌شود\n4️⃣ گزینه <b>انتخاب کیفیت ویدیو</b>، <b>استخراج صدا (MP3)</b> یا <b>دانلود با زیرنویس (ویژه 🔒)</b> را انتخاب کنید\n5️⃣ کیفیت دلخواه را بزنید\n6️⃣ فایل برای شما ارسال می‌شود\n\n✅ پس از دانلود موفق، آمار شما به‌روز می‌شود.",
    'help_search': "🔍 <b>راهنمای جستجو و دانلود ویدیو</b>\n\nبرای دانلود ویدیو، دو روش در اختیار دارید:\n\n• ارسال مستقیم لینک یوتیوب (https://youtube.com/... یا https://youtu.be/...)\n• یا تایپ عنوان ویدیو یا کلمات کلیدی مرتبط با آن.\n\nدر صورت ارسال کلمات کلیدی، ربات تا ۱۰ نتیجه مرتبط را از یوتیوب جستجو کرده و نمایش می‌دهد. کافی است یکی از نتایج را انتخاب کنید تا فرآیند دانلود آغاز شود.\n\n💡 نکته: برای نتایج دقیق‌تر، از عنوان کامل یا کلمات کلیدی مشخص استفاده نمایید.",
    'help_plans': "📊 <b>پلن‌ها و امکانات PeakTube</b>\n\n🆓 <b>پلن رایگان</b>\n• کیفیت حداکثر ۴۸۰p\n• بدون دسترسی به زیرنویس رسمی\n• حد دانلود روزانه محدود\n\n⭐ <b>پلن پریمیوم</b>\n• کیفیت تا ۱۰۸۰p Full HD\n• دسترسی کامل به زیرنویس رسمی\n• اولویت بالاتر در پردازش\n• پشتیبانی اختصاصی با هوش مصنوعی\n\n👑 <b>پلن حرفه‌ای</b>\n• کیفیت تا ۴K و بالاتر\n• سرعت و پهنای باند اختصاصی (دانلود سریع‌تر)\n• زیرنویس حک‌شده دائمی روی ویدیو\n• اولویت VIP و امکانات پیشرفته\n\nبرای مشاهده جزئیات بیشتر و ارتقا، به بخش 🛒 ارتقای حساب مراجعه فرمایید.",
    'help_profile': "👤 <b>راهنمای بخش پروفایل</b>\n\nبا انتخاب دکمه <b>پروفایل</b>، می‌توانید اطلاعات کامل حساب خود را مشاهده کنید:\n\n🆔 شناسه کاربری منحصربه‌فرد\n📛 نام کاربری تلگرام\n{emoji} نوع پلن فعلی (رایگان، پریمیوم یا حرفه‌ای)\n🗓 تاریخ عضویت در ربات\n📥 تعداد دانلودهای انجام‌شده امروز\n💾 مجموع کل دانلودها از ابتدا\n🤝 تعداد دعوت‌های موفق (رفرال)\n\nهمچنین در بخش اشتراک، حد دانلود روزانه و تعداد باقی‌مانده تا ریست روزانه نمایش داده می‌شود. تمام آمار به‌صورت لحظه‌ای به‌روزرسانی می‌گردد.",
    'help_referral': "🤝 <b>راهنمای سیستم دعوت دوستان (رفرال)</b>\n\nبا دعوت دوستان خود به PeakTube، پاداش دریافت کنید!\n\n✅ لینک اختصاصی دعوت شما در بخش <b>دعوت دوستان</b> نمایش داده می‌شود.\n✅ هر کاربری که با لینک شما وارد ربات شود و حداقل یک دانلود موفق انجام دهد، به‌عنوان دعوت موفق ثبت می‌گردد.\n✅ به ازای هر دعوت موفق، تعداد دانلود اضافی (هدیه) به حساب شما افزوده می‌شود.\n✅ در پلن‌های بالاتر، میزان پاداش دعوت بیشتر است و حتی امکان دریافت اشتراک رایگان وجود دارد.\n\nتعداد دعوت‌های موفق شما همیشه در پروفایل و بخش دعوت دوستان قابل مشاهده است.",
    'vip_plans_text': "💠 درخواست ارتقای سطح کاربری\n\nکاربر گرامی، جهت دریافت مشاوره صوتی و نهایی‌سازی خرید، لطفاً نوع سرویس مورد نظر خود را انتخاب فرمایید.\nپس از انتقال به واحد فروش، پیام آماده شده را ارسال نمایید تا دستیار هوشمند پاسخگوی شما باشد.",
}

def get_string(key: str, user_lang: str = None, **kwargs) -> str:
    """تابع سازگار با کد قدیمی - استفاده از t() برای i18n"""
    if user_lang is None:
        user_lang = DEFAULT_LANG
    return t(key, user_lang, **kwargs)

def get_main_keyboard(user_lang: str = None) -> ReplyKeyboardMarkup:
    """ایجاد کیبورد اصلی با استفاده از زبان کاربر"""
    if user_lang is None:
        user_lang = DEFAULT_LANG
    
    keyboard = [
        [t('download', user_lang)],
        [t('profile', user_lang), t('subscription', user_lang)],
    ]
    
    # نمایش دکمه خرید اشتراک فقط اگر سیستم پولی فعال باشد
    if is_payments_enabled():
        keyboard.append([t('buy_vip', user_lang)])
    
    keyboard.extend([
        [t('referral', user_lang)],
        [t('settings', user_lang)],
        [t('help', user_lang), t('about', user_lang)],
        [t('support', user_lang)],
        [t('exit', user_lang)]
    ])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard() -> InlineKeyboardMarkup:
    unread_support = sum(1 for it in load_support_queue() if it.get('status') == 'unread')
    inbox_text = "📩 مدیریت پیام‌های پشتیبانی"
    if unread_support > 0:
        inbox_text += f" ({unread_support})"

    pending_receipts = sum(1 for it in load_receipts_queue() if it.get('status') == 'pending')
    receipts_text = "🧾 بررسی رسیدها"
    if pending_receipts > 0:
        receipts_text += f" ({pending_receipts})"

    # بررسی وضعیت سیستم پولی
    payments_status = "🟢 فعال" if is_payments_enabled() else "🔴 غیرفعال"
    payments_text = f"💰 کنترل سیستم پولی ({payments_status})"

    keyboard = [
        [InlineKeyboardButton("📊 نمایش آمار کلی", callback_data="admin_show_stats")],
        [InlineKeyboardButton("👥 نمایش لیست کاربران", callback_data="admin_show_users")],
        [InlineKeyboardButton("💳 مدیریت اشتراک کاربر", callback_data="admin_manage_subscription")],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data="admin_send_broadcast")],
        [InlineKeyboardButton(inbox_text, callback_data="admin_support_inbox")],
        [InlineKeyboardButton(receipts_text, callback_data="admin_receipts_inbox")],
        [InlineKeyboardButton(payments_text, callback_data="admin_payments_switch")],
        [InlineKeyboardButton("🛡️ تنظیمات عضویت اجباری", callback_data="admin_force_join")],
        [InlineKeyboardButton("🧹 حذف فایل‌های قدیمی", callback_data="admin_do_cleanup")],
        [InlineKeyboardButton("🔄 بازنشانی آمار", callback_data="admin_reset_stats_confirm")],
        [InlineKeyboardButton("🤝 آمار رفرال‌ها", callback_data="admin_referral_stats")],
        [InlineKeyboardButton("❌ خروج", callback_data="admin_exit")]
    ]
    return InlineKeyboardMarkup(keyboard)

def load_support_queue() -> list:
    try:
        if os.path.exists(SUPPORT_QUEUE_FILE):
            with open(SUPPORT_QUEUE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"خطا در بارگذاری صف پشتیبانی: {e}", exc_info=True)
        return []

def save_support_queue(items: list):
    try:
        with open(SUPPORT_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره صف پشتیبانی: {e}", exc_info=True)

def enqueue_support_message(user_id: int, username: str, text: str, msg_id: int) -> dict:
    try:
        items = load_support_queue()
        item = {
            'id': int(time.time() * 1000),
            'user_id': user_id,
            'username': username or f"User_{user_id}",
            'text': text,
            'status': 'unread',
            'created_at': datetime.now().isoformat(),
            'admin_reply': None,
            'source_msg_id': msg_id,
        }
        items.append(item)
        save_support_queue(items)
        return item
    except Exception as e:
        logger.error(f"خطا در افزودن به صف پشتیبانی: {e}", exc_info=True)
        return {}

def mark_support_replied(item_id: int, reply_text: str):
    try:
        items = load_support_queue()
        for it in items:
            if it.get('id') == item_id:
                it['status'] = 'replied'
                it['admin_reply'] = reply_text
                it['replied_at'] = datetime.now().isoformat()
                break
        save_support_queue(items)
    except Exception as e:
        logger.error(f"خطا در علامت‌گذاری پاسخ‌داده‌شده: {e}", exc_info=True)

# ======================== توابع صف رسیدهای پرداخت ========================
def load_receipts_queue() -> list:
    try:
        if os.path.exists(RECEIPTS_QUEUE_FILE):
            with open(RECEIPTS_QUEUE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"خطا در بارگذاری صف رسیدها: {e}", exc_info=True)
        return []

def save_receipts_queue(items: list):
    try:
        with open(RECEIPTS_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره صف رسیدها: {e}", exc_info=True)

def enqueue_receipt(user_id: int, username: str, photo_file_id: str, message_id: int, plan_type: str = 'premium', duration_days: int = 30) -> dict:
    try:
        items = load_receipts_queue()
        item = {
            'id': int(time.time() * 1000),
            'user_id': user_id,
            'username': username or f"User_{user_id}",
            'photo_file_id': photo_file_id,
            'message_id': message_id,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'plan_type': plan_type,
            'duration_days': duration_days,
        }
        items.append(item)
        save_receipts_queue(items)
        logger.info(f"رسید جدید ذخیره شد: user_id={user_id}, plan={plan_type}, days={duration_days}")
        return item
    except Exception as e:
        logger.error(f"خطا در افزودن رسید به صف: {e}", exc_info=True)
        return {}

def mark_receipt_processed(item_id: int, status: str, admin_note: str = ""):
    try:
        items = load_receipts_queue()
        for it in items:
            if it.get('id') == item_id:
                it['status'] = status
                it['processed_at'] = datetime.now().isoformat()
                it['admin_note'] = admin_note
                break
        save_receipts_queue(items)
    except Exception as e:
        logger.error(f"خطا در علامت‌گذاری رسید: {e}", exc_info=True)

# ======================== توابع برای config (عضویت اجباری) ========================
def load_config() -> dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"خطا در بارگذاری config: {e}", exc_info=True)
        return {}

def save_config(data: dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره config: {e}", exc_info=True)

# ======================== سیستم چندزبانه (i18n) ========================
# استفاده از مسیر مطلق برای اطمینان از پیدا کردن فایل‌ها
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCALES_DIR = os.path.join(BASE_DIR, "locales")
DEFAULT_LANG = "fa"

# Cache برای فایل‌های زبان
_locales_cache = {}

def load_locale(lang: str) -> dict:
    """بارگذاری فایل زبان از cache یا فایل"""
    if lang in _locales_cache:
        return _locales_cache[lang]
    
    try:
        locale_path = os.path.join(LOCALES_DIR, f"{lang}.json")
        if os.path.exists(locale_path):
            with open(locale_path, 'r', encoding='utf-8') as f:
                locale_data = json.load(f)
                _locales_cache[lang] = locale_data
                return locale_data
        else:
            logger.warning(f"فایل زبان پیدا نشد: {locale_path}")
    except Exception as e:
        logger.error(f"خطا در بارگذاری فایل زبان {lang}: {e}", exc_info=True)
    
    # در صورت خطا، فایل پیش‌فرض (فارسی) را برمی‌گرداند
    if lang != DEFAULT_LANG:
        return load_locale(DEFAULT_LANG)
    
    return {}

def get_user_language(user_id: int) -> str:
    """دریافت زبان کاربر از users.json - اگر زبان وجود نداشت None برمی‌گرداند"""
    try:
        users = load_users()
        user_key = str(user_id)
        if user_key in users:
            lang = users[user_key].get('language')
            if lang:
                return lang
    except Exception as e:
        logger.error(f"خطا در دریافت زبان کاربر {user_id}: {e}", exc_info=True)
    return None

def set_user_language(user_id: int, lang: str):
    """ذخیره زبان کاربر در users.json"""
    try:
        users = load_users()
        user_key = str(user_id)
        if user_key not in users:
            # اگر کاربر وجود ندارد، یک رکورد اولیه ایجاد می‌کنیم
            users[user_key] = {}
        users[user_key]['language'] = lang
        save_users(users)
    except Exception as e:
        logger.error(f"خطا در ذخیره زبان کاربر {user_id}: {e}", exc_info=True)

def t(key: str, user_lang: str = None, **kwargs) -> str:
    """
    تابع مرکزی برای ترجمه
    key: کلید متن
    user_lang: زبان کاربر (اگر None باشد، از DEFAULT_LANG استفاده می‌شود)
    **kwargs: پارامترهای فرمت
    """
    if user_lang is None:
        user_lang = DEFAULT_LANG
    
    locale = load_locale(user_lang)
    text = locale.get(key, '')
    
    # اگر متن پیدا نشد، از فایل پیش‌فرض استفاده می‌کند
    if not text and user_lang != DEFAULT_LANG:
        locale = load_locale(DEFAULT_LANG)
        text = locale.get(key, key)  # اگر در پیش‌فرض هم نبود، خود key را برمی‌گرداند
    
    if not text:
        text = key
    
    # فرمت کردن متن با پارامترها
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError) as e:
        logger.warning(f"خطا در فرمت کردن متن '{key}': {e}")
        return text

# ======================== تابع مرکزی برای بررسی وضعیت سیستم پولی ========================
def is_payments_enabled() -> bool:
    """
    بررسی وضعیت سیستم پولی (Paywall Switch)
    اگر False باشد، تمام کاربران دسترسی کامل دارند و هیچ محدودیتی اعمال نمی‌شود.
    """
    config = load_config()
    # به صورت پیش‌فرض True است (سیستم پولی فعال)
    return config.get('payments_enabled', True)

def set_payments_enabled(enabled: bool):
    """تنظیم وضعیت سیستم پولی"""
    config = load_config()
    config['payments_enabled'] = enabled
    save_config(config)

if not os.path.exists(DOWNLOADS_FOLDER):
    os.makedirs(DOWNLOADS_FOLDER)

CHOOSING_ACTION, WAITING_LINK, SHOWING_INFO, SELECTING_QUALITY, SELECTING_SUBTITLE_LANG, AI_SUPPORT, SELECTING_LANGUAGE, ABOUT_MENU = range(8)
USER_REPLYING_SUPPORT = 11
ADMIN_PANEL, ADMIN_WAITING_USER_ID, ADMIN_WAITING_BROADCAST, ADMIN_REPLYING_SUPPORT, ADMIN_WAITING_FORCE_JOIN_CHANNEL = range(6, 11)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def load_users() -> dict:
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"خطا در بارگذاری فایل کاربران (JSON): {e}", exc_info=True)
        return {}

def save_users(users: dict):
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره فایل کاربران (JSON): {e}", exc_info=True)


# ======================== سیستم تاریخچه گفتگوهای پشتیبانی هوشمند ========================
def _get_ai_history_limit(plan: str) -> int | None:
    """
    حداکثر تعداد گفتگوهای قابل نگهداری برای هر کاربر بر اساس پلن
    free: آخرین ۳ گفتگو
    premium: آخرین ۲۰ گفتگو
    professional: نامحدود (None)
    """
    plan = (plan or "free").lower()
    if plan == "free":
        return 3
    if plan == "premium":
        return 20
    # professional و سایر پلن‌ها: نامحدود
    return None


def _get_user_ai_conversations(user_id: int) -> list[dict]:
    """دریافت لیست گفتگوهای AI یک کاربر از users.json"""
    users = load_users()
    user = users.get(str(user_id), {})
    return user.get("ai_conversations", [])


def _save_user_ai_conversations(user_id: int, conversations: list[dict]):
    """ذخیره لیست گفتگوهای AI در users.json با اعمال محدودیت بر اساس پلن"""
    users = load_users()
    key = str(user_id)
    if key not in users:
        users[key] = {}

    users[key].setdefault("username", f"User_{user_id}")

    # اعمال محدودیت بر اساس پلن
    plan = users[key].get("plan", "free")
    limit = _get_ai_history_limit(plan)

    if limit is not None and len(conversations) > limit:
        # حذف قدیمی‌ترین گفتگوها بر اساس last_updated
        conversations_sorted = sorted(
            conversations,
            key=lambda c: c.get("last_updated", ""),
        )
        conversations = conversations_sorted[-limit:]

    users[key]["ai_conversations"] = conversations
    save_users(users)


def _create_ai_conversation(user_id: int, first_message: str) -> str:
    """
    ایجاد یک گفتگوی جدید برای پشتیبانی هوشمند
    عنوان به صورت خودکار از اولین پیام کاربر ساخته می‌شود.
    """
    first_message = (first_message or "").strip()
    title = first_message if first_message else "Conversation"
    if len(title) > 40:
        title = title[:37] + "..."

    conversations = _get_user_ai_conversations(user_id)
    conv_id = str(uuid.uuid4())
    now_iso = datetime.now().isoformat()

    new_conv = {
        "conversation_id": conv_id,
        "user_id": user_id,
        "title": title,
        "messages": [],
        "last_updated": now_iso,
    }
    conversations.append(new_conv)
    _save_user_ai_conversations(user_id, conversations)
    return conv_id


def _append_ai_conversation_message(user_id: int, conversation_id: str, role: str, content: str):
    """افزودن یک پیام (کاربر یا دستیار) به گفتگوی مشخص"""
    users = load_users()
    key = str(user_id)
    if key not in users:
        return

    convs = users[key].get("ai_conversations", [])
    for conv in convs:
        if conv.get("conversation_id") == conversation_id:
            conv.setdefault("messages", [])
            conv["messages"].append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": datetime.now().isoformat(),
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                }
            )
            conv["last_updated"] = datetime.now().isoformat()
            break

    users[key]["ai_conversations"] = convs
    save_users(users)


def _get_ai_conversation(user_id: int, conversation_id: str) -> dict | None:
    """دریافت یک گفتگو بر اساس conversation_id"""
    convs = _get_user_ai_conversations(user_id)
    for conv in convs:
        if conv.get("conversation_id") == conversation_id:
            return conv
    return None


def _list_ai_conversations_sorted(user_id: int) -> list[dict]:
    """لیست گفتگوهای کاربر به ترتیب آخرین به‌روزرسانی (جدیدترین اول)"""
    convs = _get_user_ai_conversations(user_id)
    return sorted(convs, key=lambda c: c.get("last_updated", ""), reverse=True)


def _delete_ai_conversation(user_id: int, conversation_id: str) -> bool:
    """حذف یک گفتگو برای کاربر (فقط همان کاربر)"""
    users = load_users()
    key = str(user_id)
    if key not in users:
        return False
    convs = users[key].get("ai_conversations", [])
    new_convs = [c for c in convs if c.get("conversation_id") != conversation_id]
    if len(new_convs) == len(convs):
        return False
    users[key]["ai_conversations"] = new_convs
    save_users(users)
    return True

# ======================== توابع کمکی برای ارتقای پلن ========================
def _format_date_for_user(dt: datetime, user_lang: str) -> str:
    """
    فرمت کردن تاریخ برای نمایش به کاربر بر اساس زبان
    فارسی: 2026/02/13 - 18:45
    انگلیسی: 2026-02-13 18:45
    """
    if user_lang == 'fa':
        return dt.strftime("%Y/%m/%d - %H:%M")
    else:
        return dt.strftime("%Y-%m-%d %H:%M")

def invalidate_user_cache(application, user_id: int):
    """
    نامعتبر کردن cache کاربر پس از به‌روزرسانی طرح
    این تابع user_data کاربر را پاک می‌کند تا داده‌های تازه از پایگاه داده بارگیری شوند
    """
    try:
        if application and hasattr(application, 'user_data'):
            user_data = application.user_data.get(user_id)
            if user_data:
                # پاک کردن user_plan از cache تا از داده‌های تازه استفاده شود
                user_data.pop('user_plan', None)
                logger.info(f"Cache کاربر {user_id} نامعتبر شد (user_plan حذف شد)")
    except Exception as e:
        logger.error(f"خطا در نامعتبر کردن cache کاربر {user_id}: {e}", exc_info=True)

async def _send_plan_upgrade_message(bot, user_id: int, plan_name: str, plan_start_at: datetime, plan_expire_at: datetime):
    """
    ارسال پیام ارتقای پلن به کاربر با جزئیات تاریخ شروع و انقضا
    """
    try:
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # فرمت کردن تاریخ‌ها بر اساس زبان کاربر
        start_date_str = _format_date_for_user(plan_start_at, user_lang)
        expire_date_str = _format_date_for_user(plan_expire_at, user_lang)
        
        # ساخت پیام با استفاده از ترجمه
        message = t(
            'plan_upgrade_success',
            user_lang,
            plan_name=plan_name.upper(),
            plan_start_at=start_date_str,
            plan_expire_at=expire_date_str
        )
        
        await bot.send_message(chat_id=user_id, text=message)
    except Exception as e:
        logger.error(f"خطا در ارسال پیام ارتقای پلن به کاربر {user_id}: {e}", exc_info=True)

def set_last_admin_message(user_id: int, text: str):
    try:
        users = load_users()
        key = str(user_id)
        if key not in users:
            return False
        users[key].setdefault('support', {})
        users[key]['support']['last_admin_message'] = {
            'text': text,
            'timestamp': datetime.now().isoformat()
        }
        save_users(users)
        return True
    except Exception as e:
        logger.error(f"خطا در ذخیره آخرین پیام ادمین برای کاربر {user_id}: {e}", exc_info=True)
        return False

def get_user_profile(user_id: int) -> dict | None:
    try:
        users = load_users()
        return users.get(str(user_id))
    except Exception as e:
        logger.error(f"خطا در دریافت پروفایل کاربر {user_id}: {e}", exc_info=True)
        return None

def apply_referral_rewards(referrer_id: int, new_user_id: int):
    try:
        users = load_users()
        
        inviter_key = str(referrer_id)
        new_user_key = str(new_user_id)
        
        if inviter_key not in users or new_user_key not in users:
            return
        
        inviter = users[inviter_key]
        new_user = users[new_user_key]
        
        rewards = {'free': 3, 'premium': 10, 'professional': 20}
        reward_amount = rewards.get(inviter['plan'], 3)
        
        inviter['downloads_today'] = max(0, inviter['downloads_today'] - reward_amount)
        new_user['downloads_today'] = max(0, new_user['downloads_today'] - 1)
        
        save_users(users)
    except Exception as e:
        logger.error(f"خطا در اعمال پاداش رفرال: {e}", exc_info=True)

def create_user(user_id: int, username: str, referrer_id: int = None) -> dict:
    try:
        users = load_users()
        user_key = str(user_id)
        
        if referrer_id == user_id:
            referrer_id = None
        
        now_iso = datetime.now().isoformat()
        
        users[user_key] = {
            'username': username or f"User_{user_id}",
            'plan': 'free',
            'created_at': now_iso,
            'downloads_today': 0,
            'downloads_total': 0,
            'last_reset': now_iso,
            'downloads_this_month': 0,
            'last_monthly_reset': now_iso,
            'referrer_id': referrer_id,
            'joined_at': now_iso,
            'subscription_end': None,
            'ai_used_count': 0,
            'ai_window_start_time': now_iso,
        }
        save_users(users)
        
        if referrer_id is not None:
            inviter_key = str(referrer_id)
            if inviter_key in users:
                apply_referral_rewards(referrer_id, user_id)
            
        return users[user_key]
    except Exception as e:
        logger.error(f"خطا در ایجاد کاربر جدید {user_id}: {e}", exc_info=True)
        return {}

def get_referral_count(user_id: int) -> int:
    try:
        users = load_users()
        count = 0
        for u in users.values():
            if u.get('referrer_id') == user_id:
                count += 1
        return count
    except Exception as e:
        logger.error(f"خطا در شمارش رفرال‌های کاربر {user_id}: {e}", exc_info=True)
        return 0

def extract_available_qualities(formats, user_plan: str):
    try:
        qualities = {}
        max_allowed = get_max_quality_allowed(user_plan)

        for f in formats:
            height = f.get('height')
            if height and f.get('vcodec') != 'none':
                label = f"{height}p"
                if height > max_allowed:
                    label += " 🔒"
                qualities[label] = height

        return dict(sorted(qualities.items(), key=lambda x: x[1], reverse=True))
    except Exception as e:
        logger.error(f"خطا در استخراج کیفیت‌ها: {e}", exc_info=True)
        return {}

class ProgressHook:
    def __init__(self, bot, chat_id, status_msg):
        self.bot = bot
        self.chat_id = chat_id
        self.status_msg = status_msg

    async def __call__(self, d):
        if d['status'] == 'downloading':
            try:
                percent = d['_percent_str']
                await self.status_msg.edit_text(f"⏳ در حال دانلود: {percent}")
            except:
                pass

# ======================== سیستم سه‌لایه دانلود ========================

def load_direct_links() -> dict:
    """بارگذاری لینک‌های مستقیم از فایل"""
    try:
        if os.path.exists(DIRECT_LINKS_FILE):
            with open(DIRECT_LINKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"خطا در بارگذاری لینک‌های مستقیم: {e}", exc_info=True)
        return {}

def save_direct_links(links: dict):
    """ذخیره لینک‌های مستقیم در فایل"""
    try:
        with open(DIRECT_LINKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(links, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطا در ذخیره لینک‌های مستقیم: {e}", exc_info=True)

def create_direct_link(video_url: str, title: str, expires_hours: int = 24) -> str:
    """ایجاد لینک مستقیم دانلود با استخراج URL مستقیم از yt-dlp"""
    try:
        # استخراج URL مستقیم ویدیو
        ydl_opts = {
            'format': 'best',
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            # دریافت URL مستقیم
            direct_url = info.get('url')
            if not direct_url:
                # اگر url وجود نداشت، از formats استفاده می‌کنیم
                formats = info.get('formats', [])
                if formats:
                    # انتخاب بهترین فرمت
                    best_format = max(formats, key=lambda x: x.get('quality', 0) or 0)
                    direct_url = best_format.get('url')
            
            if direct_url:
                # تولید توکن امن برای لینک
                token = secrets.token_urlsafe(16)
                link_id = str(uuid.uuid4())
                
                # ذخیره اطلاعات لینک
                links = load_direct_links()
                expires_at = datetime.now() + timedelta(hours=expires_hours)
                
                links[link_id] = {
                    'token': token,
                    'direct_url': direct_url,
                    'video_url': video_url,
                    'title': title,
                    'created_at': datetime.now().isoformat(),
                    'expires_at': expires_at.isoformat(),
                    'expires_hours': expires_hours
                }
                
                save_direct_links(links)
                
                # ساخت لینک - در اینجا می‌توانید از یک سرور HTTP استفاده کنید
                # برای سادگی، لینک مستقیم را برمی‌گردانیم
                # کاربر می‌تواند این لینک را در IDM یا مرورگر استفاده کند
                return direct_url
            else:
                logger.error("نتوانست URL مستقیم را استخراج کند")
                return None
    except Exception as e:
        logger.error(f"خطا در ایجاد لینک مستقیم: {e}", exc_info=True)
        return None

def cleanup_expired_links():
    """پاکسازی لینک‌های منقضی‌شده"""
    try:
        links = load_direct_links()
        now = datetime.now()
        expired_ids = []
        
        for link_id, link_data in links.items():
            expires_at = datetime.fromisoformat(link_data['expires_at'])
            if now > expires_at:
                expired_ids.append(link_id)
        
        for link_id in expired_ids:
            del links[link_id]
        
        if expired_ids:
            save_direct_links(links)
            logger.info(f"{len(expired_ids)} لینک منقضی‌شده پاک شد")
    except Exception as e:
        logger.error(f"خطا در پاکسازی لینک‌های منقضی‌شده: {e}", exc_info=True)

async def download_with_three_layer(url: str, ydl_opts_base: dict, user_lang: str = 'fa', status_msg=None, bot=None, chat_id=None) -> tuple[bool, dict, str]:
    """
    دانلود دو لایه:
    لایه 1: دانلود مستقیم
    لایه 2: ایجاد لینک مستقیم در صورت خطا
    
    Returns: (success, info_dict, error_message)
    """
    # لایه 1: دانلود مستقیم
    try:
        # اطمینان از تنظیمات پیش‌فرض
        ydl_opts = ydl_opts_base.copy()
        if 'quiet' not in ydl_opts:
            ydl_opts['quiet'] = True
        if 'no_warnings' not in ydl_opts:
            ydl_opts['no_warnings'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return (True, info, None)
    except Exception as e:
        logger.warning(f"خطا در دانلود مستقیم: {e}")
        # لایه 2: ایجاد لینک مستقیم
        try:
            if status_msg and bot and chat_id:
                await status_msg.edit_text(t('download_retry_secure', user_lang))
            
            # استخراج اطلاعات ویدیو بدون دانلود
            info_opts = {
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            title = info.get('title', 'Video')
            direct_link = create_direct_link(url, title)
            
            if direct_link:
                return (False, info, direct_link)
            else:
                return (False, info, None)
        except Exception as e2:
            logger.error(f"خطا در ایجاد لینک مستقیم: {e2}", exc_info=True)
            return (False, None, str(e))

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.critical(f"خطای پیش‌بینی نشده در پردازش آپدیت تلگرام: {context.error}", exc_info=context.error)
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🚨 خطای سیستمی ربات:\n{context.error}\n\nTraceback:\n{traceback.format_exc()}"
            )
        except:
            pass

HELP_TEXTS = {
    "download": 'help_download',
    "search": 'help_search',
    "plans": 'help_plans',
    "profile": 'help_profile',
    "referral": 'help_referral',
}

async def show_help_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.effective_user.id if update.effective_user else None
        user_lang = get_user_language(user_id) if user_id else DEFAULT_LANG
        if user_lang is None:
            user_lang = DEFAULT_LANG
        
        keyboard = [
            [InlineKeyboardButton(t('help_download', user_lang), callback_data="help_download")],
            [InlineKeyboardButton(t('help_search', user_lang), callback_data="help_search")],
            [InlineKeyboardButton(t('help_plans', user_lang), callback_data="help_plans")],
            [InlineKeyboardButton(t('help_profile', user_lang), callback_data="help_profile")],
            [InlineKeyboardButton(t('help_referral', user_lang), callback_data="help_referral")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = t('help_title', user_lang)

        if update.message:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        elif update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"خطا در نمایش راهنمای اصلی: {e}", exc_info=True)

async def help_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        await show_help_main(update, context)
    except Exception as e:
        logger.error(f"خطا در بازگشت به راهنما: {e}", exc_info=True)

async def help_topic_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id if update.effective_user else None
        user_lang = get_user_language(user_id) if user_id else DEFAULT_LANG
        if user_lang is None:
            user_lang = DEFAULT_LANG

        data = query.data
        if not data.startswith("help_"):
            await show_help_main(update, context)
            return

        topic = data[5:]

        key = HELP_TEXTS.get(topic, 'help_download')
        text = t(key, user_lang)

        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="help_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"خطا در نمایش موضوع راهنما: {e}", exc_info=True)

async def search_youtube_videos(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        status_msg = await update.message.reply_text(t('searching', user_lang))
        
        # جستجو در یوتیوب
        ydl_opts = {
            'extract_flat': True,
            'default_search': 'ytsearch10',
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"ytsearch10:{query_text}", download=False)
        except Exception as search_error:
            logger.error(f"خطا در جستجو: {search_error}", exc_info=True)
            await status_msg.edit_text(t('error_generic', user_lang))
            return WAITING_LINK
        
        results = search_results.get('entries', [])
        
        if not results:
            await status_msg.edit_text(t('no_results', user_lang))
            return WAITING_LINK
        
        keyboard = []
        for video in results:
            if not video:
                continue
            title = video.get('title', 'بدون عنوان')
            video_id = video.get('id', '')
            duration = video.get('duration')
            
            if duration:
                duration = int(duration)
                minutes = duration // 60
                seconds = duration % 60
                duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = 'نامشخص'
            
            button_text = f"{title} ({duration_str})"
            if len(button_text) > 60:
                button_text = button_text[:57] + "..."
                
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_yt_id:{video_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_search")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_msg.edit_text(
            get_string('search_results', query=query_text),
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        context.user_data['search_message_id'] = status_msg.message_id
        
        return WAITING_LINK
        
    except Exception as e:
        logger.error(f"خطا در جستجوی یوتیوب: {e}", exc_info=True)
        try:
            await update.message.reply_text(STRINGS['error_generic'])
        except:
            pass
        return WAITING_LINK

async def select_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        if query.data == "cancel_search":
            return await cancel_download(update, context)
        if not query.data.startswith("select_yt_id:"):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=STRINGS['error_generic'])
            return WAITING_LINK
        video_id = query.data[len("select_yt_id:"):]
        url = f"https://www.youtube.com/watch?v={video_id}"
        search_message_id = context.user_data.get('search_message_id')
        if search_message_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=search_message_id)
            except:
                pass
            finally:
                context.user_data.pop('search_message_id', None)
        context.user_data['video_url_from_search'] = url
        return await show_video_info_from_search(update, context)
    except Exception as e:
        logger.error(f"خطا در انتخاب نتیجه جستجو: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=STRINGS['error_generic'])
        return WAITING_LINK

async def show_video_info_from_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # بررسی عضویت اجباری قبل از دانلود
        if not await check_force_join(user_id, context.bot):
            await send_force_join_message(update, context, user_lang)
            return WAITING_LINK
        
        url = context.user_data.pop('video_url_from_search', None)
        if not url:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=t('error_generic', user_lang))
            return CHOOSING_ACTION
        
        stats = get_user_stats(user_id)
        user_plan = stats['plan']
        status_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=t('searching', user_lang))
        
        # استخراج اطلاعات ویدیو
        ydl_opts = {
            'listsubtitles': True,
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as info_error:
            logger.error(f"خطا در استخراج اطلاعات ویدیو: {info_error}", exc_info=True)
            await status_msg.edit_text(t('error_generic', user_lang))
            return CHOOSING_ACTION
        
        title = info.get('title', 'Unknown')
        channel = info.get('uploader', 'Unknown')
        duration = info.get('duration', 0)
        views = info.get('view_count', 0)
        thumbnail = info.get('thumbnail', None)
        formats = info.get('formats', [])
        subtitles = info.get('subtitles', {})
        video_id = info.get('id', '')
        
        minutes = duration // 60
        seconds = duration % 60
        
        sorted_qualities = extract_available_qualities(formats, user_plan)
        
        clean_subs = {}
        for lang_code, subs_list in subtitles.items():
            if '-' in lang_code:
                if lang_code not in ['zh-Hans', 'zh-Hant']:
                    continue
            clean_subs[lang_code] = subs_list
        
        has_subtitle = bool(clean_subs)
        
        context.user_data['video_url'] = url
        context.user_data['video_info'] = info
        context.user_data['video_id'] = video_id
        context.user_data['qualities'] = sorted_qualities
        context.user_data['all_formats'] = formats
        context.user_data['user_plan'] = user_plan
        context.user_data['has_subtitle'] = has_subtitle
        context.user_data['manual_subtitles'] = clean_subs
        
        info_text = t('video_info', user_lang, title=title, channel=channel, minutes=minutes, seconds=seconds, views=views)
        
        subtitle_button_text = (
            t('download_with_subtitle_locked', user_lang) if is_plan_feature_locked(user_plan, 'subtitle')
            else t('download_with_subtitle', user_lang)
        )
        
        keyboard = []
        keyboard.append([InlineKeyboardButton(t('select_quality', user_lang), callback_data="proceed_to_quality")])
        keyboard.append([InlineKeyboardButton(t('audio_only', user_lang), callback_data="audio_only")])
        if has_subtitle:
            keyboard.append([InlineKeyboardButton(subtitle_button_text, callback_data="request_subtitle")])
        keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_download")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_msg.delete()
        
        if thumbnail:
            msg = await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=thumbnail,
                caption=info_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=info_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        
        context.user_data['main_info_message_id'] = msg.message_id
        
        return SHOWING_INFO
        
    except Exception as e:
        logger.error(f"خطا در دریافت اطلاعات ویدیو: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=STRINGS['error_generic'])
        return WAITING_LINK

async def request_subtitle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        # دریافت داده‌های تازه از پایگاه داده به جای استفاده از cache
        stats = get_user_stats(user_id)
        user_plan = stats['plan']
        
        if is_plan_feature_locked(user_plan, 'subtitle'):
            await query.answer(STRINGS['subtitle_locked_alert'], show_alert=True)
            return SHOWING_INFO
        
        manual_subs = context.user_data.get('manual_subtitles', {})
        if not manual_subs:
            await query.answer(STRINGS['no_main_subtitle_alert'], show_alert=True)
            return SHOWING_INFO
        
        video_id = context.user_data['video_id']
        
        lang_list = []
        for lang_code, subs_list in manual_subs.items():
            lang_name = lang_code.upper()
            if subs_list:
                lang_name = subs_list[0].get('name') or lang_name
            lang_list.append((lang_code, lang_name))
        
        priority = []
        others = []
        for code, name in lang_list:
            lower_name = name.lower()
            if 'persian' in lower_name or code in ['fa', 'per']:
                priority.append((code, name))
            elif 'english' in lower_name or code == 'en':
                priority.append((code, name))
            else:
                others.append((code, name))
        
        sorted_langs = priority + others
        
        keyboard = []
        row = []
        for code, name in sorted_langs:
            row.append(InlineKeyboardButton(name, callback_data=f"sub_dl:{code}:{video_id}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_video_info")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        caption_text = t('select_subtitle_lang', user_lang)
        
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            # ابتدا سعی می‌کنیم caption را ویرایش کنیم (برای پیام‌های عکس)
            try:
                await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=main_msg_id,
                    caption=caption_text,
                    reply_markup=reply_markup
                )
            except Exception:
                # اگر caption ویرایش نشد، سعی می‌کنیم متن را ویرایش کنیم (برای پیام‌های متنی)
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=main_msg_id,
                        text=caption_text,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.warning(f"ویرایش پیام انتخاب زبان شکست خورد: {e}")
                    await query.message.reply_text(caption_text, reply_markup=reply_markup)
        else:
            await query.message.reply_text(caption_text, reply_markup=reply_markup)
        
        return SELECTING_SUBTITLE_LANG
        
    except Exception as e:
        logger.error(f"خطا در درخواست زیرنویس: {e}", exc_info=True)
        await query.answer(STRINGS['error_generic'], show_alert=True)
        return SHOWING_INFO

async def back_to_video_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        info = context.user_data['video_info']
        title = info.get('title', 'Unknown')
        channel = info.get('uploader', 'Unknown')
        duration = info.get('duration', 0)
        views = info.get('view_count', 0)
        minutes = duration // 60
        seconds = duration % 60
        
        info_text = t('video_info', user_lang, title=title, channel=channel, minutes=minutes, seconds=seconds, views=views)
        
        # دریافت داده‌های تازه از پایگاه داده به جای استفاده از cache
        stats = get_user_stats(user_id)
        user_plan = stats['plan']
        has_subtitle = context.user_data['has_subtitle']
        
        subtitle_button_text = (
            t('download_with_subtitle_locked', user_lang) if is_plan_feature_locked(user_plan, 'subtitle')
            else t('download_with_subtitle', user_lang)
        )
        
        keyboard = []
        keyboard.append([InlineKeyboardButton(t('select_quality', user_lang), callback_data="proceed_to_quality")])
        keyboard.append([InlineKeyboardButton(t('audio_only', user_lang), callback_data="audio_only")])
        if has_subtitle:
            keyboard.append([InlineKeyboardButton(subtitle_button_text, callback_data="request_subtitle")])
        keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_download")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            # ابتدا سعی می‌کنیم caption را ویرایش کنیم (برای پیام‌های عکس)
            try:
                await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=main_msg_id,
                    caption=info_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception:
                # اگر caption ویرایش نشد، سعی می‌کنیم متن را ویرایش کنیم (برای پیام‌های متنی)
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=main_msg_id,
                        text=info_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"بازگشت به پیام اصلی شکست خورد: {e}")
                    await query.message.reply_text(info_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await query.message.reply_text(info_text, parse_mode="HTML", reply_markup=reply_markup)
        
        context.user_data.pop('selected_subtitle_lang', None)
        context.user_data.pop('subtitle_is_auto', None)
           
        return SHOWING_INFO
    except Exception as e:
        logger.error(f"خطا در بازگشت به اطلاعات ویدیو: {e}", exc_info=True)
        return SHOWING_INFO

async def handle_subtitle_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data.split(":")
        lang_code = data[1]
        video_id = data[2]
        
        context.user_data['selected_subtitle_lang'] = lang_code
        context.user_data['subtitle_is_auto'] = False
        
        manual_subs = context.user_data['manual_subtitles']
        lang_name = lang_code.upper()
        if lang_code in manual_subs and manual_subs[lang_code]:
            lang_name = manual_subs[lang_code][0].get('name', lang_code.upper())
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        subtitle_status_text = t('subtitle_preparing', user_lang, lang_name=lang_name)
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            # ابتدا سعی می‌کنیم caption را ویرایش کنیم (برای پیام‌های عکس)
            try:
                await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=main_msg_id,
                    caption=subtitle_status_text,
                    reply_markup=None
                )
            except Exception:
                # اگر caption ویرایش نشد، سعی می‌کنیم متن را ویرایش کنیم (برای پیام‌های متنی)
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=main_msg_id,
                        text=subtitle_status_text,
                        reply_markup=None
                    )
                except Exception as e:
                    logger.warning(f"ویرایش وضعیت زیرنویس شکست خورد: {e}")
                    await query.message.reply_text(subtitle_status_text)
        else:
            await query.message.reply_text(subtitle_status_text)
        
        return await proceed_to_quality(update, context)
        
    except Exception as e:
        logger.error(f"خطا در شروع دانلود با زیرنویس: {e}", exc_info=True)
        await query.answer(STRINGS['error_generic'], show_alert=True)
        return SHOWING_INFO

async def proceed_to_quality(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        qualities = context.user_data.get('qualities', {})
        if not qualities:
            await query.answer("هیچ کیفیتی مجاز نیست!", show_alert=True)
            return SHOWING_INFO
        
        keyboard = []
        for label, height in qualities.items():
            keyboard.append([InlineKeyboardButton(label, callback_data=f"quality_{height}")])
        keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_download")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        main_msg_id = context.user_data.get('main_info_message_id')
        quality_text = t('select_quality_prompt', user_lang)
        
        if main_msg_id:
            # ابتدا سعی می‌کنیم caption را ویرایش کنیم (برای پیام‌های عکس)
            try:
                await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=main_msg_id,
                    caption=quality_text,
                    reply_markup=reply_markup
                )
            except Exception:
                # اگر caption ویرایش نشد، سعی می‌کنیم متن را ویرایش کنیم (برای پیام‌های متنی)
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=main_msg_id,
                        text=quality_text,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.warning(f"ویرایش پیام کیفیت شکست خورد، پیام جدید ارسال شد: {e}")
                    await query.message.reply_text(quality_text, reply_markup=reply_markup)
        else:
            # اگر main_info_message_id وجود نداشت، پیام جدید ارسال می‌کنیم
            await query.message.reply_text(quality_text, reply_markup=reply_markup)
        
        return SELECTING_QUALITY
    except Exception as e:
        logger.error(f"خطا در انتخاب کیفیت: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await query.answer(t('error_try_again', user_lang), show_alert=True)
        return SHOWING_INFO

async def audio_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=main_msg_id)
            except:
                pass
        
        url = context.user_data.get('video_url')
        if not url:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=t('error_generic', user_lang))
            return CHOOSING_ACTION
        
        status_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=t('downloading', user_lang, percent="0%"))
        title_safe = "".join(c for c in context.user_data['video_info'].get('title', 'audio') if c.isalnum() or c in " -_")
        
        ydl_opts_base = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'ffmpeg_location': FFMPEG_LOCATION,
            'outtmpl': os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
        }
        
        # استفاده از سیستم سه‌لایه
        success, info, direct_link = await download_with_three_layer(
            url, ydl_opts_base, user_lang, status_msg, context.bot, update.effective_chat.id
        )
        
        if not success:
            # اگر لینک مستقیم ایجاد شد
            if direct_link:
                await status_msg.delete()
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"{t('download_direct_link_title', user_lang)}\n\n{t('download_direct_link_message', user_lang, link=direct_link)}",
                    parse_mode="HTML"
                )
                context.user_data.clear()
                return CHOOSING_ACTION
            else:
                # خطای دیگر
                await status_msg.edit_text(t('error_generic', user_lang))
                return CHOOSING_ACTION
        
        # اگر دانلود موفق بود
        pattern = os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.*')
        files = glob.glob(pattern)
        audio_file = None
        for f in files:
            if f.lower().endswith('.mp3'):
                audio_file = f
                break
        
        if not audio_file or not os.path.exists(audio_file):
            await status_msg.edit_text(t('error_generic', user_lang))
            return CHOOSING_ACTION
        
        title = info.get('title', 'Unknown')
        await status_msg.edit_text(t('uploading', user_lang))
        
        with open(audio_file, 'rb') as file:
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=file,
                caption=f"✅ صوت دانلود شد!\n🎵 {title}",
                title=title,
                performer=info.get('uploader', 'Unknown')
            )
        
        os.remove(audio_file)
        await status_msg.delete()
        increment_daily_download(update.effective_user.id)
        context.user_data.clear()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t('download_success', user_lang),
            reply_markup=get_main_keyboard(user_lang)
        )
        return CHOOSING_ACTION
        
    except Exception as e:
        logger.error(f"خطا در دانلود صوت: {e}", exc_info=True)
        user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
        await context.bot.send_message(chat_id=update.effective_chat.id, text=t('error_generic', user_lang))
        return CHOOSING_ACTION

async def quality_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        selected_height = int(query.data.replace("quality_", ""))
        # دریافت داده‌های تازه از پایگاه داده به جای استفاده از cache
        stats = get_user_stats(user_id)
        user_plan = stats['plan']
        max_allowed = get_max_quality_allowed(user_plan)
        
        if selected_height > max_allowed:
            await query.answer(t('quality_locked_message', user_lang), show_alert=True)
            return SELECTING_QUALITY
        
        await query.answer()
        
        url = context.user_data.get('video_url')
        
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=main_msg_id)
            except:
                pass
        
        status_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=t('downloading', user_lang, percent="0%"))
        
        title_safe = "".join(c for c in context.user_data['video_info'].get('title', 'video') if c.isalnum() or c in " -_")
        
        ydl_opts_base = {
            'format': f'bestvideo[height<={selected_height}]+bestaudio/best[height<={selected_height}]',
            'merge_output_format': 'mp4',
            'ffmpeg_location': FFMPEG_LOCATION,
            'outtmpl': os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
            'progress_hooks': [ProgressHook(context.bot, update.effective_chat.id, status_msg)],
        }
        
        # استفاده از سیستم سه‌لایه
        success, info, direct_link = await download_with_three_layer(
            url, ydl_opts_base, user_lang, status_msg, context.bot, update.effective_chat.id
        )
        
        if not success:
            # اگر لینک مستقیم ایجاد شد
            if direct_link:
                await status_msg.delete()
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"{t('download_direct_link_title', user_lang)}\n\n{t('download_direct_link_message', user_lang, link=direct_link)}",
                    parse_mode="HTML"
                )
                context.user_data.clear()
                return CHOOSING_ACTION
            else:
                # خطای دیگر
                await status_msg.edit_text(t('error_generic', user_lang))
                return CHOOSING_ACTION
        
        pattern = os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.*')
        files = glob.glob(pattern)
        video_file = None
        for f in files:
            if f.lower().endswith(('.mp4', '.webm')):
                video_file = f
                break
        
        if not video_file or not os.path.exists(video_file):
            await status_msg.edit_text(t('error_generic', user_lang))
            return CHOOSING_ACTION
        
        title = info.get('title', 'Unknown')
        await status_msg.edit_text(t('uploading', user_lang))
        
        base_caption = f"✅ ویدیو با موفقیت دانلود گردید.\n📹 {title}"
        selected_lang = context.user_data.get('selected_subtitle_lang')
        
        if not selected_lang:
            with open(video_file, 'rb') as file:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=file,
                    caption=base_caption,
                    supports_streaming=True
                )
            os.remove(video_file)
        else:
            # دانلود زیرنویس
            sub_ydl_opts = {
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': False,
                'subtitleslangs': [selected_lang],
                'subtitlesformat': 'srt',
                'outtmpl': os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.{selected_lang}.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }
            
            sub_file = None
            try:
                with yt_dlp.YoutubeDL(sub_ydl_opts) as ydl_sub:
                    ydl_sub.download([url])
                
                sub_pattern = os.path.join(DOWNLOADS_FOLDER, f'{title_safe}.{selected_lang}.*')
                sub_files = glob.glob(sub_pattern)
                sub_file = max(sub_files, key=os.path.getctime) if sub_files else None
            except Exception as e:
                logger.error(f"خطا در دانلود زیرنویس: {e}")
            
            if user_plan == 'professional' and sub_file:
                burned_file = os.path.join(DOWNLOADS_FOLDER, f'{title_safe}_hardsub.mp4')
                escaped_sub = sub_file.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                cmd = [
                    FFMPEG_LOCATION, '-i', video_file,
                    '-vf', f"subtitles='{escaped_sub}':force_style='Fontsize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,BackColour=&H80000000&,Alignment=2'",
                    '-c:a', 'copy', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-y', burned_file
                ]
                result = subprocess.run(cmd, capture_output=True)
                
                if result.returncode == 0 and os.path.exists(burned_file):
                    caption = base_caption + "\n" + get_string('hard_sub_added')
                    with open(burned_file, 'rb') as f:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=f,
                            caption=caption,
                            supports_streaming=True
                        )
                    os.remove(burned_file)
                else:
                    logger.error(f"هاردساب شکست خورد: {result.stderr.decode('utf-8', errors='ignore')}")
                    caption = base_caption + "\n⚠️ حک زیرنویس ممکن نشد، زیرنویس جداگانه ارسال گردید."
                    with open(video_file, 'rb') as f:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=f,
                            caption=caption,
                            supports_streaming=True
                        )
                    if sub_file:
                        with open(sub_file, 'rb') as s:
                            await context.bot.send_document(
                                chat_id=update.effective_chat.id,
                                document=s,
                                caption=get_string('subtitle_guide'),
                                filename=f"{title_safe}.srt"
                            )
                    os.remove(sub_file)
            
            os.remove(video_file)
        
        await status_msg.delete()
        increment_daily_download(update.effective_user.id)
        context.user_data.clear()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t('download_success', user_lang),
            reply_markup=get_main_keyboard(user_lang)
        )
        return CHOOSING_ACTION
        
    except Exception as e:
        logger.error(f"خطا در دانلود ویدیو: {e}", exc_info=True)
        try:
            user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
            await context.bot.send_message(chat_id=update.effective_chat.id, text=t('error_generic', user_lang))
        except:
            pass
        return CHOOSING_ACTION

async def cancel_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        main_msg_id = context.user_data.get('main_info_message_id')
        if main_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=main_msg_id)
            except:
                pass
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        context.user_data.clear()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=t('cancelled', user_lang), reply_markup=get_main_keyboard(user_lang))
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در لغو دانلود: {e}", exc_info=True)
        return CHOOSING_ACTION

async def show_vip_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        keyboard = [
            [InlineKeyboardButton(
                t('vip_premium_7days', user_lang), 
                url=f"https://t.me/{SALES_ADMIN_USERNAME}?text={t('vip_consult_premium_7days', user_lang)}"
            )],
            [InlineKeyboardButton(
                t('vip_premium_1month', user_lang), 
                url=f"https://t.me/{SALES_ADMIN_USERNAME}?text={t('vip_consult_premium_1month', user_lang)}"
            )],
            [InlineKeyboardButton(
                t('vip_professional_7days', user_lang), 
                url=f"https://t.me/{SALES_ADMIN_USERNAME}?text={t('vip_consult_professional_7days', user_lang)}"
            )],
            [InlineKeyboardButton(
                t('vip_professional_1month', user_lang), 
                url=f"https://t.me/{SALES_ADMIN_USERNAME}?text={t('vip_consult_professional_1month', user_lang)}"
            )],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            t('vip_plans_text', user_lang),
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در نمایش پلن‌های VIP: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

# ======================== سیستم عضویت اجباری (Force Join) ========================
# مقدار پیش‌فرض برای سازگاری با کد قدیمی
FORCE_JOIN_CHANNELS = ["@PeakTeam"]

def get_force_join_config() -> dict:
    """
    دریافت تنظیمات عضویت اجباری از config.json
    """
    config = load_config()
    force_join = config.get('force_join', {})
    
    # مقدار پیش‌فرض در صورت عدم وجود
    if not force_join:
        force_join = {
            "enabled": True,
            "channels": ["@PeakTeam"]
        }
        config['force_join'] = force_join
        save_config(config)
    
    return force_join

def save_force_join_config(force_join: dict):
    """
    ذخیره تنظیمات عضویت اجباری در config.json
    """
    config = load_config()
    config['force_join'] = force_join
    save_config(config)

async def check_force_join(user_id: int, bot) -> bool:
    """
    بررسی عضویت کاربر در کانال‌های اجباری
    همیشه وضعیت زنده را از API تلگرام بررسی می‌کند (بدون کش)
    
    Args:
        user_id: شناسه کاربر
        bot: نمونه ربات تلگرام
    
    Returns:
        True اگر کاربر عضو همه کانال‌ها باشد، False در غیر این صورت
    """
    if is_admin(user_id):
        return True  # ادمین‌ها نیاز به چک ندارند
    
    # بارگذاری تنظیمات از پایگاه داده
    force_join_config = get_force_join_config()
    
    # اگر غیرفعال باشد، بررسی نمی‌شود
    if not force_join_config.get('enabled', False):
        return True
    
    channels = force_join_config.get('channels', [])
    
    if not channels:
        return True  # اگر لیست خالی باشد، بررسی نمی‌شود
    
    for channel in channels:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.error(f"خطا در بررسی عضویت در کانال {channel}: {e}")
            # در صورت خطا، برای امنیت بیشتر False برمی‌گردانیم
            return False
    
    return True

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    کنترل‌کننده callback برای بررسی مجدد عضویت کاربر
    این تابع عضویت را به صورت زنده بررسی می‌کند و در صورت عضویت، منوی اصلی را نمایش می‌دهد
    """
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # بررسی عضویت به صورت زنده
        if await check_force_join(user_id, context.bot):
            # کاربر عضو است - نمایش منوی اصلی
            username = update.effective_user.first_name or "User"
            stats = get_user_stats(user_id)
            plan_emoji = {'free': '🆓', 'premium': '⭐', 'professional': '👑'}
            emoji = plan_emoji.get(stats['plan'], '🆓')
            limit = get_plan_limit(stats['plan'])
            referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
            actual_referral_count = get_referral_count(user_id)
            
            welcome_text = t(
                'welcome',
                user_lang,
                username=username,
                emoji=emoji,
                plan=stats['plan'].upper(),
                current=stats['downloads_today'],
                limit=limit,
                remaining=stats['remaining_today'],
                referral_link=referral_link,
                ref_count=actual_referral_count
            )
            
            reply_markup = get_main_keyboard(user_lang)
            verified_text = t('membership_verified', user_lang)
            
            try:
                await query.message.delete()
            except:
                pass
            
            # نمایش پیام موفقیت عضویت
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=verified_text,
                parse_mode="HTML"
            )
            
            # نمایش هشدار راه‌اندازی مجدد (راه حل موقت UX)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=restart_hint_text,
                parse_mode="HTML"
            )
            
            # نمایش منوی اصلی
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            return CHOOSING_ACTION
        else:
            # کاربر هنوز عضو نیست - نمایش دوباره پیام عضویت اجباری
            await send_force_join_message(update, context, user_lang)
            return CHOOSING_ACTION
            
    except Exception as e:
        logger.error(f"خطا در بررسی عضویت: {e}", exc_info=True)
        return CHOOSING_ACTION

async def send_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_lang: str = None) -> None:
    """
    ارسال پیام عضویت اجباری با دکمه لینک به کانال و دکمه بررسی عضویت
    """
    if user_lang is None:
        user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
    
    # بارگذاری تنظیمات از پایگاه داده
    force_join_config = get_force_join_config()
    channels = force_join_config.get('channels', [])
    
    if not channels:
        return
    
    # متن پیام بر اساس زبان
    if user_lang == 'fa':
        text = "⚠️ برای استفاده از ربات ابتدا باید عضو کانال زیر شوید 👇"
    else:
        text = "⚠️ To use this bot, you must first join the channel below 👇"
    
    # ساخت دکمه‌ها برای همه کانال‌ها
    join_button_text = t('force_join_button', user_lang)
    check_button_text = t('check_membership', user_lang)
    
    keyboard = []
    # افزودن دکمه عضویت برای هر کانال
    for channel in channels:
        channel_username = channel.lstrip('@')
        channel_url = f"https://t.me/{channel_username}"
        keyboard.append([InlineKeyboardButton(join_button_text, url=channel_url)])
    
    # افزودن دکمه بررسی عضویت در انتها
    keyboard.append([InlineKeyboardButton(check_button_text, callback_data="check_membership")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # ارسال پیام
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        except:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        username = update.effective_user.first_name or "User"
        
        referrer_id = None
        if context.args:
            arg = context.args[0]
            if arg.startswith("ref_"):
                try:
                    referrer_id = int(arg[4:])
                except ValueError:
                    pass
        
        profile = get_user_profile(user_id)
        if profile is None:
            create_user(user_id, username, referrer_id)
        
        # بررسی وجود زبان کاربر
        user_lang = get_user_language(user_id)
        
        # اگر زبان وجود نداشت، صفحه انتخاب زبان نمایش داده می‌شود
        if user_lang is None:
            return await show_language_selection(update, context)
        
        # اگر زبان وجود داشت، پیام خوش‌آمدگویی نمایش داده می‌شود
        stats = get_user_stats(user_id)
        plan_emoji = {'free': '🆓', 'premium': '⭐', 'professional': '👑'}
        emoji = plan_emoji.get(stats['plan'], '🆓')
        limit = get_plan_limit(stats['plan'])
        referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        actual_referral_count = get_referral_count(user_id)
        
        welcome_text = t(
            'welcome',
            user_lang,
            username=username,
            emoji=emoji,
            plan=stats['plan'].upper(),
            current=stats['downloads_today'],
            limit=limit,
            remaining=stats['remaining_today'],
            referral_link=referral_link,
            ref_count=actual_referral_count
        )
        
        reply_markup = get_main_keyboard(user_lang)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در فرمان /start: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t('error_generic', DEFAULT_LANG)
        )
        return CHOOSING_ACTION

async def show_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش صفحه انتخاب زبان برای کاربرانی که زبان انتخاب نکرده‌اند"""
    try:
        # برای نمایش صفحه انتخاب زبان، از هر دو زبان استفاده می‌کنیم
        # یا می‌توانیم از زبان پیش‌فرض استفاده کنیم
        text_fa = t('language_selection_prompt', 'fa')
        text_en = t('language_selection_prompt', 'en')
        text = f"{text_fa}\n\n{text_en}"
        
        keyboard = [
            [InlineKeyboardButton(t('language_fa', 'fa'), callback_data="initial_lang_fa")],
            [InlineKeyboardButton(t('language_en', 'en'), callback_data="initial_lang_en")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return SELECTING_LANGUAGE
    except Exception as e:
        logger.error(f"خطا در نمایش انتخاب زبان: {e}", exc_info=True)
        await update.message.reply_text(t('error_generic', DEFAULT_LANG))
        return SELECTING_LANGUAGE

async def handle_initial_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> int:
    """مدیریت انتخاب زبان اولیه و نمایش پیام خوش‌آمدگویی"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        username = update.effective_user.first_name or "User"
        
        # ذخیره زبان کاربر
        set_user_language(user_id, lang)
        
        # نمایش پیام تأیید
        confirm_text = t('language_selected', lang)
        await query.edit_message_text(confirm_text)
        
        # نمایش پیام خوش‌آمدگویی
        stats = get_user_stats(user_id)
        plan_emoji = {'free': '🆓', 'premium': '⭐', 'professional': '👑'}
        emoji = plan_emoji.get(stats['plan'], '🆓')
        limit = get_plan_limit(stats['plan'])
        referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        actual_referral_count = get_referral_count(user_id)
        
        welcome_text = t(
            'welcome',
            lang,
            username=username,
            emoji=emoji,
            plan=stats['plan'].upper(),
            current=stats['downloads_today'],
            limit=limit,
            remaining=stats['remaining_today'],
            referral_link=referral_link,
            ref_count=actual_referral_count
        )
        
        reply_markup = get_main_keyboard(lang)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در انتخاب زبان اولیه: {e}", exc_info=True)
        await query.edit_message_text(t('error_generic', DEFAULT_LANG))
        return CHOOSING_ACTION

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id)
        if user_lang is None:
            # اگر زبان وجود نداشت، به انتخاب زبان هدایت می‌شود
            return await show_language_selection(update, context)
        
        # بررسی عضویت اجباری قبل از هرگونه اقدام در منوی اصلی
        if not await check_force_join(user_id, context.bot):
            await send_force_join_message(update, context, user_lang)
            return CHOOSING_ACTION
        
        text = update.message.text.strip()
        # مقایسه با متن‌های ترجمه‌شده
        if text == t('download', user_lang):
            
            can_dl, current, limit = can_user_download(user_id)
            if not can_dl and is_payments_enabled():
                # فقط اگر سیستم پولی فعال است، پیام محدودیت نمایش داده می‌شود
                await update.message.reply_text(
                    t('download_limit_reached', user_lang, limit=limit, current=current),
                    parse_mode="HTML"
                )
                return CHOOSING_ACTION
            
            await update.message.reply_text(t('enter_link', user_lang), reply_markup=ReplyKeyboardRemove())
            return WAITING_LINK
        
        elif text == t('profile', user_lang):
            stats = get_user_stats(user_id)
            # استفاده از ترجمه برای نام پلن
            plan_names = {
                'free': t('free', user_lang) if 'free' in load_locale(user_lang) else ('رایگان' if user_lang == 'fa' else 'Free'),
                'premium': t('premium', user_lang) if 'premium' in load_locale(user_lang) else ('پریمیوم' if user_lang == 'fa' else 'Premium'),
                'professional': t('professional', user_lang) if 'professional' in load_locale(user_lang) else ('حرفه‌ای' if user_lang == 'fa' else 'Professional')
            }
            plan_emoji = {'free': '🆓', 'premium': '⭐', 'professional': '👑'}
            plan_name = plan_names.get(stats['plan'], 'نامشخص' if user_lang == 'fa' else 'Unknown')
            emoji = plan_emoji.get(stats['plan'], '🆓')
            actual_referral_count = get_referral_count(user_id)
            
            joined_at = stats.get('joined_at', stats.get('created_at', 'نامشخص'))
            joined_date = joined_at[:10] if joined_at and joined_at != 'نامشخص' else ('نامشخص' if user_lang == 'fa' else 'Unknown')
            
            downloads_today = stats.get('downloads_today', 0)
            downloads_total = stats.get('downloads_total', 0)
            
            profile_text = t(
                'profile_text',
                user_lang,
                user_id=user_id,
                username=stats.get('username', 'نامشخص' if user_lang == 'fa' else 'Unknown'),
                emoji=emoji,
                plan_name=plan_name,
                joined_date=joined_date,
                downloads_today=downloads_today,
                downloads_total=downloads_total,
                ref_count=actual_referral_count
            )
            
            await update.message.reply_text(profile_text, parse_mode="HTML")
            return CHOOSING_ACTION
        
        elif text == t('subscription', user_lang):
            stats = get_user_stats(user_id)
            limit = get_plan_limit(stats['plan'])
            plan_names = {
                'free': t('free', user_lang) if 'free' in load_locale(user_lang) else ('رایگان' if user_lang == 'fa' else 'Free'),
                'premium': t('premium', user_lang) if 'premium' in load_locale(user_lang) else ('پریمیوم' if user_lang == 'fa' else 'Premium'),
                'professional': t('professional', user_lang) if 'professional' in load_locale(user_lang) else ('حرفه‌ای' if user_lang == 'fa' else 'Professional')
            }
            plan_name = plan_names.get(stats['plan'], 'نامشخص' if user_lang == 'fa' else 'Unknown')
            
            subscription_text = t(
                'subscription_text',
                user_lang,
                plan_name=plan_name,
                limit=limit,
                downloads_today=stats.get('downloads_today', 0),
                remaining=stats.get('remaining_today', 0)
            )
            
            await update.message.reply_text(subscription_text, parse_mode="HTML")
            return CHOOSING_ACTION
        
        elif text == t('buy_vip', user_lang):
            # بررسی اینکه آیا سیستم پولی فعال است
            if not is_payments_enabled():
                await update.message.reply_text(t('payments_disabled', user_lang), reply_markup=get_main_keyboard(user_lang))
                return CHOOSING_ACTION
            return await show_vip_plans(update, context)
        
        elif text == t('referral', user_lang):
            referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
            actual_referral_count = get_referral_count(user_id)
            
            referral_text = t(
                'referral_text',
                user_lang,
                link=referral_link,
                count=actual_referral_count
            )
            
            await update.message.reply_text(referral_text, parse_mode="HTML", disable_web_page_preview=True)
            return CHOOSING_ACTION
        
        elif text == t('help', user_lang):
            return await show_help_main(update, context)
        
        elif text == t('about', user_lang):
            return await show_about_menu(update, context)
        
        elif text == t('support', user_lang):
            return await support_entry(update, context)
        
        elif text == t('settings', user_lang):
            return await show_settings(update, context, user_lang)
        
        elif text == t('exit', user_lang):
            exit_text = t('exit', user_lang) if 'exit' in load_locale(user_lang) else ("✅ با موفقیت خارج شدید!" if user_lang == 'fa' else "✅ Exited successfully!")
            await update.message.reply_text(exit_text, reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در پردازش دکمه‌ها: {e}", exc_info=True)
        user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def get_video_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # بررسی عضویت اجباری قبل از دانلود
        if not await check_force_join(user_id, context.bot):
            await send_force_join_message(update, context, user_lang)
            return WAITING_LINK
        
        text = update.message.text.strip()
        if "youtube.com" in text or "youtu.be" in text:
            context.user_data['video_url_from_search'] = text
            return await show_video_info_from_search(update, context)
        else:
            return await search_youtube_videos(update, context, text)
    except Exception as e:
        logger.error(f"خطا در دریافت اطلاعات ویدیو: {e}", exc_info=True)
        user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return WAITING_LINK

async def support_entry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """هندلر callback query برای دکمه پشتیبانی با UX بهبود یافته"""
    query = update.callback_query
    
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG

        # بررسی عضویت اجباری قبل از پشتیبانی هوش مصنوعی
        if not await check_force_join(user_id, context.bot):
            await query.answer()
            await send_force_join_message(update, context, user_lang)
            return CHOOSING_ACTION

        # شروع یک جلسه جدید پشتیبانی هوشمند → پاک کردن کانتکست قبلی
        context.user_data.pop("active_ai_conversation_id", None)
        context.user_data.pop("support_history", None)
        context.user_data.pop("support_history_initialized_from_conversation", None)
        
        # نمایش Alert (این خودش callback query را answer می‌کند)
        # استفاده از show_alert=True برای نمایش Alert مودال
        await query.answer(t('support_entry_alert', user_lang), show_alert=True)        
        # تأخیر کوتاه برای اطمینان از نمایش Alert
        await asyncio.sleep(0.5)
        
        # ارسال پیام موقت
        temp_message = await query.message.reply_text(t('support_connecting', user_lang))
        # تأخیر ۲ ثانیه
        await asyncio.sleep(2)
        
        # دریافت نام کاربر
        first_name = update.effective_user.first_name or (update.effective_user.username or t('support_user_dear', user_lang))
        
        # ویرایش پیام به پیام خوش‌آمدگویی
        welcome_text = t('support_welcome_ai', user_lang, name=first_name)
        
        keyboard = [
            [InlineKeyboardButton(t('ai_history_button', user_lang), callback_data="ai_history")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await temp_message.edit_text(welcome_text, parse_mode="HTML", reply_markup=reply_markup)
        
        return AI_SUPPORT
    except Exception as e:
        logger.error(f"خطا در ورود به پشتیبانی: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await query.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def support_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """هندلر پیام متنی برای دکمه پشتیبانی (keyboard button) - نمایش inline button برای Alert"""
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # نمایش پیام با inline button برای فعال‌سازی Alert
        keyboard = [[InlineKeyboardButton(t('support_enter_button_text', user_lang), callback_data="support_ai")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            t('support_enter_button', user_lang),
            reply_markup=reply_markup
        )
        
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در ورود به پشتیبانی: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def handle_ai_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'

        user_message = update.message.text.strip()
        if not user_message:
            await update.message.reply_text(t('support_please_text', user_lang))
            return AI_SUPPORT

        # مدیریت گفتگوهای ذخیره‌شده (Chat History)
        active_conv_id = context.user_data.get("active_ai_conversation_id")
        if not active_conv_id:
            # شروع گفتگو جدید و ساخت عنوان از اولین پیام کاربر
            active_conv_id = _create_ai_conversation(user_id, user_message)
            context.user_data["active_ai_conversation_id"] = active_conv_id
            context.user_data.pop("support_history", None)
            context.user_data.pop("support_history_initialized_from_conversation", None)

        # بررسی محدودیت پشتیبانی هوشمند برای کاربران FREE (با زمان ریست)
        can_use_ai, ai_current_count, ai_limit, reset_time_iso = check_ai_support_limit(user_id)
        if not can_use_ai:
            # کاربر به سقف مجاز رسیده است - محاسبه زمان ریست و نمایش به کاربر
            reset_time_str = ""
            if reset_time_iso:
                try:
                    rt = datetime.fromisoformat(reset_time_iso)
                    # نمایش به صورت ساعت:دقیقه در زمان محلی سرور (به عنوان زمان محلی کاربر)
                    reset_time_str = rt.strftime("%H:%M")
                except Exception:
                    reset_time_str = ""

            if user_lang == 'fa':
                limit_message = (
                    "⚠️ محدودیت پشتیبانی هوشمند\n"
                    "شما به سقف مجاز نسخه رایگان رسیده‌اید.\n"
                    f"پشتیبانی هوشمند شما در ساعت {reset_time_str} دوباره فعال می‌شود."
                    if reset_time_str
                    else "⚠️ محدودیت پشتیبانی هوشمند\nشما به سقف مجاز نسخه رایگان رسیده‌اید."
                )
            else:
                limit_message = (
                    f"⚠️ AI Support Limit Reached\nYour AI support will reset at {reset_time_str}."
                    if reset_time_str
                    else "⚠️ AI Support Limit Reached\nYou have reached the free tier limit."
                )

            # طبق قوانین: درخواست کاربر پردازش نمی‌شود و پیامی هم به AI فرستاده نمی‌شود
            await update.message.reply_text(limit_message)
            return AI_SUPPORT

        # گرفتن وضعیت کاربر (داینامیک)
        stats = get_user_stats(user_id)
        plan = stats.get('plan', 'free').upper()
        downloads_left = stats.get('remaining_today', 0)
        # محاسبه تعداد باقی‌مانده AI برای نمایش در context
        daily_ai_left = ai_limit - ai_current_count if plan == 'FREE' else 999
        paid_enabled = is_payments_enabled()

        user_context = f"""User context:
- Plan: {plan}
- Daily downloads left: {downloads_left}
- Daily AI support left: {daily_ai_left}
- Language: {'Persian (fa)' if user_lang == 'fa' else 'English (en)'}
- Paid system enabled: {str(paid_enabled).lower()}"""

        full_system_prompt = user_context + "\n\n" + """
You are NOVA, the official intelligent support assistant of the PeakTube Telegram bot.

Your identity:
- You are not a general chatbot.
- You are the official digital support agent designed and developed by the Peakeam.
- Regarding the AI ​​API, if a question is asked by the user, answer that I was designed and developed by the PeakTeam group and no AI API is used (very important).
- If users ask the Peak team about building a bot for platforms like PeakTube, explain that it is possible.
- you deployed by PeakTeam group

If the user asks about your model, AI, or how you work:
- You must say that you were designed and developed by the PeakTube team.
- Do not mention OpenAI, GPT, or any external company or technology.

Core scope (STRICT RULE):
- PeakTube Smart Support is designed ONLY for YouTube.
- You must say that PeakTube supports ONLY YouTube downloads.
- Even if PeakTube supports other platforms internally, you must NOT mention them.
- If the user asks about any other platform (Instagram, TikTok, etc),
  politely say that PeakTube Smart Support only handles YouTube.
  
Your domain:
You only help with YouTube-related features inside PeakTube:
- Downloading YouTube videos
- YouTube errors and issues
- Video quality and formats (YouTube)
- Daily limits
- Subscriptions and plans
- Account and settings
- IDM download links (YouTube)
- Language settings

Behavior:
- Be polite, friendly, and professional.
- Sound natural and human.
- Act like real customer support.

Language rules (VERY IMPORTANT):
- You can ONLY speak in Persian (fa) or English (en).(very important)
- You must NEVER use any other language.(very important)
- If user language is Persian, reply fully in Persian.
- If user language is English, reply fully in English.
- Do not mix languages in one response.

Restrictions:
- Never mention being an AI model.
- Never mention OpenAI, GPT, or any external system.
- Never answer outside PeakTube and YouTube domain.
- Never talk about politics, religion, or personal topics.
"""

        # آماده‌سازی هیستوری برای ارسال به مدل (بر اساس گفتگوهای ذخیره‌شده)
        support_history_initialized = context.user_data.get("support_history_initialized_from_conversation", False)
        support_history = []
        if not support_history_initialized:
            # اولین بار برای این سشن: سیستم پرامپت + پیام‌های قبلی گفتگو (در صورت وجود)
            support_history.append({"role": "system", "content": full_system_prompt})
            conv = _get_ai_conversation(user_id, active_conv_id)
            if conv:
                # فقط آخرین چند پیام را برای کانتکست مدل می‌فرستیم
                for msg in conv.get("messages", [])[-6:]:
                    support_history.append(
                        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                    )
            context.user_data["support_history"] = support_history
            context.user_data["support_history_initialized_from_conversation"] = True
        else:
            support_history = context.user_data.get("support_history", [])

        # افزودن پیام جدید کاربر به هیستوری و دیسک
        support_history.append({"role": "user", "content": user_message})
        _append_ai_conversation_message(user_id, active_conv_id, "user", user_message)

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # تغییرات بهینه‌سازی: استفاده از http_client مشترک + max_tokens کمتر + history محدود
        history = support_history[-6:]  # فقط ۶ پیام آخر

        response = await http_client.post(
            f"{OPENROUTER_BASE_URL}/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": f"https://t.me/{BOT_USERNAME}",
                "X-Title": "PeakTube Support Bot",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 300,  # کمتر برای سرعت
            }
        )
        response.raise_for_status()
        data = response.json()
        full_reply = data['choices'][0]['message']['content'].strip()

        if not full_reply:
            await update.message.reply_text(t('support_no_response', user_lang))
            return AI_SUPPORT

        # افزایش تعداد استفاده از پشتیبانی هوشمند (فقط برای کاربران FREE)
        increment_ai_support_usage(user_id)

        # ذخیره پاسخ در هیستوری و دیسک
        support_history.append({"role": "assistant", "content": full_reply})
        context.user_data["support_history"] = support_history
        _append_ai_conversation_message(user_id, active_conv_id, "assistant", full_reply)

        await update.message.reply_text(full_reply)

        keyboard = [
            [InlineKeyboardButton(t('ai_history_button', user_lang), callback_data="ai_history")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
        ]
        await update.message.reply_text(t('support_another_question', user_lang), reply_markup=InlineKeyboardMarkup(keyboard))

        return AI_SUPPORT

    except (httpx.TimeoutException, httpx.HTTPStatusError):
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_traffic_high', user_lang))
        return AI_SUPPORT

    except Exception as e:
        logger.error(f"خطا در پشتیبانی هوشمند: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_error', user_lang))
        return AI_SUPPORT

        # اضافه کردن پاسخ به تاریخچه
        context.user_data['support_history'].append({"role": "assistant", "content": full_reply})

        # ارسال پاسخ
        await update.message.reply_text(full_reply)

        # دکمه بازگشت
        keyboard = [[InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]]
        await update.message.reply_text(t('support_another_question', user_lang), reply_markup=InlineKeyboardMarkup(keyboard))

        return AI_SUPPORT

    except (httpx.TimeoutException, httpx.HTTPStatusError):
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_traffic_high', user_lang))
        return AI_SUPPORT

    except Exception as e:
        logger.error(f"خطا در پشتیبانی هوشمند: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_error', user_lang))
        return AI_SUPPORT

        # اضافه کردن پاسخ AI به تاریخچه
        context.user_data['support_history'].append({"role": "assistant", "content": full_reply})

        # ارسال پاسخ به کاربر
        await update.message.reply_text(full_reply)

        # دکمه بازگشت به منو
        keyboard = [[InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]]
        await update.message.reply_text(t('support_another_question', user_lang), reply_markup=InlineKeyboardMarkup(keyboard))

        return AI_SUPPORT

    except (httpx.TimeoutException, httpx.HTTPStatusError):
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_traffic_high', user_lang))
        return AI_SUPPORT

    except Exception as e:
        logger.error(f"خطا در پشتیبانی هوشمند: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or 'fa'
        await update.message.reply_text(t('support_error', user_lang))
        return AI_SUPPORT

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user_lang: str) -> int:
    """نمایش منوی تنظیمات"""
    try:
        text = t('settings_title', user_lang)
        
        keyboard = [
            [InlineKeyboardButton(t('language_selection', user_lang), callback_data="settings_language")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در نمایش تنظیمات: {e}", exc_info=True)
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def settings_language_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش پنل انتخاب زبان"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_lang = get_user_language(user_id)
    
    text = t('select_language', user_lang)
    
    keyboard = [
        [InlineKeyboardButton(t('language_fa', user_lang), callback_data="set_lang_fa")],
        [InlineKeyboardButton(t('language_en', user_lang), callback_data="set_lang_en")],
        [InlineKeyboardButton(t('back', user_lang), callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    return CHOOSING_ACTION

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> int:
    """تنظیم زبان کاربر"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    set_user_language(user_id, lang)
    
    # پیام تأیید با زبان جدید
    if lang == 'fa':
        message = t('language_changed_fa', lang)
    else:
        message = t('language_changed_en', lang)
    
    keyboard = [[InlineKeyboardButton(t('back', lang), callback_data="settings_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)
    
    # به‌روزرسانی کیبورد اصلی با زبان جدید
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t('back_to_menu', lang),
            reply_markup=get_main_keyboard(lang)
        )
    except:
        pass
    
    return CHOOSING_ACTION

async def settings_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بازگشت به منوی تنظیمات"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_lang = get_user_language(user_id)
    
    return await show_settings_callback(update, context, user_lang)

async def show_about_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش منوی درباره ما"""
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # اگر از callback query است
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            message = query.message
        else:
            message = update.message
        
        text = t('about_menu_title', user_lang)
        
        keyboard = [
            [InlineKeyboardButton(t('about_peaktube', user_lang), callback_data="about_peaktube")],
            [InlineKeyboardButton(t('about_future_vision', user_lang), callback_data="about_future_vision")],
            [InlineKeyboardButton(t('about_team', user_lang), callback_data="about_team")],
            [InlineKeyboardButton(t('about_why', user_lang), callback_data="about_why")],
            [InlineKeyboardButton(t('about_terms', user_lang), callback_data="about_terms")],
            [InlineKeyboardButton(t('about_contact', user_lang), callback_data="about_contact")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش منوی درباره ما: {e}", exc_info=True)
        user_lang = get_user_language(update.effective_user.id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def about_peaktube(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش درباره PeakTube"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        # پاسخ فوری به callback
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_peaktube_content', user_lang)
        
        # بررسی اینکه متن خالی نباشد
        if not text or text == 'about_peaktube_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        # اگر متن خیلی طولانی است، آن را تقسیم می‌کنیم
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            # اگر edit_message_text خطا داد، از reply_text استفاده می‌کنیم
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش درباره PeakTube: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_future_vision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش چشم‌انداز آینده"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_future_vision_content', user_lang)
        
        if not text or text == 'about_future_vision_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش چشم‌انداز آینده: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش تیم PeakTube"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_team_content', user_lang)
        
        if not text or text == 'about_team_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش تیم: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_why(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش چرا PeakTube"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_why_content', user_lang)
        
        if not text or text == 'about_why_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش چرا PeakTube: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_terms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش شرایط استفاده"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_terms_content', user_lang)
        
        if not text or text == 'about_terms_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش شرایط استفاده: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بخش تماس با ما"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        text = t('about_contact_content', user_lang)
        
        if not text or text == 'about_contact_content':
            text = "⚠️ محتوا در حال حاضر در دسترس نیست."
        
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        keyboard = [[InlineKeyboardButton(t('back', user_lang), callback_data="about_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as edit_error:
            logger.warning(f"خطا در ویرایش پیام، استفاده از reply_text: {edit_error}")
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
        
        return ABOUT_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش بخش تماس با ما: {e}", exc_info=True)
        try:
            await query.answer("خطا در نمایش محتوا", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def about_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بازگشت به منوی درباره ما"""
    query = update.callback_query
    if not query:
        return ABOUT_MENU
    
    try:
        await query.answer()
        return await show_about_menu(update, context)
    except Exception as e:
        logger.error(f"خطا در بازگشت به منوی درباره ما: {e}", exc_info=True)
        try:
            await query.answer("خطا در بازگشت", show_alert=True)
        except:
            pass
        return ABOUT_MENU

async def show_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user_lang: str) -> int:
    """نمایش منوی تنظیمات (برای callback)"""
    query = update.callback_query
    
    text = t('settings_title', user_lang)
    
    keyboard = [
        [InlineKeyboardButton(t('language_selection', user_lang), callback_data="settings_language")],
        [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    
    return CHOOSING_ACTION


async def show_ai_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش لیست تاریخچه گفتگوهای پشتیبانی هوشمند"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_lang = get_user_language(user_id) or DEFAULT_LANG

    conversations = _list_ai_conversations_sorted(user_id)

    if not conversations:
        text = t('ai_history_empty', user_lang)
        keyboard = [
            [InlineKeyboardButton(t('ai_history_back_to_support', user_lang), callback_data="ai_history_back")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
        ]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return AI_SUPPORT

    keyboard = []
    for idx, conv in enumerate(conversations, start=1):
        title = conv.get("title", "Conversation")
        last_updated = conv.get("last_updated")
        date_str = ""
        if last_updated:
            try:
                dt = datetime.fromisoformat(last_updated)
                date_str = dt.strftime("%Y/%m/%d")
            except Exception:
                date_str = last_updated[:10]

        display = f"{idx}. {title}"
        if date_str:
            display += f" – {date_str}"

        conv_id = conv.get("conversation_id")
        # ردیف شامل دکمه باز کردن و دکمه حذف
        keyboard.append(
            [
                InlineKeyboardButton(display, callback_data=f"ai_open:{conv_id}"),
                InlineKeyboardButton(t('ai_history_delete_button', user_lang), callback_data=f"ai_delete:{conv_id}"),
            ]
        )

    keyboard.append([InlineKeyboardButton(t('ai_history_back_to_support', user_lang), callback_data="ai_history_back")])
    keyboard.append([InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")])

    text = t('ai_history_title', user_lang)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return AI_SUPPORT


async def ai_history_back_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بازگشت از لیست تاریخچه به صفحه اصلی پشتیبانی هوشمند"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_lang = get_user_language(user_id) or DEFAULT_LANG

    first_name = update.effective_user.first_name or (update.effective_user.username or t('support_user_dear', user_lang))
    welcome_text = t('support_welcome_ai', user_lang, name=first_name)

    keyboard = [
        [InlineKeyboardButton(t('ai_history_button', user_lang), callback_data="ai_history")],
        [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
    ]

    await query.edit_message_text(welcome_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return AI_SUPPORT


async def ai_history_delete_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش پیام تایید حذف برای یک گفتگو"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_lang = get_user_language(user_id) or DEFAULT_LANG

    data = query.data.split(":", 1)
    if len(data) != 2:
        await query.edit_message_text(t('error_generic', user_lang))
        return AI_SUPPORT

    conv_id = data[1]
    # تأیید مالکیت گفتگو
    conv = _get_ai_conversation(user_id, conv_id)
    if not conv or conv.get("user_id") != user_id:
        await query.edit_message_text(t('ai_history_access_denied', user_lang))
        return AI_SUPPORT
    # متن تایید حذف
    text = t('ai_history_delete_confirm', user_lang)

    keyboard = [
        [InlineKeyboardButton("✅ OK", callback_data=f"ai_delete_confirm:{conv_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ai_history")],
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return AI_SUPPORT


async def ai_history_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """حذف نهایی گفتگو و به‌روزرسانی لیست / بازگشت به پشتیبانی"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_lang = get_user_language(user_id) or DEFAULT_LANG

    data = query.data.split(":", 1)
    if len(data) != 2:
        await query.edit_message_text(t('error_generic', user_lang))
        return AI_SUPPORT

    conv_id = data[1]

    # قبل از حذف، اطمینان از اینکه گفتگو متعلق به همین کاربر است
    conv = _get_ai_conversation(user_id, conv_id)
    if not conv or conv.get("user_id") != user_id:
        await query.edit_message_text(t('ai_history_access_denied', user_lang))
        return AI_SUPPORT

    # حذف از دیتابیس فقط برای همین کاربر
    deleted = _delete_ai_conversation(user_id, conv_id)

    # اگر گفتگو فعال بود، آن را ریست می‌کنیم
    active_id = context.user_data.get("active_ai_conversation_id")
    if active_id == conv_id:
        context.user_data.pop("active_ai_conversation_id", None)
        context.user_data.pop("support_history", None)
        context.user_data.pop("support_history_initialized_from_conversation", None)

    if not deleted:
        # اگر چیزی حذف نشد، لیست را فقط رفرش می‌کنیم
        await show_ai_history(update, context)
        return AI_SUPPORT

    # بعد از حذف: اگر گفتگو فعال بود، به منوی اصلی پشتیبانی برگردیم
    if active_id == conv_id:
        success_text = t('ai_history_delete_success', user_lang)
        first_name = update.effective_user.first_name or (update.effective_user.username or t('support_user_dear', user_lang))
        welcome_text = t('support_welcome_ai', user_lang, name=first_name)
        full_text = success_text + "\n\n" + welcome_text

        keyboard = [
            [InlineKeyboardButton(t('ai_history_button', user_lang), callback_data="ai_history")],
            [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
        ]
        await query.edit_message_text(full_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return AI_SUPPORT

    # اگر گفتگو فعال نبود، فقط لیست تاریخچه را رفرش می‌کنیم و پیام موفقیت می‌دهیم
    # ابتدا متن موفقیت را در همین پیام نشان می‌دهیم و سپس لیست را نمایش می‌دهیم
    await query.edit_message_text(t('ai_history_delete_success', user_lang))
    # نمایش مجدد تاریخچه (در پیام جدید)
    await show_ai_history(update, context)
    return AI_SUPPORT


async def open_ai_history_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """باز کردن یک گفتگو از تاریخچه و ادامه آن"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_lang = get_user_language(user_id) or DEFAULT_LANG

    data = query.data.split(":", 1)
    if len(data) != 2:
        await query.edit_message_text(t('error_generic', user_lang))
        return AI_SUPPORT

    conv_id = data[1]
    conv = _get_ai_conversation(user_id, conv_id)
    # تأیید مالکیت: گفتگو باید متعلق به همین کاربر باشد
    if not conv or conv.get("user_id") != user_id:
        await query.edit_message_text(t('ai_history_access_denied', user_lang))
        return AI_SUPPORT

    last_updated = conv.get("last_updated")
    if last_updated:
        try:
            dt = datetime.fromisoformat(last_updated)
            date_str = dt.strftime("%Y/%m/%d %H:%M")
        except Exception:
            date_str = last_updated
    else:
        date_str = "-"

    # متن هدر گفتگو + اطلاع از بارگذاری
    header_text = t(
        'ai_history_conversation_title',
        user_lang,
        title=conv.get("title", "Conversation"),
        date=date_str,
    )
    header_text += "\n\n" + t('ai_history_loaded_header', user_lang)

    keyboard = [
        [InlineKeyboardButton(t('ai_history_back_to_support', user_lang), callback_data="ai_history_back")],
        [InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")],
    ]

    chat_id = update.effective_chat.id

    # ویرایش پیام فعلی به هدر
    await query.edit_message_text(header_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    # نمایش تمام پیام‌های ذخیره‌شده به ترتیب زمانی (قدیمی‌تر اول)، فقط از دیتابیس
    messages = conv.get("messages", [])
    try:
        messages_sorted = sorted(
            messages,
            key=lambda m: m.get("timestamp", ""),
        )
    except Exception:
        messages_sorted = messages

    for msg in messages_sorted:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not content:
            continue
        # پیام‌ها فقط رندر می‌شوند؛ هیچ فراخوانی مجدد به AI انجام نمی‌شود
        try:
            await context.bot.send_message(chat_id=chat_id, text=content)
        except Exception as e:
            logger.warning(f"خطا در ارسال پیام تاریخچه برای کاربر {user_id}: {e}")

    # تنظیم گفتگو به عنوان گفتگو‌ی فعال برای ادامه چت
    context.user_data["active_ai_conversation_id"] = conv_id
    context.user_data.pop("support_history", None)
    context.user_data.pop("support_history_initialized_from_conversation", None)

    return AI_SUPPORT

async def user_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        profile = get_user_profile(user_id)
        data = (profile or {}).get('support', {}).get('last_admin_message') if profile else None
        if not data:
            await update.message.reply_text(t('no_message_inbox', user_lang))
            return CHOOSING_ACTION
        ts = data.get('timestamp', '')
        txt = data.get('text', '')
        caption = t('inbox_title', user_lang, time=ts, text=txt)
        keyboard = [[InlineKeyboardButton(t('back_to_menu', user_lang), callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(caption, parse_mode="HTML", reply_markup=reply_markup)
        return USER_REPLYING_SUPPORT
    except Exception as e:
        logger.error(f"خطا در /inbox: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return CHOOSING_ACTION

async def send_reply_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        if not update.message or not update.message.text:
            await update.message.reply_text("پاسخ نامعتبر است.")
            return USER_REPLYING_SUPPORT

        user_id = update.effective_user.id
        username = update.effective_user.username or "کاربر"
        text = update.message.text.strip()

        enqueue_support_message(user_id, username, text, update.message.message_id)

        for admin_id in ADMIN_IDS:
            await context.bot.send_message(chat_id=admin_id, text=f"پاسخ کاربر {username} ({user_id}):\n{text}", parse_mode="HTML")

        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await update.message.reply_text(t('support_reply_sent', user_lang), reply_markup=get_main_keyboard(user_lang))
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در ارسال پاسخ کاربر: {e}", exc_info=True)
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        await update.message.reply_text(t('error_generic', user_lang))
        return USER_REPLYING_SUPPORT

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        
        # پاک کردن فقط وضعیت‌های مربوط به پشتیبانی هوشمند / گفتگوهای فعال
        context.user_data.pop("active_ai_conversation_id", None)
        context.user_data.pop("support_history", None)
        context.user_data.pop("support_history_initialized_from_conversation", None)
        await query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=t('back_to_menu', user_lang), 
            reply_markup=get_main_keyboard(user_lang)
        )
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در بازگشت به منو: {e}", exc_info=True)
        return CHOOSING_ACTION

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True) -> int:
    query = update.callback_query if hasattr(update, 'callback_query') else None
    if query:
        await query.answer()

    text = get_string('admin_panel_title')
    reply_markup = get_admin_keyboard()

    try:
        if query and edit:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            if query:
                await query.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await update.message.reply_text(text=text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"ویرایش منوی ادمین شکست خورد، پیام جدید ارسال شد: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

    return ADMIN_PANEL

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ شما دسترسی ندارید")
        return ConversationHandler.END

    return await show_admin_panel(update, context, edit=False)

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    return await show_admin_panel(update, context, edit=True)


async def admin_payments_switch_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پنل کنترل سیستم پولی"""
    query = update.callback_query
    await query.answer()

    payments_enabled = is_payments_enabled()
    status_emoji = "🟢" if payments_enabled else "🔴"
    status_text = "فعال" if payments_enabled else "غیرفعال"

    text = (
        f"💰 <b>کنترل سیستم پولی / Paywall Switch</b>\n\n"
        f"وضعیت فعلی: {status_emoji} <b>{status_text}</b>\n\n"
    )

    if payments_enabled:
        text += (
            "در حالت فعال:\n"
            "• محدودیت‌های پلن‌ها اعمال می‌شود\n"
            "• پیام‌های ارتقا نمایش داده می‌شود\n"
            "• محدودیت کیفیت و دانلود فعال است\n\n"
            "با غیرفعال کردن:\n"
            "• تمام کاربران دسترسی کامل خواهند داشت\n"
            "• هیچ محدودیتی اعمال نمی‌شود"
        )
    else:
        text += (
            "در حالت غیرفعال:\n"
            "• تمام کاربران دسترسی کامل دارند\n"
            "• هیچ محدودیتی اعمال نمی‌شود\n"
            "• پیام‌های ارتقا نمایش داده نمی‌شود\n\n"
            "با فعال کردن:\n"
            "• محدودیت‌های پلن‌ها دوباره اعمال می‌شود"
        )

    keyboard = []
    if payments_enabled:
        keyboard.append([InlineKeyboardButton("🔴 غیرفعال‌سازی سیستم پولی", callback_data="admin_payments_disable")])
    else:
        keyboard.append([InlineKeyboardButton("🟢 فعال‌سازی سیستم پولی", callback_data="admin_payments_enable")])
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_PANEL

async def admin_payments_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """فعال‌سازی سیستم پولی"""
    query = update.callback_query
    await query.answer()

    set_payments_enabled(True)

    await query.edit_message_text(
        "✅ <b>سیستم پولی فعال شد</b>\n\n"
        "محدودیت‌های پلن‌ها دوباره اعمال می‌شود.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]
        ])
    )
    return ADMIN_PANEL

async def admin_payments_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """غیرفعال‌سازی سیستم پولی"""
    query = update.callback_query
    await query.answer()

    set_payments_enabled(False)

    await query.edit_message_text(
        "🔴 <b>سیستم پولی غیرفعال شد</b>\n\n"
        "تمام کاربران اکنون دسترسی کامل دارند.\n"
        "هیچ محدودیتی اعمال نمی‌شود.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]
        ])
    )
    return ADMIN_PANEL

async def admin_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """هندلر پیام‌های متنی ادمین"""
    if context.user_data.get('waiting_for_force_join_channel'):
        channel = update.message.text.strip()
        
        # بررسی فرمت کانال
        if not (channel.startswith('@') or channel.startswith('-100')):
            await update.message.reply_text(
                "❌ فرمت اشتباه است.\n"
                "لطفاً آیدی کانال را به صورت زیر ارسال کنید:\n"
                "مثال: @PeakTeam\n"
                "یا: -1001234567890"
            )
            return ADMIN_WAITING_FORCE_JOIN_CHANNEL
        
        # اضافه کردن کانال
        force_join_config = get_force_join_config()
        channels = force_join_config.get('channels', [])
        
        if channel in channels:
            await update.message.reply_text(f"⚠️ کانال {channel} قبلاً اضافه شده است.")
        else:
            channels.append(channel)
            force_join_config['channels'] = channels
            save_force_join_config(force_join_config)
            await update.message.reply_text(
                f"✅ کانال {channel} با موفقیت اضافه شد.\n\n"
                f"تعداد کل کانال‌ها: {len(channels)}"
            )
        
        context.user_data.pop('waiting_for_force_join_channel', None)
        return await admin_force_join_panel(update, context)
    
    # اگر پیام ادمین برای چیز دیگری بود، نادیده بگیر یا هندلرهای دیگر
    return ADMIN_PANEL

# ======================== توابع مدیریت عضویت اجباری ========================

async def admin_force_join_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پنل مدیریت عضویت اجباری"""
    query = update.callback_query
    if query:
        await query.answer()
    
    force_join_config = get_force_join_config()
    enabled = force_join_config.get('enabled', False)
    channels = force_join_config.get('channels', [])
    
    status_emoji = "🟢" if enabled else "🔴"
    status_text = "فعال" if enabled else "غیرفعال"
    
    text = (
        f"🛡️ <b>تنظیمات عضویت اجباری</b>\n\n"
        f"وضعیت: {status_emoji} <b>{status_text}</b>\n"
        f"تعداد کانال‌ها: <b>{len(channels)}</b>\n\n"
    )
    
    if channels:
        text += "<b>کانال‌های فعلی:</b>\n"
        for idx, channel in enumerate(channels, 1):
            text += f"{idx}. {channel}\n"
    else:
        text += "⚠️ هیچ کانالی اضافه نشده است.\n"
    
    text += "\nگزینه‌ها:"
    
    keyboard = []
    
    # دکمه تغییر وضعیت
    if enabled:
        keyboard.append([InlineKeyboardButton("🔴 غیرفعال‌سازی", callback_data="admin_force_join_disable")])
    else:
        keyboard.append([InlineKeyboardButton("🟢 فعال‌سازی", callback_data="admin_force_join_enable")])
    
    # دکمه‌های مدیریت کانال‌ها
    keyboard.append([InlineKeyboardButton("➕ اضافه کردن کانال", callback_data="admin_force_join_add")])
    
    if channels:
        keyboard.append([InlineKeyboardButton("➖ حذف کانال", callback_data="admin_force_join_remove")])
        keyboard.append([InlineKeyboardButton("👁️ مشاهده کانال‌ها", callback_data="admin_force_join_view")])
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    
    return ADMIN_PANEL

async def admin_force_join_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """فعال‌سازی عضویت اجباری"""
    query = update.callback_query
    await query.answer()
    
    force_join_config = get_force_join_config()
    force_join_config['enabled'] = True
    save_force_join_config(force_join_config)
    
    await query.edit_message_text(
        "✅ <b>عضویت اجباری فعال شد</b>\n\n"
        "از این پس کاربران باید عضو کانال‌های تعیین شده باشند.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
        ])
    )
    return ADMIN_PANEL

async def admin_force_join_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """غیرفعال‌سازی عضویت اجباری"""
    query = update.callback_query
    await query.answer()
    
    force_join_config = get_force_join_config()
    force_join_config['enabled'] = False
    save_force_join_config(force_join_config)
    
    await query.edit_message_text(
        "🔴 <b>عضویت اجباری غیرفعال شد</b>\n\n"
        "کاربران دیگر نیازی به عضویت در کانال‌ها ندارند.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
        ])
    )
    return ADMIN_PANEL

async def admin_force_join_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """اضافه کردن کانال جدید"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['waiting_for_force_join_channel'] = True
    
    await query.edit_message_text(
        "➕ <b>اضافه کردن کانال</b>\n\n"
        "لطفاً آیدی کانال را ارسال کنید:\n\n"
        "مثال:\n"
        "@PeakTeam\n"
        "یا\n"
        "-1001234567890\n\n"
        "⚠️ توجه: کانال باید با @ شروع شود یا یک عدد منفی باشد.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="admin_force_join")]
        ])
    )
    return ADMIN_WAITING_FORCE_JOIN_CHANNEL

async def admin_force_join_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """حذف کانال"""
    query = update.callback_query
    await query.answer()
    
    force_join_config = get_force_join_config()
    channels = force_join_config.get('channels', [])
    
    if not channels:
        await query.edit_message_text(
            "⚠️ هیچ کانالی برای حذف وجود ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
            ])
        )
        return ADMIN_PANEL
    
    # ساخت کیبورد با لیست کانال‌ها
    keyboard = []
    for idx, channel in enumerate(channels):
        keyboard.append([InlineKeyboardButton(
            f"🗑️ حذف {channel}",
            callback_data=f"admin_force_join_delete_{idx}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")])
    
    text = "➖ <b>حذف کانال</b>\n\nلطفاً کانالی که می‌خواهید حذف کنید را انتخاب کنید:"
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_PANEL

async def admin_force_join_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """حذف کانال انتخاب شده"""
    query = update.callback_query
    await query.answer()
    
    # استخراج ایندکس از callback_data
    idx = int(query.data.split('_')[-1])
    
    force_join_config = get_force_join_config()
    channels = force_join_config.get('channels', [])
    
    if 0 <= idx < len(channels):
        deleted_channel = channels.pop(idx)
        force_join_config['channels'] = channels
        save_force_join_config(force_join_config)
        
        await query.edit_message_text(
            f"✅ کانال <b>{deleted_channel}</b> با موفقیت حذف شد.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
            ])
        )
    else:
        await query.edit_message_text(
            "❌ خطا در حذف کانال.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
            ])
        )
    
    return ADMIN_PANEL

async def admin_force_join_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مشاهده لیست کانال‌ها"""
    query = update.callback_query
    await query.answer()
    
    force_join_config = get_force_join_config()
    channels = force_join_config.get('channels', [])
    
    if not channels:
        text = "👁️ <b>مشاهده کانال‌ها</b>\n\n⚠️ هیچ کانالی اضافه نشده است."
    else:
        text = "👁️ <b>مشاهده کانال‌ها</b>\n\n"
        text += f"تعداد کل: <b>{len(channels)}</b>\n\n"
        for idx, channel in enumerate(channels, 1):
            text += f"{idx}. {channel}\n"
    
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_force_join")]
        ])
    )
    return ADMIN_PANEL

async def admin_manage_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=get_string('admin_upgrade_prompt'),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="admin_back")]])
    )
    return ADMIN_WAITING_USER_ID

async def handle_invalid_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer(text="⚠️ این دکمه منسوخ شده است. منو به‌روزرسانی شد.", show_alert=True)
    return await show_admin_panel(update, context, edit=False)

async def admin_show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        users = load_users()
        total_users = len(users)
        total_downloads = sum(u.get('downloads_total', 0) for u in users.values())
        plan_counts = {'free': 0, 'premium': 0, 'professional': 0}
        for user in users.values():
            plan_counts[user.get('plan', 'free')] += 1
        stats_text = get_string('admin_stats', total_users=total_users, total_downloads=total_downloads, free=plan_counts['free'], premium=plan_counts['premium'], professional=plan_counts['professional'])
        keyboard = [[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]
        await query.edit_message_text(stats_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در نمایش آمار: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_show_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        users = load_users()
        if not users:
            await query.edit_message_text("هیچ کاربری ثبت نشده", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
            return ADMIN_PANEL
        users_list = "\n".join([f"👤 {uid} | دانلودها: {u.get('downloads_total', 0)}" for uid, u in list(users.items())[:20]])
        text = get_string('admin_users', count=len(users), users_list=users_list)
        if len(users) > 20:
            text += f"\n... و {len(users)-20} کاربر دیگر"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در نمایش کاربران: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        users = load_users()
        total_referrals = sum(1 for u in users.values() if u.get('referrer_id') is not None)
        top_referrers = sorted([(uid, get_referral_count(int(uid)), u.get('username', 'نامشخص')) for uid, u in users.items() if get_referral_count(int(uid)) > 0], key=lambda x: x[1], reverse=True)[:10]
        top_list = "\n".join([f"{i}. {username} ({uid}) → {count} دعوت" for i, (uid, count, username) in enumerate(top_referrers, 1)]) or "هنوز رفرالی ثبت نشده"
        text = get_string('admin_referral_stats', total=total_referrals, top_list=top_list)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در آمار رفرال: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_support_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()

        items = [it for it in load_support_queue() if it.get('status') == 'unread']
        if not items:
            text = get_string('admin_inbox_title') + get_string('admin_inbox_empty')
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
            return ADMIN_PANEL

        keyboard = [
            [InlineKeyboardButton(
                f"{it['username']} ({it['user_id']}) — {it['text'][:40]}",
                callback_data=f"admin_support_view:{it['id']}"
            ) for it in items[:20]]
        ]
        keyboard.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")])

        text = get_string('admin_inbox_title') + get_string('admin_inbox_select')
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در اینباکس پشتیبانی: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_support_view_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        msg_id = int(query.data.split(":")[1])
        item = next((x for x in load_support_queue() if x.get('id') == msg_id), None)
        if not item:
            await query.edit_message_text("پیام یافت نشد.")
            return ADMIN_PANEL
        text = get_string('admin_view_message', id=item['id'], username=item['username'], user_id=item['user_id'], created_at=item['created_at'], text=item['text'])
        keyboard = [
            [InlineKeyboardButton("✉️ پاسخ", callback_data=f"admin_support_reply:{item['id']}")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]
        ]
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در مشاهده پیام: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_support_start_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        msg_id = int(query.data.split(":")[1])
        item = next((x for x in load_support_queue() if x.get('id') == msg_id), None)
        if not item:
            await query.edit_message_text("پیام یافت نشد.")
            return ADMIN_PANEL
        context.user_data['reply_target_user_id'] = item['user_id']
        context.user_data['reply_target_msg_id'] = msg_id
        await query.edit_message_text(get_string('admin_reply_prompt', username=item['username'], user_id=item['user_id']))
        return ADMIN_REPLYING_SUPPORT
    except Exception as e:
        logger.error(f"خطا در شروع پاسخ: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_support_receive_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        reply_text = update.message.text
        target_user_id = context.user_data.get('reply_target_user_id')
        msg_id = context.user_data.get('reply_target_msg_id')
        if not target_user_id or not msg_id:
            await update.message.reply_text("هدف پاسخ مشخص نیست.")
            return ADMIN_PANEL
        await context.bot.send_message(chat_id=target_user_id, text=reply_text)
        set_last_admin_message(int(target_user_id), reply_text)
        await context.bot.send_message(chat_id=target_user_id, text="پیام جدید از ادمین دریافت شد. برای مشاهده /inbox")
        mark_support_replied(int(msg_id), reply_text)
        context.user_data.clear()
        await update.message.reply_text(STRINGS['reply_sent_admin'])
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در دریافت پاسخ: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(get_string('admin_broadcast_prompt'), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="admin_back")]]))
        return ADMIN_WAITING_BROADCAST
    except Exception as e:
        logger.error(f"خطا در ارسال همگانی: {e}", exc_info=True)
        return ADMIN_PANEL

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        text = update.message.text
        users = load_users()
        sent = failed = 0
        status_msg = await update.message.reply_text("در حال ارسال...")
        for uid in users:
            try:
                await context.bot.send_message(int(uid), f"📢 پیام از ادمین:\n\n{text}", parse_mode="HTML")
                sent += 1
            except:
                failed += 1
        await status_msg.edit_text(get_string('admin_broadcast_sent', sent=sent, failed=failed, total=len(users)))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در پخش: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_do_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        deleted = 0
        now = time.time()
        for file in os.listdir(DOWNLOADS_FOLDER):
            path = os.path.join(DOWNLOADS_FOLDER, file)
            if os.path.isfile(path) and now - os.path.getctime(path) > 86400:
                os.remove(path)
                deleted += 1
        await query.edit_message_text(get_string('admin_cleanup', count=deleted), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در پاکسازی: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_reset_stats_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(get_string('admin_reset_confirm'), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله", callback_data="confirm_reset")],
            [InlineKeyboardButton("❌ لغو", callback_data="admin_back")]
        ]))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در تایید ریست: {e}", exc_info=True)
        return ADMIN_PANEL

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        save_users({})
        await query.edit_message_text(get_string('admin_reset_done'), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در ریست: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        await query.message.delete()
        context.user_data.clear()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=STRINGS['admin_exit'], reply_markup=get_main_keyboard())
        return CHOOSING_ACTION
    except Exception as e:
        logger.error(f"خطا در خروج ادمین: {e}", exc_info=True)
        return CHOOSING_ACTION

async def admin_subscription_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = int(update.message.text.strip())
        user = get_user_profile(user_id)
        if not user:
            await update.message.reply_text(f"کاربر {user_id} پیدا نشد!")
            return ADMIN_PANEL

        info = f"👤 کاربر: {user.get('username','نامشخص')}\n🆔 شناسه: {user_id}\n📊 پلن فعلی: {user.get('plan','نامشخص')}\n💾 کل دانلودها: {user.get('downloads_total', 0)}"

        keyboard = [
            [InlineKeyboardButton("🎁 بازگشت به رایگان (Free)", callback_data=f"set_sub_free_{user_id}")],
            [InlineKeyboardButton("💎 فعالسازی پرمیوم (۳۰ روزه)", callback_data=f"set_sub_premium_{user_id}")],
            [InlineKeyboardButton("🔥 فعالسازی حرفه‌ای (۳۰ روزه)", callback_data=f"set_sub_pro_{user_id}")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]
        ]

        await update.message.reply_text(info + "\n\nبرای انتخاب یکی از گزینه‌های زیر روی دکمه مربوط کلیک کنید:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_PANEL
    except ValueError:
        await update.message.reply_text("شناسه باید عدد باشد!")
        return ADMIN_WAITING_USER_ID
    except Exception as e:
        logger.error(f"خطا در پنل مدیریت اشتراک: {e}", exc_info=True)
        await update.message.reply_text("خطایی رخ داد.")
        return ADMIN_PANEL

async def admin_set_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        if not data.startswith("set_sub_"):
            await query.edit_message_text("داده نامعتبر.")
            return ADMIN_PANEL

        remaining = data[len("set_sub_"):]
        parts = remaining.rsplit("_", 1)
        if len(parts) != 2:
            await query.edit_message_text("داده نامعتبر.")
            return ADMIN_PANEL

        plan_type = parts[0]
        try:
            target_user_id = int(parts[1])
        except ValueError:
            await query.edit_message_text("شناسه کاربر نامعتبر.")
            return ADMIN_PANEL

        plan_map = {
            'free': 'free',
            'premium': 'premium',
            'pro': 'professional'
        }
        new_plan = plan_map.get(plan_type)
        if not new_plan:
            await query.edit_message_text("پلن نامعتبر.")
            return ADMIN_PANEL

        users = load_users()
        key = str(target_user_id)
        if key not in users:
            await query.edit_message_text(f"کاربر {target_user_id} پیدا نشد.")
            return ADMIN_PANEL

        now = datetime.now()
        users[key]['plan'] = new_plan
        if new_plan in ['premium', 'professional']:
            expire_date = now + timedelta(days=30)
            users[key]['subscription_end'] = expire_date.isoformat()
            users[key]['plan_start_at'] = now.isoformat()
            users[key]['plan_expire_at'] = expire_date.isoformat()
        else:
            users[key]['subscription_end'] = None
            users[key]['plan_start_at'] = None
            users[key]['plan_expire_at'] = None

        users[key]['downloads_today'] = 0
        users[key]['last_reset'] = now.isoformat()

        save_users(users)

        # نامعتبر کردن cache کاربر برای استفاده از داده‌های تازه
        try:
            if hasattr(context, 'application') and context.application:
                invalidate_user_cache(context.application, target_user_id)
        except Exception as e:
            logger.warning(f"خطا در نامعتبر کردن cache کاربر {target_user_id}: {e}")

        display_names = {'free': 'رایگان', 'premium': 'پریمیوم', 'professional': 'حرفه‌ای'}
        display = display_names.get(new_plan, new_plan)
        success_msg = f"✅ کاربر {target_user_id} به سطح {display} ارتقا یافت.\n"
        if new_plan in ['premium', 'professional']:
            success_msg += "⏰ اشتراک به مدت ۳۰ روز فعال شد."

        try:
            await query.edit_message_text(success_msg)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=success_msg)

        # ارسال پیام ارتقای پلن با جزئیات تاریخ شروع و انقضا
        if new_plan in ['premium', 'professional']:
            user_lang = get_user_language(target_user_id) or DEFAULT_LANG
            plan_display_name = t('plan_premium', user_lang) if new_plan == 'premium' else t('plan_professional', user_lang)
            try:
                await _send_plan_upgrade_message(context.bot, target_user_id, plan_display_name, now, expire_date)
            except Exception as e:
                logger.warning(f"ارسال پیام ارتقای پلن به کاربر {target_user_id} شکست شد: {e}")

        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در تنظیم اشتراک توسط ادمین: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ خطا در انجام عملیات.")
        except:
            pass
        return ADMIN_PANEL

# ======================== هندلر دریافت رسید از ادمین ========================
async def handle_admin_receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id not in ADMIN_IDS or not update.message.photo:
            return

        caption = update.message.caption or ""
        if "#FINANCE_REPORT" not in caption:
            return

        user_id_match = re.search(r"user_id\s*[:=]?\s*(\d+)", caption, re.IGNORECASE)
        if not user_id_match:
            await update.message.reply_text("⚠️ user_id در کپشن یافت نشد.\nمثال:\n#FINANCE_REPORT\nuser_id: 123456789\nPLAN: پرمیوم یک ماهه")
            return

        user_id = int(user_id_match.group(1))

        users = load_users()
        username = users.get(str(user_id), {}).get('username', f"User_{user_id}")

        plan_match = re.search(r"PLAN[:\s]+(.+)", caption, re.IGNORECASE)
        plan_text = plan_match.group(1).strip().lower() if plan_match else ""

        if any(k in plan_text for k in ['حرفه', 'pro', 'professional', '🔥']):
            plan_type = 'professional'
        else:
            plan_type = 'premium'

        if any(k in plan_text for k in ['7', 'هفت', 'هفته']):
            duration_days = 7
        elif any(k in plan_text for k in ['30', 'سی', 'یک ماه', 'ماهه']):
            duration_days = 30
        else:
            duration_days = 30

        photo = update.message.photo[-1]
        enqueue_receipt(user_id, username, photo.file_id, update.message.message_id, plan_type, duration_days)

        await update.message.reply_text(
            f"✅ رسید کاربر {user_id} با موفقیت به صف بررسی اضافه شد.\n"
            f"پلن تشخیص‌داده‌شده: {plan_type} ({duration_days} روزه)"
        )
    except Exception as e:
        logger.error(f"خطا در پردازش رسید ادمین: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در ثبت رسید رخ داد.")

async def admin_receipts_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()

        pending_items = [it for it in load_receipts_queue() if it.get('status') == 'pending']
        if not pending_items:
            text = "🧾 <b>بررسی رسیدها</b>\n\nهیچ رسید بررسی‌نشده‌ای وجود ندارد."
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]]))
            logger.info("صف رسیدها خالی است.")
            return ADMIN_PANEL

        item = pending_items[0]

        plan_display = "👑 حرفه‌ای" if item['plan_type'] == 'professional' else "⭐ پرمیوم"
        plan_display += f" ({item['duration_days']} روزه)"

        caption = (
            f"🧾 رسید جدید برای بررسی:\n"
            f"👤 کاربر: {item['user_id']}\n"
            f"💎 پلن درخواستی: {plan_display}\n\n"
            f"آیا تایید می‌کنید؟"
        )

        keyboard = [
            [InlineKeyboardButton("✅ تایید و فعال‌سازی", callback_data=f"receipt_approve:{item['id']}")],
            [InlineKeyboardButton("❌ رد فیش", callback_data=f"receipt_reject:{item['id']}")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]
        ]

        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=item['photo_file_id'],
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.message.delete()
        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در نمایش صف رسیدها: {e}", exc_info=True)
        return ADMIN_PANEL

async def admin_receipt_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()

        data = query.data.split(":")
        action = data[0].split("_")[1]
        item_id = int(data[1])

        item = next((x for x in load_receipts_queue() if x.get('id') == item_id), None)
        if not item or item['status'] != 'pending':
            await query.edit_message_caption(caption="⚠️ این رسید قبلاً پردازش شده است.", parse_mode="HTML")
            return ADMIN_PANEL

        user_id = item['user_id']
        plan_type = item['plan_type']
        duration_days = item['duration_days']
        
        user_lang = get_user_language(user_id) or DEFAULT_LANG
        plan_name = t('plan_professional', user_lang) if plan_type == 'professional' else t('plan_premium', user_lang)

        if action == "approve":
            users = load_users()
            key = str(user_id)
            if key not in users:
                create_user(user_id, item['username'])

            users = load_users()
            now = datetime.now()
            expire_date = now + timedelta(days=duration_days)
            
            users[key]['plan'] = plan_type
            users[key]['subscription_end'] = expire_date.isoformat()
            users[key]['plan_start_at'] = now.isoformat()
            users[key]['plan_expire_at'] = expire_date.isoformat()
            users[key]['downloads_today'] = 0
            save_users(users)

            # نامعتبر کردن cache کاربر برای استفاده از داده‌های تازه
            try:
                if hasattr(context, 'application') and context.application:
                    invalidate_user_cache(context.application, user_id)
            except Exception as e:
                logger.warning(f"خطا در نامعتبر کردن cache کاربر {user_id}: {e}")

            # ارسال پیام ارتقای پلن با جزئیات تاریخ شروع و انقضا
            try:
                await _send_plan_upgrade_message(context.bot, user_id, plan_name, now, expire_date)
            except Exception as e:
                logger.warning(f"ارسال پیام ارتقای پلن به کاربر {user_id} شکست خورد: {e}")
            
            status_text = f"✅ <b>تایید شده</b>\nپلن {plan_name} ({duration_days} روزه) فعال شد."
        else:
            user_message = t('payment_rejected', user_lang)
            status_text = "❌ <b>رد شده</b>"
            
            try:
                await context.bot.send_message(chat_id=user_id, text=user_message)
            except Exception as e:
                logger.warning(f"ارسال نوتیفیکیشن به کاربر {user_id} شکست خورد: {e}")
                status_text += "\n⚠️ نوتیفیکیشن به کاربر ارسال نشد."

        mark_receipt_processed(item_id, 'approved' if action == "approve" else 'rejected')

        new_caption = query.message.caption + f"\n\n{status_text}"
        await query.edit_message_caption(caption=new_caption, parse_mode="HTML", reply_markup=None)

        return ADMIN_PANEL
    except Exception as e:
        logger.error(f"خطا در پردازش اقدام رسید: {e}", exc_info=True)
        return ADMIN_PANEL

def main():
    # پاکسازی لینک‌های منقضی‌شده در شروع
    cleanup_expired_links()
    
    application = Application.builder().token(TOKEN).build()

    application.add_error_handler(global_error_handler)
    
    application.add_handler(CallbackQueryHandler(help_back_to_main, pattern="^help_back$"))
    application.add_handler(CallbackQueryHandler(help_topic_selected, pattern="^help_"))
    
    application.add_handler(CommandHandler("inbox", user_inbox))

    # هندلرهای رسیدها
    application.add_handler(CallbackQueryHandler(admin_receipts_inbox, pattern="^admin_receipts_inbox$"))
    application.add_handler(CallbackQueryHandler(admin_receipt_action, pattern="^receipt_(approve|reject):"))
    application.add_handler(MessageHandler(filters.PHOTO & filters.User(user_id=ADMIN_IDS), handle_admin_receipt_photo))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("admin", admin_panel)],
        states={
            CHOOSING_ACTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons),
                CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"),
                CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
                CallbackQueryHandler(support_entry_callback, pattern="^support_ai$"),
                CallbackQueryHandler(settings_language_panel, pattern="^settings_language$"),
                CallbackQueryHandler(lambda u, c: set_language(u, c, 'fa'), pattern="^set_lang_fa$"),
                CallbackQueryHandler(lambda u, c: set_language(u, c, 'en'), pattern="^set_lang_en$"),
                CallbackQueryHandler(settings_back, pattern="^settings_back$"),
                # About menu handlers - also work from CHOOSING_ACTION state
                CallbackQueryHandler(about_peaktube, pattern="^about_peaktube$"),
                CallbackQueryHandler(about_future_vision, pattern="^about_future_vision$"),
                CallbackQueryHandler(about_team, pattern="^about_team$"),
                CallbackQueryHandler(about_why, pattern="^about_why$"),
                CallbackQueryHandler(about_terms, pattern="^about_terms$"),
                CallbackQueryHandler(about_contact, pattern="^about_contact$"),
                CallbackQueryHandler(about_back, pattern="^about_back$"),
            ],
            WAITING_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_video_info),
                CallbackQueryHandler(select_search_result, pattern="^(select_yt_id:|cancel_search$)"),
            ],
            SHOWING_INFO: [
                CallbackQueryHandler(proceed_to_quality, pattern="^proceed_to_quality$"),
                CallbackQueryHandler(audio_only, pattern="^audio_only$"),
                CallbackQueryHandler(request_subtitle, pattern="^request_subtitle$"),
                CallbackQueryHandler(cancel_download, pattern="^cancel_download$"),
            ],
            SELECTING_QUALITY: [
                CallbackQueryHandler(quality_selected, pattern="^quality_"),
                CallbackQueryHandler(cancel_download, pattern="^cancel_download$"),
            ],
            SELECTING_SUBTITLE_LANG: [
                CallbackQueryHandler(handle_subtitle_download, pattern="^sub_dl:"),
                CallbackQueryHandler(back_to_video_info, pattern="^back_to_video_info$"),
            ],
            SELECTING_LANGUAGE: [
                CallbackQueryHandler(lambda u, c: handle_initial_language_selection(u, c, 'fa'), pattern="^initial_lang_fa$"),
                CallbackQueryHandler(lambda u, c: handle_initial_language_selection(u, c, 'en'), pattern="^initial_lang_en$"),
            ],
            AI_SUPPORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_support),
                CallbackQueryHandler(show_ai_history, pattern="^ai_history$"),
                CallbackQueryHandler(open_ai_history_conversation, pattern="^ai_open:"),
                CallbackQueryHandler(ai_history_delete_prompt, pattern="^ai_delete:"),
                CallbackQueryHandler(ai_history_delete_confirm, pattern="^ai_delete_confirm:"),
                CallbackQueryHandler(ai_history_back_to_support, pattern="^ai_history_back$"),
                CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
            ],
            ABOUT_MENU: [
                CallbackQueryHandler(about_peaktube, pattern="^about_peaktube$"),
                CallbackQueryHandler(about_future_vision, pattern="^about_future_vision$"),
                CallbackQueryHandler(about_team, pattern="^about_team$"),
                CallbackQueryHandler(about_why, pattern="^about_why$"),
                CallbackQueryHandler(about_terms, pattern="^about_terms$"),
                CallbackQueryHandler(about_contact, pattern="^about_contact$"),
                CallbackQueryHandler(about_back, pattern="^about_back$"),
                CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
            ],
            ADMIN_PANEL: [
                CallbackQueryHandler(admin_show_stats, pattern="^admin_show_stats$"),
                CallbackQueryHandler(admin_show_users, pattern="^admin_show_users$"),
                CallbackQueryHandler(admin_manage_subscription, pattern="^admin_manage_subscription$"),
                CallbackQueryHandler(admin_send_broadcast, pattern="^admin_send_broadcast$"),
                CallbackQueryHandler(admin_support_inbox, pattern="^admin_support_inbox$"),
                CallbackQueryHandler(admin_support_view_message, pattern="^admin_support_view:"),
                CallbackQueryHandler(admin_support_start_reply, pattern="^admin_support_reply:"),
                CallbackQueryHandler(admin_payments_switch_panel, pattern="^admin_payments_switch$"),
                CallbackQueryHandler(admin_payments_enable, pattern="^admin_payments_enable$"),
                CallbackQueryHandler(admin_payments_disable, pattern="^admin_payments_disable$"),
                CallbackQueryHandler(admin_force_join_panel, pattern="^admin_force_join$"),
                CallbackQueryHandler(admin_force_join_enable, pattern="^admin_force_join_enable$"),
                CallbackQueryHandler(admin_force_join_disable, pattern="^admin_force_join_disable$"),
                CallbackQueryHandler(admin_force_join_add, pattern="^admin_force_join_add$"),
                CallbackQueryHandler(admin_force_join_remove, pattern="^admin_force_join_remove$"),
                CallbackQueryHandler(admin_force_join_delete, pattern="^admin_force_join_delete_"),
                CallbackQueryHandler(admin_force_join_view, pattern="^admin_force_join_view$"),
                CallbackQueryHandler(admin_do_cleanup, pattern="^admin_do_cleanup$"),
                CallbackQueryHandler(admin_reset_stats_confirm, pattern="^admin_reset_stats_confirm$"),
                CallbackQueryHandler(confirm_reset, pattern="^confirm_reset$"),
                CallbackQueryHandler(admin_referral_stats, pattern="^admin_referral_stats$"),
                CallbackQueryHandler(admin_back, pattern="^admin_back$"),
                CallbackQueryHandler(admin_exit, pattern="^admin_exit$"),
                CallbackQueryHandler(admin_set_sub_handler, pattern="^set_sub_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS), admin_handle_text),
                CallbackQueryHandler(handle_invalid_admin_callback),
            ],
            ADMIN_WAITING_USER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+$'), admin_subscription_panel),
                CallbackQueryHandler(admin_back, pattern="^admin_back$"),
            ],
            ADMIN_WAITING_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message),
                CallbackQueryHandler(admin_back, pattern="^admin_back$"),
            ],
            ADMIN_REPLYING_SUPPORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_support_receive_reply),
                CallbackQueryHandler(admin_back, pattern="^admin_back$"),
            ],
            ADMIN_WAITING_FORCE_JOIN_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS), admin_handle_text),
                CallbackQueryHandler(admin_force_join_panel, pattern="^admin_force_join$"),
            ],
            USER_REPLYING_SUPPORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, send_reply_to_admin),
                CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_chat=True,
        allow_reentry=True,
        per_message=False,
    )

    application.add_handler(conv_handler)

    logger.info("ربات در حال اجراست...")
    application.run_polling()

if __name__ == '__main__':
    main()