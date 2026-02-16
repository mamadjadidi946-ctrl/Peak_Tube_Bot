# ai_handler.py
"""
کلاس PeakAI - ارتباط با OpenRouter
نسخه نهایی با لاگ‌گیری حرفه‌ای، دیباگ دقیق، مدیریت خطا و استریم
"""

import httpx
import json
import logging
import asyncio
import time
from datetime import datetime
from pathlib import Path

# تنظیم مسیر فایل لاگ
LOG_FILE = Path("peak_ai.log")

# تنظیم لاگر
logger = logging.getLogger("PeakAI")
logger.setLevel(logging.DEBUG)

# فرمت لاگ با میلی‌ثانیه
formatter = logging.Formatter(
    "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(funcName)s:%(lineno)d] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Handler فایل (همه سطوح)
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# Handler کنسول (INFO و بالاتر)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.propagate = False

logger.info("لاگ‌گیری PeakAI شروع شد → فایل: %s", LOG_FILE.absolute())


class PeakAI:
    def __init__(self, api_key: str, model: str = "stepfun/step-3.5-flash:free"):
        """
        مقداردهی اولیه کلاس
        """
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

        self.system_prompt = (
            "شما PeakAI هستید، دستیار رسمی و هوشمند ربات PeakTube. "
            "همیشه با لحن کاملاً رسمی، محترمانه و کتابی به زبان فارسی پاسخ دهید. "
            "پاسخ‌های شما باید کوتاه، مفید و دقیق باشند. "
            "فقط به وظایف زیر پاسخ دهید: خلاصه‌سازی محتوای ویدیوهای یوتیوب، "
            "جستجوی هوشمند ویدیوها یا محتوای مرتبط، و ایده‌پردازی برای محتوای جدید بر اساس موضوعات یوتیوب. "
            "اگر سؤال کاربر مربوط به پشتیبانی فنی، مشکلات ربات، مسائل مالی، اشتراک‌ها یا هر موضوع دیگری خارج از وظایف شما باشد، "
            "پاسخ ندهید و فقط بگویید: "
            "'با پوزش، این موضوع خارج از حیطه وظایف من است. لطفاً از منوی اصلی گزینه پشتیبانی را انتخاب نمایید.'"
        )

        logger.info("PeakAI مقداردهی شد | مدل: %s | کلید: %s***", self.model, self.api_key[:6])

    async def check_health(self) -> bool:
        """بررسی وضعیت اتصال به OpenRouter"""
        start = time.perf_counter()
        logger.debug("شروع بررسی سلامت API")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                elapsed = time.perf_counter() - start
                logger.info("بررسی سلامت → کد: %d | زمان: %.3f ثانیه", resp.status_code, elapsed)

                if resp.status_code == 200:
                    logger.debug("اتصال برقرار است")
                    return True
                else:
                    logger.warning("بررسی سلامت ناموفق → کد: %d | پاسخ: %s", resp.status_code, resp.text[:300])
                    return False

        except httpx.RequestError as e:
            logger.error("خطای شبکه در health check: %s", str(e))
            return False
        except Exception as e:
            logger.exception("خطای غیرمنتظره در health check")
            return False

    async def generate_response(self, user_message: str, task_type: str = "summarize"):
        """
        تولید پاسخ استریم‌شده - فقط yield مجاز است
        """
        start_time = time.perf_counter()
        logger.info("درخواست جدید | وظیفه: %s | طول پیام: %d کاراکتر", task_type, len(user_message))

        task_prompts = {
            "summarize": "لطفاً محتوای ویدیو یوتیوب را به صورت خلاصه، رسمی و کتابی خلاصه کنید. فقط خلاصه ارائه دهید.",
            "search": "لطفاً ویدیوها یا محتوای مرتبط با سؤال کاربر در یوتیوب را جستجو و پیشنهاد کنید. پیشنهادها را به صورت لیست مرتب ارائه دهید.",
            "idea": "لطفاً ایده‌های خلاقانه برای محتوای جدید بر اساس موضوع کاربر ایده‌پردازی کنید. ایده‌ها را به صورت رسمی و کتابی توصیف کنید."
        }
        task_prompt = task_prompts.get(task_type, "")

        messages = [
            {"role": "system", "content": self.system_prompt + " " + task_prompt},
            {"role": "user", "content": user_message}
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me/PeakTubeBot",
            "X-Title": "PeakTube AI Bot",
        }

        safe_headers = {k: v if k != "Authorization" else f"Bearer {v[:6]}***" for k, v in headers.items()}
        logger.debug("ارسال درخواست → URL: %s | مدل: %s | هدرها: %s", self.base_url, self.model, safe_headers)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": True,
                    }
                )

                elapsed_initial = time.perf_counter() - start_time
                logger.info("پاسخ اولیه دریافت شد → کد: %d | زمان تا پاسخ اول: %.3f ثانیه",
                            response.status_code, elapsed_initial)

                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode(errors="replace")
                    error_msg = f"خطا {response.status_code}: {error_text[:300]}"
                    logger.error("خطای HTTP → کد: %d | بدنه پاسخ: %s", response.status_code, error_text[:1000])
                    yield error_msg
                    return

                full_text = ""
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        if line.strip() == "data: [DONE]":
                            break
                        try:
                            data = json.loads(line[6:].strip())
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_text += content
                                yield content
                        except json.JSONDecodeError:
                            logger.debug("خط JSON نامعتبر رد شد")
                            continue
                        except Exception as inner_e:
                            logger.warning("خطا در پردازش خط استریم: %s", inner_e)
                            continue

                total_time = time.perf_counter() - start_time
                logger.info("پاسخ کامل شد → طول: %d کاراکتر | زمان کل: %.3f ثانیه", len(full_text), total_time)

                if not full_text.strip():
                    logger.warning("پاسخ خالی دریافت شد")
                    yield "متأسفانه پاسخی تولید نشد. لطفاً دوباره تلاش کنید."

        except httpx.HTTPStatusError as http_err:
            try:
                error_body = await http_err.response.aread()
                error_text = error_body.decode(errors="replace")
            except:
                error_text = "(نتوانست بدنه پاسخ خوانده شود)"

            error_msg = f"خطای ارتباط (کد {http_err.response.status_code}): {error_text[:300]}"
            logger.error("خطای HTTP → کد: %d | بدنه: %s", http_err.response.status_code, error_text[:1000])
            yield error_msg

        except httpx.RequestError as req_err:
            logger.error("خطای شبکه/درخواست: %s", str(req_err))
            yield "مشکل ارتباط شبکه با سرویس هوش مصنوعی رخ داد."

        except Exception as e:
            logger.exception("خطای غیرمنتظره در generate_response")
            yield "خطای داخلی در پردازش درخواست. لطفاً با پشتیبانی تماس بگیرید."

        finally:
            total_time = time.perf_counter() - start_time
            logger.debug("پایان متد generate_response | زمان کل: %.3f ثانیه", total_time)


# تست سریع (برای چک کردن)
async def test_peak_ai():
    # کلید واقعی خود را اینجا بگذارید
    API_KEY = "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"  # ← کلید خود را جایگزین کنید

    client = PeakAI(api_key=API_KEY)

    healthy = await client.check_health()
    print("وضعیت اتصال:", "سالم" if healthy else "مشکل دارد\n")

    if not healthy:
        print("تست متوقف شد.")
        return

    test_message = "خلاصه ویدیو https://youtu.be/dQw4w9WgXcQ را بده"
    print("تست استریم:\n")

    start = time.perf_counter()
    async for chunk in client.generate_response(test_message, "summarize"):
        print(chunk, end="", flush=True)
    print(f"\n\nزمان کل: {time.perf_counter() - start:.2f} ثانیه")


if __name__ == "__main__":
    asyncio.run(test_peak_ai())