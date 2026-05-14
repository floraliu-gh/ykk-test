from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    ImageSendMessage,
    AudioSendMessage,
)
import random
import os, requests, csv, traceback, time
from io import StringIO
import tempfile
from mutagen import File as MutagenFile
from collections import OrderedDict

app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# Google Sheet CSV
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1OS7fRHSzIoJlVymXFp_A06p1Wj0G719-EiPRPpV0_tg/export?format=csv"

# ========= 快取設定 =========
SHEET_CACHE = []
SHEET_LAST_FETCH = 0
SHEET_TTL = 10

AUDIO_DURATION_CACHE = {}

user_cache = OrderedDict()
MAX_USERS = 800
# ===========================

def get_sheet_rows():
    global SHEET_CACHE, SHEET_LAST_FETCH
    now = time.time()

    if SHEET_CACHE and now - SHEET_LAST_FETCH < SHEET_TTL:
        return SHEET_CACHE

    res = requests.get(SHEET_CSV_URL, timeout=10)
    res.raise_for_status()

    decoded_content = res.content.decode("utf-8-sig")
    f = StringIO(decoded_content)
    reader = csv.DictReader(f)

    SHEET_CACHE = list(reader)[1:]
    SHEET_LAST_FETCH = now
    return SHEET_CACHE


def get_audio_duration_ms(url):
    """回傳假長度，跳過下載過程"""
    # 如果你有快取就回傳快取
    if url in AUDIO_DURATION_CACHE:
        return AUDIO_DURATION_CACHE[url]
    return 3000

def get_images(keyword):
    """搜尋 Google Sheet"""
    try:
        rows = get_sheet_rows()
        results = []

        keyword_clean = keyword.replace(" ", "").lower()
        if not keyword_clean:
            return []

        use_artist = keyword_clean.startswith(("/", "／", "∕"))
        random_pick = keyword_clean.startswith("🎲")

        if use_artist:
            keyword_clean = keyword_clean[1:]

        if random_pick:
            valid_rows = [row for row in rows if row.get("圖片網址", "").strip()]
            if not valid_rows:
                return []
                
            picked = random.choice(valid_rows)
            return [{
                "no": picked.get("編號", "").strip(),
                "keyword": picked.get("關鍵字", "").strip(),
                "url": picked.get("圖片網址", "").strip(),
                "episode": picked.get("集數資訊", "").strip(),
                "audio": picked.get("音檔", "").strip(),
                "artist": picked.get("藝人", "").strip()
            }]

        for row in rows:
            kw = row.get("藝人" if use_artist else "關鍵字", "").strip().lower()
            if all(ch in kw for ch in keyword_clean):
                results.append({
                    "no": row.get("編號", "").strip(),
                    "keyword": row.get("關鍵字", "").strip(),
                    "url": row.get("圖片網址", "").strip(),
                    "episode": row.get("集數資訊", "").strip(),
                    "audio": row.get("音檔", "").strip(),
                    "artist": row.get("藝人", "").strip()
                })

        return results
    except Exception:
        traceback.print_exc()
        return []

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/ping", methods=["GET"])
def ping():
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()

    last_results = user_cache.get(user_id, [])

    # ===== 數字選擇圖片 =====
    if user_input.isdigit():
        if last_results:
            selected = [r for r in last_results if r["no"] == user_input]
            if selected:
                data = selected[0]
                
                msgs = []
                raw_urls = data["url"].replace('\n', ',').split(',')
                valid_urls = [u.strip() for u in raw_urls if u.strip()]

                has_episode = bool(data.get('episode', '').strip())
                has_audio = bool(data.get("audio", '').strip())
                
                max_images = 5
                if has_episode: max_images -= 1
                if has_audio: max_images -= 1

                for u in valid_urls[:max_images]:
                    msgs.append(
                        ImageSendMessage(
                            original_content_url=u,
                            preview_image_url=u
                        )
                    )

                if has_episode:
                    msgs.append(TextSendMessage(text=data['episode'].strip()))
                
                if not msgs and not has_audio:
                    return

                if has_audio:
                    duration = get_audio_duration_ms(data["audio"].strip())
                    msgs.append(AudioSendMessage(
                        original_content_url=data["audio"].strip(),
                        duration=duration
                    ))

                try:
                    line_bot_api.reply_message(event.reply_token, msgs)
                except Exception as e:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text=f"LINE 拒絕傳送！錯誤：\n{str(e)}")
                    )
                return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒有這張圖片餒！")
        )
        return

    # ===== 關鍵字搜尋 =====
    results = get_images(user_input)

    user_cache[user_id] = results
    user_cache.move_to_end(user_id)
    if len(user_cache) > MAX_USERS:
        user_cache.popitem(last=False)
    
    if results:
        if len(results) == 1:
            data = results[0]
            msgs = []
            raw_urls = data["url"].replace('\n', ',').split(',')
            valid_urls = [u.strip() for u in raw_urls if u.strip()]

            has_episode = bool(data.get('episode', '').strip())
            has_audio = bool(data.get("audio", '').strip())
            
            max_images = 5
            if has_episode: max_images -= 1
            if has_audio: max_images -= 1

            for u in valid_urls[:max_images]:
                msgs.append(
                    ImageSendMessage(
                        original_content_url=u,
                        preview_image_url=u
                    )
                )
            if has_episode:
                msgs.append(TextSendMessage(text=data['episode'].strip()))
                
            if not msgs:
                return
            
            if has_audio:
                duration = get_audio_duration_ms(data["audio"].strip())
                msgs.append(AudioSendMessage(
                    original_content_url=data["audio"].strip(),
                    duration=duration
                ))

            # --- 最重要的防護罩，絕對不能漏掉！ ---
            try:
                line_bot_api.reply_message(event.reply_token, msgs)
            except Exception as e:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"LINE 拒絕傳送！錯誤：\n{str(e)}")
                )
            return

        # 多筆結果 -> 列表顯示 (處理字數過長問題)
        reply_messages = []
        current_text = "請輸入圖片編號以查看圖片：\n"
        
        # 用來計算當前累積了多少字，這裡設定 4000 (上限是 5000)
        MAX_CHARS = 4000 

        for data in results:
            line = f"{data['no']}. {data['keyword']}\n"
            
            # 如果加上這一行會超過單一訊息限制，就先把目前的存成一個訊息框
            if len(current_text) + len(line) > MAX_CHARS:
                reply_messages.append(TextSendMessage(text=current_text.strip()))
                current_text = "" # 清空，準備裝下一批
                
                if len(reply_messages) >= 5:
                    current_text = "結果太多，僅顯示前 5 頁內容..."
                    break

            current_text += line

        # 把最後剩餘的文字加進去
        if current_text and len(reply_messages) < 5:
            reply_messages.append(TextSendMessage(text=current_text.strip()))

        line_bot_api.reply_message(event.reply_token, reply_messages)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒有這張圖片餒！")
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
