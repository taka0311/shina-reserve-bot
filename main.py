import asyncio
import nest_asyncio
import re
import smtplib
import os
from email.mime.text import MIMEText
from email.utils import formatdate
from playwright.async_api import async_playwright

nest_asyncio.apply()

# --- 1. 抽出関数 ---
def extract_valid_slots(raw_text):
    slots = []
    lines = raw_text.split('\n')
    current_date = ""
    current_facility = ""
    for line in lines:
        line = line.strip()
        if not line: continue
        if re.search(r"\d{1,2}月\d{1,2}日\(.*曜\)\d{4}年", line):
            current_date = line
            continue
        if ("公園" in line or "センター" in line or "体育館" in line) and "～" not in line:
            if "館" not in line and "施設" not in line: current_facility = line
            continue
        if "時" in line and "分" in line and "～" in line:
            end_time_part = line.split("～")[1] 
            hour_match = re.search(r"(\d+)時", end_time_part)
            if hour_match:
                end_hour = int(hour_match.group(1)) 
                is_weekend = any(day in current_date for day in ["金曜", "日曜", "祝"])
                if is_weekend or (not is_weekend and end_hour > 17):
                    clean_facility = current_facility.replace('\t', ' ')
                    slots.append(f"{current_date} | {clean_facility} | {line}")
    return slots

# --- 2. メール送信関数 ---
def send_gmail(body_text):
    from_addr = os.getenv("GMAIL_USER") # GitHub Secretsから取得
    to_addr = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_PASS")  # GitHub Secretsから取得

    msg = MIMEText(body_text)
    msg['Subject'] = "【品川区施設予約】テニスコート空き状況レポート"
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Date'] = formatdate()

    try:
        s = smtplib.SMTP('smtp.gmail.com', 587)
        s.ehlo()
        s.starttls()
        s.login(from_addr, password)
        s.sendmail(from_addr, [to_addr], msg.as_string())
        s.close()
        print("メールを送信しました！")
    except Exception as e:
        print(f"メール送信エラー: {e}")

# --- 3. メイン処理 ---
async def run_reservation_bot():
    target_districts = ["1400_0", "1300_0"] 
    all_merged_slots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for district_id in target_districts:
            print(f"[{district_id}] 検索開始...")
            try:
                await page.goto("https://www.cm9.eprs.jp/shinagawa/web/index.jsp")
                await page.wait_for_timeout(3000)

                main_frame = None
                for f in page.frames:
                    if await f.locator('select#bname').count() > 0:
                        main_frame = f
                        break
                if not main_frame: continue

                try: await main_frame.locator('select[name*="period"], #thismonth, #period').select_option(label="1か月")
                except: pass
                
                await main_frame.locator('select#bname').select_option(value=district_id)
                await page.wait_for_timeout(2000)
                await main_frame.locator('select#purpose').select_option(value="31000000_31011700")
                await page.wait_for_timeout(1000)
                await main_frame.locator('button#btn-go').click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(5000)

                main_frame = None
                for f in page.frames:
                    if await f.locator('a:has-text("日付順")').count() > 0:
                        main_frame = f
                        break
                if not main_frame: continue

                date_sort_btn = main_frame.get_by_role("link", name="日付順", exact=True)
                if await date_sort_btn.count() > 0:
                    await date_sort_btn.click()
                    await page.wait_for_timeout(3000)

                while True:
                    more_btn = main_frame.locator('button#unreserved-moreBtn')
                    if await more_btn.is_visible():
                        await more_btn.click()
                        await page.wait_for_timeout(1000)
                    else: break

                all_data = await main_frame.locator('body').inner_text()
                district_slots = extract_valid_slots(all_data)
                if district_slots: all_merged_slots.extend(district_slots)

            except Exception as e:
                print(f"エラー: {e}")
                continue

        await browser.close()

    # --- 4. 差分検知と結果送信 ---
    if all_merged_slots:
        slots_text = "\n".join(all_merged_slots)
        history_file = "last_result.txt"
        last_result = ""

        if os.path.exists(history_file):
            with open(history_file, "r", encoding="utf-8") as f:
                last_result = f.read()

        if slots_text == last_result:
            print("前回と変更がないため送信をスキップします。")
        else:
            custom_message = (
                '予約はこちらから（"https://www.cm9.eprs.jp/shinagawa/web/index.jsp"）\n'
                '-----------------------------------------------------------------\n\n'
            )
            final_body = custom_message + slots_text
            send_gmail(final_body)
            
            with open(history_file, "w", encoding="utf-8") as f:
                f.write(slots_text)
    else:
        print("空きはありませんでした。")

asyncio.run(run_reservation_bot())
