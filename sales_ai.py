# sales_ai.py
"""
کلاس SalesAI - ارتباط با OpenRouter برای سیستم فروش
کاملاً جدا از هوش مصنوعی پشتیبانی (PeakAI)
"""

import httpx
import json
import logging
import asyncio
import time
from datetime import datetime
from pathlib import Path

# تنظیم مسیر فایل لاگ
LOG_FILE = Path("peak_sales_ai.log")

# تنظیم لاگر
logger = logging.getLogger("SalesAI")
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

logger.info("لاگ‌گیری SalesAI شروع شد → فایل: %s", LOG_FILE.absolute())


class SalesAI:
    def __init__(self, api_key: str, model: str = "stepfun/step-3.5-flash:free"):
        """
        مقداردهی اولیه کلاس SalesAI
        این کلاس کاملاً جدا از PeakAI (هوش مصنوعی پشتیبانی) است
        """
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

        self.system_prompt = (
            "شما دستیار فروش رسمی ربات PeakTube هستید. "
            "وظیفه شما کمک به کاربران در فرآیند خرید اشتراک VIP است. "
            "همیشه با لحن کاملاً رسمی، محترمانه و حرفه‌ای به زبان فارسی پاسخ دهید. "
            "پاسخ‌های شما باید واضح، مفید و مختصر باشند. "
            "شما فقط باید در مورد خرید اشتراک، قیمت‌ها، روش‌های پرداخت و ارسال رسید صحبت کنید. "
            "اگر کاربر سوالی خارج از موضوع فروش بپرسد، به آرامی او را به ادامه فرآیند خرید هدایت کنید."
        )

        logger.info("SalesAI مقداردهی شد | مدل: %s | کلید: %s***", self.model, self.api_key[:6])

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

    async def generate_sales_response(self, user_message: str, plan_info: dict = None):
        """
        تولید پاسخ برای جریان فروش
        plan_info شامل: plan_type, duration_days, price, card_number
        """
        start_time = time.perf_counter()
        logger.info("درخواست جدید فروش | طول پیام: %d کاراکتر", len(user_message))

        # ساخت prompt مخصوص فروش
        sales_context = ""
        if plan_info:
            plan_name = "پریمیوم" if plan_info.get('plan_type') == 'premium' else "حرفه‌ای"
            duration = plan_info.get('duration_days', 30)
            price = plan_info.get('price', 'نامشخص')
            card_number = plan_info.get('card_number', 'نامشخص')
            
            sales_context = (
                f"کاربر در حال خرید اشتراک {plan_name} ({duration} روزه) است. "
                f"قیمت: {price} تومان. "
                f"شماره کارت برای پرداخت: {card_number}. "
                f"لطفاً به کاربر توضیح دهید که باید تصویر رسید پرداخت را ارسال کند."
            )

        messages = [
            {"role": "system", "content": self.system_prompt + " " + sales_context},
            {"role": "user", "content": user_message}
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://t.me/PeakTubeBot",
            "X-Title": "PeakTube Sales AI Bot",
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
            logger.exception("خطای غیرمنتظره در generate_sales_response")
            yield "خطای داخلی در پردازش درخواست. لطفاً با پشتیبانی تماس بگیرید."

        finally:
            total_time = time.perf_counter() - start_time
            logger.debug("پایان متد generate_sales_response | زمان کل: %.3f ثانیه", total_time)

